---
title: Agentic Chat
emoji: 🔴
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: BYO-key agentic chat that researches GitHub on a schedule
---

# agentic-chat

BYO-key agentic chat for a small trusted group. The agent uses tool calling to
search the web, scrape pages, search GitHub, send email, and read/write
sandboxed files. A background scheduler runs research topics on an interval —
each run summarizes findings into a knowledge base that future chats retrieve
via embeddings (RAG).

## Run locally (macOS)

```bash
./run.sh                                      # starts on :8088
./.venv/bin/python -m app.cli invite "You"    # mint invite, paste in UI
```

The Fernet master key is generated and stored in macOS Keychain on first run.

## Deploy to Hugging Face Spaces (free)

This repo is configured as a Docker Space. Required Space secrets:

| Secret | Required | What |
|---|---|---|
| `AGENTIC_CHAT_MASTER_KEY` | yes | Fernet master key. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `HF_TOKEN` | recommended | HF token with write access — used for SQLite backup to a private dataset |
| `AGENTIC_CHAT_BACKUP_REPO` | recommended | `you/agentic-chat-data` — a private dataset repo for backups |

On boot, the app restores `data.tar.gz` from the backup dataset if present.
Every 30 minutes (and on shutdown) it pushes a fresh backup. Set up
[cron-job.org](https://cron-job.org) (free) to ping `https://<your-space>.hf.space/healthz`
every 10 minutes so the Space stays warm.

## Environment variables (full list)

| Var | Default | Purpose |
|---|---|---|
| `AGENTIC_CHAT_MASTER_KEY` | — | Fernet master key (env override for Keychain) |
| `AGENTIC_CHAT_SESSION_SECRET` | random | Session secret override |
| `AGENTIC_CHAT_BACKUP_REPO` | — | HF dataset repo id for SQLite backups |
| `HF_TOKEN` | — | HF write token for backups |
| `AGENTIC_CHAT_FROM_EMAIL` | `onboarding@resend.dev` | `From:` for `send_email` tool |
| `PORT` | `7860` | Listen port (HF Spaces enforces 7860) |

## License

Private project. Not redistributable.
