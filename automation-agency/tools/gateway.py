"""
Gateway service — the things n8n should not be doing.

n8n is excellent at orchestration and poor at anything requiring cryptographic
correctness, tight loops, or code you want to unit-test. Four jobs live here:

  1. WhatsApp webhook ingestion with real HMAC signature verification.
  2. Tenant resolution, so a webhook lands in exactly one client's data.
  3. Lead scoring.
  4. The POPIA consent gate, exposed to n8n over HTTP.

Everything sets `app.tenant_id` transaction-locally before touching data, so a
pooled connection can never carry one client's context into the next request.

Run:  uvicorn gateway:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

log = logging.getLogger("gateway")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

DATABASE_URL = os.environ["DATABASE_URL"]
GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
N8N_BASE = os.getenv("N8N_INTERNAL_URL", "http://n8n:5678")

pool: AsyncConnectionPool


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    pool = AsyncConnectionPool(DATABASE_URL, min_size=2, max_size=12, open=False)
    await pool.open(wait=True)
    yield
    await pool.close()


app = FastAPI(title="Automation Agency Gateway", lifespan=lifespan, version="1.0.0")


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def require_api_key(x_api_key: str = Header(default="")) -> None:
    """Internal auth for calls originating from n8n on the private network."""
    if not hmac.compare_digest(x_api_key, GATEWAY_API_KEY):
        raise HTTPException(status_code=401, detail="bad api key")


@asynccontextmanager
async def tenant_tx(tenant_id: str | None):
    """
    Open a transaction with the tenant GUC set for its lifetime.

    `set_config(..., true)` scopes the setting to the transaction, so RLS
    cannot leak across pooled connections. Passing None deliberately leaves the
    context empty — every policy then evaluates to "see nothing", which is the
    correct default for webhook resolution before the tenant is known.
    """
    async with pool.connection() as conn:  # type: AsyncConnection
        conn.row_factory = dict_row
        async with conn.transaction():
            if tenant_id:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', %s, true)", (tenant_id,)
                )
            yield conn


SA_MOBILE = re.compile(r"^(?:\+?27|0)(\d{9})$")


def normalise_msisdn(raw: str) -> str | None:
    """
    Everything becomes E.164 before it is stored. SA numbers arrive as
    082 123 4567, 0821234567, 27821234567 and +27 82 123 4567 — often all four
    inside one client's CRM export. Silent mismatches here look like "WhatsApp
    is broken" and cost hours.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    m = SA_MOBILE.match(digits)
    if m:
        return f"+27{m.group(1)}"
    if digits.startswith("+") and 8 <= len(digits) - 1 <= 15:
        return digits
    return None


# ---------------------------------------------------------------------------
# WhatsApp webhooks
# ---------------------------------------------------------------------------

