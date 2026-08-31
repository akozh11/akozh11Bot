"""Обёртки над методами Bot для устойчивости к лимитам Telegram (ошибка 429)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

log = logging.getLogger(__name__)


async def safe_send_message(bot: Bot, chat_id: int | str, text: str, **kwargs: Any):
    """send_message с повторной попыткой при ошибке 429 (Too Many Requests).

    Возвращает Message при успехе, иначе None (ошибка уже залогирована).
    """
    for attempt in range(3):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as e:
            log.warning(
                "Flood control от Telegram: жду %.1f сек. (попытка %d/3)",
                e.retry_after,
                attempt + 1,
            )
            await asyncio.sleep(e.retry_after)
        except TelegramAPIError:
            log.exception("Ошибка Telegram API при отправке сообщения в %s", chat_id)
            return None
    log.error("Не удалось отправить сообщение в %s после нескольких попыток", chat_id)
    return None
