from __future__ import annotations

import os
import secrets
import sys

from cryptography.fernet import Fernet

SERVICE = "agentic-chat"
ACCOUNT_FERNET = "fernet_master"
ACCOUNT_VAPID = "vapid_private"
ACCOUNT_SESSION = "session_secret"

ENV_MASTER_KEY = "AGENTIC_CHAT_MASTER_KEY"
ENV_SESSION_SECRET = "AGENTIC_CHAT_SESSION_SECRET"


def _from_env_or_keychain(env_var: str, account: str, generator) -> str:
    val = os.getenv(env_var)
    if val:
        return val
    try:
        import keyring  # type: ignore
        existing = keyring.get_password(SERVICE, account)
        if existing:
            return existing
        new = generator()
        keyring.set_password(SERVICE, account, new)
        return new
    except Exception:
        return ""


def get_fernet() -> Fernet:
    key = _from_env_or_keychain(ENV_MASTER_KEY, ACCOUNT_FERNET, lambda: Fernet.generate_key().decode())
    if not key:
        sys.stderr.write(
            f"FATAL: no Fernet master key. Set {ENV_MASTER_KEY} env var or run on macOS with Keychain access.\n"
            f"  Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        )
        raise SystemExit(1)
    return Fernet(key.encode() if isinstance(key, str) else key)


def get_session_secret() -> str:
    val = _from_env_or_keychain(ENV_SESSION_SECRET, ACCOUNT_SESSION, lambda: secrets.token_urlsafe(48))
    return val or secrets.token_urlsafe(48)


def encrypt(plaintext: str) -> bytes:
    return get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return get_fernet().decrypt(ciphertext).decode()
