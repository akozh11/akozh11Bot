"""
Интеграция с Gemini API (пакет google-genai) — генерация текста постов и
еженедельных рекомендаций на основе накопленной аналитики.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from google import genai
from google.genai import errors, types

import config

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Захватывающий заголовок поста"},
        "body": {
            "type": "string",
            "description": "Глубокий технический разбор проекта и его пользы, 2-4 абзаца",
        },
        "cta_question": {
            "type": "string",
            "description": "Открытый вопрос к читателям для вовлечения в обсуждение",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 тегов без решётки, например: python, ai, utility",
        },
    },
    "required": ["title", "body", "cta_question", "tags"],
}

_SYSTEM_PROMPT = """\
Ты — редактор технического Telegram-канала о разработке и опенсорсе.
Пишешь подробные, увлекательные лонгпосты о GitHub-проектах: экспертным,
живым тоном, который провоцирует конструктивное обсуждение без негатива.

Структура, которую ты должен выдержать в поле body:
1. С какой проблемой сталкиваются разработчики и как её решает проект.
2. Технический разбор: как это устроено, из чего сделано, что интересного
   в подходе или архитектуре.
3. Почему это может быть полезно читателю прямо сейчас.

Не используй HTML- или Markdown-разметку в ответе — только чистый текст.
Отвечай строго в формате JSON согласно предоставленной схеме.
"""


def _build_prompt(repo_context: dict[str, Any], refinement: str | None = None) -> str:
    release = repo_context.get("latest_release")
    release_txt = (
        f"Последний релиз: {release['tag']} — {release['name']}\n{release['body']}"
        if release
        else "Свежих релизов нет, ориентируйся на README и последние коммиты."
    )
    commits_txt = "\n".join(
        f"- {c['sha']}: {c['message']}" for c in repo_context.get("recent_commits", [])
    )
    prompt = f"""\
Репозиторий: {repo_context['full_name']}
Ссылка: {repo_context['url']}
Описание: {repo_context.get('description', '')}
Язык: {repo_context.get('language', '')}
Темы: {', '.join(repo_context.get('topics', []))}
Звёзды: {repo_context.get('stars', 0)}

{release_txt}

Недавние коммиты:
{commits_txt}

Выдержка из README:
{repo_context.get('readme_excerpt', '')[:4000]}
"""
    if refinement:
        prompt += f"\n\nДополнительное пожелание от редактора для этой версии: {refinement}\n"
    return prompt


def _extract_json(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    return t.strip()


def _fallback_post(repo_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": repo_context.get("full_name", "Новый проект"),
        "body": (
            "Не удалось автоматически сгенерировать разбор проекта. "
            "Отредактируйте черновик вручную или нажмите «Перегенерировать»."
        ),
        "cta_question": "Как думаете, стоит попробовать этот инструмент?",
        "tags": ["needs_review"],
    }


async def _generate_content_with_retry(
    client: genai.Client,
    model: str,
    contents: str,
    config_obj: types.GenerateContentConfig,
    retries: int = 3,
    delay: float = 2.0,
) -> Any:
    """Выполняет асинхронный запрос к Gemini API с повторными попытками при ошибках 503/500/429."""
    for attempt in range(1, retries + 1):
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config_obj,
            )
        except (errors.ServerError, errors.APIError) as e:
            if attempt == retries:
                raise e
            log.warning(
                "Gemini API вернул ошибку (%s). Повтор %d/%d через %.1f сек...",
                e, attempt, retries, delay
            )
            await asyncio.sleep(delay)
            delay *= 2


async def generate_post(repo_context: dict[str, Any], refinement: str | None = None) -> dict[str, Any]:
    """Генерирует структурированный черновик поста."""
    client = _get_client()
    prompt = _build_prompt(repo_context, refinement)
    gen_config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=_POST_SCHEMA,
        temperature=0.9,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    try:
        response = await _generate_content_with_retry(
            client=client,
            model=config.GEMINI_MODEL,
            contents=prompt,
            config_obj=gen_config,
        )
        data = json.loads(_extract_json(response.text))
    except Exception:
        log.exception(
            "Ошибка генерации поста через Gemini для %s", repo_context.get("full_name")
        )
        data = _fallback_post(repo_context)

    data.setdefault("tags", [])
    data.setdefault("title", repo_context.get("full_name", ""))
    data.setdefault("body", "")
    data.setdefault("cta_question", "")
    return data


async def generate_weekly_recommendations(stats_summary: str) -> str:
    """Просит Gemini дать краткие советы на основе сводки за неделю."""
    client = _get_client()
    prompt = f"""\
Вот сводка вовлечённости аудитории Telegram-канала о разработке за последнюю неделю:

{stats_summary}

Дай 2-4 коротких практических совета для автора канала: на какие темы/теги
делать упор, что стоит попробовать в следующей неделе. Пиши кратко,
без разметки, как будто это заметки для себя.
"""
    gen_config = types.GenerateContentConfig(
        temperature=0.7,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    try:
        response = await _generate_content_with_retry(
            client=client,
            model=config.GEMINI_MODEL,
            contents=prompt,
            config_obj=gen_config,
        )
        return (response.text or "").strip()
    except Exception:
        log.exception("Ошибка генерации еженедельных рекомендаций")
        return "Не удалось получить рекомендации от Gemini на этой неделе."