"""
Local AI spam filter — main entry point.

Loop:
  1. Connect to IMAP.
  2. Process any unchecked messages in INBOX.
  3. Wait in IMAP IDLE for new-mail notifications (or poll every POLL_INTERVAL s).
  4. On notification, go back to step 2.
  5. On any error, reconnect after a short back-off.
"""

import logging
import signal
import sys
import time

import config
import store
from classifier import classify
from imap_client import ImapManager
from preprocessor import parse_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("spam-filter")

_running = True


def _handle_signal(sig, _frame):
    global _running
    logger.info("Received signal %d — shutting down", sig)
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Per-message logic
# ---------------------------------------------------------------------------

def process_message(imap: ImapManager, uid: int) -> None:
    if store.is_processed(config.IMAP_INBOX, uid):
        return

    logger.info("UID %d — fetching", uid)
    try:
        raw = imap.fetch_raw(uid)
    except Exception as exc:
        logger.error("UID %d — fetch failed: %s", uid, exc)
        return

    headers, body = parse_message(raw)
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    logger.info("UID %d — classifying  from=%r  subject=%r", uid, sender, subject)

    verdict, confidence, reason = classify(headers, body)
    logger.info(
        "UID %d — verdict=%-4s  confidence=%.2f  reason=%s",
        uid, verdict, confidence, reason,
    )

    action = _take_action(imap, uid, verdict, confidence)
    store.mark_processed(
        config.IMAP_INBOX, uid, verdict, confidence, reason, action,
        subject=subject, sender=sender,
    )


def _take_action(imap: ImapManager, uid: int, verdict: str, confidence: float) -> str:
    if verdict == "spam":
        if confidence >= config.SPAM_CONFIDENCE_THRESHOLD:
            imap.move_to_junk(uid)
            logger.info("UID %d — moved to Junk", uid)
            return "junk"
        if confidence >= config.POTENTIAL_SPAM_THRESHOLD:
            imap.move_to_potential_spam(uid)
            logger.info("UID %d — moved to Potential Spam", uid)
            return "potential_spam"

    # Ham or low-confidence spam — leave in INBOX, just mark as checked.
    imap.mark_checked(uid)
    return "none"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _process_inbox(imap: ImapManager) -> None:
    uids = imap.fetch_unchecked_uids()
    if uids:
        logger.info("Found %d unchecked message(s)", len(uids))
    for uid in uids:
        if not _running:
            break
        process_message(imap, uid)


def run() -> None:
    store.init_db()
    imap = ImapManager()
    back_off = 5

    while _running:
        try:
            imap.connect()
            back_off = 5  # reset after a successful connect

            # Catch up on any mail that arrived while we were down.
            _process_inbox(imap)

            while _running:
                try:
                    got_notification = imap.idle_wait(timeout=config.POLL_INTERVAL)
                    if got_notification:
                        _process_inbox(imap)
                except Exception as exc:
                    logger.warning("IDLE error (%s) — reconnecting", exc)
                    break

        except Exception as exc:
            logger.error("Connection error: %s", exc)
        finally:
            imap.disconnect()

        if _running:
            logger.info("Reconnecting in %ds…", back_off)
            time.sleep(back_off)
            back_off = min(back_off * 2, 120)

    logger.info("Stopped.")


if __name__ == "__main__":
    run()
