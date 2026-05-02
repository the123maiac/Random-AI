# BYO-Key Agentic Chat — Design Spec

**Date:** 2026-05-01
**Status:** Approved (brainstorm complete)
**Owner:** devness88@gmail.com

## 1. Goal

A small, self-hosted web app where the user and a few trusted friends/family can:

1. Bring their own AI API key from any provider (OpenAI, NVIDIA NIM via build.nvidia.com, Groq, OpenRouter, Anthropic, Gemini, local Ollama, etc.)
2. Chat with that AI from a desktop browser or a phone (installable PWA)
3. Give the AI agentic capabilities: web search, URL fetching, email, push-to-phone, and sandboxed file read/write
4. Schedule recurring agent tasks in natural language ("every morning at 7am, search for AI news and email me a summary")

The app runs on the owner's Mac, exposed to the public internet via Cloudflare Tunnel.

## 2. Users & Access

- **Audience:** Small trusted group (~5 people total — owner + friends/family)
- **Auth:** Invite-only access codes. Owner generates a 12-character code per person via CLI; user pastes it once on the site, gets a 90-day session cookie. No passwords, no email reset flow. Lost code → owner regenerates.

## 3. Hosting

- Runs as a single FastAPI process on the owner's Mac at `localhost:8088`
- Cloudflare Tunnel maps a stable public hostname (e.g. `chat.<owner-domain>`) → `localhost:8088`
- launchd plist keeps the app running, restarts on crash, starts on login
- Phones reach the same URL — same code path, responsive layout, PWA-installable

## 4. AI Provider Support

Three API shapes supported at launch:

| Type | Covers |
|---|---|
| `openai_compat` | OpenAI, NVIDIA NIM, Groq, Together, OpenRouter, local Ollama, and any OpenAI-compatible endpoint |
| `anthropic` | Claude (native Anthropic SDK) |
| `gemini` | Google Gemini (native google-genai SDK) |

For each stored key, the user provides: provider type, label, API key, optional base URL (for `openai_compat`), and a default model name.

## 5. Agentic Capabilities

The agent has a tool-calling loop with five tools in v1:

| Tool | Purpose | Backing service |
|---|---|---|
| `web_search` | Search the web; returns top 5 `{title, url, snippet}` | Brave Search API (free tier: 2k/month) |
| `fetch_url` | Read a webpage as clean text (8k char cap) | httpx + readability-lxml |
| `send_email` | Send an email | Resend API (free tier: 3k/month) |
| `send_to_phone` | Push a message to user's phone | Telegram bot AND Web Push (both, in parallel) |
| `file_rw` | Read/write/list files in a sandboxed per-user folder | Local filesystem, chrooted to `data/user_files/<user_id>/` |

**Notes:**
- The agent uses `web_search` + `fetch_url` together to read top results and synthesize the best answer (no external "best result" picker — the LLM does that work itself).
- `send_to_phone` posts to Telegram and Web Push in parallel; either delivery succeeding counts as success.
- `file_rw` rejects any path containing `..` or absolute paths; all access is relative to the user's chroot dir.
- Hard cap: 15 agent steps per run (LLM call + tool dispatch = 1 step). On exceeding, the run is marked failed.

## 6. Scheduled Tasks (Natural-Language Scheduling)

User types something like *"every morning at 7am, search for AI news and email me a summary"*.

1. User clicks an explicit "Schedule this" button on a chat message (v1). Auto-detection of scheduling intent from free-form chat is deferred to v1.1.
2. A dedicated LLM call with a strict JSON Schema parses the selected message into:
   ```json
   {
     "name": "AI news digest",
     "cron": "0 7 * * *",
     "goal": "Search for top AI news from the last 24 hours and email a summary to me",
     "allowed_tools": ["web_search", "fetch_url", "send_email"],
     "delivery": {"email": "user@example.com", "telegram": false, "webpush": false}
   }
   ```
3. UI shows a confirmation card with parsed values + Confirm / Edit / Cancel.
4. On confirm: row inserted into `tasks` table; APScheduler picks it up.
5. On trigger: agent runs with **only** `allowed_tools` enabled (least privilege). Output delivered per `delivery` config. Run logged in `task_runs`.

## 7. Phone Delivery

Two channels, both active:

- **Telegram bot:** User opens app → Settings → "Link Telegram" → app shows a one-time code → user sends `/link <code>` to the bot → server stores `telegram_chat_id` on their user row.
- **Web Push:** User installs PWA → service worker registers → browser sends a `subscription` JSON to the server → stored on `users.webpush_subscription`. Server uses `pywebpush` + VAPID keys (generated once at first run, stored in macOS Keychain).

## 8. Encryption

- A Fernet master key is generated at first run and stored in **macOS Keychain** via the `keyring` library (service: `agentic-chat`, account: `fernet_master`).
- All `api_keys.encrypted_key` rows are Fernet-encrypted with that key.
- VAPID private key for Web Push is also stored in Keychain (service: `agentic-chat`, account: `vapid_private`).
- On startup, the app reads both from Keychain into memory. If Keychain access is denied, the app refuses to boot — no plaintext fallback.

## 9. Architecture

### Request flow — chat
```
phone/browser → Cloudflare Tunnel → FastAPI → provider router → OpenAI/Anthropic/Gemini SDK
                                          ↓
                                     SQLite (chats, messages, keys, users, tasks)
```

### Request flow — agent run
```
user goal → agent loop → LLM call with tool schemas
                ↑                ↓
                └── tool result ← tool dispatch (search / fetch / email / telegram / file / push)
```

