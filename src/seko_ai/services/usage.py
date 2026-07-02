"""Per-user LLM usage summaries, sourced from LiteLLM's activity API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seko_ai.models import User
from seko_ai.services.keys import litellm_user_id
from seko_ai.services.litellm_client import LiteLLMClient, LiteLLMError


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage for a user (best-effort from LiteLLM's response)."""

    username: str
    total_tokens: int
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    available: bool = True


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize(username: str, raw: dict[str, Any], *, available: bool = True) -> UsageSummary:
    """Reduce a LiteLLM activity payload to totals, tolerating schema variation."""
    meta = raw.get("metadata") or {}
    results = raw.get("results") or []

    tokens = meta.get("total_tokens")
    requests = meta.get("total_api_requests", meta.get("total_requests"))
    prompt_tokens = _first_present(meta, "total_prompt_tokens", "prompt_tokens")
    completion_tokens = _first_present(meta, "total_completion_tokens", "completion_tokens")

    if tokens is None:
        tokens = sum(_num(_metric(r, "total_tokens")) for r in results)
    if requests is None:
        requests = sum(_num(_metric(r, "api_requests")) for r in results)
    if prompt_tokens is None:
        prompt_tokens = sum(_num(_metric(r, "prompt_tokens")) for r in results)
    if completion_tokens is None:
        completion_tokens = sum(_num(_metric(r, "completion_tokens")) for r in results)

    return UsageSummary(
        username=username,
        total_tokens=int(_num(tokens)),
        total_requests=int(_num(requests)),
        prompt_tokens=int(_num(prompt_tokens)),
        completion_tokens=int(_num(completion_tokens)),
        available=available,
    )


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _metric(result: dict[str, Any], key: str) -> Any:
    metrics = result.get("metrics") or {}
    return metrics.get(key, result.get(key))


async def user_summary(litellm: LiteLLMClient, user: User) -> UsageSummary:
    """Fetch and summarize a single user's usage; degrade gracefully on error."""
    from datetime import UTC, datetime, timedelta

    end = datetime.now(UTC).date()
    start = end - timedelta(days=30)
    try:
        raw = await litellm.user_daily_activity(
            litellm_user_id(user), start_date=start.isoformat(), end_date=end.isoformat()
        )
    except LiteLLMError:
        return UsageSummary(
            username=user.username,
            total_tokens=0,
            total_requests=0,
            prompt_tokens=0,
            completion_tokens=0,
            available=False,
        )
    return summarize(user.username, raw)
