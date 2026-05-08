from __future__ import annotations

import hashlib
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone

from .. import crypto, db, embeddings, usage
from ..providers.openai_compat import OpenAICompatProvider
from . import tools

log = logging.getLogger("research")

SUMMARY_PROMPT = """You are a research assistant. Summarize the source below for a developer trying to learn coding patterns.

Output:
- A 4-8 sentence summary focused on technical takeaways: patterns, libraries, design choices, gotchas, code idioms.
- Skip marketing fluff and table-of-contents content.
- If the source is a GitHub README, prioritize architecture, dependencies, and notable design decisions.
- If there is nothing technically useful, output exactly: SKIP

URL: {url}
TITLE: {title}

SOURCE:
{text}
"""


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:32]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _summarize(provider: OpenAICompatProvider, model: str, url: str, title: str, text: str) -> str:
    msgs = [{"role": "user", "content": SUMMARY_PROMPT.format(url=url, title=title, text=text[:6000])}]
    return (await provider.chat(msgs, model)).strip()


async def run_topic(topic: dict) -> dict:
    """Execute one research run for a topic. Returns {status, found, added, error}."""
    user_id = topic["user_id"]

    if usage.is_killed():
        return {"status": "skipped", "reason": "kill_switch"}

    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO agent_runs (user_id, topic_id, kind, status) VALUES (?, ?, 'research', 'running')",
            (user_id, topic["id"]),
        )
        run_id = cur.lastrowid
        key_row = c.execute(
            "SELECT provider, encrypted_key, base_url, default_model FROM api_keys WHERE id = ? AND user_id = ?",
            (topic["provider_key_id"], user_id),
        ).fetchone()

    if not key_row or key_row["provider"] != "openai_compat":
        _finish(run_id, "failed", error="research requires an openai_compat key")
        return {"status": "failed", "error": "no openai_compat key"}

    api_key = crypto.decrypt(key_row["encrypted_key"])
    base_url = key_row["base_url"]
    chat_model = topic["model"] or key_row["default_model"]
    provider = OpenAICompatProvider(api_key=api_key, base_url=base_url)
    embedder = embeddings.Embedder(api_key=api_key, base_url=base_url)

    found = 0
    added = 0
    errors: list[str] = []

    try:
        # 1) GitHub repo search for the topic
        gh = await tools.github_search(user_id, topic["query"], "repositories", count=6)
        repo_items = gh.get("items") or []
        # 2) Web search for diverse sources
        ws = await tools.web_search(user_id, topic["query"], count=5)
        web_items = ws.get("results") or []

        candidates: list[tuple[str, str]] = []  # (url, title)
        for r in repo_items:
            if r.get("url"):
                candidates.append((r["url"] + "/blob/HEAD/README.md", r.get("name") or r["url"]))
        for r in web_items:
            if r.get("url"):
                candidates.append((r["url"], r.get("title") or r["url"]))

        # Try README via api.github.com first for repos (more reliable)
        for r in repo_items:
            full = r.get("name")
            if not full:
                continue
            candidates.append((f"https://raw.githubusercontent.com/{full}/HEAD/README.md", f"{full} README"))

        seen_hashes: set[str] = set()
        with db.connect() as c:
            existing = c.execute(
                "SELECT content_hash FROM knowledge WHERE user_id = ?", (user_id,)
            ).fetchall()
            for row in existing:
                seen_hashes.add(row["content_hash"])

        for url, title in candidates[:10]:
            if usage.is_killed():
                break
            ok, _, _ = usage.check(user_id, "fetch")
            if not ok:
                break
            ch = _hash(f"{url}|{title}")
            if ch in seen_hashes:
                continue
            found += 1

            doc = await tools.fetch_url(user_id, url, prefer_firecrawl=False)
            if "error" in doc or not doc.get("text"):
                continue
            text = doc["text"]
            if len(text) < 200:
                continue

            try:
                summary = await _summarize(provider, chat_model, url, title, text)
            except Exception as e:
                errors.append(f"summarize {url}: {e}")
                continue
            if summary.strip().upper().startswith("SKIP") or len(summary) < 40:
                continue

            try:
                vec = (await embedder.embed([summary]))[0]
                blob = embeddings.pack(vec)
            except Exception as e:
                errors.append(f"embed {url}: {e}")
                blob = None

            with db.connect() as c:
                try:
                    c.execute(
                        """INSERT INTO knowledge
                           (user_id, topic_id, source_url, title, summary, content_hash, embedding)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (user_id, topic["id"], url, title[:200], summary, ch, blob),
                    )
                    seen_hashes.add(ch)
                    added += 1
                except Exception as e:
                    if "UNIQUE" not in str(e):
                        errors.append(f"insert {url}: {e}")

    except Exception as e:
        log.exception("research run crashed")
        _finish(run_id, "failed", error=f"{e}\n{traceback.format_exc()[:500]}")
        return {"status": "failed", "error": str(e)}

    next_run = _utcnow() + timedelta(hours=topic["interval_hours"])
    with db.connect() as c:
        c.execute(
            "UPDATE research_topics SET last_run_at = CURRENT_TIMESTAMP, next_run_at = ? WHERE id = ?",
            (next_run, topic["id"]),
        )

    output = json.dumps({"found": found, "added": added, "errors": errors[:5]})
    _finish(run_id, "ok", output=output)
    return {"status": "ok", "found": found, "added": added, "errors": errors}


def _finish(run_id: int, status: str, output: str = "", error: str | None = None) -> None:
    with db.connect() as c:
        c.execute(
            "UPDATE agent_runs SET status=?, ended_at=CURRENT_TIMESTAMP, output=?, error=? WHERE id=?",
            (status, output[:8000], (error or "")[:2000], run_id),
        )
