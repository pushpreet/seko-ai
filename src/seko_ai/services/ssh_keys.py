"""SSH public key management (GitHub-style: multiple named keys per user)."""

from __future__ import annotations

import base64
import binascii
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.models import SSHKey, User

_VALID_PREFIXES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)


class InvalidSSHKey(ValueError):
    """Raised when a submitted string is not a valid OpenSSH public key."""


def parse_public_key(raw: str) -> tuple[str, str]:
    """Validate an OpenSSH public key, returning (normalized_key, fingerprint).

    Accepts the standard ``<type> <base64> [comment]`` form. The fingerprint is the
    OpenSSH SHA256 form (``SHA256:...``) computed from the key blob.
    """
    text = raw.strip()
    parts = text.split()
    if len(parts) < 2 or parts[0] not in _VALID_PREFIXES:
        raise InvalidSSHKey("Not a valid OpenSSH public key.")
    key_type, blob = parts[0], parts[1]
    try:
        decoded = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidSSHKey("The key data is not valid base64.") from exc
    # The blob must self-describe its algorithm matching the prefix.
    if not decoded.startswith(len(key_type).to_bytes(4, "big") + key_type.encode()):
        raise InvalidSSHKey("The key type does not match its data.")
    digest = hashlib.sha256(decoded).digest()
    fingerprint = "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
    normalized = " ".join(parts[:2]) + (f" {' '.join(parts[2:])}" if len(parts) > 2 else "")
    return normalized, fingerprint


def list_keys(session: Session, user_id: int) -> list[SSHKey]:
    """Return a user's SSH keys, newest first."""
    stmt = select(SSHKey).where(SSHKey.user_id == user_id).order_by(SSHKey.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def add_key(session: Session, user: User, *, title: str, public_key: str) -> SSHKey:
    """Validate and store a new SSH key for the user (deduped by fingerprint)."""
    normalized, fingerprint = parse_public_key(public_key)
    existing = session.execute(
        select(SSHKey).where(SSHKey.user_id == user.id, SSHKey.fingerprint == fingerprint)
    ).scalar_one_or_none()
    if existing is not None:
        raise InvalidSSHKey("You have already added this key.")
    key = SSHKey(
        user_id=user.id,
        title=title.strip() or "key",
        public_key=normalized,
        fingerprint=fingerprint,
    )
    session.add(key)
    session.flush()
    return key


def delete_key(session: Session, user_id: int, key_id: int) -> bool:
    """Delete a user's SSH key by id. Returns True if a key was removed."""
    key = session.execute(
        select(SSHKey).where(SSHKey.id == key_id, SSHKey.user_id == user_id)
    ).scalar_one_or_none()
    if key is None:
        return False
    session.delete(key)
    session.flush()
    return True


def authorized_keys(session: Session, user_id: int) -> str:
    """Return all of a user's public keys, newline-joined (for SEKO_AUTHORIZED_KEYS)."""
    return "\n".join(k.public_key for k in list_keys(session, user_id))


def has_keys(session: Session, user_id: int) -> bool:
    """Whether the user has at least one SSH key registered."""
    return session.execute(
        select(SSHKey.id).where(SSHKey.user_id == user_id).limit(1)
    ).first() is not None
