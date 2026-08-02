"""
Pure functions shared by the gateway. Standard library only, deliberately.

Both of these sit on a boundary where a subtle mistake is expensive and silent:
one decides whether a number reaches a client's customer, the other decides
whether an unauthenticated request gets to write to a client's database.

They live apart from gateway.py so they can be tested without FastAPI, psycopg
or a running database — which means the test suite runs on a bare CI runner
with nothing but pytest installed, rather than skipping and leaving the build
with no tests collected at all.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Optional

# South African mobile numbers, in the four shapes they actually arrive in.
SA_MOBILE = re.compile(r"^(?:\+?27|0)(\d{9})$")


def normalise_msisdn(raw: str) -> Optional[str]:
    """
    Normalise a phone number to E.164.

    SA numbers arrive as 082 123 4567, 0821234567, 27821234567 and
    +27 82 123 4567 — often all four inside one client's CRM export. A number
    stored in two forms is two contacts, two consent records and a duplicate
    message; it presents to the client as "WhatsApp is broken" and costs hours
    to find. Everything is normalised on the way in.

    Returns None when the input cannot be interpreted, which callers must treat
    as "do not contact" rather than falling back to the raw string.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d+]", "", raw)

    m = SA_MOBILE.match(digits)
    if m:
        return "+27" + m.group(1)

    # Already-international numbers pass through if they are plausibly E.164.
    if digits.startswith("+") and 8 <= len(digits) - 1 <= 15:
        return digits

    return None


def verify_signature(body: bytes, header: str, app_secret: str) -> bool:
    """
    Verify Meta's X-Hub-Signature-256 header against the raw request body.

    Two things matter here. The digest is computed over the bytes exactly as
    received — re-serialising parsed JSON changes key order and whitespace and
    the signature will never match. And the comparison is constant-time, so the
    endpoint does not leak the expected digest one byte at a time.

    An empty app secret returns False rather than accepting everything: a
    missing configuration value must fail closed.
    """
    if not app_secret or not header or not header.startswith("sha256="):
        return False

    provided = header[len("sha256="):]
    if not provided:
        return False

    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
