"""Business logic for per-user LiteLLM virtual keys."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from seko_ai import metrics
from seko_ai.config import Settings
from seko_ai.models import ApiKey, ApiKeyIdentity, User
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError

MAX_KEY_NAME_LENGTH = 64


class InvalidKeyName(ValueError):
    """Raised when a user-facing API key name is invalid or already used."""


def normalize_name(name: str) -> tuple[str, str]:
    """Return a display name and its case-insensitive uniqueness value."""
    display = " ".join(name.split())
    if not display:
        raise InvalidKeyName("Enter a name for this API key.")
    if len(display) > MAX_KEY_NAME_LENGTH:
        raise InvalidKeyName(f"Key names must be {MAX_KEY_NAME_LENGTH} characters or fewer.")
    return display, display.casefold()


def create_identity(session: Session, user: User, name: str) -> ApiKeyIdentity:
    """Create a named logical key, enforcing per-user case-insensitive uniqueness."""
    display, normalized = normalize_name(name)
    existing = session.execute(
        select(ApiKeyIdentity.id).where(
            ApiKeyIdentity.user_id == user.id,
            ApiKeyIdentity.normalized_name == normalized,
        )
    ).first()
    if existing is not None:
        raise InvalidKeyName("You already have an API key with that name.")

    identity = ApiKeyIdentity(
        user_id=user.id,
        name=display,
        normalized_name=normalized,
    )
    try:
        with session.begin_nested():
            session.add(identity)
            session.flush()
    except IntegrityError as exc:
        raise InvalidKeyName("You already have an API key with that name.") from exc
    return identity


def rename_identity(
    session: Session, user_id: int, key_id: int, name: str
) -> ApiKeyIdentity | None:
    """Rename a user-owned logical key, or return None when the key is out of scope."""
    api_key = get_key(session, user_id, key_id)
    if api_key is None or api_key.identity is None:
        return None
    display, normalized = normalize_name(name)
    duplicate = session.execute(
        select(ApiKeyIdentity.id).where(
            ApiKeyIdentity.user_id == user_id,
            ApiKeyIdentity.normalized_name == normalized,
            ApiKeyIdentity.id != api_key.identity.id,
        )
    ).first()
    if duplicate is not None:
        raise InvalidKeyName("You already have an API key with that name.")
    identity = api_key.identity
    try:
        with session.begin_nested():
            identity.name = display
            identity.normalized_name = normalized
            session.flush()
    except IntegrityError as exc:
        raise InvalidKeyName("You already have an API key with that name.") from exc
    return identity


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
    """Return a user's active API keys, newest first."""
    stmt = (
        select(ApiKey)
        .where(
            ApiKey.user_id == user_id,
            ApiKey.active.is_(True),
        )
        .order_by(ApiKey.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def get_key(session: Session, user_id: int, key_id: int) -> ApiKey | None:
    """Return a specific user-owned key, or None."""
    stmt = select(ApiKey).where(
        ApiKey.id == key_id,
        ApiKey.user_id == user_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def deactivate_by_alias(session: Session, key_alias: str) -> None:
    """Mark the local ApiKey row for an alias inactive (after revoking it at LiteLLM)."""
    key = session.execute(select(ApiKey).where(ApiKey.key_alias == key_alias)).scalar_one_or_none()
    if key is not None:
        key.active = False
        session.flush()


async def create_key_for_user(
    session: Session,
    client: LiteLLMClient,
    user: User,
    settings: Settings,
    *,
    name: str | None = None,
    identity: ApiKeyIdentity | None = None,
) -> tuple[ApiKey, str]:
    """Mint a LiteLLM virtual key for the user and persist its metadata.

    Returns the persisted ``ApiKey`` and the plaintext key value (shown to the user once).
    """
    if identity is None and name is not None:
        display, normalized = normalize_name(name)
        existing = session.execute(
            select(ApiKeyIdentity.id).where(
                ApiKeyIdentity.user_id == user.id,
                ApiKeyIdentity.normalized_name == normalized,
            )
        ).first()
        if existing is not None:
            raise InvalidKeyName("You already have an API key with that name.")
        name = display

    alias = make_alias(user.username)
    result = await client.generate_key(
        user_id=litellm_user_id(user),
        key_alias=alias,
        # NOTE: deliberately no `models=` allowlist. LiteLLM's get_complete_model_list()
        # resolves key models -> team models -> ALL proxy models, so an empty/absent list
        # means "every model on the proxy", including ones added later at runtime. That is
        # what we want: new/experimental models on the homelab proxy become usable with no
        # key surgery and no re-issuing. (This proxy serves a handful of trusted friends;
        # per-key model scoping bought nothing but maintenance.)
        # Do NOT "fix" this to models=["*"] — the wildcard passes request auth but breaks
        # GET /v1/models, which is what drives the Open WebUI model dropdown.
        metadata={"seko_user_id": user.id, "seko_username": user.username},
    )
    plaintext = result.get("key")
    if not plaintext:
        await client.delete_keys(key_aliases=[alias])
        raise ValueError("LiteLLM did not return a key value")

    if identity is None and name is not None:
        try:
            identity = create_identity(session, user, name)
        except InvalidKeyName:
            await client.delete_keys(key_aliases=[alias])
            raise

    api_key = ApiKey(
        user_id=user.id,
        identity_id=identity.id if identity is not None else None,
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
    replacement, plaintext = await create_key_for_user(
        session,
        client,
        user,
        settings,
        identity=api_key.identity,
    )
    try:
        await revoke_key(session, client, api_key)
    except LiteLLMError as revoke_error:
        try:
            await client.delete_keys(key_aliases=[replacement.key_alias])
        except LiteLLMError as cleanup_error:
            replacement.active = False
            session.flush()
            raise LiteLLMError(
                f"{revoke_error}; replacement cleanup also failed "
                f"(alias={replacement.key_alias}): {cleanup_error}"
            ) from cleanup_error
        session.delete(replacement)
        session.flush()
        raise
    return replacement, plaintext
