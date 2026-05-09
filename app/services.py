from __future__ import annotations

from . import crypto, db

SERVICES = ("firecrawl", "github", "resend")


def set_service_key(user_id: int, service: str, token: str) -> None:
    if service not in SERVICES:
        raise ValueError(f"unknown service: {service}")
    enc = crypto.encrypt(token)
    with db.connect() as c:
        c.execute(
            """INSERT INTO service_keys (user_id, service, encrypted_token)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, service) DO UPDATE SET encrypted_token = excluded.encrypted_token""",
            (user_id, service, enc),
        )


def get_service_key(user_id: int, service: str) -> str | None:
    with db.connect() as c:
        row = c.execute(
            "SELECT encrypted_token FROM service_keys WHERE user_id = ? AND service = ?",
            (user_id, service),
        ).fetchone()
    if not row:
        return None
    return crypto.decrypt(row["encrypted_token"])


def list_service_keys(user_id: int) -> list[dict]:
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, service, created_at FROM service_keys WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_service_key(user_id: int, service: str) -> int:
    with db.connect() as c:
        cur = c.execute(
            "DELETE FROM service_keys WHERE user_id = ? AND service = ?",
            (user_id, service),
        )
    return cur.rowcount
