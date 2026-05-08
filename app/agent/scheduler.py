from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .. import backup, db, usage
from . import research

log = logging.getLogger("scheduler")
_scheduler: AsyncIOScheduler | None = None
_running_topics: set[int] = set()


def _utcnow():
    return datetime.now(timezone.utc)


def _due_topics() -> list[dict]:
    now = _utcnow().isoformat()
    with db.connect() as c:
        rows = c.execute(
            """SELECT t.id, t.user_id, t.name, t.query, t.interval_hours,
                      t.provider_key_id, t.model, t.last_run_at, t.next_run_at
               FROM research_topics t
               WHERE t.enabled = 1
                 AND t.provider_key_id IS NOT NULL
                 AND (t.next_run_at IS NULL OR t.next_run_at <= ?)""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


async def _tick() -> None:
    if usage.is_killed():
        return
    for t in _due_topics():
        if t["id"] in _running_topics:
            continue
        _running_topics.add(t["id"])
        asyncio.create_task(_run(t))


async def _run(topic: dict) -> None:
    try:
        log.info("running topic %s (%s)", topic["id"], topic["name"])
        await research.run_topic(topic)
    except Exception:
        log.exception("topic %s crashed", topic["id"])
    finally:
        _running_topics.discard(topic["id"])


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    s = AsyncIOScheduler(timezone="UTC")
    s.add_job(_tick, "interval", minutes=2, id="research_tick", replace_existing=True)
    s.add_job(backup.push_backup, "interval", minutes=30, id="backup_push", replace_existing=True)
    s.start()
    _scheduler = s
    log.info("scheduler started")


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
