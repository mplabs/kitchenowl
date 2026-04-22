# Spam Filter — Self-Improving Feedback Loop

## Goal

Reduce false negatives by turning the user's own mail-client actions into training signal. Three additive phases — each one is independently shippable and provides value on its own.

| Phase | What it adds | Depends on |
|-------|--------------|-----------|
| 1 — Feedback Capture | IMAP move monitoring → confirmed examples DB | nothing |
| 2 — Dynamic Few-Shot | Prompt examples sampled from that DB | Phase 1 pool ≥ ~10 per label |
| 3 — RAG | Retrieve semantically nearest examples per message | Phase 2 |

---

## Phase 1 — IMAP Move Monitoring

### Concept

A message appearing in `Junk` **without** our `$SpamChecked` flag was moved by the user — a confirmed spam example we missed. A message moved back from `Junk` to `INBOX` is a confirmed ham example (false positive correction).

### Data model

New table in `store.py`:

```sql
CREATE TABLE confirmed_examples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT NOT NULL CHECK (label IN ('spam','ham')),
    sender       TEXT,
    subject      TEXT,
    body_snippet TEXT,          -- first ~500 chars after HTML stripping
    source       TEXT,          -- 'manual_move_to_junk' | 'rescue_from_junk' | 'seed'
    message_id   TEXT UNIQUE,   -- dedup against repeated IMAP UIDs
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_examples_label_date ON confirmed_examples(label, created_at DESC);
```

### New module: `feedback_watcher.py`

A second IDLE loop, separate IMAP connection (IDLE is stateful per folder), running in its own thread.

Responsibilities:
- Watch `IMAP_JUNK_FOLDER` for new arrivals
  - If the message lacks `$SpamChecked` → `label='spam', source='manual_move_to_junk'`
- Watch `IMAP_INBOX` for rescues
  - If a UID that previously had `$Junk` flag reappears in INBOX → `label='ham', source='rescue_from_junk'`
  - Tag rescues with `$SpamRescued` so we never double-count

### Edge cases

- On first run, scan only the last `FEEDBACK_INITIAL_BACKFILL` messages (default 50) to avoid backfilling years of history.
- Skip messages older than `FEEDBACK_RESCUE_WINDOW_DAYS` for rescue detection (default 7).
- Dedup on `Message-ID` header — users may move the same message multiple times.

### Config additions

```
FEEDBACK_ENABLED=true
FEEDBACK_RESCUE_WINDOW_DAYS=7
FEEDBACK_INITIAL_BACKFILL=50
```

### Implementation steps

1. Add `confirmed_examples` table to `store.init_db()`.
2. Add `store.add_example(label, sender, subject, body, source, message_id)`.
3. Create `feedback_watcher.py` mirroring `imap_client.py` structure.
4. Spawn it from `main.py` as a daemon thread alongside the primary INBOX loop.
5. Share `ImapManager` *class* but give the watcher its own instance (separate IDLE socket).

### Validation

Deploy and leave running for ~2 weeks. Inspect `confirmed_examples` — expect steady growth and correct labelling. No code changes to the classifier yet.

---

## Phase 2 — Dynamic Few-Shot Examples

### Concept

Replace the hardcoded `_FEW_SHOT` block in `classifier.py` with examples sampled from `confirmed_examples` on every call. The prompt now reflects *your* mail, not generic examples.

### Changes to `classifier.py`

- Remove `_FEW_SHOT` constant.
- Add `_build_few_shot() -> str` that:
  1. Samples `FEWSHOT_SPAM_EXAMPLES` spam + `FEWSHOT_HAM_EXAMPLES` ham rows.
  2. Recency-biased: 70% drawn from last 30 days, 30% from older history.
  3. Truncates each `body_snippet` to 200 words so the prompt stays within budget.
