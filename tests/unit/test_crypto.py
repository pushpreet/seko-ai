"""Tests for envelope encryption (crypto service)."""

from __future__ import annotations

import base64

import pytest

from seko_ai.services import crypto

MASTER = base64.b64encode(b"0" * 32).decode()


def test_load_master_key_valid() -> None:
    assert crypto.load_master_key(MASTER) == b"0" * 32


@pytest.mark.parametrize("bad", ["", "not-base64!!", base64.b64encode(b"short").decode()])
def test_load_master_key_invalid(bad: str) -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.load_master_key(bad)


def test_wrap_unwrap_roundtrip() -> None:
    master = crypto.load_master_key(MASTER)
    dek = crypto.generate_dek()
    wrapped = crypto.wrap_dek(master, dek)
    assert wrapped != base64.b64encode(dek).decode()  # not stored in the clear
    assert crypto.unwrap_dek(master, wrapped) == dek


def test_wrap_is_nondeterministic() -> None:
    master = crypto.load_master_key(MASTER)
    dek = crypto.generate_dek()
    assert crypto.wrap_dek(master, dek) != crypto.wrap_dek(master, dek)


def test_unwrap_with_wrong_master_fails() -> None:
    master = crypto.load_master_key(MASTER)
    other = crypto.load_master_key(base64.b64encode(b"1" * 32).decode())
    wrapped = crypto.wrap_dek(master, crypto.generate_dek())
    with pytest.raises(crypto.CryptoError, match="authentication"):
        crypto.unwrap_dek(other, wrapped)


@pytest.mark.parametrize("bad", ["not-base64!!", base64.b64encode(b"tiny").decode()])
def test_unwrap_malformed(bad: str) -> None:
    master = crypto.load_master_key(MASTER)
    with pytest.raises(crypto.CryptoError):
        crypto.unwrap_dek(master, bad)


def test_dek_passphrase_stable_ascii() -> None:
    dek = crypto.generate_dek()
    p = crypto.dek_passphrase(dek)
    assert crypto.dek_passphrase(dek) == p
    assert p.isascii()
