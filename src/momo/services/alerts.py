from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AlertMessage:
    title: str
    body: str
    url: str | None = None


class AlertChannel:
    def send(self, message: AlertMessage) -> None:
        raise NotImplementedError


class ConsoleAlertChannel(AlertChannel):
    def send(self, message: AlertMessage) -> None:
        print(f"[alert] {message.title}\n{message.body}")
        if message.url:
            print(f"  {message.url}")


class TelegramAlertChannel(AlertChannel):
    """Stub for Phase 2 — configure TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID later."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, message: AlertMessage) -> None:
        if not self.token or not self.chat_id:
            raise RuntimeError("Telegram is not configured")
        raise NotImplementedError("Telegram delivery not implemented yet")


def default_channel() -> AlertChannel:
    return ConsoleAlertChannel()