- Falls back to a small built-in default pool (3 spam + 3 ham, same as today's hardcoded examples) if `confirmed_examples` has fewer than `FEWSHOT_FALLBACK_THRESHOLD` rows for either label.

### New function in `store.py`

```python
def sample_examples(label: str, n: int, recency_bias: float = 0.7) -> list[dict]:
    """Return n random rows for `label`, weighted toward recent entries."""
```

### Config additions

```
FEWSHOT_SPAM_EXAMPLES=3
FEWSHOT_HAM_EXAMPLES=3
FEWSHOT_FALLBACK_THRESHOLD=4
MAX_PROMPT_CHARS=16000    # hard cap; truncate examples if exceeded
```

### Token-budget safety

After assembling the prompt, check length. If > `MAX_PROMPT_CHARS`, drop the longest example's body snippet first, then shorten the incoming message's body, then drop one example.

### Validation

Before enabling, pull a labelled sample of ~50 messages from `processed_messages`, re-classify with the new prompt, and compare verdicts. Expect parity or improvement on categories where you previously saw false negatives.

---

## Phase 3 — Retrieval-Augmented Few-Shot (RAG)

### Concept

Instead of random-recent sampling, retrieve the **semantically nearest** confirmed examples for each incoming message. Prompt size stays bounded (top-K is fixed); relevance per token goes up.

### Prerequisites

- `sqlite-vec` extension — <https://github.com/asg017/sqlite-vec>. Single .so loaded into the existing SQLite connection; no extra service.
- Embedding model pulled in Ollama: `nomic-embed-text` (768 dim, ~280 MB) or equivalent.

### Data model

Add a vector column via `sqlite-vec`:

```sql
CREATE VIRTUAL TABLE confirmed_examples_vec USING vec0(
    example_id INTEGER PRIMARY KEY,
    embedding  FLOAT[768]
);
```

Keep embeddings in the virtual table so the main row schema stays readable.

Add to `confirmed_examples`:
```sql
ALTER TABLE confirmed_examples ADD COLUMN embed_model TEXT;
-- stores which model produced the embedding, so we can re-embed on upgrades
```

### New module: `embeddings.py`

```python
def embed(text: str) -> list[float]:
    """POST to OLLAMA_URL/api/embeddings. Retries + timeout."""
```

### Retrieval logic (in `classifier.py`)

```python
retrieval_text = f"{subject}\n{body[:800]}"
query_vec = embed(retrieval_text)
spam_examples = store.retrieve_similar(query_vec, label='spam',
                                       k=RAG_TOP_K_SPAM,
                                       min_similarity=RAG_MIN_SIMILARITY)
ham_examples  = store.retrieve_similar(query_vec, label='ham',
                                       k=RAG_TOP_K_HAM,
                                       min_similarity=RAG_MIN_SIMILARITY)
```

### Pool hygiene

- **Dedup on insert**: if cosine similarity ≥ 0.95 vs any existing same-label row, skip.
- **Cap pool**: keep newest 500 rows per label; drop oldest on overflow.
- **Optional MMR**: greedy re-rank of top 2K candidates to balance similarity-to-query against diversity-among-results. Adds ~5 ms per classification. Worth doing if retrieval returns repetitive near-duplicates.

### Backfill

One-shot script `scripts/backfill_embeddings.py`: iterate `confirmed_examples WHERE embed_model IS NULL`, embed in batches, insert into vec table.

### Feature flag

Everything RAG-related is gated by `RAG_ENABLED`. When false, `classifier.py` uses Phase 2 sampling. This lets you A/B the two strategies against each other.

### Config additions

```
RAG_ENABLED=false
OLLAMA_EMBED_MODEL=nomic-embed-text
RAG_TOP_K_SPAM=3
RAG_TOP_K_HAM=3
RAG_MIN_SIMILARITY=0.5
RAG_POOL_CAP_PER_LABEL=500
RAG_DEDUP_THRESHOLD=0.95
```

### Fallback chain

```
Phase 3 (RAG)  ─► embed() fails ─► Phase 2 (dynamic sampling)
Phase 2         ─► pool too small ─► hardcoded default examples
```

Never block classification on a failed improvement.

### Implementation steps

1. Load `sqlite-vec` extension in `store._db()`.
2. Add `confirmed_examples_vec` virtual table + `embed_model` column.
3. Implement `embeddings.py` with retry + 30 s timeout.
4. Write `scripts/backfill_embeddings.py`.
5. Add `store.retrieve_similar(query_vec, label, k, min_similarity)`.
6. Wire retrieval into `_build_few_shot()` behind `RAG_ENABLED`.
7. Add dedup check to `store.add_example()`.
8. Add pool-cap enforcement (on insert, `DELETE … WHERE id NOT IN (SELECT id … ORDER BY created_at DESC LIMIT cap)`).

---

## Observability

Extend `processed_messages`:

```sql
ALTER TABLE processed_messages ADD COLUMN example_ids_used TEXT;  -- JSON array
ALTER TABLE processed_messages ADD COLUMN strategy       TEXT;    -- 'hardcoded'|'sampled'|'rag'
```

Lets you attribute classification mistakes back to the examples in the prompt and compare strategies on the same metric (`COUNT(*) WHERE verdict='ham' AND later-moved-to-Junk`).

---

## Rollout checklist

- [ ] Phase 1: migration, feedback watcher, deploy, let pool grow 2 weeks.
- [ ] Phase 2: switch to dynamic sampling behind default-on flag; verify no regression on labelled sample of 50 messages.
- [ ] Phase 3a: install `sqlite-vec`, pull embedding model, backfill, wire retrieval — flag off.
- [ ] Phase 3a: enable `RAG_ENABLED=true` for 1 week, compare `strategy` accuracy in `processed_messages`.
- [ ] Phase 3b (optional): MMR diversity, pool cap cron, nightly cleanup.

## Non-goals

- Fine-tuning the LLM itself — overkill for this volume.
- A web UI for labelling — the mail client *is* the UI.
- Multi-user support — this is a personal service.
