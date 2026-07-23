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


@dataclass(frozen=True)
class LabeledUsage:
    """Aggregated usage for a non-user bucket shown with a display label."""

    label: str
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class UsageReport:
    """Usage grouped for the dashboard's user, service, and unknown sections."""

    users: dict[int, UsageSummary]
    services: list[LabeledUsage]
    unknown: list[LabeledUsage]


@dataclass
class _Totals:
    total_tokens: int = 0
    total_requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class UsageAttribution:
    """Raw per-key attribution totals before dashboard summary rows are built."""

    users: dict[int, _Totals]
    services: dict[str, _Totals]
    unknown: dict[str, _Totals]
    unknown_labels: dict[str, str]


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


def _key_alias(entry: dict[str, Any]) -> str | None:
    raw_alias = (entry.get("metadata") or {}).get("key_alias")
    if raw_alias is None:
        return None
    alias = str(raw_alias).strip()
    return alias or None


def _token_text(token: Any) -> str:
    return str(token)


def _masked_token(token: str) -> str:
    cleaned = token.strip()
    return f"…{cleaned[-4:]}" if cleaned else "…"


def _service_prefixes(prefixes: Sequence[str]) -> tuple[str, ...]:
    return tuple(prefix.strip() for prefix in prefixes if prefix.strip())


def _add_metrics(bucket: _Totals, metrics: dict[str, Any]) -> None:
    bucket.total_tokens += _num(metrics.get("total_tokens"))
    bucket.total_requests += _num(metrics.get("api_requests"))
    bucket.prompt_tokens += _num(metrics.get("prompt_tokens"))
    bucket.completion_tokens += _num(metrics.get("completion_tokens"))


def _labeled_usage(label: str, totals: _Totals) -> LabeledUsage:
    return LabeledUsage(
        label=label,
        total_tokens=totals.total_tokens,
        total_requests=totals.total_requests,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
    )


def _sorted_labeled(
    totals: dict[str, _Totals], labels: dict[str, str] | None = None
) -> list[LabeledUsage]:
    rows = [
        _labeled_usage((labels or {}).get(identifier, identifier), total)
        for identifier, total in totals.items()
    ]
    return sorted(rows, key=lambda row: (-row.total_tokens, -row.completion_tokens, row.label))


def order_user_summaries(summaries: Iterable[UsageSummary]) -> list[UsageSummary]:
    """Order user rows for the dashboard by total tokens, descending."""
    return sorted(
        summaries,
        key=lambda s: (-s.total_tokens, -s.completion_tokens, s.username),
    )


def attribute(
    rows: Iterable[dict[str, Any]],
    by_token: dict[str, int],
    by_alias: dict[str, int],
    *,
    service_prefixes: Sequence[str] = (),
) -> UsageAttribution:
    """Bucket each api_key's per-day metrics into users, services, or unknown totals.

    User-owned keys are matched first by LiteLLM token and then by alias. Unowned keys
    with a configured service alias prefix become service rows; all other unowned keys
    become visible unknown rows instead of being silently dropped.
    """
    users: dict[int, _Totals] = defaultdict(_Totals)
    services: dict[str, _Totals] = defaultdict(_Totals)
    unknown: dict[str, _Totals] = defaultdict(_Totals)
    unknown_labels: dict[str, str] = {}
    prefixes = _service_prefixes(service_prefixes)
    for row in rows:
        breakdown = (row.get("breakdown") or {}).get("api_keys") or {}
        for token, entry in breakdown.items():
            entry = entry or {}
            metrics = entry.get("metrics") or {}
            alias = _key_alias(entry)
            token_text = _token_text(token)
            uid = by_token.get(token_text)
            if uid is None and alias is not None:
                uid = by_alias.get(alias)
            if uid is not None:
                _add_metrics(users[uid], metrics)
                continue

            if alias is not None and any(alias.startswith(prefix) for prefix in prefixes):
                _add_metrics(services[alias], metrics)
                continue

            identifier = alias if alias is not None else token_text
            label = alias if alias is not None else _masked_token(token_text)
            unknown_labels.setdefault(identifier, label)
            _add_metrics(unknown[identifier], metrics)
    return UsageAttribution(
        users=dict(users),
        services=dict(services),
        unknown=dict(unknown),
        unknown_labels=unknown_labels,
    )


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
    service_prefixes: Sequence[str] = (),
    window_days: int = USAGE_WINDOW_DAYS,
) -> UsageReport:
    """Return usage grouped for ``users`` over the recent window.

    Fetches the global daily activity once and attributes ``api_keys`` usage to owners.
    Degrades gracefully: on a LiteLLM error every user summary is marked unavailable
    (zeros), and non-user buckets are omitted.
    """
    end = datetime.now(UTC).date()
    start = end - timedelta(days=window_days)
    try:
        rows = await litellm.daily_activity(start_date=start.isoformat(), end_date=end.isoformat())
    except LiteLLMError:
        return UsageReport(
            users={user.id: _summary(user, None, available=False) for user in users},
            services=[],
            unknown=[],
        )

    by_token, by_alias = _key_index(api_keys)
    attribution = attribute(rows, by_token, by_alias, service_prefixes=service_prefixes)
    return UsageReport(
        users={user.id: _summary(user, attribution.users.get(user.id)) for user in users},
        services=_sorted_labeled(attribution.services),
        unknown=_sorted_labeled(attribution.unknown, attribution.unknown_labels),
    )
