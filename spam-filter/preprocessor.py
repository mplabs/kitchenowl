import re
import logging
from email import policy
from email.parser import BytesParser

import html2text

logger = logging.getLogger(__name__)

# Maximum words sent to the LLM (keeps token usage low while capturing the signal)
_MAX_WORDS = 1500

_html_converter = html2text.HTML2Text()
_html_converter.ignore_links = True
_html_converter.ignore_images = True
_html_converter.ignore_emphasis = True


def parse_message(raw: bytes) -> tuple[dict, str]:
    """Return (headers_dict, body_text) from a raw RFC822 message."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    headers = {
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "subject": str(msg.get("Subject", "")),
        "reply_to": str(msg.get("Reply-To", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
    }

    body = _extract_body(msg)
    body = _truncate(body, _MAX_WORDS)
    return headers, body


def _extract_body(msg) -> str:
    plain = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                plain = _safe_get_content(part)
            elif ct == "text/html" and not html:
                html = _safe_get_content(part)
    else:
        ct = msg.get_content_type()
        if ct == "text/plain":
            plain = _safe_get_content(msg)
        elif ct == "text/html":
            html = _safe_get_content(msg)

    body = plain if plain else _html_converter.handle(html) if html else ""
    return _clean(body)


def _safe_get_content(part) -> str:
    try:
        return part.get_content()
    except Exception as exc:
        logger.debug("Could not decode part: %s", exc)
        payload = part.get_payload(decode=True)
        if payload:
            for enc in ("utf-8", "latin-1", "ascii"):
                try:
                    return payload.decode(enc, errors="replace")
                except Exception:
                    continue
    return ""


def _clean(text: str) -> str:
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + " [truncated]"
    return text
