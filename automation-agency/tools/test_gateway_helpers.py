"""
Unit tests for the gateway's boundary functions.

These import `verification` only — standard library, no FastAPI, no psycopg, no
database — so they run on a bare CI runner and never skip. That matters more
than it looks: pytest exits 5 when it collects zero tests, and a `pytest` step
under `bash -e` treats that as a failed build. A suite that skips itself into
nothing is worse than no suite at all.

The full integration suite lives in smoke_check.py and needs a live PostgreSQL
instance plus a running gateway.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from verification import normalise_msisdn, verify_signature

SECRET = "test-app-secret"


# ---------------------------------------------------------------------------
# Phone number normalisation
#
# Every SA client's CRM export contains the same number written several ways.
# A silent mismatch here presents to the client as "WhatsApp is broken".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "0821234567",
        "082 123 4567",
        "082-123-4567",
        "(082) 123 4567",
        "+27 82 123 4567",
        "+27821234567",
        "27821234567",
        "27 82 123 4567",
    ],
)
def test_every_sa_spelling_collapses_to_one_form(raw):
    assert normalise_msisdn(raw) == "+27821234567"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not a number", "12345", "08212345", "abc082def", "+", "0"],
)
def test_unparseable_input_returns_none(raw):
    """None means "do not contact" — callers must never fall back to the raw string."""
    assert normalise_msisdn(raw) is None


def test_international_numbers_pass_through():
    assert normalise_msisdn("+442071234567") == "+442071234567"
    assert normalise_msisdn("+12125551234") == "+12125551234"


def test_normalisation_is_idempotent():
    once = normalise_msisdn("082 123 4567")
    assert normalise_msisdn(once) == once


def test_number_that_is_too_long_is_rejected():
    assert normalise_msisdn("+1234567890123456789") is None


# ---------------------------------------------------------------------------
# Webhook signature verification
#
# The boundary between "a message from Meta" and "anything on the internet".
# ---------------------------------------------------------------------------

def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted():
    body = b'{"entry":[]}'
    assert verify_signature(body, _sign(body), SECRET) is True


def test_signature_over_a_different_body_rejected():
    assert verify_signature(b'{"entry":[]}', _sign(b'{"entry":[1]}'), SECRET) is False


def test_signature_from_the_wrong_secret_rejected():
    body = b'{"entry":[]}'
    assert verify_signature(body, _sign(body, "attacker-secret"), SECRET) is False


@pytest.mark.parametrize(
    "header",
    ["", "abc", "sha1=abc", "sha256=", "sha256=" + "0" * 64, "SHA256=" + "0" * 64],
)
def test_malformed_or_forged_headers_rejected(header):
    assert verify_signature(b'{"entry":[]}', header, SECRET) is False


def test_missing_app_secret_fails_closed():
    """
    An unset secret must reject everything. Failing open here would accept any
    unsigned request the moment a deploy forgot one environment variable.
    """
    body = b'{"entry":[]}'
    assert verify_signature(body, _sign(body), "") is False


def test_whitespace_change_invalidates_the_signature():
    """
    Meta signs the raw bytes. Re-serialising parsed JSON changes whitespace and
    key order and the digest stops matching — which is exactly why the handler
    verifies the body as received rather than the parsed object.
    """
    assert verify_signature(b'{"a": 1, "b": 2}', _sign(b'{"a":1,"b":2}'), SECRET) is False


def test_empty_body_still_verifies_correctly():
    assert verify_signature(b"", _sign(b""), SECRET) is True