@app.get("/webhooks/whatsapp")
async def verify_whatsapp(request: Request) -> Response:
    """Meta's one-time subscription handshake."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == META_VERIFY_TOKEN
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="verification failed")


def verify_signature(body: bytes, header: str) -> bool:
    """
    Meta signs the raw body with the app secret as sha256. Verify against the
    bytes exactly as received — re-serialising the parsed JSON changes key
    order and whitespace and the digest will never match.
    """
    if not header.startswith("sha256=") or not META_APP_SECRET:
        return False
    expected = hmac.new(
        META_APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@app.post("/webhooks/whatsapp")
async def receive_whatsapp(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, Any]:
    raw = await request.body()
    if not verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")

    payload = await request.json()
    handled = 0

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            if not phone_number_id:
                continue

            tenant_id = await resolve_tenant(phone_number_id)
            if tenant_id is None:
                # A webhook for a number we do not manage. Log and drop rather
                # than guess — writing it to the wrong tenant is far worse.
                log.warning("unmapped phone_number_id=%s", phone_number_id)
                continue

            handled += await ingest_messages(tenant_id, value)
            handled += await ingest_statuses(tenant_id, value)

    return {"received": True, "handled": handled}


async def resolve_tenant(phone_number_id: str) -> str | None:
    async with tenant_tx(None) as conn:
        row = await (
            await conn.execute(
                """
                SELECT tenant_id FROM agency.integration
                WHERE provider = 'meta_whatsapp'
                  AND external_ref = %s
                  AND is_active
                """,
                (phone_number_id,),
            )
        ).fetchone()
    return str(row["tenant_id"]) if row else None


async def ingest_messages(tenant_id: str, value: dict[str, Any]) -> int:
    count = 0
    async with tenant_tx(tenant_id) as conn:
        for msg in value.get("messages", []):
            msisdn = normalise_msisdn(msg.get("from", ""))
            if not msisdn:
                continue

            contact = await (
                await conn.execute(
                    """
                    INSERT INTO core.contact (tenant_id, msisdn, source)
                    VALUES (%s, %s, 'whatsapp_inbound')
                    ON CONFLICT (tenant_id, msisdn) WHERE msisdn IS NOT NULL
                    DO UPDATE SET last_activity_at = now()
                    RETURNING id
                    """,
                    (tenant_id, msisdn),
                )
            ).fetchone()

            body = msg.get("text", {}).get("body") or f"[{msg.get('type')}]"
            await conn.execute(
                """
                INSERT INTO core.message
                  (tenant_id, contact_id, channel, direction, body,
                   provider, provider_msg_id, status, sent_at)
                VALUES (%s, %s, 'whatsapp', 'inbound', %s,
                        'meta_cloud', %s, 'received', to_timestamp(%s))
                ON CONFLICT (provider, provider_msg_id)
                  WHERE provider_msg_id IS NOT NULL DO NOTHING
                """,
                (tenant_id, contact["id"], body, msg.get("id"),
                 int(msg.get("timestamp", 0))),
            )

            await conn.execute(
                """
                INSERT INTO core.event (tenant_id, contact_id, kind, payload)
                VALUES (%s, %s, 'whatsapp.inbound', %s)
                """,
                (tenant_id, contact["id"], {"type": msg.get("type"), "body": body}),
            )
            count += 1

    if count:
        await notify_n8n(tenant_id, "whatsapp.inbound", value)
    return count


async def ingest_statuses(tenant_id: str, value: dict[str, Any]) -> int:
    """
    Delivery receipts. These feed the cost and deliverability columns of the
    monthly report — and a sudden spike in `failed` is the earliest warning
    that a template was rejected or a number got rate-limited.
    """
    count = 0
    async with tenant_tx(tenant_id) as conn:
        for st in value.get("statuses", []):
            status = st.get("status")
            column = {
                "delivered": "delivered_at",
                "read": "read_at",
            }.get(status)
            ts = datetime.fromtimestamp(int(st.get("timestamp", 0)), tz=timezone.utc)

            if column:
                await conn.execute(
                    f"""
                    UPDATE core.message
                       SET status = %s, {column} = COALESCE({column}, %s)
                     WHERE provider = 'meta_cloud' AND provider_msg_id = %s
                    """,
                    (status, ts, st.get("id")),
                )
            else:
                await conn.execute(
                    """
                    UPDATE core.message SET status = %s, error = %s
                     WHERE provider = 'meta_cloud' AND provider_msg_id = %s
                    """,
                    (status, {"errors": st.get("errors")}, st.get("id")),
                )
            count += 1
    return count


async def notify_n8n(tenant_id: str, event: str, payload: dict[str, Any]) -> None:
    """Hand off to the orchestration layer; never block the webhook on it."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{N8N_BASE}/webhook/router",
                json={"tenant_id": tenant_id, "event": event, "payload": payload},
                headers={"x-api-key": GATEWAY_API_KEY},
            )
    except httpx.HTTPError as exc:
        # Meta retries on non-2xx, which would duplicate the DB writes we have
        # already committed. Swallow, log, and let the reconciliation job in
        # tools/maintenance.py pick up anything n8n missed.
        log.error("n8n handoff failed for %s: %s", event, exc)


# ---------------------------------------------------------------------------
# Consent gate — n8n calls this before every marketing send
# ---------------------------------------------------------------------------

class ConsentCheck(BaseModel):
    tenant_id: str
    contact_id: str
    channel: Literal["whatsapp", "sms", "email", "voice", "in_person"]
    purpose: str


@app.post("/consent/check", dependencies=[Depends(require_api_key)])
async def consent_check(req: ConsentCheck) -> dict[str, bool]:
    async with tenant_tx(req.tenant_id) as conn:
        row = await (
            await conn.execute(
                "SELECT core.may_contact(%s, %s, %s) AS allowed",
                (req.contact_id, req.channel, req.purpose),
            )
        ).fetchone()
    return {"allowed": bool(row["allowed"])}


class ConsentGrant(BaseModel):
    tenant_id: str
    contact_id: str
    channel: Literal["whatsapp", "sms", "email", "voice", "in_person"]
    purpose: str
    basis: Literal["express_consent", "existing_customer", "legitimate_service"]
    granted: bool = True
    evidence: dict[str, Any] = Field(default_factory=dict)


