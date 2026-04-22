import os


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name!r} is not set")
    return value


# IMAP settings
IMAP_HOST = _required("IMAP_HOST")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = _required("IMAP_USER")
IMAP_PASSWORD = _required("IMAP_PASSWORD")
IMAP_USE_SSL = os.environ.get("IMAP_USE_SSL", "true").lower() == "true"
IMAP_INBOX = os.environ.get("IMAP_INBOX", "INBOX")
IMAP_JUNK_FOLDER = os.environ.get("IMAP_JUNK_FOLDER", "Junk")
IMAP_POTENTIAL_SPAM_FOLDER = os.environ.get("IMAP_POTENTIAL_SPAM_FOLDER", "Potential Spam")

# Ollama settings
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Classification thresholds
SPAM_CONFIDENCE_THRESHOLD = float(os.environ.get("SPAM_CONFIDENCE_THRESHOLD", "0.85"))
POTENTIAL_SPAM_THRESHOLD = float(os.environ.get("POTENTIAL_SPAM_THRESHOLD", "0.60"))

# Service settings
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # seconds
DB_PATH = os.environ.get("DB_PATH", "/data/spam_filter.db")
