"""
aiohttp-сервер: принимает GitHub webhooks (модуль A, "личные проекты") и
обслуживает редирект-ссылки для отслеживания кликов по кнопке
«🔗 GitHub» в опубликованных постах (модуль D).
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiohttp import web

import config
import database
import github_service
import pipeline

log = logging.getLogger(__name__)


def create_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook/github", handle_github_webhook)
    app.router.add_get("/r/{post_id}/github", handle_github_click_redirect)
    return app


async def handle_github_webhook(request: web.Request) -> web.Response:
    body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256")

    if config.GITHUB_WEBHOOK_SECRET and not github_service.verify_webhook_signature(
        config.GITHUB_WEBHOOK_SECRET, body, signature
    ):
        log.warning("Неверная подпись GitHub webhook — запрос отклонён")
        return web.Response(status=401, text="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()
    bot: Bot = request.app["bot"]

    try:
        if event == "release" and payload.get("action") == "published":
            repo = payload["repository"]
            release = payload["release"]
            await pipeline.process_new_repo_event(
                bot,
                full_name=repo["full_name"],
                url=repo["html_url"],
                dedup_suffix=f"release:{release['tag_name']}",
                source="webhook",
            )
        elif event == "push":
            repo = payload["repository"]
            default_branch = repo.get("default_branch", "main")
            ref = payload.get("ref", "")
            head_commit = payload.get("head_commit")
            if ref == f"refs/heads/{default_branch}" and head_commit:
                sha = head_commit["id"]
                await pipeline.process_new_repo_event(
                    bot,
                    full_name=repo["full_name"],
                    url=repo["html_url"],
                    dedup_suffix=f"commit:{sha[:12]}",
                    source="webhook",
                )
        # Прочие события (issues, stars и т.д.) сейчас игнорируются —
        # при необходимости добавьте дополнительные ветки здесь.
    except Exception:
        log.exception("Ошибка обработки GitHub webhook (event=%s)", event)
        return web.Response(status=500, text="internal error")

    return web.Response(status=200, text="ok")


async def handle_github_click_redirect(request: web.Request) -> web.Response:
    try:
        post_id = int(request.match_info["post_id"])
    except ValueError:
        raise web.HTTPNotFound()

    post = await database.get_post(post_id)
    if post is None:
        raise web.HTTPNotFound()

    await database.bump_metric(post_id, "github_clicks")
    raise web.HTTPFound(post["repo_url"])
