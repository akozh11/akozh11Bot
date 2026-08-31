"""
Расчёт ERR (эффективной вовлечённости), топ постов и текстовые сводки
для /stats, /top_projects и еженедельного отчёта (модули D и E).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import config
import database
import gemini_service


def calc_err(row: dict) -> float:
    """Взвешенная сумма кликов/комментариев/реакций/репостов, см. config.ERR_WEIGHTS."""
    w = config.ERR_WEIGHTS
    return (
        row["github_clicks"] * w["github_clicks"]
        + row["discuss_clicks"] * w["discuss_clicks"]
        + row["comments_count"] * w["comments"]
        + row["reactions_count"] * w["reactions"]
        + row["reposts_count"] * w["reposts"]
    )


async def build_quick_stats_text(days: int = 7) -> str:
    since = dt.datetime.utcnow() - dt.timedelta(days=days)
    rows = await database.get_posts_with_metrics(since=since)
    if not rows:
        return f"За последние {days} дн. опубликованных постов не было."

    total_err = sum(calc_err(r) for r in rows)
    total_clicks = sum(r["github_clicks"] for r in rows)
    total_comments = sum(r["comments_count"] for r in rows)

    lines = [
        f"📊 Статистика за {days} дн.",
        f"Постов опубликовано: {len(rows)}",
        f"Суммарный ERR: {total_err:.1f}",
        f"Кликов на GitHub: {total_clicks}",
        f"Комментариев: {total_comments}",
    ]
    return "\n".join(lines)


async def build_top_projects_text(limit: int = 10, days: int | None = 7) -> str:
    since = dt.datetime.utcnow() - dt.timedelta(days=days) if days else None
    rows = await database.get_posts_with_metrics(since=since)
    if not rows:
        return "Опубликованных постов пока нет."

    ranked = sorted(rows, key=calc_err, reverse=True)[:limit]
    lines = ["🏆 Топ проектов:"]
    for i, r in enumerate(ranked, start=1):
        lines.append(
            f"{i}. {r['repo_full_name']} — ERR {calc_err(r):.1f} "
            f"({r['comments_count']} коммент., {r['github_clicks']} кликов)\n{r['repo_url']}"
        )
    return "\n\n".join(lines)


def _tag_breakdown(rows: list[dict]) -> list[tuple[str, float, int]]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        err = calc_err(r)
        for tag in (r["tags"] or "").split(","):
            tag = tag.strip()
            if not tag:
                continue
            totals[tag] += err
            counts[tag] += 1
    breakdown = [(tag, totals[tag], counts[tag]) for tag in totals]
    breakdown.sort(key=lambda x: x[1], reverse=True)
    return breakdown


async def build_weekly_report_text() -> str:
    since = dt.datetime.utcnow() - dt.timedelta(days=7)
    rows = await database.get_posts_with_metrics(since=since)

    if not rows:
        return "📅 Еженедельный отчёт\n\nНа этой неделе публикаций не было."

    ranked = sorted(rows, key=calc_err, reverse=True)
    top3 = ranked[:3]
    tag_stats = _tag_breakdown(rows)

    lines = ["📅 Еженедельный отчёт", "", "🏆 ТОП-3 проекта недели:"]
    for i, r in enumerate(top3, start=1):
        lines.append(f"{i}. {r['repo_full_name']} — ERR {calc_err(r):.1f}\n{r['repo_url']}")

    lines.append("")
    lines.append("🏷 Темы с наибольшим откликом:")
    for tag, total, count in tag_stats[:5]:
        avg = total / count if count else 0
        lines.append(f"#{tag}: суммарный ERR {total:.1f}, постов {count}, в среднем {avg:.1f}")

    summary_for_gemini = "\n".join(lines)
    recommendation = await gemini_service.generate_weekly_recommendations(summary_for_gemini)

    lines.append("")
    lines.append("💡 Рекомендации от Gemini:")
    lines.append(recommendation)

    return "\n".join(lines)
