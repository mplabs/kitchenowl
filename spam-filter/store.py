import sqlite3
import logging
from contextlib import contextmanager

import config

logger = logging.getLogger(__name__)


@contextmanager
def _db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                folder    TEXT    NOT NULL,
                uid       INTEGER NOT NULL,
                verdict   TEXT    NOT NULL,
                confidence REAL   NOT NULL,
                reason    TEXT,
                action    TEXT,
                subject   TEXT,
                sender    TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (folder, uid)
            )
        """)
    logger.info("Database initialised at %s", config.DB_PATH)


def is_processed(folder: str, uid: int) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_messages WHERE folder=? AND uid=?",
            (folder, uid),
        ).fetchone()
    return row is not None


def mark_processed(
    folder: str,
    uid: int,
    verdict: str,
    confidence: float,
    reason: str,
    action: str,
    subject: str = "",
    sender: str = "",
) -> None:
    with _db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO processed_messages
                (folder, uid, verdict, confidence, reason, action, subject, sender)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (folder, uid, verdict, confidence, reason, action, subject, sender),
        )
