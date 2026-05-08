from __future__ import annotations

import base64
import os
import secrets

import keyring
from cryptography.fernet import Fernet

SERVICE = "agentic-chat"
ACCOUNT_FERNET = "fernet_master"
ACCOUNT_VAPID = "vapid_private"
ACCOUNT_SESSION = "session_secret"

_ENV_BYPASS = os.getenv("AGENTIC_CHAT_DEV_KEY")


def _get_or_create(account: str, generator) -> str:
    val = keyring.get_password(SERVICE, account)
    if val:
        return val
    val = generator()
    keyring.set_password(SERVICE, account, val)
    return val


def get_fernet() -> Fernet:
    if _ENV_BYPASS:
        return Fernet(_ENV_BYPASS.encode())
    key = _get_or_create(ACCOUNT_FERNET, lambda: Fernet.generate_key().decode())
    return Fernet(key.encode())


def get_session_secret() -> str:
    if _ENV_BYPASS:
        return "dev-session-secret-do-not-use-in-prod"
    return _get_or_create(ACCOUNT_SESSION, lambda: secrets.token_urlsafe(48))


def encrypt(plaintext: str) -> bytes:
    return get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return get_fernet().decrypt(ciphertext).decode()
