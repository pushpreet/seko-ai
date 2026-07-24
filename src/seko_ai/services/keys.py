"""Business logic for per-user LiteLLM virtual keys."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai import metrics
from seko_ai.config import Settings
from seko_ai.models import ApiKey, User
from seko_ai.services.litellm_client import LiteLLMClient


def make_alias(username: str) -> str:
    """Build a unique, human-readable LiteLLM key alias for a user."""
    slug = "".join(c if c.isalnum() else "-" for c in username.lower()).strip("-") or "user"
    return f"seko-{slug}-{uuid.uuid4().hex[:8]}"


def mask_key(key: str) -> str:
    """Return a non-sensitive display hint for a key (prefix + last 4)."""
    if len(key) <= 8:
        return "****"
    return f"{key[:5]}…{key[-4:]}"


def litellm_user_id(user: User) -> str:
    """Stable LiteLLM user_id for a seko-ai user (ties spend/keys to the person)."""
    return f"seko-user-{user.id}"


def list_user_keys(session: Session, user_id: int) -> list[ApiKey]:
    """Return a user's own (non-workspace) active API keys, newest first."""
    stmt = (
        select(ApiKey)
        .where(
            ApiKey.user_id == user_id,
            ApiKey.active.is_(True),
            ApiKey.workspace_id.is_(None),
        )
        .order_by(ApiKey.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def get_key(session: Session, user_id: int, key_id: int) -> ApiKey | None:
    """Return a specific user-owned (non-workspace) key, or None."""
    stmt = select(ApiKey).where(
        ApiKey.id == key_id,
        ApiKey.user_id == user_id,
        ApiKey.workspace_id.is_(None),
    )
    return session.execute(stmt).scalar_one_or_none()


def deactivate_by_alias(session: Session, key_alias: str) -> None:
    """Mark the local ApiKey row for an alias inactive (after revoking it at LiteLLM)."""
    key = session.execute(
        select(ApiKey).where(ApiKey.key_alias == key_alias)
    ).scalar_one_or_none()
    if key is not None:
        key.active = False
        session.flush()


async def create_key_for_user(
    session: Session,
    client: LiteLLMClient,
    user: User,
    settings: Settings,
    *,
    workspace_id: int | None = None,
) -> tuple[ApiKey, str]:
    """Mint a LiteLLM virtual key for the user and persist its metadata.

    Returns the persisted ``ApiKey`` and the plaintext key value (shown to the user once).
    Pass ``workspace_id`` for a workspace-scoped key (hidden from the user's /keys list).
    """
    alias = make_alias(user.username)
    result = await client.generate_key(
        user_id=litellm_user_id(user),
        key_alias=alias,
        models=[settings.llm_model, settings.llm_embedding_model],
        metadata={"seko_user_id": user.id, "seko_username": user.username},
    )
    plaintext = result.get("key")
    if not plaintext:
        raise ValueError("LiteLLM did not return a key value")

    api_key = ApiKey(
        user_id=user.id,
        workspace_id=workspace_id,
        litellm_key_id=str(result.get("token") or result.get("key_name") or alias),
        key_alias=alias,
        masked_key=mask_key(plaintext),
        active=True,
    )
    session.add(api_key)
    session.flush()
    metrics.KEYS_ISSUED.inc()
    return api_key, plaintext


async def revoke_key(session: Session, client: LiteLLMClient, api_key: ApiKey) -> None:
    """Revoke a key at LiteLLM and mark it inactive locally."""
    await client.delete_keys(key_aliases=[api_key.key_alias])
    api_key.active = False
    session.flush()


async def rotate_key(
    session: Session,
    client: LiteLLMClient,
    user: User,
    api_key: ApiKey,
    settings: Settings,
) -> tuple[ApiKey, str]:
    """Revoke an existing key and issue a fresh one for the same user."""
    await revoke_key(session, client, api_key)
    return await create_key_for_user(session, client, user, settings)
