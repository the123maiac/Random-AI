from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, crypto, db, embeddings, services, usage
from .agent import loop as agent_loop, research, scheduler
from .providers import get_provider

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROVIDERS = ("openai_compat", "anthropic", "gemini")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    crypto.get_fernet()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="agentic-chat", lifespan=lifespan)


# ===== auth =====

class LoginIn(BaseModel):
    code: str


@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response):
    user = auth.find_user_by_code(body.code)
    if not user:
        raise HTTPException(401, "Invalid invite code")
    token, _ = auth.create_session(user["id"])
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=False)
    return {"display_name": user["display_name"]}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        auth.delete_session(token)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
async def me(user=Depends(auth.require_user)):
    return user


# ===== provider keys =====

class KeyIn(BaseModel):
    provider: str
    label: str
    api_key: str
    base_url: str | None = None
    default_model: str


@app.get("/api/keys")
async def list_keys(user=Depends(auth.require_user)):
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, provider, label, base_url, default_model, created_at
               FROM api_keys WHERE user_id = ? ORDER BY id DESC""",
            (user["user_id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/keys")
async def add_key(body: KeyIn, user=Depends(auth.require_user)):
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"provider must be one of {PROVIDERS}")
    enc = crypto.encrypt(body.api_key)
    with db.connect() as c:
        cur = c.execute(
            """INSERT INTO api_keys (user_id, provider, label, base_url, default_model, encrypted_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user["user_id"], body.provider, body.label, body.base_url, body.default_model, enc),
        )
    return {"id": cur.lastrowid}