@app.post("/consent/record", dependencies=[Depends(require_api_key)])
async def consent_record(req: ConsentGrant) -> dict[str, str]:
    """
    Append-only. Withdrawal is a new row with granted=false, never an UPDATE,
    so the ledger reconstructs the permission state on any past date.
    """
    async with tenant_tx(req.tenant_id) as conn:
        row = await (
            await conn.execute(
                """
                INSERT INTO core.consent
                  (tenant_id, contact_id, channel, purpose, basis, granted, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (req.tenant_id, req.contact_id, req.channel, req.purpose,
                 req.basis, req.granted, req.evidence),
            )
        ).fetchone()

        if not req.granted:
            await conn.execute(
                """
                INSERT INTO core.suppression (tenant_id, identifier, channel, reason)
                SELECT %s,
                       CASE WHEN %s = 'email' THEN lower(email::text) ELSE msisdn END,
                       %s, 'opt_out'
                FROM core.contact WHERE id = %s
                ON CONFLICT DO NOTHING
                """,
                (req.tenant_id, req.channel, req.channel, req.contact_id),
            )
    return {"consent_id": str(row["id"])}


# ---------------------------------------------------------------------------
# Lead scoring (real estate)
# ---------------------------------------------------------------------------

class LeadScoreRequest(BaseModel):
    tenant_id: str
    lead_id: str


@app.post("/score/lead", dependencies=[Depends(require_api_key)])
async def score_lead(req: LeadScoreRequest) -> dict[str, Any]:
    """
    Deliberately a transparent weighted rubric rather than a model.

    A principal will challenge why a lead was routed to their best agent, and
    "the model said so" loses that argument. Explainable beats accurate at this
    scale — and with a few thousand leads per client per year there is not
    enough signal to justify anything heavier.
    """
    async with tenant_tx(req.tenant_id) as conn:
        lead = await (
            await conn.execute(
                """
                SELECT l.*, p.asking_price_cents, p.suburb,
                       (SELECT count(*) FROM realestate.lead l2
                         WHERE l2.tenant_id = l.tenant_id
                           AND l2.contact_id = l.contact_id) AS enquiry_count
                FROM realestate.lead l
                LEFT JOIN realestate.property p ON p.id = l.property_id
                WHERE l.id = %s
                """,
                (req.lead_id,),
            )
        ).fetchone()

        if lead is None:
            raise HTTPException(status_code=404, detail="lead not found")

        score, reasons = 0, []

        bond_points = {"pre_approved": 30, "cash": 30, "applied": 18,
                       "not_started": 5, "unknown": 0, "declined": -20}
        pts = bond_points.get(lead["bond_status"] or "unknown", 0)
        score += pts
        reasons.append(f"bond status {lead['bond_status'] or 'unknown'}: {pts:+d}")

        # Repeat enquiries are the strongest free signal in the dataset.
        repeat = min(int(lead["enquiry_count"] or 1) - 1, 3) * 10
        score += repeat
        reasons.append(f"{lead['enquiry_count']} enquiries: {repeat:+d}")

        if lead["budget_max_cents"] and lead["asking_price_cents"]:
            ratio = lead["budget_max_cents"] / lead["asking_price_cents"]
            if ratio >= 1.0:
                score += 20
                reasons.append("budget covers asking price: +20")
            elif ratio >= 0.85:
                score += 10
                reasons.append("budget within 15% of asking: +10")
            else:
                score -= 10
                reasons.append("budget below range: -10")

        source_points = {"show_day": 20, "referral": 20, "website": 12,
                         "property24": 10, "private_property": 10,
                         "walk_in": 15, "facebook": 4}
        pts = source_points.get(lead["source"], 0)
        score += pts
        reasons.append(f"source {lead['source']}: {pts:+d}")

        if lead["intent"] == "seller":
            score += 25
            reasons.append("seller intent: +25")

        score = max(0, min(100, score))
        band = "hot" if score >= 65 else "warm" if score >= 35 else "cool"

        await conn.execute(
            """
            INSERT INTO core.event (tenant_id, contact_id, kind, payload)
            VALUES (%s, %s, 'lead.scored', %s)
            """,
            (req.tenant_id, lead["contact_id"],
             {"lead_id": req.lead_id, "score": score, "band": band,
              "reasons": reasons}),
        )

    return {"lead_id": req.lead_id, "score": score, "band": band, "reasons": reasons}


@app.get("/health")
async def health() -> dict[str, str]:
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok"}
