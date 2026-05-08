from __future__ import annotations

import argparse
import sys

from . import auth, db

DEFAULT_CODING_TOPICS = [
    ("FastAPI patterns", "FastAPI production patterns dependency injection async best practices"),
    ("Python async", "Python asyncio httpx anyio production patterns"),
    ("LLM agent loops", "LLM tool use agent loop OpenAI function calling production"),
    ("Retrieval augmented generation", "RAG vector search embeddings production architecture"),
    ("TypeScript Next.js", "Next.js App Router server components patterns 2026"),
    ("SQLite production", "SQLite WAL concurrency production best practices"),
    ("Test-driven development", "pytest fixtures test design patterns coverage"),
    ("Clean architecture", "clean architecture hexagonal SOLID refactoring real-world examples"),
]


def cmd_init(_args) -> int:
    db.init_db()
    print(f"Initialized DB at {db.DB_PATH}")
    return 0


def cmd_invite(args) -> int:
    db.init_db()
    user_id, code = auth.create_user(args.name)
    print(f"User #{user_id} ({args.name}) created.")
    print(f"Invite code: {code}")
    print("Give this to the user. It is shown ONCE — the DB stores only a bcrypt hash.")
    return 0


def cmd_users(_args) -> int:
    db.init_db()
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, display_name, created_at FROM users ORDER BY id"
        ).fetchall()
    if not rows:
        print("(no users)")
        return 0
    for r in rows:
        print(f"#{r['id']}  {r['display_name']}  created {r['created_at']}")
    return 0


def cmd_seed_topics(args) -> int:
    db.init_db()
    with db.connect() as c:
        user = c.execute(
            "SELECT id FROM users WHERE id = ? OR display_name = ?",
            (args.user if args.user.isdigit() else -1, args.user),
        ).fetchone()
        if not user:
            print(f"User '{args.user}' not found.")
            return 1
        key = c.execute(
            "SELECT id FROM api_keys WHERE user_id = ? AND provider = 'openai_compat' ORDER BY id LIMIT 1",
            (user["id"],),
        ).fetchone()
        if not key:
            print("No openai_compat provider key for that user. Add one in the UI first.")
            return 1
        for name, q in DEFAULT_CODING_TOPICS:
            c.execute(
                """INSERT INTO research_topics
                   (user_id, name, query, interval_hours, enabled, provider_key_id)
                   VALUES (?, ?, ?, 4, 1, ?)""",
                (user["id"], name, q, key["id"]),
            )
    print(f"Seeded {len(DEFAULT_CODING_TOPICS)} coding topics for user #{user['id']}.")
    return 0


def cmd_revoke(args) -> int:
    db.init_db()
    with db.connect() as c:
        cur = c.execute("DELETE FROM users WHERE id = ?", (args.user_id,))
    print(f"Deleted {cur.rowcount} user(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-chat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize the SQLite database").set_defaults(func=cmd_init)

    p = sub.add_parser("invite", help="Generate an invite code for a new user")
    p.add_argument("name", help="Display name")
    p.set_defaults(func=cmd_invite)

    sub.add_parser("users", help="List users").set_defaults(func=cmd_users)

    p = sub.add_parser("seed-topics", help="Seed default coding research topics for a user")
    p.add_argument("user", help="User ID or display name")
    p.set_defaults(func=cmd_seed_topics)

    p = sub.add_parser("revoke", help="Delete a user (cascades all data)")
    p.add_argument("user_id", type=int)
    p.set_defaults(func=cmd_revoke)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
