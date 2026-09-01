"""Telegram notifications — best-effort, never raises into the caller.

Configured via optional .env vars TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
(loaded by config.load_config). When either is absent the notifier is
disabled and send() becomes a no-op.
"""

from __future__ import annotations

import logging

import requests

from .config import TELEGRAM_ENV_VARS

log = logging.getLogger("notify")

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None):
        self._token = token
        self._chat_id = chat_id
        self.enabled = bool(token and chat_id)

    @classmethod
    def from_config(cls, config: dict) -> "TelegramNotifier":
        return cls(
            config.get(TELEGRAM_ENV_VARS[0]),   # TELEGRAM_BOT_TOKEN
            config.get(TELEGRAM_ENV_VARS[1]),   # TELEGRAM_CHAT_ID
        )

    def send(self, text: str) -> None:
        """Fire a Telegram message. Failures are logged, never propagated."""
        if not self.enabled:
            log.debug("Telegram disabled — skipping: %s", text)
            return
        try:
            resp = requests.post(
                _API.format(token=self._token),
                json={"chat_id": self._chat_id, "text": text},
                timeout=10,
            )
            if resp.status_code != 200:
                log.warning("Telegram send failed: HTTP %s — %s", resp.status_code, resp.text[:200])
        except Exception:
            log.exception("Telegram send error")
