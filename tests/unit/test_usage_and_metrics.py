"""Tests for usage attribution (per-key breakdown -> per-user) and metrics."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from seko_ai.models import ApiKey, User
from seko_ai.services import usage as us


def _user(uid: int, username: str, *, admin: bool = False) -> User:
    return User(id=uid, subject=f"sub-{uid}", username=username, is_admin=admin)


def _key(uid: int, *, token: str, alias: str) -> ApiKey:
    return ApiKey(user_id=uid, litellm_key_id=token, key_alias=alias, active=True)


def _day(*keys: tuple[str, str, dict[str, int]]) -> dict[str, Any]:
    """Build one activity result row with an api_keys breakdown.

    Each ``keys`` item is ``(token, alias, metrics)``.
    """
    return {
        "date": "2026-07-08",
        "breakdown": {
            "api_keys": {
                token: {"metrics": metrics, "metadata": {"key_alias": alias}}
                for token, alias, metrics in keys
            }
        },
    }


def _metrics(total: int, req: int, prompt: int, completion: int) -> dict[str, int]:
    return {
        "total_tokens": total,
        "api_requests": req,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }


def test_attribute_buckets_keys_to_owners() -> None:
    keys = [
        _key(1, token="tok-1", alias="seko-alice-1"),
        _key(2, token="tok-2", alias="seko-bob-1"),
    ]
    by_token, by_alias = us._key_index(keys)
    rows = [
        _day(("tok-1", "seko-alice-1", _metrics(100, 2, 70, 30))),
        _day(("tok-2", "seko-bob-1", _metrics(50, 1, 35, 15))),
    ]
    totals = us.attribute(rows, by_token, by_alias)
    assert totals[1].total_tokens == 100
    assert totals[1].total_requests == 2
    assert totals[2].total_tokens == 50


def test_attribute_sums_multiple_keys_and_days_per_user() -> None:
    keys = [
        _key(1, token="tok-a", alias="seko-alice-a"),
        _key(1, token="tok-b", alias="seko-alice-b"),  # rotated: same user, second key
    ]
    by_token, by_alias = us._key_index(keys)
    rows = [
        _day(
            ("tok-a", "seko-alice-a", _metrics(100, 2, 70, 30)),
            ("tok-b", "seko-alice-b", _metrics(10, 1, 6, 4)),
        ),
        _day(("tok-a", "seko-alice-a", _metrics(5, 1, 4, 1))),
    ]
    totals = us.attribute(rows, by_token, by_alias)
    assert totals[1].total_tokens == 115
    assert totals[1].total_requests == 4
    assert totals[1].prompt_tokens == 80
    assert totals[1].completion_tokens == 35


def test_attribute_falls_back_to_alias_when_token_unknown() -> None:
    # Local row only knows the alias (e.g. token id drifted); alias still maps it.
    keys = [_key(3, token="stored-token", alias="seko-carol-1")]
    by_token, by_alias = us._key_index(keys)
    rows = [_day(("different-token", "seko-carol-1", _metrics(9, 1, 5, 4)))]
    totals = us.attribute(rows, by_token, by_alias)
    assert totals[3].total_tokens == 9


def test_attribute_ignores_unknown_and_blank_keys() -> None:
    keys = [_key(1, token="tok-1", alias="seko-alice-1")]
    by_token, by_alias = us._key_index(keys)
    rows = [
        _day(("tok-1", "seko-alice-1", _metrics(100, 2, 70, 30))),
        _day(("ghost-token", "seko-ghost", _metrics(999, 9, 900, 99))),  # not ours
    ]
    totals = us.attribute(rows, by_token, by_alias)
    assert totals[1].total_tokens == 100
    assert 999 not in {t.total_tokens for t in totals.values()}


async def test_collect_returns_per_user_summaries() -> None:
    class Fake:
        async def daily_activity(self, *, start_date: str, end_date: str, page_size: int = 1000):
            return [
                _day(
                    ("tok-1", "seko-alice-1", _metrics(100, 2, 70, 30)),
                    ("tok-2", "seko-bob-1", _metrics(50, 1, 35, 15)),
                )
            ]

    users = [_user(1, "alice"), _user(2, "bob")]
    keys = [
        _key(1, token="tok-1", alias="seko-alice-1"),
        _key(2, token="tok-2", alias="seko-bob-1"),
    ]
    summaries = await us.collect(Fake(), users, keys)
    assert summaries[1].total_tokens == 100
    assert summaries[1].username == "alice"
    assert summaries[1].available is True
    assert summaries[2].total_tokens == 50
    # A user with no matching keys still gets a (zeroed, available) summary.
    users.append(_user(3, "carol"))
    summaries = await us.collect(Fake(), users, keys)
    assert summaries[3].total_tokens == 0
    assert summaries[3].available is True


async def test_collect_degrades_gracefully_on_litellm_error() -> None:
    from seko_ai.services.litellm_client import LiteLLMError

    class Failing:
        async def daily_activity(self, *, start_date: str, end_date: str, page_size: int = 1000):
            raise LiteLLMError("boom")

    users = [_user(1, "alice")]
    summaries = await us.collect(Failing(), users, [])
    assert summaries[1].available is False
    assert summaries[1].total_tokens == 0


def test_metrics_endpoint_exposes_series(client: TestClient) -> None:
    body = client.get("/metrics").text
    for name in [
        "seko_workspaces_active",
        "seko_users_total",
        "seko_logins_total",
        "seko_keys_issued_total",
        "seko_backups_total",
    ]:
        assert name in body
