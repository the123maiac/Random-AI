from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .. import services, usage

USER_AGENT = "agentic-chat/0.1"
USER_FILES_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "user_files"
FETCH_CHAR_CAP = 8000


def _no_key(name: str) -> str:
    return f"⚠ {name} key not configured. Add it in Settings → Service keys."


def _user_dir(user_id: int) -> Path:
    p = USER_FILES_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(user_id: int, rel: str) -> Path | None:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    base = _user_dir(user_id)
    full = (base / rel).resolve()
    if not str(full).startswith(str(base.resolve())):
        return None
    return full


# -------- tool implementations --------

async def web_search(user_id: int, query: str, count: int = 5) -> dict:
    ok, _, cap = usage.check(user_id, "web_search")
    if not ok:
        return {"error": f"web_search daily cap reached ({cap}/day) or kill switch on"}
    key = services.get_service_key(user_id, "brave_search")
    if not key:
        return {"error": _no_key("brave_search")}
    usage.increment(user_id, "web_search")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(count, 10)},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return {"error": f"brave {r.status_code}: {r.text[:200]}"}
        data = r.json()
    results = []
    for w in (data.get("web", {}).get("results") or [])[:count]:
        results.append({"title": w.get("title"), "url": w.get("url"), "snippet": w.get("description")})
    return {"results": results}


async def _fetch_httpx(url: str) -> str:
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(url)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" in ct or "xml" in ct:
            from readability import Document  # type: ignore
            doc = Document(r.text)
            from lxml import html as lxml_html  # type: ignore
            tree = lxml_html.fromstring(doc.summary())
            text = tree.text_content()
            return f"# {doc.short_title()}\n\n{text.strip()}"
        return r.text


async def fetch_url(user_id: int, url: str, prefer_firecrawl: bool = False) -> dict:
    ok, _, _ = usage.check(user_id, "fetch")
    if not ok:
        return {"error": "fetch daily cap reached or kill switch on"}
    usage.increment(user_id, "fetch")

    fc_key = services.get_service_key(user_id, "firecrawl") if prefer_firecrawl else None
    if fc_key:
        ok2, _, _ = usage.check(user_id, "firecrawl")
        if ok2:
            usage.increment(user_id, "firecrawl")
            try:
                from firecrawl import FirecrawlApp  # type: ignore
                def _scrape():
                    app = FirecrawlApp(api_key=fc_key)
                    return app.scrape_url(url, params={"formats": ["markdown"]})
                doc = await asyncio.to_thread(_scrape)
                md = (doc.get("markdown") or doc.get("content") or "")[:FETCH_CHAR_CAP]
                if md:
                    return {"url": url, "text": md, "via": "firecrawl"}
            except Exception as e:
                pass  # fall through to httpx

    try:
        text = await _fetch_httpx(url)
        return {"url": url, "text": text[:FETCH_CHAR_CAP], "via": "httpx"}
    except Exception as e:
        return {"error": f"fetch failed: {e}"}


async def github_search(user_id: int, query: str, kind: str = "repositories", count: int = 8) -> dict:
    ok, _, cap = usage.check(user_id, "github")
    if not ok:
        return {"error": f"github daily cap reached ({cap}/day) or kill switch on"}
    if kind not in ("repositories", "code"):
        return {"error": "kind must be 'repositories' or 'code'"}
    token = services.get_service_key(user_id, "github")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    usage.increment(user_id, "github")
    url = f"https://api.github.com/search/{kind}"
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(url, params={"q": query, "per_page": min(count, 20)}, headers=headers)
        if r.status_code != 200:
            return {"error": f"github {r.status_code}: {r.text[:200]}"}
        data = r.json()
    items = []
    for it in (data.get("items") or [])[:count]:
        if kind == "repositories":
            items.append({
                "name": it.get("full_name"),
                "url": it.get("html_url"),
                "stars": it.get("stargazers_count"),
                "description": it.get("description"),
                "language": it.get("language"),
                "topics": it.get("topics") or [],
            })
        else:
            items.append({
                "repo": it.get("repository", {}).get("full_name"),
                "path": it.get("path"),
                "url": it.get("html_url"),
            })
    return {"items": items}


async def send_email(user_id: int, to: str, subject: str, body: str) -> dict:
    if not to or "@" not in to:
        return {"error": "invalid 'to' address"}
    key = services.get_service_key(user_id, "resend")
    if not key:
        return {"error": _no_key("resend")}
    from_addr = os.getenv("AGENTIC_CHAT_FROM_EMAIL", "onboarding@resend.dev")
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": from_addr, "to": [to], "subject": subject, "text": body},
        )
        if r.status_code >= 300:
            return {"error": f"resend {r.status_code}: {r.text[:200]}"}
    return {"sent": True, "to": to}


async def file_rw(user_id: int, action: str, path: str = "", content: str | None = None) -> dict:
    if action not in ("read", "write", "list", "delete"):
        return {"error": "action must be read|write|list|delete"}
    if action == "list":
        base = _user_dir(user_id)
        rel = path or ""
        target = _safe_path(user_id, rel) if rel else base
        if target is None or not target.exists():
            return {"items": []}
        if target.is_file():
            return {"items": [{"name": target.name, "type": "file", "size": target.stat().st_size}]}
        out = []
        for p in sorted(target.iterdir()):
            out.append({"name": p.name, "type": "dir" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None})
        return {"items": out}
    p = _safe_path(user_id, path)
    if p is None:
        return {"error": "invalid path"}
    if action == "read":
        if not p.exists() or not p.is_file():
            return {"error": "not found"}
        return {"content": p.read_text(encoding="utf-8", errors="replace")[:FETCH_CHAR_CAP]}
    if action == "write":
        if content is None:
            return {"error": "content required"}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"written": str(p.relative_to(_user_dir(user_id)))}
    if action == "delete":
        if p.exists():
            p.unlink()
        return {"deleted": True}
    return {"error": "unreachable"}


# -------- registry --------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web with Brave Search. Returns top results as {title,url,snippet}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "default": 5, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a URL and return its main text content (8k char cap). Set prefer_firecrawl=true for JS-heavy sites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "prefer_firecrawl": {"type": "boolean", "default": False},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_search",
            "description": "Search GitHub for repositories or code. Use this to find big projects to learn from.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string", "enum": ["repositories", "code"], "default": "repositories"},
                    "count": {"type": "integer", "default": 8, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email via Resend.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_rw",
            "description": "Read/write/list/delete files in your sandboxed workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "list", "delete"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
]

DISPATCH = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "github_search": github_search,
    "send_email": send_email,
    "file_rw": file_rw,
}


async def dispatch(user_id: int, name: str, args_json: str, allowed: set[str] | None = None) -> str:
    if allowed is not None and name not in allowed:
        return json.dumps({"error": f"tool '{name}' not allowed in this run"})
    fn = DISPATCH.get(name)
    if not fn:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid JSON arguments"})
    try:
        result = await fn(user_id=user_id, **args)
    except TypeError as e:
        return json.dumps({"error": f"bad arguments: {e}"})
    except Exception as e:
        return json.dumps({"error": f"tool error: {e}"})
    return json.dumps(result)[:12000]
