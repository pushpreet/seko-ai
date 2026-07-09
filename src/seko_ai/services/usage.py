"""Per-user LLM usage summaries, derived from LiteLLM's per-key activity breakdown.

LiteLLM's ``/user/daily/activity`` endpoint ignores a ``user_id`` filter for admin
(master-key) callers and returns *global* totals — so seko fetches the global activity
once and attributes each key's usage to its owner using the local :class:`ApiKey` table
(matched by LiteLLM token, falling back to the key alias). This avoids showing the same
combined total for every user.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from seko_ai.models import ApiKey, User
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError

USAGE_WINDOW_DAYS = 30


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage for a user, summed over that user's LiteLLM keys."""

    username: str
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    available: bool = True


@dataclass
class _Totals:
    total_tokens: int = 0
    total_requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _num(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _key_index(api_keys: Iterable[ApiKey]) -> tuple[dict[str, int], dict[str, int]]:
    """Map LiteLLM token -> user_id and key_alias -> user_id from local key rows."""
    by_token: dict[str, int] = {}
    by_alias: dict[str, int] = {}
    for key in api_keys:
        if key.litellm_key_id:
            by_token[key.litellm_key_id] = key.user_id
        if key.key_alias:
            by_alias[key.key_alias] = key.user_id
    return by_token, by_alias


def attribute(
    rows: Iterable[dict[str, Any]],
    by_token: dict[str, int],
    by_alias: dict[str, int],
) -> dict[int, _Totals]:
    """Bucket each api_key's per-day metrics into its owning seko user's totals.

    Keys we don't recognise (e.g. blank/legacy ``user_id`` traffic) are ignored.
    """
    totals: dict[int, _Totals] = defaultdict(_Totals)
    for row in rows:
        breakdown = (row.get("breakdown") or {}).get("api_keys") or {}
        for token, entry in breakdown.items():
            metrics = entry.get("metrics") or {}
            alias = (entry.get("metadata") or {}).get("key_alias")
            uid = by_token.get(token)
            if uid is None and alias is not None:
                uid = by_alias.get(alias)
            if uid is None:
                continue
            bucket = totals[uid]
            bucket.total_tokens += _num(metrics.get("total_tokens"))
            bucket.total_requests += _num(metrics.get("api_requests"))
            bucket.prompt_tokens += _num(metrics.get("prompt_tokens"))
            bucket.completion_tokens += _num(metrics.get("completion_tokens"))
    return totals


def _summary(user: User, totals: _Totals | None, *, available: bool = True) -> UsageSummary:
    t = totals or _Totals()
    return UsageSummary(
        username=user.username,
        total_tokens=t.total_tokens,
        total_requests=t.total_requests,
        prompt_tokens=t.prompt_tokens,
        completion_tokens=t.completion_tokens,
        available=available,
    )


async def collect(
    litellm: LiteLLMClient,
    users: Sequence[User],
    api_keys: Iterable[ApiKey],
    *,
    window_days: int = USAGE_WINDOW_DAYS,
) -> dict[int, UsageSummary]:
    """Return ``{user.id: UsageSummary}`` for ``users`` over the recent window.

    Fetches the global daily activity once and attributes ``api_keys`` usage to owners.
    Degrades gracefully: on a LiteLLM error every summary is marked unavailable (zeros).
    """
    end = datetime.now(UTC).date()
    start = end - timedelta(days=window_days)
    try:
        rows = await litellm.daily_activity(
            start_date=start.isoformat(), end_date=end.isoformat()
        )
    except LiteLLMError:
        return {user.id: _summary(user, None, available=False) for user in users}

    by_token, by_alias = _key_index(api_keys)
    totals = attribute(rows, by_token, by_alias)
    return {user.id: _summary(user, totals.get(user.id)) for user in users}
