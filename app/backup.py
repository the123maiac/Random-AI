from __future__ import annotations

import logging
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from . import db

log = logging.getLogger("backup")

ENV_REPO = "AGENTIC_CHAT_BACKUP_REPO"   # e.g. "you/agentic-chat-data"
ENV_TOKEN = "HF_TOKEN"                   # write-scoped HF token
BACKUP_FILE = "data.tar.gz"

DATA_DIR = db.DB_PATH.parent
INCLUDE = ("app.db", "user_files")


def _enabled() -> bool:
    return bool(os.getenv(ENV_REPO) and os.getenv(ENV_TOKEN))


def _api():
    from huggingface_hub import HfApi  # type: ignore
    return HfApi(token=os.getenv(ENV_TOKEN))


def _ensure_repo() -> None:
    from huggingface_hub import HfApi  # type: ignore
    api = HfApi(token=os.getenv(ENV_TOKEN))
    api.create_repo(repo_id=os.getenv(ENV_REPO), repo_type="dataset", private=True, exist_ok=True)


def _make_tar(dest: Path) -> None:
    with tarfile.open(dest, "w:gz") as tar:
        for name in INCLUDE:
            p = DATA_DIR / name
            if p.exists():
                tar.add(p, arcname=name)


def _checkpoint_db() -> None:
    """Force WAL checkpoint so app.db is consistent before tarring."""
    if not (DATA_DIR / "app.db").exists():
        return
    try:
        conn = sqlite3.connect(DATA_DIR / "app.db")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        log.exception("wal checkpoint failed")


def restore_on_boot() -> None:
    """Pull latest backup from HF Dataset if present and DB doesn't already exist."""
    if not _enabled():
        log.info("backup: not configured (set %s and %s to enable)", ENV_REPO, ENV_TOKEN)
        return
    if (DATA_DIR / "app.db").exists():
        log.info("backup: local DB already exists, skipping restore")
        return
    try:
        from huggingface_hub import hf_hub_download  # type: ignore
        path = hf_hub_download(
            repo_id=os.getenv(ENV_REPO),
            filename=BACKUP_FILE,
            repo_type="dataset",
            token=os.getenv(ENV_TOKEN),
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(DATA_DIR)
        log.info("backup: restored from %s", os.getenv(ENV_REPO))
    except Exception as e:
        log.warning("backup: nothing to restore (%s)", e)


def push_backup() -> dict:
    if not _enabled():
        return {"status": "disabled"}
    try:
        _ensure_repo()
        _checkpoint_db()
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        _make_tar(tmp_path)
        size = tmp_path.stat().st_size
        api = _api()
        api.upload_file(
            path_or_fileobj=str(tmp_path),
            path_in_repo=BACKUP_FILE,
            repo_id=os.getenv(ENV_REPO),
            repo_type="dataset",
            commit_message="auto-backup",
        )
        tmp_path.unlink(missing_ok=True)
        log.info("backup: pushed %s bytes", size)
        return {"status": "ok", "bytes": size}
    except Exception as e:
        log.exception("backup push failed")
        return {"status": "failed", "error": str(e)}
