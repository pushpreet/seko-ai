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
    total_spend: float
    total_tokens: int
    total_requests: int
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

    spend = meta.get("total_spend")
    tokens = meta.get("total_tokens")
    requests = meta.get("total_api_requests", meta.get("total_requests"))

    if spend is None:
        spend = sum(_num(_metric(r, "spend")) for r in results)
    if tokens is None:
        tokens = sum(_num(_metric(r, "total_tokens")) for r in results)
    if requests is None:
        requests = sum(_num(_metric(r, "api_requests")) for r in results)

    return UsageSummary(
        username=username,
        total_spend=round(_num(spend), 4),
        total_tokens=int(_num(tokens)),
        total_requests=int(_num(requests)),
        available=available,
    )


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
        return UsageSummary(user.username, 0.0, 0, 0, available=False)
    return summarize(user.username, raw)
