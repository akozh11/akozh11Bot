"""
Работа с GitHub: REST API для сбора данных о репозиториях (модуль A) +
проверка подписи вебхуков + периодический опрос отслеживаемых репозиториев.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Awaitable, Callable, Optional

import aiohttp

import config
import database

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


async def _get(session: aiohttp.ClientSession, url: str) -> Optional[dict | list]:
    async with session.get(url, headers=_headers()) as resp:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < 5:
            log.warning("GitHub API rate limit почти исчерпан: осталось %s (url=%s)", remaining, url)
        if resp.status == 404:
            return None
        if resp.status == 403:
            log.error("GitHub API вернул 403 (rate limit или нет доступа): %s", url)
            return None
        resp.raise_for_status()
        return await resp.json()


async def fetch_repo_info(full_name: str) -> Optional[dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        return await _get(session, f"{API_ROOT}/repos/{full_name}")  # type: ignore[return-value]


async def fetch_readme_text(full_name: str) -> str:
    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"{API_ROOT}/repos/{full_name}/readme")
        if not data or "content" not in data:  # type: ignore[operator]
            return ""
        try:
            raw = base64.b64decode(data["content"])  # type: ignore[index]
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            log.exception("Не удалось декодировать README для %s", full_name)
            return ""


async def fetch_latest_release(full_name: str) -> Optional[dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        return await _get(session, f"{API_ROOT}/repos/{full_name}/releases/latest")  # type: ignore[return-value]


async def fetch_recent_commits(full_name: str, limit: int = 5) -> list[dict[str, Any]]:
    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"{API_ROOT}/repos/{full_name}/commits?per_page={limit}")
        return data or []  # type: ignore[return-value]


async def build_repo_context(full_name: str, url: str) -> dict[str, Any]:
    """Собирает всё, что нужно Gemini для написания поста: README, релиз, коммиты."""
    info = await fetch_repo_info(full_name) or {}
    readme = await fetch_readme_text(full_name)
    release = await fetch_latest_release(full_name)
    commits = await fetch_recent_commits(full_name, limit=5)
    return {
        "full_name": full_name,
        "url": url,
        "description": info.get("description") or "",
        "language": info.get("language") or "",
        "topics": info.get("topics") or [],
        "stars": info.get("stargazers_count", 0),
        "readme_excerpt": readme[:6000],  # ограничиваем размер промпта
        "latest_release": (
            {
                "tag": release.get("tag_name"),
                "name": release.get("name"),
                "body": (release.get("body") or "")[:2000],
            }
            if release
            else None
        ),
        "recent_commits": [
            {"sha": c["sha"][:7], "message": c["commit"]["message"].split("\n")[0]}
            for c in commits
        ],
    }


def verify_webhook_signature(secret: str, payload_body: bytes, signature_header: str | None) -> bool:
    """Проверяет подпись X-Hub-Signature-256 согласно документации GitHub."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    got = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, got)


# Колбэк вызывается для каждого нового события: (row отслеживаемого репо, dedup_suffix)
PollCallback = Callable[[Any, str], Awaitable[None]]


async def poll_tracked_repos(on_new_content: PollCallback) -> None:
    """
    Периодическая задача (вызывается из планировщика): обходит tracked_repos,
    сравнивает последний увиденный релиз/коммит с текущим состоянием на GitHub
    и вызывает on_new_content(repo_row, dedup_suffix) при появлении нового события.
    """
    repos = await database.get_tracked_repos()
    for repo in repos:
        full_name = repo["full_name"]
        try:
            release = await fetch_latest_release(full_name)
            if release and release.get("tag_name") != repo["last_release_tag"]:
                await database.update_repo_pointer(full_name, release_tag=release["tag_name"])
                await on_new_content(repo, f"release:{release['tag_name']}")
                continue  # не постим сразу и релиз, и коммит в одном цикле опроса

            commits = await fetch_recent_commits(full_name, limit=1)
            if commits:
                sha = commits[0]["sha"]
                if sha != repo["last_commit_sha"]:
                    await database.update_repo_pointer(full_name, commit_sha=sha)
                    await on_new_content(repo, f"commit:{sha[:12]}")
        except Exception:
            log.exception("Ошибка при опросе репозитория %s", full_name)