@app.delete("/api/keys/{key_id}")
async def delete_key(key_id: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        cur = c.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user["user_id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ===== service keys (Brave / Firecrawl / GitHub / Resend) =====

class ServiceKeyIn(BaseModel):
    service: str
    token: str


@app.get("/api/service-keys")
async def list_service_keys(user=Depends(auth.require_user)):
    return services.list_service_keys(user["user_id"])


@app.post("/api/service-keys")
async def set_service_key(body: ServiceKeyIn, user=Depends(auth.require_user)):
    if body.service not in services.SERVICES:
        raise HTTPException(400, f"service must be one of {services.SERVICES}")
    services.set_service_key(user["user_id"], body.service, body.token)
    return {"ok": True}


@app.delete("/api/service-keys/{service}")
async def delete_service_key(service: str, user=Depends(auth.require_user)):
    n = services.delete_service_key(user["user_id"], service)
    if n == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ===== chats =====

class ChatIn(BaseModel):
    title: str | None = None
    provider_key_id: int
    model: str | None = None


@app.get("/api/chats")
async def list_chats(user=Depends(auth.require_user)):
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, title, provider_key_id, model, created_at
               FROM chats WHERE user_id = ? ORDER BY id DESC""",
            (user["user_id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/chats")
async def create_chat(body: ChatIn, user=Depends(auth.require_user)):
    with db.connect() as c:
        key = c.execute(
            "SELECT id, default_model FROM api_keys WHERE id = ? AND user_id = ?",
            (body.provider_key_id, user["user_id"]),
        ).fetchone()
        if not key:
            raise HTTPException(404, "API key not found")
        model = body.model or key["default_model"]
        cur = c.execute(
            "INSERT INTO chats (user_id, title, provider_key_id, model) VALUES (?, ?, ?, ?)",
            (user["user_id"], body.title or "New chat", body.provider_key_id, model),
        )
    return {"id": cur.lastrowid, "model": model}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        cur = c.execute("DELETE FROM chats WHERE id = ? AND user_id = ?", (chat_id, user["user_id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.get("/api/chats/{chat_id}/messages")
async def list_messages(chat_id: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        chat = c.execute(
            "SELECT id FROM chats WHERE id = ? AND user_id = ?", (chat_id, user["user_id"])
        ).fetchone()
        if not chat:
            raise HTTPException(404, "Chat not found")
        rows = c.execute(
            "SELECT id, role, content, created_at FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    return [{"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"]}
            for r in rows if r["role"] in ("user", "assistant")]


class MessageIn(BaseModel):
    content: str = Field(min_length=1)
    agentic: bool = False
    use_knowledge: bool = True


async def _knowledge_context(user_id: int, query: str, api_key: str, base_url: str | None) -> str:
    try:
        embedder = embeddings.Embedder(api_key=api_key, base_url=base_url)
        qvec = await embedder.embed_query(query)
    except Exception:
        return ""
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, source_url, title, summary, embedding FROM knowledge WHERE user_id = ? AND embedding IS NOT NULL",
            (user_id,),
        ).fetchall()
    if not rows:
        return ""
    items = [(i, embeddings.unpack(r["embedding"])) for i, r in enumerate(rows)]
    top = embeddings.top_k(qvec, items, k=6)
    pieces = []
    for idx, score in top:
        if score < 0.45:
            continue
        r = rows[idx]
        pieces.append(f"[{r['title']}]({r['source_url']})\n{r['summary']}")
    if not pieces:
        return ""
    return "## Relevant notes from your knowledge base\n\n" + "\n\n---\n\n".join(pieces)


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: int, body: MessageIn, user=Depends(auth.require_user)):
    with db.connect() as c:
        chat = c.execute(
            """SELECT c.id, c.model, c.provider_key_id,
                      k.provider, k.encrypted_key, k.base_url, k.default_model
               FROM chats c JOIN api_keys k ON k.id = c.provider_key_id
               WHERE c.id = ? AND c.user_id = ? AND k.user_id = ?""",
            (chat_id, user["user_id"], user["user_id"]),
        ).fetchone()
        if not chat:
            raise HTTPException(404, "Chat or key not found")
        c.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, "user", body.content),
        )
        history = c.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()

    api_key = crypto.decrypt(chat["encrypted_key"])
    base_url = chat["base_url"]
    model = chat["model"] or chat["default_model"]

    msgs: list[dict] = []
    if body.use_knowledge and chat["provider"] == "openai_compat":
        ctx = await _knowledge_context(user["user_id"], body.content, api_key, base_url)
        if ctx:
            msgs.append({"role": "system", "content": ctx})
    msgs.extend({"role": r["role"], "content": r["content"]} for r in history)

    if body.agentic and chat["provider"] == "openai_compat":
        result = await agent_loop.run_agent(
            user_id=user["user_id"], api_key=api_key, base_url=base_url, model=model, messages=msgs,
        )
        reply = result["final_text"]
    else:
        try:
            provider = get_provider(chat["provider"], api_key, base_url)
            reply = await provider.chat(messages=msgs, model=model)
        except Exception as e:
            raise HTTPException(502, f"Provider error: {e}")

    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, "assistant", reply),
        )
        msg_id = cur.lastrowid
    return {"id": msg_id, "role": "assistant", "content": reply}


# ===== research topics =====

class TopicIn(BaseModel):
    name: str
    query: str
    interval_hours: int = 4
    provider_key_id: int | None = None
    model: str | None = None
    enabled: bool = True


@app.get("/api/topics")
async def list_topics(user=Depends(auth.require_user)):
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, name, query, interval_hours, enabled, provider_key_id, model,
                      last_run_at, next_run_at, created_at
               FROM research_topics WHERE user_id = ? ORDER BY id""",
            (user["user_id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/topics")
async def create_topic(body: TopicIn, user=Depends(auth.require_user)):
    iv = max(1, body.interval_hours)
    with db.connect() as c:
        cur = c.execute(
            """INSERT INTO research_topics
               (user_id, name, query, interval_hours, enabled, provider_key_id, model)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["user_id"], body.name, body.query, iv, 1 if body.enabled else 0,
             body.provider_key_id, body.model),
        )
    return {"id": cur.lastrowid}


@app.patch("/api/topics/{topic_id}")
async def update_topic(topic_id: int, body: TopicIn, user=Depends(auth.require_user)):
    iv = max(1, body.interval_hours)
    with db.connect() as c:
        cur = c.execute(
            """UPDATE research_topics
               SET name=?, query=?, interval_hours=?, enabled=?, provider_key_id=?, model=?
               WHERE id=? AND user_id=?""",
            (body.name, body.query, iv, 1 if body.enabled else 0, body.provider_key_id,
             body.model, topic_id, user["user_id"]),
        )
    if cur.rowcount == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.delete("/api/topics/{topic_id}")
async def delete_topic(topic_id: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        cur = c.execute(
            "DELETE FROM research_topics WHERE id = ? AND user_id = ?",
            (topic_id, user["user_id"]),
        )
    if cur.rowcount == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@app.post("/api/topics/{topic_id}/run-now")
async def run_topic_now(topic_id: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        row = c.execute(
            """SELECT id, user_id, name, query, interval_hours, provider_key_id, model
               FROM research_topics WHERE id = ? AND user_id = ?""",
            (topic_id, user["user_id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    if not row["provider_key_id"]:
        raise HTTPException(400, "Set a provider key on this topic first")
    asyncio.create_task(research.run_topic(dict(row)))
    return {"started": True}


# ===== feed (knowledge entries) =====

@app.get("/api/feed")
async def feed(user=Depends(auth.require_user), limit: int = 50, topic_id: int | None = None):
    limit = max(1, min(limit, 200))
    with db.connect() as c:
        if topic_id is not None:
            rows = c.execute(
                """SELECT k.id, k.source_url, k.title, k.summary, k.created_at, k.topic_id, t.name AS topic_name
                   FROM knowledge k LEFT JOIN research_topics t ON t.id = k.topic_id
                   WHERE k.user_id = ? AND k.topic_id = ?
                   ORDER BY k.id DESC LIMIT ?""",
                (user["user_id"], topic_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT k.id, k.source_url, k.title, k.summary, k.created_at, k.topic_id, t.name AS topic_name
                   FROM knowledge k LEFT JOIN research_topics t ON t.id = k.topic_id
                   WHERE k.user_id = ?
                   ORDER BY k.id DESC LIMIT ?""",
                (user["user_id"], limit),
            ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/api/feed/{kid}")
async def delete_feed(kid: int, user=Depends(auth.require_user)):
    with db.connect() as c:
        cur = c.execute("DELETE FROM knowledge WHERE id = ? AND user_id = ?", (kid, user["user_id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ===== usage / kill switch =====

@app.get("/api/usage")
async def get_usage(user=Depends(auth.require_user)):
    return usage.usage_today(user["user_id"])


@app.get("/api/runs")
async def list_runs(user=Depends(auth.require_user), limit: int = 30):
    limit = max(1, min(limit, 200))
    with db.connect() as c:
        rows = c.execute(
            """SELECT id, kind, status, steps, started_at, ended_at, output, error, topic_id
               FROM agent_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user["user_id"], limit),
        ).fetchall()
    return [dict(r) for r in rows]


class KillIn(BaseModel):
    killed: bool


@app.post("/api/kill-switch")
async def set_kill(body: KillIn, user=Depends(auth.require_user)):
    usage.set_killed(body.killed)
    return {"killed": usage.is_killed()}


# ===== static =====

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json")


@app.get("/sw.js")
async def sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")
