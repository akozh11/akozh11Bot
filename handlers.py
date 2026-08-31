"""
Хендлеры команд, колбэков инлайн-кнопок (модуль C) и пассивного сбора
метрик через обновления реакций/комментариев (модуль D).
"""
from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageReactionCountUpdated
import analytics_service
import config
import database
import gemini_service
import github_service
import keyboards
import pipeline
import states
import telegram_utils
import text_utils
import html

log = logging.getLogger(__name__)
router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# --------------------------------- Команды ---------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Этот бот приватный.")
        return
    await message.answer(
        "Привет! Я слежу за GitHub-репозиториями и готовлю черновики постов "
        "для согласования.\n\n"
        "Команды:\n"
        "/add_repo &lt;ссылка&gt; — сделать пост по конкретному репозиторию\n"
        "/stats — статистика за последние 7 дней\n"
        "/top_projects — топ постов за всё время"
    )


@router.message(Command("add_repo"))
async def cmd_add_repo(message: Message, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        return

    arg = message.text.partition(" ")[2].strip()
    if not arg:
        # Используем &lt; и &gt; вместо скобок < >
        await message.answer("Использование: /add_repo &lt;ссылка или owner/repo&gt;")
        return

    parsed = text_utils.parse_owner_repo(arg)
    if not parsed:
        await message.answer("Не смог распознать ссылку на репозиторий GitHub.")
        return

    owner, repo = parsed
    full_name = f"{owner}/{repo}"
    url = f"https://github.com/{full_name}"

    # Безопасное экранирование строк через стандартный html.escape
    safe_full_name = html.escape(full_name)

    status_msg = await message.answer(
        f"Собираю данные по <b>{safe_full_name}</b> и прошу Gemini написать пост…"
    )

    try:
        await pipeline.process_new_repo_event(
            bot,
            full_name=full_name,
            url=url,
            dedup_suffix=f"manual:{int(time.time())}",
            source="manual",
        )
        await status_msg.edit_text(
            f"Готово! Черновик по <b>{safe_full_name}</b> отправлен выше."
        )
    except Exception:
        log.exception("Ошибка обработки /add_repo для %s", full_name)
        try:
            await status_msg.edit_text("Что-то пошло не так при генерации поста. Проверьте логи.")
        except Exception:
            await message.answer("Что-то пошло не так при генерации поста. Проверьте логи.")


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    text = await analytics_service.build_quick_stats_text(days=7)
    await message.answer(text)


@router.message(Command("top_projects"))
async def cmd_top_projects(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    text = await analytics_service.build_top_projects_text(limit=10, days=None)
    await message.answer(text)


# ------------------------- Колбэки кнопок черновика -------------------------

@router.callback_query(F.data.startswith("pub:"))
async def cb_publish(query: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Недоступно", show_alert=True)
        return
    post_id = int(query.data.split(":", 1)[1])
    post = await database.get_post(post_id)
    if post is None:
        await query.answer("Черновик не найден", show_alert=True)
        return
    if post["status"] != "draft":
        await query.answer("Этот пост уже обработан", show_alert=True)
        return

    sent = await telegram_utils.safe_send_message(
        bot,
        config.CHANNEL_ID,
        post["draft_text"],
        reply_markup=keyboards.published_keyboard(post_id, post["repo_url"]),
    )
    if sent is None:
        await query.answer("Не удалось опубликовать, проверьте логи", show_alert=True)
        return

    await database.publish_post(post_id, sent.message_id)
    await query.message.edit_text(query.message.text + "\n\n✅ Опубликовано в канале.", reply_markup=None)
    await query.answer("Опубликовано!")


@router.callback_query(F.data.startswith("rej:"))
async def cb_reject(query: CallbackQuery) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Недоступно", show_alert=True)
        return
    post_id = int(query.data.split(":", 1)[1])
    await database.reject_post(post_id)
    await query.message.edit_text(query.message.text + "\n\n❌ Отклонено.", reply_markup=None)
    await query.answer("Черновик отклонён")


@router.callback_query(F.data.startswith("regen:"))
async def cb_regenerate_prompt(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Недоступно", show_alert=True)
        return
    post_id = int(query.data.split(":", 1)[1])
    await state.update_data(post_id=post_id)
    await state.set_state(states.DraftEditing.waiting_for_regen_note)
    await query.message.reply(
        "Что поправить при перегенерации? Опишите пожелание одним сообщением, "
        "или отправьте «-», чтобы просто попробовать другой вариант."
    )
    await query.answer()


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit_prompt(query: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(query.from_user.id):
        await query.answer("Недоступно", show_alert=True)
        return
    post_id = int(query.data.split(":", 1)[1])
    await state.update_data(post_id=post_id)
    await state.set_state(states.DraftEditing.waiting_for_manual_text)
    await query.message.reply("Пришлите новый текст поста целиком (HTML-разметка Telegram допустима).")
    await query.answer()


@router.callback_query(F.data.startswith("disc:"))
async def cb_discuss_click(query: CallbackQuery) -> None:
    post_id = int(query.data.split(":", 1)[1])
    await database.bump_metric(post_id, "discuss_clicks")
    alert_text = "Комментарии — прямо под этим постом в канале 👇"
    if config.DISCUSSION_GROUP_URL:
        alert_text += f"\n{config.DISCUSSION_GROUP_URL}"
    await query.answer(alert_text, show_alert=True)


# ---------------- FSM: ответы на регенерацию / ручную правку ----------------

@router.message(states.DraftEditing.waiting_for_regen_note)
async def on_regen_note(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    post_id = data["post_id"]
    await state.clear()

    note = None if message.text.strip() == "-" else message.text.strip()
    post = await database.get_post(post_id)
    if post is None:
        await message.answer("Черновик не найден.")
        return

    status_msg = await message.answer("Перегенерирую пост…")
    context = await github_service.build_repo_context(post["repo_full_name"], post["repo_url"])
    generated = await gemini_service.generate_post(context, refinement=note)
    new_text = pipeline.assemble_post_html(generated, post["repo_url"])
    await database.update_draft_text(post_id, new_text, tags=generated["tags"])

    await status_msg.delete()
    await message.answer(
        f"🔄 Новая версия черновика #{post_id}\n\n{new_text}",
        reply_markup=keyboards.draft_keyboard(post_id),
    )


@router.message(states.DraftEditing.waiting_for_manual_text)
async def on_manual_edit(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    post_id = data["post_id"]
    await state.clear()

    await database.update_draft_text(post_id, message.html_text)
    await message.answer(
        f"✏️ Черновик #{post_id} обновлён вручную.",
        reply_markup=keyboards.draft_keyboard(post_id),
    )


# --------------- Пассивный сбор метрик: реакции и комментарии ---------------

@router.message_reaction_count()
async def on_reaction_count_update(update: MessageReactionCountUpdated) -> None:
    """Telegram присылает это обновление при изменении реакций на посте в канале
    (боту нужны права администратора канала)."""
    post = await database.get_post_by_channel_message(update.message_id)
    if post is None:
        return
    total = sum(r.total_count for r in update.reactions)
    await database.set_metric_counts(post["id"], reactions=total)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_discussion_message(message: Message) -> None:
    """Best-effort подсчёт комментариев: если к каналу привязана группа
    обсуждений (DISCUSSION_GROUP_ID) и бот состоит в ней, каждое сообщение,
    отвечающее на автоматически пересланный пост канала, засчитывается как
    комментарий к этому посту.

    Поведение зависит от того, как Telegram передаёт метаданные пересылки
    (forward_origin / forward_from_message_id) — при необходимости
    подстройте под текущую версию Bot API.
    """
    if not config.DISCUSSION_GROUP_ID or message.chat.id != config.DISCUSSION_GROUP_ID:
        return
    if not message.reply_to_message:
        return

    origin_message_id = _extract_forwarded_channel_message_id(message.reply_to_message)
    if origin_message_id is None:
        return

    post = await database.get_post_by_channel_message(origin_message_id)
    if post is None:
        return

    await database.bump_metric(post["id"], "comments_count")


def _extract_forwarded_channel_message_id(msg: Message) -> int | None:
    origin = getattr(msg, "forward_origin", None)
    if origin is not None and getattr(origin, "message_id", None):
        return origin.message_id
    # Фолбэк для более старых версий Bot API/aiogram
    return getattr(msg, "forward_from_message_id", None)
