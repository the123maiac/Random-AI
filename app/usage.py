from __future__ import annotations

from datetime import datetime, timezone

from . import db

DEFAULT_CAPS = {
    "llm_step": 200,
    "web_search": 50,
    "fetch": 200,
    "firecrawl": 16,        # ~500/mo on Firecrawl free tier
    "github": 800,          # 5000/h authenticated, well under
}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_caps() -> dict[str, int]:
    with db.connect() as c:
        rows = c.execute("SELECT key, value FROM app_state WHERE key LIKE 'cap_%'").fetchall()
    caps = dict(DEFAULT_CAPS)
    for r in rows:
        bucket = r["key"][4:]
        try:
            caps[bucket] = int(r["value"])
        except ValueError:
            pass
    return caps


def set_cap(bucket: str, value: int) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"cap_{bucket}", str(value)),
        )


def is_killed() -> bool:
    with db.connect() as c:
        row = c.execute("SELECT value FROM app_state WHERE key = 'kill_switch'").fetchone()
    return bool(row and row["value"] == "1")


def set_killed(killed: bool) -> None:
    with db.connect() as c:
        c.execute(
            "INSERT INTO app_state (key, value) VALUES ('kill_switch', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if killed else "0",),
        )


def increment(user_id: int, bucket: str, n: int = 1) -> None:
    with db.connect() as c:
        c.execute(
            """INSERT INTO usage_log (user_id, bucket, day, count) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, bucket, day) DO UPDATE SET count = count + excluded.count""",
            (user_id, bucket, _today(), n),
        )


def used_today(user_id: int, bucket: str) -> int:
    with db.connect() as c:
        row = c.execute(
            "SELECT count FROM usage_log WHERE user_id = ? AND bucket = ? AND day = ?",
            (user_id, bucket, _today()),
        ).fetchone()
    return row["count"] if row else 0


def check(user_id: int, bucket: str) -> tuple[bool, int, int]:
    """Returns (allowed, used, cap)."""
    if is_killed():
        return False, 0, 0
    cap = get_caps().get(bucket, 0)
    used = used_today(user_id, bucket)
    return used < cap, used, cap


def usage_today(user_id: int) -> dict:
    caps = get_caps()
    with db.connect() as c:
        rows = c.execute(
            "SELECT bucket, count FROM usage_log WHERE user_id = ? AND day = ?",
            (user_id, _today()),
        ).fetchall()
    used = {r["bucket"]: r["count"] for r in rows}
    return {
        "killed": is_killed(),
        "buckets": [
            {"bucket": b, "used": used.get(b, 0), "cap": caps.get(b, 0)}
            for b in DEFAULT_CAPS
        ],
    }
