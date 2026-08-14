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
class LabeledUsage:
    """Aggregated usage with a display label and optional model children."""

    label: str
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    models: tuple[LabeledUsage, ...] = ()


@dataclass(frozen=True)
class KeyUsage:
    """Usage for one logical user key, potentially spanning rotated tokens."""

    label: str
    masked_key: str | None
    active: bool
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    models: tuple[LabeledUsage, ...] = ()


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage for a user, summed over that user's logical keys."""

    username: str
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    available: bool = True
    keys: tuple[KeyUsage, ...] = ()


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
    user_keys: dict[int, dict[str, _Totals]]
    user_key_models: dict[int, dict[str, dict[str, _Totals]]]
    services: dict[str, _Totals]
    service_models: dict[str, dict[str, _Totals]]
    unknown: dict[str, _Totals]
    unknown_models: dict[str, dict[str, _Totals]]
    unknown_labels: dict[str, str]


@dataclass(frozen=True)
class _OwnedKey:
    """Attribution target for one physical LiteLLM token."""

    user_id: int
    bucket_id: str


@dataclass
class _KeyDescriptor:
    """Display metadata collected across a logical key's physical tokens."""

    label: str
    masked_key: str | None
    active: bool


def _num(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _key_index(
    api_keys: Iterable[ApiKey],
) -> tuple[
    dict[str, _OwnedKey],
    dict[str, _OwnedKey],
    dict[int, dict[str, _KeyDescriptor]],
]:
    """Index physical tokens/aliases and collect logical-key display metadata."""
    by_token: dict[str, _OwnedKey] = {}
    by_alias: dict[str, _OwnedKey] = {}
    descriptors: dict[int, dict[str, _KeyDescriptor]] = defaultdict(dict)
    for key in api_keys:
        if key.identity is not None:
            bucket_id = f"identity:{key.identity.id}"
            label = key.identity.name
        else:
            bucket_id = f"token:{key.litellm_key_id or key.key_alias}"
            label = key.key_alias or _masked_token(key.litellm_key_id)
        owner = _OwnedKey(user_id=key.user_id, bucket_id=bucket_id)
        if key.litellm_key_id:
            by_token[key.litellm_key_id] = owner
        if key.key_alias:
            by_alias[key.key_alias] = owner
        current = descriptors[key.user_id].get(bucket_id)
        if current is None:
            descriptors[key.user_id][bucket_id] = _KeyDescriptor(
                label=label,
                masked_key=key.masked_key,
                active=key.active,
            )
        elif key.active:
            current.active = True
            current.masked_key = key.masked_key
    return by_token, by_alias, dict(descriptors)


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


def _labeled_usage(
    label: str,
    totals: _Totals,
    models: tuple[LabeledUsage, ...] = (),
) -> LabeledUsage:
    return LabeledUsage(
        label=label,
        total_tokens=totals.total_tokens,
        total_requests=totals.total_requests,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        models=models,
    )


def _sorted_labeled(
    totals: dict[str, _Totals],
    labels: dict[str, str] | None = None,
    model_totals: dict[str, dict[str, _Totals]] | None = None,
) -> list[LabeledUsage]:
    rows = [
        _labeled_usage(
            (labels or {}).get(identifier, identifier),
            total,
            tuple(_sorted_labeled((model_totals or {}).get(identifier, {}))),
        )
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
    by_token: dict[str, _OwnedKey],
    by_alias: dict[str, _OwnedKey],
    *,
    service_prefixes: Sequence[str] = (),
) -> UsageAttribution:
    """Bucket each api_key's per-day metrics into users, services, or unknown totals.

    User-owned keys are matched first by LiteLLM token and then by alias. Unowned keys
    with a configured service alias prefix become service rows; all other unowned keys
    become visible unknown rows instead of being silently dropped.
    """
    users: dict[int, _Totals] = defaultdict(_Totals)
    user_keys: dict[int, dict[str, _Totals]] = defaultdict(lambda: defaultdict(_Totals))
    user_key_models: dict[int, dict[str, dict[str, _Totals]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(_Totals))
    )
    services: dict[str, _Totals] = defaultdict(_Totals)
    service_models: dict[str, dict[str, _Totals]] = defaultdict(lambda: defaultdict(_Totals))
    unknown: dict[str, _Totals] = defaultdict(_Totals)
    unknown_models: dict[str, dict[str, _Totals]] = defaultdict(lambda: defaultdict(_Totals))
    unknown_labels: dict[str, str] = {}
    prefixes = _service_prefixes(service_prefixes)

    def classify(token: Any, entry: dict[str, Any]) -> tuple[str, _OwnedKey | str]:
        alias = _key_alias(entry)
        token_text = _token_text(token)
        owner = by_token.get(token_text)
        if owner is None and alias is not None:
            owner = by_alias.get(alias)
        if owner is not None:
            return "user", owner
        if alias is not None and any(alias.startswith(prefix) for prefix in prefixes):
            return "service", alias
        identifier = alias if alias is not None else token_text
        unknown_labels.setdefault(
            identifier,
            alias if alias is not None else _masked_token(token_text),
        )
        return "unknown", identifier

    for row in rows:
        breakdown = (row.get("breakdown") or {}).get("api_keys") or {}
        for token, entry in breakdown.items():
            entry = entry or {}
            metrics = entry.get("metrics") or {}
            kind, target = classify(token, entry)
            if kind == "user":
                owner = target
                assert isinstance(owner, _OwnedKey)
                _add_metrics(users[owner.user_id], metrics)
                _add_metrics(user_keys[owner.user_id][owner.bucket_id], metrics)
                continue
            identifier = target
            assert isinstance(identifier, str)
            if kind == "service":
                _add_metrics(services[identifier], metrics)
                continue
            _add_metrics(unknown[identifier], metrics)

        models = (row.get("breakdown") or {}).get("models") or {}
        for model, model_entry in models.items():
            key_breakdown = (model_entry or {}).get("api_key_breakdown") or {}
            for token, entry in key_breakdown.items():
                entry = entry or {}
                metrics = entry.get("metrics") or {}
                kind, target = classify(token, entry)
                if kind == "user":
                    owner = target
                    assert isinstance(owner, _OwnedKey)
                    _add_metrics(
                        user_key_models[owner.user_id][owner.bucket_id][str(model)],
                        metrics,
                    )
                    continue
                identifier = target
                assert isinstance(identifier, str)
                if kind == "service":
                    _add_metrics(service_models[identifier][str(model)], metrics)
                    continue
                _add_metrics(unknown_models[identifier][str(model)], metrics)
    return UsageAttribution(
        users=dict(users),
        user_keys={uid: dict(totals) for uid, totals in user_keys.items()},
        user_key_models={
            uid: {bucket: dict(models) for bucket, models in buckets.items()}
            for uid, buckets in user_key_models.items()
        },
        services=dict(services),
        service_models={key: dict(models) for key, models in service_models.items()},
        unknown=dict(unknown),
        unknown_models={key: dict(models) for key, models in unknown_models.items()},
        unknown_labels=unknown_labels,
    )


def _key_usage(
    descriptor: _KeyDescriptor,
    totals: _Totals,
    models: dict[str, _Totals],
) -> KeyUsage:
    return KeyUsage(
        label=descriptor.label,
        masked_key=descriptor.masked_key,
        active=descriptor.active,
        total_tokens=totals.total_tokens,
        total_requests=totals.total_requests,
        prompt_tokens=totals.prompt_tokens,
        completion_tokens=totals.completion_tokens,
        models=tuple(_sorted_labeled(models)),
    )


def _summary(
    user: User,
    totals: _Totals | None,
    *,
    key_totals: dict[str, _Totals] | None = None,
    key_models: dict[str, dict[str, _Totals]] | None = None,
    descriptors: dict[str, _KeyDescriptor] | None = None,
    available: bool = True,
) -> UsageSummary:
    t = totals or _Totals()
    keys: list[KeyUsage] = []
    for bucket_id, descriptor in (descriptors or {}).items():
        bucket_totals = (key_totals or {}).get(bucket_id)
        if bucket_totals is None and not descriptor.active:
            continue
        keys.append(
            _key_usage(
                descriptor,
                bucket_totals or _Totals(),
                (key_models or {}).get(bucket_id, {}),
            )
        )
    keys.sort(key=lambda row: (-row.total_tokens, -row.completion_tokens, row.label.casefold()))
    return UsageSummary(
        username=user.username,
        total_tokens=t.total_tokens,
        total_requests=t.total_requests,
        prompt_tokens=t.prompt_tokens,
        completion_tokens=t.completion_tokens,
        available=available,
        keys=tuple(keys),
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

    by_token, by_alias, descriptors = _key_index(api_keys)
    attribution = attribute(rows, by_token, by_alias, service_prefixes=service_prefixes)
    return UsageReport(
        users={
            user.id: _summary(
                user,
                attribution.users.get(user.id),
                key_totals=attribution.user_keys.get(user.id),
                key_models=attribution.user_key_models.get(user.id),
                descriptors=descriptors.get(user.id),
            )
            for user in users
        },
        services=_sorted_labeled(
            attribution.services,
            model_totals=attribution.service_models,
        ),
        unknown=_sorted_labeled(
            attribution.unknown,
            attribution.unknown_labels,
            attribution.unknown_models,
        ),
    )
