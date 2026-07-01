"""Envelope encryption for per-user data keys.

Each user gets a random 256-bit data-encryption key (DEK) used to encrypt their workspace
home volume (gocryptfs on the LLM host). The DEK is never stored in the clear: it is wrapped
with the seko-ai master key (AES-256-GCM) and the wrapped form is kept in the DB. The admin
holds the master key (from SOPS), so recovery/restore is possible — matching the chosen
"at-rest per-user encryption, admin holds keys" model.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DEK_BYTES = 32
_NONCE_BYTES = 12


class CryptoError(RuntimeError):
    """Raised on key configuration or unwrap failures."""


def load_master_key(master_key_b64: str) -> bytes:
    """Decode and validate the base64 master key (must be 32 bytes)."""
    if not master_key_b64:
        raise CryptoError("master key is not configured (SEKO_MASTER_KEY)")
    try:
        raw = base64.b64decode(master_key_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CryptoError("master key is not valid base64") from exc
    if len(raw) != _DEK_BYTES:
        raise CryptoError(f"master key must decode to {_DEK_BYTES} bytes, got {len(raw)}")
    return raw


def generate_dek() -> bytes:
    """Return a fresh random 256-bit data-encryption key."""
    return os.urandom(_DEK_BYTES)


def wrap_dek(master_key: bytes, dek: bytes) -> str:
    """Encrypt (wrap) a DEK with the master key; returns base64(nonce || ciphertext)."""
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(master_key).encrypt(nonce, dek, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def unwrap_dek(master_key: bytes, wrapped_b64: str) -> bytes:
    """Decrypt (unwrap) a wrapped DEK produced by :func:`wrap_dek`."""
    try:
        blob = base64.b64decode(wrapped_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CryptoError("wrapped DEK is not valid base64") from exc
    if len(blob) <= _NONCE_BYTES:
        raise CryptoError("wrapped DEK is too short")
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        return AESGCM(master_key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise CryptoError("wrapped DEK failed authentication (wrong master key?)") from exc


def dek_passphrase(dek: bytes) -> str:
    """Derive a stable ASCII passphrase from a DEK for gocryptfs (base64, url-safe)."""
    return base64.urlsafe_b64encode(dek).decode("ascii")
