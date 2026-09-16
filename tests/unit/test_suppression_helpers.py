from __future__ import annotations

import pytest

from core.suppression import (
    build_idempotency_key,
    domain_of,
    normalize_domain,
    normalize_email,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Person@Example.com", "person@example.com"),
        ("  person@example.com  ", "person@example.com"),
    ],
)
def test_email_normalization(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com/pricing", "example.com"),
        ("WWW.example.com", "example.com"),
        ("example.com", "example.com"),
    ],
)
def test_domain_normalization(raw, expected):
    assert normalize_domain(raw) == expected


def test_phone_normalization_keeps_plus():
    assert normalize_phone("+1 (555) 010-9999") == "+15550109999"


def test_domain_of_email():
    assert domain_of("Person@Example.com") == "example.com"
    assert domain_of("not-an-email") is None


def test_idempotency_key_is_stable_and_distinct():
    key = build_idempotency_key("lead", 1, "step-1")
    assert key == build_idempotency_key("lead", 1, "step-1")
    assert key != build_idempotency_key("lead", 1, "step-2")


def test_idempotency_key_carries_no_pii():
    key = build_idempotency_key("person@example.com", "subject")
    assert "person@example.com" not in key
    assert len(key) == 64
