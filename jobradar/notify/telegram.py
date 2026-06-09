import os
import requests
from jobradar.notify.base import Notifier

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier(Notifier):
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def _configured(self) -> bool:
        return bool(self.token and self.chat_id and
                    self.token != "your_bot_token_here" and
                    self.chat_id != "your_chat_id_here")

    def send(self, posting, score: float, reason: str) -> None:
        text = (
            f"*{posting.title}*\n"
            f"{posting.company} — {posting.location or 'location unknown'}\n"
            f"Score: {score:.1f} | {reason}\n"
            f"[Apply]({posting.url})"
        )
        self.send_text(text)

    def send_text(self, text: str) -> None:
        """Send a plain Markdown message (used for the run heartbeat, §4.7)."""
        if not self._configured():
            print(f"  [telegram] not configured — would send: {text[:80]}")
            return
        resp = requests.post(
            _API.format(token=self.token),
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
