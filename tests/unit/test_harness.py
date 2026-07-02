"""Tests for the agent-harness helpers."""

from __future__ import annotations

import pytest

from seko_ai.harness import (
    DEFAULT_HARNESS,
    HARNESS_BINARIES,
    HARNESS_CHOICES,
    harness_binary,
    normalize_harness,
)


def test_default_harness_is_known() -> None:
    assert DEFAULT_HARNESS in HARNESS_BINARIES


def test_choices_cover_all_binaries() -> None:
    assert {value for value, _ in HARNESS_CHOICES} == set(HARNESS_BINARIES)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("pi", "pi"), ("oh-my-pi", "oh-my-pi"), ("  pi  ", "pi")],
)
def test_normalize_known(value: str, expected: str) -> None:
    assert normalize_harness(value) == expected


@pytest.mark.parametrize("value", ["", None, "bogus", "PI", "omp"])
def test_normalize_unknown_falls_back(value: str | None) -> None:
    assert normalize_harness(value) == DEFAULT_HARNESS


@pytest.mark.parametrize(
    ("harness", "binary"),
    [("pi", "pi"), ("oh-my-pi", "omp"), ("nope", "pi"), (None, "pi")],
)
def test_harness_binary(harness: str | None, binary: str) -> None:
    assert harness_binary(harness) == binary
