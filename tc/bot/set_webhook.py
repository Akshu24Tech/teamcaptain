"""Register (or remove) the Telegram webhook for always-on hosting.

    python -m tc.bot.set_webhook            # point Telegram at PUBLIC_URL/telegram/webhook
    python -m tc.bot.set_webhook --delete   # revert to local long-polling (tc.bot.poller)

Needs TELEGRAM_BOT_TOKEN, PUBLIC_URL and WEBHOOK_SECRET in the environment / .env.
Run it once after each deploy (or whenever PUBLIC_URL or the secret changes).
"""

from __future__ import annotations

import asyncio
import sys

from tc.bot.telegram import TelegramClient, TelegramError
from tc.config import settings


async def set_webhook() -> None:
    url = f"{settings.public_url.rstrip('/')}/telegram/webhook"
    tg = TelegramClient()
    try:
        await tg.call(
            "setWebhook",
            url=url,
            secret_token=settings.webhook_secret or None,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        info = await tg.call("getWebhookInfo")
        print(f"Webhook set to {url}")
        print(f"getWebhookInfo: {info}")
    finally:
        await tg.close()


async def delete_webhook() -> None:
    tg = TelegramClient()
    try:
        await tg.call("deleteWebhook", drop_pending_updates=False)
        print("Webhook deleted. The bot can now run with long polling (tc.bot.poller).")
    finally:
        await tg.close()


def main() -> None:
    delete = "--delete" in sys.argv[1:]
    if not delete and not settings.public_url:
        raise TelegramError("PUBLIC_URL is not set; cannot register a webhook")
    asyncio.run(delete_webhook() if delete else set_webhook())


if __name__ == "__main__":
    main()
