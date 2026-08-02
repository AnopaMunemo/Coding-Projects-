"""
End-to-end smoke test for the gateway + PostgreSQL layer.

Run this after any change to gateway.py or db/*.sql, and after every deploy.
It exercises the paths that are expensive to get wrong: webhook signature
verification, tenant routing, idempotency, and the POPIA consent gate.

    # terminal 1
    export DATABASE_URL=postgresql://agency_app@127.0.0.1:5439/agency
    export GATEWAY_API_KEY=... META_APP_SECRET=... META_VERIFY_TOKEN=...
    uvicorn gateway:app --port 8000

    # terminal 2
    python smoke_test.py

Exit code 0 means every assertion held. Anything else and you do not deploy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx
import psycopg
from psycopg.rows import dict_row

GATEWAY = os.getenv("GATEWAY_URL", "http://127.0.0.1:8000")
ADMIN_URL = os.environ["ADMIN_DATABASE_URL"]
API_KEY = os.environ["GATEWAY_API_KEY"]
APP_SECRET = os.environ["META_APP_SECRET"]
VERIFY_TOKEN = os.environ["META_VERIFY_TOKEN"]

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def wa_payload(phone_number_id: str, msg_id: str, frm: str, text: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": phone_number_id},
                    "messages": [{
                        "from": frm,
                        "id": msg_id,
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def db():
    return psycopg.connect(ADMIN_URL, row_factory=dict_row, autocommit=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def provision() -> tuple[str, str]:
    """Two tenants and one mapped WhatsApp number. Returns (tenant_id, pnid)."""
    pnid = "PNID_" + uuid.uuid4().hex[:10]
    with db() as conn:
        row = conn.execute(
            """
            INSERT INTO agency.tenant (slug, legal_name, trading_name, niche, status,
                   plan_tier, monthly_retainer_cents, operator_agreement_signed_on)
            VALUES (%s,'Smoke Test Realty (Pty) Ltd','Smoke Realty','real_estate',
                    'active','growth',3300000, current_date)
            RETURNING id
            """,
            (f"smoke-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        tenant_id = str(row["id"])
        conn.execute(
            """
            INSERT INTO agency.integration (tenant_id, provider, external_ref, is_active)
            VALUES (%s, 'meta_whatsapp', %s, true)
            """,
            (tenant_id, pnid),
        )
    return tenant_id, pnid


def count_messages(tenant_id: str, provider_msg_id: str) -> int:
    with db() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM core.message WHERE tenant_id = %s AND provider_msg_id = %s",
            (tenant_id, provider_msg_id),
        ).fetchone()["n"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health(c: httpx.Client) -> None:
    print("\nhealth")
    r = c.get(f"{GATEWAY}/health")
    check("health returns 200", r.status_code == 200, r.text)
    check("health reports ok", r.json().get("status") == "ok")


def test_verify_handshake(c: httpx.Client) -> None:
    print("\nMeta subscription handshake")
    r = c.get(f"{GATEWAY}/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "12345"})
    check("correct verify token echoes the challenge",
          r.status_code == 200 and r.text == "12345", r.text)

    r = c.get(f"{GATEWAY}/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong",
        "hub.challenge": "12345"})
    check("wrong verify token is rejected", r.status_code == 403, str(r.status_code))


def test_signature(c: httpx.Client, tenant_id: str, pnid: str) -> None:
    print("\nwebhook signature verification")
    msg_id = "wamid." + uuid.uuid4().hex
    body = json.dumps(wa_payload(pnid, msg_id, "27821234567", "Hi, is the Fourways house still available?")).encode()

    r = c.post(f"{GATEWAY}/webhooks/whatsapp", content=body,
               headers={"Content-Type": "application/json",
                        "X-Hub-Signature-256": sign(body)})
    check("valid signature accepted", r.status_code == 200, r.text)
    check("message persisted", count_messages(tenant_id, msg_id) == 1)

    bad_id = "wamid." + uuid.uuid4().hex
    bad_body = json.dumps(wa_payload(pnid, bad_id, "27829999999", "forged")).encode()
    r = c.post(f"{GATEWAY}/webhooks/whatsapp", content=bad_body,
               headers={"Content-Type": "application/json",
                        "X-Hub-Signature-256": "sha256=" + "0" * 64})
    check("forged signature rejected with 401", r.status_code == 401, str(r.status_code))
    check("forged payload wrote nothing", count_messages(tenant_id, bad_id) == 0)

    r = c.post(f"{GATEWAY}/webhooks/whatsapp", content=bad_body,
               headers={"Content-Type": "application/json"})
    check("missing signature rejected", r.status_code == 401, str(r.status_code))


def test_idempotency(c: httpx.Client, tenant_id: str, pnid: str) -> None:
    print("\nidempotency (Meta retries webhooks)")
    msg_id = "wamid." + uuid.uuid4().hex
    body = json.dumps(wa_payload(pnid, msg_id, "27825550123", "Same message twice")).encode()
    hdr = {"Content-Type": "application/json", "X-Hub-Signature-256": sign(body)}

    c.post(f"{GATEWAY}/webhooks/whatsapp", content=body, headers=hdr)
    c.post(f"{GATEWAY}/webhooks/whatsapp", content=body, headers=hdr)
    check("duplicate delivery stored once", count_messages(tenant_id, msg_id) == 1,
          f"found {count_messages(tenant_id, msg_id)}")


def test_unmapped_number(c: httpx.Client) -> None:
    print("\nunmapped phone_number_id")
    msg_id = "wamid." + uuid.uuid4().hex
    body = json.dumps(wa_payload("PNID_NOT_OURS", msg_id, "27821112222", "stray")).encode()
    r = c.post(f"{GATEWAY}/webhooks/whatsapp", content=body,
               headers={"Content-Type": "application/json",
                        "X-Hub-Signature-256": sign(body)})
    check("accepted without error", r.status_code == 200, r.text)
    check("nothing handled", r.json().get("handled") == 0, r.text)
    with db() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM core.message WHERE provider_msg_id = %s",
            (msg_id,)).fetchone()["n"]
    check("stray message not written to ANY tenant", n == 0, f"found {n}")


def test_api_key(c: httpx.Client, tenant_id: str) -> None:
    print("\ninternal API auth")
    payload = {"tenant_id": tenant_id, "contact_id": str(uuid.uuid4()),
               "channel": "whatsapp", "purpose": "direct_marketing"}
    r = c.post(f"{GATEWAY}/consent/check", json=payload)
    check("no API key rejected", r.status_code == 401, str(r.status_code))
    r = c.post(f"{GATEWAY}/consent/check", json=payload,
               headers={"x-api-key": "wrong-key"})
    check("wrong API key rejected", r.status_code == 401, str(r.status_code))


def test_consent_gate(c: httpx.Client, tenant_id: str) -> None:
    print("\nPOPIA s69 consent gate")
    with db() as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        contact = conn.execute(
            """
            INSERT INTO core.contact (tenant_id, first_name, last_name, msisdn, source)
            VALUES (%s,'Naledi','Sithole','+27824447777','show_day') RETURNING id
            """, (tenant_id,)).fetchone()
    cid = str(contact["id"])
    hdr = {"x-api-key": API_KEY}
    base = {"tenant_id": tenant_id, "contact_id": cid,
            "channel": "whatsapp", "purpose": "direct_marketing"}

    r = c.post(f"{GATEWAY}/consent/check", json=base, headers=hdr)
    check("fails closed with no consent record", r.json()["allowed"] is False, r.text)

    c.post(f"{GATEWAY}/consent/record", headers=hdr, json={
        **base, "basis": "express_consent", "granted": True,
        "evidence": {"source": "show_day_register", "wording_version": "v2"}})
    r = c.post(f"{GATEWAY}/consent/check", json=base, headers=hdr)
    check("opens after express consent", r.json()["allowed"] is True, r.text)

    c.post(f"{GATEWAY}/consent/record", headers=hdr, json={
        **base, "basis": "express_consent", "granted": False})
    r = c.post(f"{GATEWAY}/consent/check", json=base, headers=hdr)
    check("closes on withdrawal", r.json()["allowed"] is False, r.text)

    c.post(f"{GATEWAY}/consent/record", headers=hdr, json={
        **base, "basis": "express_consent", "granted": True})
    r = c.post(f"{GATEWAY}/consent/check", json=base, headers=hdr)
    check("stays closed -- suppression outranks a later re-grant",
          r.json()["allowed"] is False, r.text)

    with db() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM core.consent WHERE contact_id = %s", (cid,)
        ).fetchone()["n"]
    check("consent ledger is append-only (3 rows)", n == 3, f"found {n}")


def test_lead_scoring(c: httpx.Client, tenant_id: str) -> None:
    print("\nlead scoring")
    with db() as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        prop = conn.execute(
            """
            INSERT INTO realestate.property (tenant_id, reference, suburb, city, province,
                   asking_price_cents, agent_ref)
            VALUES (%s,'SMOKE-1','Bryanston','Johannesburg','Gauteng',175000000,'n.pillay')
            RETURNING id
            """, (tenant_id,)).fetchone()
        hot_contact = conn.execute(
            """
            INSERT INTO core.contact (tenant_id, first_name, msisdn, source)
            VALUES (%s,'Kagiso','+27823338888','show_day') RETURNING id
            """, (tenant_id,)).fetchone()
        hot = conn.execute(
            """
            INSERT INTO realestate.lead (tenant_id, contact_id, property_id, intent, source,
                   budget_max_cents, bond_status)
            VALUES (%s,%s,%s,'buyer','show_day',180000000,'pre_approved') RETURNING id
            """, (tenant_id, hot_contact["id"], prop["id"])).fetchone()
        cold_contact = conn.execute(
            """
            INSERT INTO core.contact (tenant_id, first_name, msisdn, source)
            VALUES (%s,'Anon','+27821119999','facebook') RETURNING id
            """, (tenant_id,)).fetchone()
        cold = conn.execute(
            """
            INSERT INTO realestate.lead (tenant_id, contact_id, property_id, intent, source,
                   budget_max_cents, bond_status)
            VALUES (%s,%s,%s,'buyer','facebook',60000000,'declined') RETURNING id
            """, (tenant_id, cold_contact["id"], prop["id"])).fetchone()

    hdr = {"x-api-key": API_KEY}
    h = c.post(f"{GATEWAY}/score/lead", headers=hdr,
               json={"tenant_id": tenant_id, "lead_id": str(hot["id"])}).json()
    cl = c.post(f"{GATEWAY}/score/lead", headers=hdr,
                json={"tenant_id": tenant_id, "lead_id": str(cold["id"])}).json()

    check("pre-approved show-day lead scores hot",
          h["band"] == "hot", f"{h['score']} / {h['band']}")
    check("declined facebook lead scores cool",
          cl["band"] == "cool", f"{cl['score']} / {cl['band']}")
    check("hot outranks cold", h["score"] > cl["score"], f"{h['score']} vs {cl['score']}")
    check("score is explainable", len(h["reasons"]) >= 4, str(h["reasons"]))
    check("score bounded 0-100", 0 <= h["score"] <= 100 and 0 <= cl["score"] <= 100)

    r = c.post(f"{GATEWAY}/score/lead", headers=hdr,
               json={"tenant_id": tenant_id, "lead_id": str(uuid.uuid4())})
    check("unknown lead returns 404", r.status_code == 404, str(r.status_code))


def test_cross_tenant(c: httpx.Client) -> None:
    print("\ncross-tenant isolation via the gateway")
    a_tenant, a_pnid = provision()
    b_tenant, b_pnid = provision()

    msg_id = "wamid." + uuid.uuid4().hex
    body = json.dumps(wa_payload(a_pnid, msg_id, "27827776666", "tenant A only")).encode()
    c.post(f"{GATEWAY}/webhooks/whatsapp", content=body,
           headers={"Content-Type": "application/json",
                    "X-Hub-Signature-256": sign(body)})

    check("landed in tenant A", count_messages(a_tenant, msg_id) == 1)
    check("did NOT land in tenant B", count_messages(b_tenant, msg_id) == 0)


def main() -> int:
    with httpx.Client(timeout=30.0) as c:
        tenant_id, pnid = provision()
        print(f"provisioned tenant {tenant_id} with phone_number_id {pnid}")
        test_health(c)
        test_verify_handshake(c)
        test_signature(c, tenant_id, pnid)
        test_idempotency(c, tenant_id, pnid)
        test_unmapped_number(c)
        test_api_key(c, tenant_id)
        test_consent_gate(c, tenant_id)
        test_lead_scoring(c, tenant_id)
        test_cross_tenant(c)

    print(f"\n{'=' * 58}")
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    FAILED: {f}")
    print("=" * 58)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
