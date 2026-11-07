"""Run the Telegram bot with long polling: `python -m tc.bot.poller`.

Long polling needs no public URL, so it works on a laptop behind college Wi-Fi.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import inngest

from tc.bot.handlers import handle_update
from tc.bot.telegram import TelegramClient
from tc.db import create_tables
from tc.inngest_client import client

log = logging.getLogger("teamcaptain.bot")


async def send_event(name: str, data: dict[str, Any]) -> None:
    await client.send(inngest.Event(name=name, data=data))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await create_tables()
    tg = TelegramClient()
    me = await tg.call("getMe")
    log.info("Bot @%s is listening", me.get("username"))
    offset: int | None = None
    try:
        while True:
            try:
                updates = await tg.get_updates(offset)
            except Exception:
                log.exception("getUpdates failed; retrying in 5s")
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await handle_update(update, tg, send_event)
                except Exception:
                    # One bad update must never stop the bot.
                    log.exception("Failed to handle update %s", update.get("update_id"))
    finally:
        await tg.close()


if __name__ == "__main__":
    asyncio.run(main())