### Project layout
```
agentic-chat/
├── pyproject.toml
├── run.sh                       # starts uvicorn + cloudflared
├── app/
│   ├── main.py                  # FastAPI app, routes
│   ├── cli.py                   # invite code generation, key rotation
│   ├── db.py                    # SQLite schema + helpers
│   ├── auth.py                  # invite codes, sessions
│   ├── crypto.py                # keychain + encrypt/decrypt
│   ├── providers/
│   │   ├── base.py              # common interface
│   │   ├── openai_compat.py
│   │   ├── anthropic.py
│   │   └── gemini.py
│   ├── agent/
│   │   ├── loop.py              # tool-calling loop
│   │   ├── tools.py             # tool implementations
│   │   └── scheduler.py         # APScheduler + NL parser
│   ├── delivery/
│   │   ├── telegram.py
│   │   └── webpush.py
│   └── static/                  # PWA: index.html, app.js, sw.js, manifest.json
└── data/
    ├── app.db                   # SQLite (gitignored)
    ├── logs/
    └── user_files/<user_id>/    # per-user file_rw chroot
```

### Why one process
APScheduler runs in-process inside FastAPI. For ~5 users and a handful of scheduled tasks, this avoids Redis, Celery, and IPC. If load ever requires it, the scheduler can be split out without changing the data model.

## 10. Data Model (SQLite)

```sql
users (
  id INTEGER PRIMARY KEY,
  invite_code_hash TEXT UNIQUE,    -- bcrypt of the code
  display_name TEXT,
  telegram_chat_id TEXT,           -- nullable
  webpush_subscription TEXT,       -- nullable, JSON
  created_at TIMESTAMP
)

sessions (
  id TEXT PRIMARY KEY,             -- random token (cookie value)
  user_id INTEGER,
  expires_at TIMESTAMP
)

api_keys (
  id INTEGER PRIMARY KEY,
  user_id INTEGER,
  provider TEXT,                   -- 'openai_compat' | 'anthropic' | 'gemini'
  label TEXT,
  base_url TEXT,                   -- nullable
  default_model TEXT,
  encrypted_key BLOB,              -- Fernet ciphertext
  created_at TIMESTAMP
)

chats (id, user_id, title, provider_key_id, model, created_at)
messages (id, chat_id, role, content, tool_calls JSON, created_at)

tasks (
  id, user_id, name, goal_prompt,
  cron_expr,
  allowed_tools JSON,
  provider_key_id, model,
  delivery JSON,                   -- {telegram: bool, webpush: bool, email: str|null}
  enabled BOOL, last_run_at, next_run_at
)

task_runs (id, task_id, status, started_at, ended_at, output, error)
```

## 11. Provider Abstraction

```python
class Provider(Protocol):
    async def chat(self, messages, model, tools=None, stream=False) -> AsyncIterator[ChatChunk]: ...
    def normalize_tools(self, tools: list[Tool]) -> Any:     # to provider's tool schema
    def parse_tool_calls(self, response) -> list[ToolCall]:  # from provider's response
```

The agent loop never branches on provider type — it talks only to this interface.

## 12. Tech Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI + Uvicorn (Python 3.11+) |
| DB | SQLite (single file) |
| Frontend | HTML + vanilla JS + Tailwind via CDN (no build step) |
| PWA | `manifest.json` + service worker (~50 lines) |
| Encryption | `cryptography` (Fernet) + `keyring` (macOS Keychain) |
| LLM SDKs | `openai`, `anthropic`, `google-genai` |
| Agent loop | Hand-rolled Python (no CrewAI, no LangChain — per user preference) |
| Scheduler | APScheduler (in-process) |
| HTTP | `httpx` |
| HTML extract | `readability-lxml` |
| Email | Resend API |
| Web search | Brave Search API |
| Telegram | `python-telegram-bot` |
| Web Push | `pywebpush` + VAPID |
| Public URL | `cloudflared` (Cloudflare Tunnel) |
| Process supervision | macOS launchd |

## 13. Error Handling

- **Provider API errors:** user-facing message + retry button; full stack trace logged.
- **Tool errors:** returned to the LLM as `{"error": "..."}` so the agent can recover or report back to the user.
- **Scheduled task failure:** `task_runs.status='failed'`, error sent to user via configured delivery channel.
- **Keychain unavailable at startup:** app refuses to boot. No plaintext fallback.
- **Cloudflare Tunnel down:** local access still works at `http://localhost:8088`.

## 14. Testing

- `pytest` for:
  - Provider adapters (mock SDK responses for all three shapes)
  - Agent loop (mock provider, real tool dispatch with stub tools)
  - NL scheduler parser (golden-file tests for ~10 example utterances)
  - Encryption round-trip (real Keychain in a test service namespace)
  - Invite-code auth flow
- One end-to-end smoke test: spin up FastAPI in a subprocess, post a chat with a stub provider, assert response shape + DB state.
- No browser/UI tests in v1.

## 15. Out of Scope for v1

Deliberate cuts to keep v1 shippable:

- Streaming responses (chat is non-streaming first; SSE in v1.1)
- File uploads / vision / image generation
- Per-tool rate limiting (Cloudflare Tunnel + invite gating is sufficient)
- Multi-user shared chats
- Native iOS/Android apps
- Password-based auth, email magic links, OAuth
- Full audit log UI (`task_runs` table is the source of truth; query via SQLite directly)
