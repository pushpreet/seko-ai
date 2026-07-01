"""User persistence and access-control evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from seko_ai.models import User


@dataclass(frozen=True)
class AccessDecision:
    """Result of evaluating a user's OIDC groups against the access policy."""

    allowed: bool
    is_admin: bool


def evaluate_access(groups: list[str], users_group: str, admins_group: str) -> AccessDecision:
    """Decide whether a set of OIDC groups grants access, and whether it's an admin.

    Admins (members of ``admins_group``) are always allowed. Otherwise the user must be a
    member of ``users_group``.
    """
    normalized = {g.strip() for g in groups if g and g.strip()}
    is_admin = admins_group in normalized
    allowed = is_admin or users_group in normalized
    return AccessDecision(allowed=allowed, is_admin=is_admin)


def get_user_by_subject(session: Session, subject: str) -> User | None:
    """Return the user with the given OIDC subject, or None."""
    return session.execute(select(User).where(User.subject == subject)).scalar_one_or_none()


def upsert_user(
    session: Session,
    *,
    subject: str,
    username: str,
    email: str | None,
    display_name: str | None,
    is_admin: bool,
) -> User:
    """Create or update a user from OIDC claims and return the persisted row."""
    user = get_user_by_subject(session, subject)
    if user is None:
        user = User(subject=subject)
        session.add(user)
    user.username = username
    user.email = email
    user.display_name = display_name
    user.is_admin = is_admin
    session.flush()
    return user


def session_payload(user: User) -> dict[str, object]:
    """Return the minimal, serializable user info stored in the session cookie."""
    return {
        "id": user.id,
        "subject": user.subject,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }
