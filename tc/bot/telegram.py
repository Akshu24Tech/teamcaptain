"""Minimal async Telegram Bot API client (free, no extra library)."""

from __future__ import annotations

import html
from typing import Any

import httpx

from tc.config import settings


class TelegramError(RuntimeError):
    pass


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def mention(telegram_user_id: int, name: str) -> str:
    """An HTML mention that notifies the user even without a public @username."""
    return f'<a href="tg://user?id={telegram_user_id}">{esc(name)}</a>'


def buttons(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in rows
        ]
    }


class TelegramClient:
    def __init__(self, token: str | None = None) -> None:
        token = token or settings.telegram_bot_token
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self._base = f"{settings.telegram_api_base}/bot{token}"
        self._http = httpx.AsyncClient(timeout=settings.telegram_poll_timeout_s + 10)

    async def call(self, method: str, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        response = await self._http.post(f"{self._base}/{method}", json=payload)
        body = response.json()
        if not body.get("ok"):
            raise TelegramError(f"{method} failed: {body.get('description')}")
        return body["result"]

    async def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        return await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            reply_parameters=(
                {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
                if reply_to_message_id else None
            ),
            link_preview_options={"is_disabled": True},
        )

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        await self.call(
            "editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML"
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        return await self.call(
            "getUpdates",
            offset=offset,
            timeout=settings.telegram_poll_timeout_s,
            allowed_updates=["message", "callback_query"],
        )

    async def close(self) -> None:
        await self._http.aclose()
