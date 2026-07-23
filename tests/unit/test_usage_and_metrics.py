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
    totals = us.attribute(rows, by_token, by_alias).users
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
    totals = us.attribute(rows, by_token, by_alias).users
    assert totals[1].total_tokens == 115
    assert totals[1].total_requests == 4
    assert totals[1].prompt_tokens == 80
    assert totals[1].completion_tokens == 35


def test_attribute_falls_back_to_alias_when_token_unknown() -> None:
    # Local row only knows the alias (e.g. token id drifted); alias still maps it.
    keys = [_key(3, token="stored-token", alias="seko-carol-1")]
    by_token, by_alias = us._key_index(keys)
    rows = [_day(("different-token", "seko-carol-1", _metrics(9, 1, 5, 4)))]
    totals = us.attribute(rows, by_token, by_alias).users
    assert totals[3].total_tokens == 9


def test_attribute_keeps_unknown_out_of_users_bucket() -> None:
    keys = [_key(1, token="tok-1", alias="seko-alice-1")]
    by_token, by_alias = us._key_index(keys)
    rows = [
        _day(("tok-1", "seko-alice-1", _metrics(100, 2, 70, 30))),
        _day(("ghost-token", "seko-ghost", _metrics(999, 9, 900, 99))),  # not ours
    ]
    attribution = us.attribute(rows, by_token, by_alias)
    totals = attribution.users
    assert totals[1].total_tokens == 100
    assert 999 not in {t.total_tokens for t in totals.values()}
    assert attribution.unknown["seko-ghost"].total_tokens == 999


def test_attribute_buckets_service_keys_by_full_alias() -> None:
    rows = [
        _day(
            ("svc-token-1", "hermes-pk", _metrics(100, 2, 70, 30)),
            ("svc-token-2", "hermes-personal", _metrics(50, 1, 35, 15)),
        ),
        _day(("svc-token-3", "hermes-pk", _metrics(25, 1, 20, 5))),
    ]
    attribution = us.attribute(rows, {}, {}, service_prefixes=["hermes"])

    assert attribution.users == {}
    assert attribution.services["hermes-pk"].total_tokens == 125
    assert attribution.services["hermes-personal"].total_tokens == 50
    assert attribution.unknown == {}


def test_attribute_buckets_non_service_alias_as_unknown() -> None:
    rows = [_day(("ghost-token", "random-key", _metrics(42, 3, 30, 12)))]
    attribution = us.attribute(rows, {}, {}, service_prefixes=["hermes"])

    assert attribution.services == {}
    assert attribution.unknown["random-key"].total_tokens == 42
    assert attribution.unknown_labels["random-key"] == "random-key"


def test_attribute_unknown_empty_when_all_keys_are_user_or_service() -> None:
    keys = [_key(1, token="tok-1", alias="seko-alice-1")]
    by_token, by_alias = us._key_index(keys)
    rows = [
        _day(
            ("tok-1", "seko-alice-1", _metrics(100, 2, 70, 30)),
            ("svc-token", "hermes-pk", _metrics(50, 1, 35, 15)),
        )
    ]

    attribution = us.attribute(rows, by_token, by_alias, service_prefixes=["hermes"])

    assert attribution.users[1].total_tokens == 100
    assert attribution.services["hermes-pk"].total_tokens == 50
    assert attribution.unknown == {}


def test_attribute_matches_service_prefix() -> None:
    rows = [_day(("svc-token", "hermes-anything", _metrics(42, 3, 30, 12)))]
    attribution = us.attribute(rows, {}, {}, service_prefixes=["hermes"])

    assert attribution.services["hermes-anything"].total_tokens == 42
    assert attribution.unknown == {}


