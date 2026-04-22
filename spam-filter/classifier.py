import json
import logging

import requests

import config

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a strict email spam classifier. Analyse the email provided and return ONLY a \
valid JSON object — no prose, no markdown fences.

Spam signals: unsolicited commercial offers, prize/lottery claims, urgency language, \
suspicious sender domains, pharmaceutical/adult offers, phishing attempts, \
mismatched Reply-To vs From domains, excessive capitalisation or exclamation marks.

Ham signals: direct replies in a thread, recognised corporate/personal domains, \
transactional mail (receipts, shipping, 2FA), newsletters the user opted into, \
personal correspondence.

Response schema (JSON only):
{"verdict": "spam" | "ham", "confidence": <float 0.0–1.0>, "reason": "<one sentence>"}
"""

_FEW_SHOT = """\
Examples:

From: winner@lottery-intl-claim.com
Subject: YOU WON $1,000,000 — Claim NOW!!!
Body: Congratulations! You have been selected as our lucky winner. \
Send your bank details immediately to claim your prize before it expires!
→ {"verdict": "spam", "confidence": 0.98, "reason": "Classic prize scam with urgency and suspicious domain."}

From: noreply@github.com
Subject: [kitchenowl] PR #42 — Fix authentication bug
Body: octocat opened a pull request. Review the changes on GitHub.
→ {"verdict": "ham", "confidence": 0.99, "reason": "Legitimate GitHub notification from a known domain."}

From: deals@shop-amazing-prices.biz
Subject: 80% OFF — Today Only!!!
Body: Don't miss our incredible sale. Limited time offer. Buy now or lose forever!
→ {"verdict": "spam", "confidence": 0.93, "reason": "Unsolicited commercial offer with urgency language and unknown domain."}

From: alice@company.com
Subject: Re: Budget meeting tomorrow
Body: Sounds good, see you at 10 am.
→ {"verdict": "ham", "confidence": 0.98, "reason": "Short thread reply from a corporate address."}
"""


def classify(headers: dict, body: str) -> tuple[str, float, str]:
    """
    Returns (verdict, confidence, reason).
    verdict is "spam" or "ham".
    Falls back to ("ham", 0.0, reason) on any error so we never lose mail.
    """
    prompt = _build_prompt(headers, body)
    try:
        response = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.05},
            },
            timeout=90,
        )
        response.raise_for_status()
        raw = response.json().get("response", "{}")
        return _parse_response(raw)
    except requests.exceptions.Timeout:
        msg = "Ollama request timed out"
        logger.warning(msg)
        return "ham", 0.0, msg
    except requests.exceptions.ConnectionError:
        msg = "Cannot reach Ollama — leaving message in INBOX"
        logger.warning(msg)
        return "ham", 0.0, msg
    except Exception as exc:
        msg = f"Unexpected classifier error: {exc}"
        logger.error(msg)
        return "ham", 0.0, msg


def _build_prompt(headers: dict, body: str) -> str:
    return (
        f"{_FEW_SHOT}\n"
        f"Now classify this email:\n"
        f"From: {headers.get('from', '')}\n"
        f"To: {headers.get('to', '')}\n"
        f"Subject: {headers.get('subject', '')}\n"
        f"Reply-To: {headers.get('reply_to', '')}\n"
        f"Body:\n{body}\n"
    )


def _parse_response(raw: str) -> tuple[str, float, str]:
    try:
        data = json.loads(raw)
        verdict = str(data.get("verdict", "ham")).lower().strip()
        confidence = float(data.get("confidence", 0.0))
        reason = str(data.get("reason", ""))

        if verdict not in ("spam", "ham"):
            logger.warning("Unexpected verdict %r — treating as ham", verdict)
            verdict = "ham"
            confidence = 0.0

        confidence = max(0.0, min(1.0, confidence))
        return verdict, confidence, reason
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Could not parse classifier response %r: %s", raw, exc)
        return "ham", 0.0, f"Parse error: {exc}"
