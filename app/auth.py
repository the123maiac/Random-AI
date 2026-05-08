from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, HTTPException, status

from . import db

CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LENGTH = 12
SESSION_DAYS = 90
COOKIE_NAME = "ac_session"


def generate_invite_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def hash_code(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()


def verify_code(code: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(code.encode(), hashed.encode())
    except ValueError:
        return False


def create_user(display_name: str) -> tuple[int, str]:
    code = generate_invite_code()
    hashed = hash_code(code)
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO users (invite_code_hash, display_name) VALUES (?, ?)",
            (hashed, display_name),
        )
        return cur.lastrowid, code


def find_user_by_code(code: str) -> dict | None:
    code = code.strip().upper()
    if len(code) != CODE_LENGTH:
        return None
    with db.connect() as c:
        rows = c.execute("SELECT id, invite_code_hash, display_name FROM users").fetchall()
    for row in rows:
        if verify_code(code, row["invite_code_hash"]):
            return dict(row)
    return None


def create_session(user_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with db.connect() as c:
        c.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
    return token, expires


def get_session(token: str | None) -> dict | None:
    if not token:
        return None
    with db.connect() as c:
        row = c.execute(
            """SELECT s.id AS sid, s.expires_at, u.id AS uid, u.display_name
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.id = ?""",
            (token,),
        ).fetchone()
    if not row:
        return None
    expires = row["expires_at"]
    if isinstance(expires, str):
        expires = datetime.fromisoformat(expires)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        delete_session(token)
        return None
    return {"user_id": row["uid"], "display_name": row["display_name"]}


def delete_session(token: str) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM sessions WHERE id = ?", (token,))


def require_user(ac_session: str | None = Cookie(default=None)) -> dict:
    sess = get_session(ac_session)
    if not sess:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return sess
