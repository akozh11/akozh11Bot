"""Инлайн-клавиатуры для черновиков и опубликованных постов."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config


def draft_keyboard(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Опубликовать", callback_data=f"pub:{post_id}")],
            [
                InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"regen:{post_id}"),
                InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{post_id}"),
            ],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rej:{post_id}")],
        ]
    )


def published_keyboard(post_id: int, repo_url: str) -> InlineKeyboardMarkup:
    if config.ENABLE_CLICK_TRACKING:
        # Кнопка ведёт на наш редирект (/r/<post_id>/github), который логирует
        # клик в БД и уже потом перенаправляет на настоящий репозиторий.
        github_url = f"{config.PUBLIC_BASE_URL}/r/{post_id}/github"
    else:
        github_url = repo_url

    buttons = [
        InlineKeyboardButton(text="🔗 GitHub", url=github_url),
        InlineKeyboardButton(text="💬 Обсудить", callback_data=f"disc:{post_id}"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
