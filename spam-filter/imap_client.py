import logging
from typing import Optional

from imapclient import IMAPClient

import config

logger = logging.getLogger(__name__)

# Custom IMAP keyword to mark messages we have already evaluated.
# Avoids re-processing on reconnect.
_CHECKED_FLAG = "$SpamChecked"
_JUNK_FLAG = "$Junk"


class ImapManager:
    def __init__(self) -> None:
        self._client: Optional[IMAPClient] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        logger.info("Connecting to %s:%d", config.IMAP_HOST, config.IMAP_PORT)
        self._client = IMAPClient(
            config.IMAP_HOST,
            port=config.IMAP_PORT,
            ssl=config.IMAP_USE_SSL,
            use_uid=True,
        )
        self._client.login(config.IMAP_USER, config.IMAP_PASSWORD)
        logger.info("Authenticated as %s", config.IMAP_USER)
        self._ensure_folders()

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _ensure_folders(self) -> None:
        existing = {info[2] for info in self._client.list_folders()}
        for folder in (config.IMAP_JUNK_FOLDER, config.IMAP_POTENTIAL_SPAM_FOLDER):
            if folder not in existing:
                self._client.create_folder(folder)
                logger.info("Created folder: %s", folder)

    # ------------------------------------------------------------------
    # Message discovery
    # ------------------------------------------------------------------

    def fetch_unchecked_uids(self) -> list[int]:
        """Return UIDs in INBOX that have not yet been evaluated."""
        self._client.select_folder(config.IMAP_INBOX, readonly=False)
        # Search for messages that do NOT carry our checked flag.
        # Falls back to ALL if the server rejects keyword search.
        try:
            uids = self._client.search(["NOT", "KEYWORD", _CHECKED_FLAG])
        except Exception:
            logger.warning("Keyword search unsupported — falling back to ALL")
            uids = self._client.search(["ALL"])
        return list(uids)

    # ------------------------------------------------------------------
    # Message fetching
    # ------------------------------------------------------------------

    def fetch_raw(self, uid: int) -> bytes:
        data = self._client.fetch([uid], ["RFC822"])
        return data[uid][b"RFC822"]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def move_to_junk(self, uid: int) -> None:
        """Set $Junk flag (RFC 8457), copy to Junk folder, delete from INBOX."""
        self._client.select_folder(config.IMAP_INBOX, readonly=False)
        self._set_flag(uid, _JUNK_FLAG)
        self._move(uid, config.IMAP_JUNK_FOLDER)
        logger.debug("UID %d moved to %s", uid, config.IMAP_JUNK_FOLDER)

    def move_to_potential_spam(self, uid: int) -> None:
        """Copy message to Potential Spam folder and delete from INBOX."""
        self._client.select_folder(config.IMAP_INBOX, readonly=False)
        self._move(uid, config.IMAP_POTENTIAL_SPAM_FOLDER)
        logger.debug("UID %d moved to %s", uid, config.IMAP_POTENTIAL_SPAM_FOLDER)

    def mark_checked(self, uid: int) -> None:
        """Tag the message so we do not evaluate it again."""
        self._client.select_folder(config.IMAP_INBOX, readonly=False)
        self._set_flag(uid, _CHECKED_FLAG)

    def _set_flag(self, uid: int, flag: str) -> None:
        try:
            self._client.add_flags([uid], [flag])
        except Exception as exc:
            logger.debug("Could not set flag %r on UID %d: %s", flag, uid, exc)

    def _move(self, uid: int, destination: str) -> None:
        # Mark checked first so a crash mid-move does not leave a re-processable copy.
        self._set_flag(uid, _CHECKED_FLAG)
        try:
            # RFC 6851 MOVE command — most servers support this.
            self._client.move([uid], destination)
        except Exception:
            # Fallback: COPY + delete.
            self._client.copy([uid], destination)
            self._client.delete_messages([uid])
            self._client.expunge()

    # ------------------------------------------------------------------
    # IMAP IDLE
    # ------------------------------------------------------------------

    def idle_wait(self, timeout: int = 300) -> bool:
        """
        Block in IDLE mode for up to *timeout* seconds.
        Returns True if the server sent any notification (new mail likely),
        False on timeout.
        """
        self._client.select_folder(config.IMAP_INBOX, readonly=False)
        self._client.idle()
        responses = self._client.idle_check(timeout=timeout)
        self._client.idle_done()
        return bool(responses)
