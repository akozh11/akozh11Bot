"""Небольшие текстовые хелперы: экранирование HTML, парсинг ссылок на репозитории."""
from __future__ import annotations

import html
import re

_REPO_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$"
)
_SLUG_RE = re.compile(r"^([\w.-]+)/([\w.-]+)$")


def escape_html(text: str) -> str:
    """Экранирует спецсимволы Telegram HTML-разметки (&, <, >).

    Используется для ЛЮБОГО текста, пришедшего от Gemini или из README,
    перед вставкой в HTML-сообщение — чтобы не словить ошибку
    'Can't parse entities' от Telegram Bot API.
    """
    return html.escape(text or "", quote=False)


def parse_owner_repo(text: str) -> tuple[str, str] | None:
    """Достаёт (owner, repo) из ссылки на GitHub или строки вида 'owner/repo'."""
    text = text.strip()
    m = _REPO_URL_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    m = _SLUG_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    return None


def truncate(text: str, limit: int = 4000) -> str:
    """Telegram ограничивает сообщения ~4096 символами — режем с запасом."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def format_tags(tags: list[str]) -> str:
    """Приводит список тегов к строке вида '#python #ai #utility', экранируя HTML."""
    clean = []
    for t in tags:
        t = html.escape(t.strip().lstrip("#").replace(" ", "_"), quote=False)
        if t:
            clean.append(f"#{t}")
    return " ".join(clean)
