"""
Конфигурация проекта.
Все параметры читаются из переменных окружения (см. ..env).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_list(env_name: str) -> list[str]:
    raw = os.getenv(env_name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_int_list(env_name: str) -> list[int]:
    return [int(x) for x in _get_list(env_name)]


def _get_bool(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Telegram ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = _get_int_list("ADMIN_IDS")
CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")  # например -1001234567890

# Группа обсуждений, привязанная к каналу (нужна только для подсчёта
# комментариев — необязательна).
DISCUSSION_GROUP_URL: str = os.getenv("DISCUSSION_GROUP_URL", "")
_discussion_group_id_raw = os.getenv("DISCUSSION_GROUP_ID", "").strip()
DISCUSSION_GROUP_ID: int | None = int(_discussion_group_id_raw) if _discussion_group_id_raw else None

# --- Gemini ---
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
# Проверьте актуальный список доступных моделей для вашего ключа/региона —
# названия моделей у Gemini обновляются чаще, чем хотелось бы.
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- GitHub ---
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
TRACKED_REPOS: list[str] = _get_list("TRACKED_REPOS")  # ["owner/repo", ...]
POLL_INTERVAL_MINUTES: int = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))

# --- База данных ---
DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(Path(__file__).parent / "bot.db"))

# --- Веб-сервер: приём GitHub webhook + редирект-ссылки для трекинга кликов ---
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))
# Публичный адрес, по которому GitHub / кнопки в Telegram будут стучаться
# к WEBHOOK_HOST:WEBHOOK_PORT (домен за reverse-proxy, туннель и т.п.)
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")
# Если сервер недоступен из интернета — выключите трекинг кликов, и кнопка
# "GitHub" будет вести на репозиторий напрямую, без промежуточного редиректа.
ENABLE_CLICK_TRACKING: bool = _get_bool("ENABLE_CLICK_TRACKING", True)

# --- Планировщик ---
TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")

# --- Веса для расчёта ERR (эффективной вовлечённости) ---
# Подберите под свою аудиторию — это просто стартовые коэффициенты.
ERR_WEIGHTS = {
    "github_clicks": 1.0,
    "discuss_clicks": 1.5,
    "comments": 3.0,
    "reactions": 1.0,
    "reposts": 4.0,
}


def validate() -> None:
    """Быстрая проверка обязательных параметров при старте."""
    missing = []
    for name in ("BOT_TOKEN", "GEMINI_API_KEY", "CHANNEL_ID"):
        if not globals()[name]:
            missing.append(name)
    if not ADMIN_IDS:
        missing.append("ADMIN_IDS")
    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing)
        )