def test_attribute_masks_token_label_when_alias_blank_or_missing() -> None:
    rows = [
        {
            "date": "2026-07-08",
            "breakdown": {
                "api_keys": {
                    "legacy-token-abcd": {
                        "metrics": _metrics(10, 1, 6, 4),
                        "metadata": {"key_alias": ""},
                    },
                    "legacy-token-wxyz": {
                        "metrics": _metrics(20, 2, 12, 8),
                        "metadata": {},
                    },
                }
            },
        }
    ]

    attribution = us.attribute(rows, {}, {}, service_prefixes=["hermes"])

    assert attribution.unknown["legacy-token-abcd"].total_tokens == 10
    assert attribution.unknown_labels["legacy-token-abcd"] == "…abcd"
    assert attribution.unknown["legacy-token-wxyz"].total_tokens == 20
    assert attribution.unknown_labels["legacy-token-wxyz"] == "…wxyz"


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
    report = await us.collect(Fake(), users, keys, service_prefixes=["hermes"])
    summaries = report.users
    assert summaries[1].total_tokens == 100
    assert summaries[1].username == "alice"
    assert summaries[1].available is True
    assert summaries[2].total_tokens == 50
    # A user with no matching keys still gets a (zeroed, available) summary.
    users.append(_user(3, "carol"))
    summaries = (await us.collect(Fake(), users, keys, service_prefixes=["hermes"])).users
    assert summaries[3].total_tokens == 0
    assert summaries[3].available is True


async def test_collect_degrades_gracefully_on_litellm_error() -> None:
    from seko_ai.services.litellm_client import LiteLLMError

    class Failing:
        async def daily_activity(self, *, start_date: str, end_date: str, page_size: int = 1000):
            raise LiteLLMError("boom")

    users = [_user(1, "alice")]
    report = await us.collect(Failing(), users, [], service_prefixes=["hermes"])
    assert report.users[1].available is False
    assert report.users[1].total_tokens == 0
    assert report.services == []
    assert report.unknown == []


def test_order_user_summaries_by_total_tokens_desc() -> None:
    # alice has more total tokens but fewer generated; alice should sort first.
    alice = us.UsageSummary(
        username="alice",
        total_tokens=1000,
        total_requests=1,
        prompt_tokens=990,
        completion_tokens=10,
    )
    bob = us.UsageSummary(
        username="bob",
        total_tokens=100,
        total_requests=1,
        prompt_tokens=10,
        completion_tokens=90,
    )
    # Pass in ascending-total order to prove the function reorders by total tokens desc.
    ordered = us.order_user_summaries([bob, alice])
    assert [s.username for s in ordered] == ["alice", "bob"]


def test_order_user_summaries_tiebreaks_on_completion_then_name() -> None:
    # Equal totals -> higher completion first; equal completion -> alphabetical username.
    lo_gen = us.UsageSummary(
        username="zoe",
        total_tokens=100,
        total_requests=1,
        prompt_tokens=90,
        completion_tokens=10,
    )
    hi_gen = us.UsageSummary(
        username="amy",
        total_tokens=100,
        total_requests=1,
        prompt_tokens=40,
        completion_tokens=60,
    )
    tie = us.UsageSummary(
        username="bob",
        total_tokens=100,
        total_requests=1,
        prompt_tokens=40,
        completion_tokens=60,
    )
    ordered = us.order_user_summaries([lo_gen, tie, hi_gen])
    # hi_gen and tie share total+completion -> username order (amy before bob); zoe last.
    assert [s.username for s in ordered] == ["amy", "bob", "zoe"]


async def test_collect_orders_service_rows_by_total_tokens_desc() -> None:
    class Fake:
        async def daily_activity(self, *, start_date: str, end_date: str, page_size: int = 1000):
            # hermes-pk has more total tokens; hermes-personal has more *generated* tokens.
            return [
                _day(
                    ("t1", "hermes-pk", _metrics(1000, 1, 990, 10)),
                    ("t2", "hermes-personal", _metrics(100, 1, 10, 90)),
                )
            ]

    report = await us.collect(Fake(), [], [], service_prefixes=["hermes"])
    # Sorted by total tokens descending: hermes-pk (1000) before hermes-personal (100).
    assert [row.label for row in report.services] == ["hermes-pk", "hermes-personal"]


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
