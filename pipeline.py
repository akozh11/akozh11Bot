"""
Общий пайплайн: получить данные о репозитории → сгенерировать пост через
Gemini → сохранить черновик в БД → отправить админам на согласование.

Используется из: /add_repo, GitHub webhook и периодического опроса (scheduler).
"""
from __future__ import annotations

import logging

from aiogram import Bot

import config
import database
import gemini_service
import github_service
import keyboards
import telegram_utils
import text_utils

log = logging.getLogger(__name__)


def assemble_post_html(generated: dict, url: str) -> str:
    """Собирает финальный HTML-текст поста из структурированного ответа Gemini.

    Мы сами расставляем HTML-теги вокруг уже экранированного текста — это
    исключает ошибки парсинга Telegram (Can't parse entities), так как
    экранируется весь пользовательский/сгенерированный текст.
    """
    title = text_utils.escape_html(generated["title"])
    body = text_utils.escape_html(generated["body"])
    cta = text_utils.escape_html(generated["cta_question"])
    tags_line = text_utils.format_tags(generated["tags"])

    parts = [
        f"<b>{title}</b>",
        "",
        body,
        "",
        f"❓ {cta}",
        "",
        tags_line,
        "",
        f"🔗 {text_utils.escape_html(url)}",
    ]
    return text_utils.truncate("\n".join(p for p in parts if p is not None))


async def process_new_repo_event(
    bot: Bot, *, full_name: str, url: str, dedup_suffix: str, source: str
) -> None:
    """Полный цикл обработки одного события (новый релиз/коммит/ручной запрос)."""
    dedup_key = f"{full_name}:{dedup_suffix}"
    if await database.is_duplicate(dedup_key):
        log.info("Пропускаю дубликат: %s", dedup_key)
        return

    await database.upsert_tracked_repo(full_name, url, source=source)

    context = await github_service.build_repo_context(full_name, url)
    generated = await gemini_service.generate_post(context)
    final_text = assemble_post_html(generated, url)

    post_id = await database.create_draft_post(
        repo_full_name=full_name,
        repo_url=url,
        dedup_key=dedup_key,
        tags=generated["tags"],
        draft_text=final_text,
    )

    await _send_draft_to_admins(bot, post_id, final_text)


async def _send_draft_to_admins(bot: Bot, post_id: int, text: str) -> None:
    markup = keyboards.draft_keyboard(post_id)
    for admin_id in config.ADMIN_IDS:
        msg = await telegram_utils.safe_send_message(
            bot,
            admin_id,
            f"🆕 Черновик поста #{post_id}\n\n{text}",
            reply_markup=markup,
            disable_web_page_preview=False,
        )
        if msg is not None:
            await database.set_admin_message_id(post_id, msg.message_id)
