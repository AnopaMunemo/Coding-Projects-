"""
Prospect audit — turn a list of businesses into a ranked pitch queue.

The premise: the gaps you sell against are visible from outside the business.
A med spa with no online booking, no WhatsApp link and 40 Google reviews whose
most recent is eleven months old is telling you, in public, that nobody is
running their calendar. You do not need to guess, and you certainly should not
be cold-calling in name order.

Two scores, deliberately separate:

  pain_score  how broken their systems are      (why they need you)
  fit_score   whether they can carry R16 500+/m (whether it is worth the call)

Priority is 60% pain, 40% fit. Sorting on pain alone marches you straight into
the single-room studio with no website and no budget.

Usage
-----
  python prospect_audit.py seed --niche med_spa --csv leads.csv
  python prospect_audit.py audit --niche med_spa --limit 50
  python prospect_audit.py queue --niche med_spa --top 25 > monday_calls.csv

`seed` expects columns: business_name, website, suburb, city, province,
public_phone (optional), public_email (optional).

Requires DATABASE_URL. Google Places enrichment is optional and switched on by
setting GOOGLE_PLACES_API_KEY; without it the review signals are simply absent
and the pain score is computed from the website alone.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from psycopg import AsyncConnection
from psycopg.rows import dict_row
# psycopg 3 does not implicitly adapt dict -> jsonb; every jsonb parameter
# needs an explicit wrapper or the whole transaction aborts.
from psycopg.types.json import Jsonb

DATABASE_URL = os.environ["DATABASE_URL"]
PLACES_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Detection vocabulary.
#
# Keep this list current — it is the actual intellectual property of the tool.
# A booking vendor you fail to recognise produces a false "no online booking",
# which produces a cold call that opens with a wrong assumption, which is the
# fastest way to lose a room.
# ---------------------------------------------------------------------------
BOOKING_VENDORS = (
    "fresha", "timely", "phorest", "zenoti", "bookem", "calendly", "acuityscheduling",
    "setmore", "simplybook", "appointedd", "square.site", "mindbodyonline",
    "gettimely", "vagaro", "janeapp", "goodx", "healthbridge", "meddbase",
    "onlinebooking", "book-now", "/booking",
)

CRM_TAGS = (
    "hubspot", "activecampaign", "mailchimp", "klaviyo", "gohighlevel",
    "zoho", "salesforce", "pipedrive", "sendinblue", "brevo", "everlytic",
    "intercom", "drift", "tawk.to", "freshworks", "manychat",
)

PROPERTY_SOFTWARE = (
    "entegral", "propdata", "prop-data", "flexmls", "base.entegral",
    "propctrl", "eazi", "reapit", "propertybase",
)

CHAT_WIDGETS = ("tawk.to", "intercom", "crisp.chat", "livechat", "zendesk", "hubspot")
ANALYTICS = ("googletagmanager", "google-analytics", "gtag(", "fbq(", "facebook.net")
WHATSAPP = ("wa.me", "api.whatsapp.com", "whatsapp://")


@dataclass
class Audit:
    url: str
    ok: bool = False
    status: int | None = None
    load_ms: int | None = None
    bytes: int = 0
    signals: dict[str, bool] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)


async def fetch(client: httpx.AsyncClient, url: str) -> tuple[str, Audit]:
    audit = Audit(url=url)
    started = datetime.now(timezone.utc)
    try:
        r = await client.get(url, follow_redirects=True)
        audit.status = r.status_code
        audit.ok = r.status_code < 400
        audit.bytes = len(r.content)
        audit.load_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return (r.text if audit.ok else ""), audit
    except (httpx.HTTPError, UnicodeDecodeError) as exc:
        audit.detail["error"] = f"{type(exc).__name__}: {exc}"
        return "", audit


async def audit_site(client: httpx.AsyncClient, website: str, niche: str) -> Audit:
    """
    Fetch the homepage plus the contact page, since booking links and WhatsApp
    buttons hide on /contact far more often than they sit on the homepage.
    """
    if not website.startswith("http"):
        website = "https://" + website

    html, audit = await fetch(client, website)
    if not audit.ok:
        # An unreachable site is itself a finding, and a strong one.
        audit.signals["site_unreachable"] = True
        return audit

    soup = BeautifulSoup(html, "html.parser")
    corpus = html.lower()

    # Pull in the contact page too.
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(k in href for k in ("contact", "book", "appointment", "kontak")):
            extra, _ = await fetch(client, urljoin(website, a["href"]))
            corpus += extra.lower()
            break

    s = audit.signals
    s["no_online_booking"] = not any(v in corpus for v in BOOKING_VENDORS)
    s["no_whatsapp"] = not any(v in corpus for v in WHATSAPP)
    s["no_crm_tag"] = not any(v in corpus for v in CRM_TAGS)
    s["no_chat_widget"] = not any(v in corpus for v in CHAT_WIDGETS)
    s["no_analytics"] = not any(v in corpus for v in ANALYTICS)
    s["no_lead_form"] = not soup.find("form")
    s["mailto_only"] = bool(re.search(r"mailto:", corpus)) and not soup.find("form")
    s["not_mobile_ready"] = not soup.find("meta", attrs={"name": "viewport"})
    s["slow_site"] = bool(audit.load_ms and audit.load_ms > 3000)
    s["heavy_page"] = audit.bytes > 4_000_000

    # Stale copyright: a footer still claiming 2023 usually means nobody has
    # touched the site — or the marketing — in a long while.
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(20\d{2})", corpus)]
    if years:
        audit.detail["copyright_year"] = max(years)
        s["stale_site"] = max(years) < date.today().year - 1

    if niche == "real_estate":
        s["no_property_software"] = not any(v in corpus for v in PROPERTY_SOFTWARE)
        s["no_valuation_capture"] = not any(
            k in corpus for k in ("valuation", "what is my home worth", "free market analysis")
        )
        # Listing count is a size proxy; crude but reliable enough to rank on.
        audit.detail["listing_links"] = len(
            [a for a in soup.find_all("a", href=True)
             if re.search(r"/(property|listing|for-sale|to-rent)/", a["href"], re.I)]
        )

    if niche == "med_spa":
        s["no_price_list"] = not any(
            k in corpus for k in ("price", "pricing", "tariff", "from r")
        )
        s["no_consult_cta"] = not any(
            k in corpus for k in ("book a consult", "consultation", "free assessment")
        )
        # Device names imply financed capital equipment, which implies a real
        # utilisation problem and a real budget.
        devices = [d for d in ("coolsculpt", "hydrafacial", "morpheus", "ultherapy",
                               "candela", "cynosure", "lumenis", "venus", "emsculpt",
                               "profhilo", "dermapen", "co2", "picosure")
                   if d in corpus]
        audit.detail["devices"] = devices
        s["has_capital_equipment"] = bool(devices)

    return audit


async def enrich_places(client: httpx.AsyncClient, name: str, city: str) -> dict[str, Any]:
    """
    Google Places: review count, rating, and — the one that matters — the date
    of the most recent review. A clinic whose newest review is nine months old
    is not asking anyone for reviews, which means nobody is running the
    post-visit follow-up either.
    """
    if not PLACES_KEY:
        return {}
    try:
        r = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            json={"textQuery": f"{name} {city} South Africa"},
            headers={
                "X-Goog-Api-Key": PLACES_KEY,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.rating,"
                    "places.userRatingCount,places.reviews"
                ),
            },
            timeout=15.0,
        )
        r.raise_for_status()
        places = r.json().get("places", [])
        if not places:
            return {}
        p = places[0]
        reviews = p.get("reviews", [])
        latest = None
        if reviews:
            stamps = [rv.get("publishTime") for rv in reviews if rv.get("publishTime")]
            if stamps:
                latest = max(stamps)[:10]
        return {
            "google_rating": p.get("rating"),
            "google_review_count": p.get("userRatingCount"),
            "last_review_at": latest,
        }
    except httpx.HTTPError:
        return {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

PAIN_WEIGHTS = {
    # Weighted by how directly the gap maps to money the owner is losing, not
    # by how easy it is to spot.
    "no_online_booking": 18,
    "no_whatsapp": 16,
    "no_crm_tag": 12,
    "no_lead_form": 10,
    "site_unreachable": 10,
    "no_chat_widget": 5,
    "no_analytics": 8,
    "stale_site": 8,
    "slow_site": 6,
    "not_mobile_ready": 6,
    "mailto_only": 5,
    "no_property_software": 10,
    "no_valuation_capture": 8,
    "no_price_list": 4,
    "no_consult_cta": 6,
    "reviews_stale": 12,
}


def score_pain(signals: dict[str, bool], places: dict[str, Any]) -> int:
    total = sum(w for k, w in PAIN_WEIGHTS.items() if signals.get(k))

    last = places.get("last_review_at")
    if last:
        try:
            if date.fromisoformat(last) < date.today() - timedelta(days=120):
                total += PAIN_WEIGHTS["reviews_stale"]
        except ValueError:
            pass

    return min(100, total)


def score_fit(niche: str, signals: dict[str, bool], detail: dict[str, Any],
              places: dict[str, Any]) -> int:
    """
    Affordability. The retainer bands assume a business already turning over
    roughly R400 000/month or more; anything smaller is a referral, not a
    prospect.
    """
    score = 20  # baseline for existing at all

    reviews = places.get("google_review_count") or 0
    score += min(30, reviews // 5)          # 150+ reviews maxes this out

    rating = places.get("google_rating") or 0
    if rating >= 4.3:
        score += 10                          # good service, bad systems: ideal
    elif rating and rating < 3.5:
        score -= 15                          # the problem is not automation

    if niche == "real_estate":
        listings = detail.get("listing_links", 0)
        score += min(35, listings)           # ~35 live listings == a real agency
    else:
        if signals.get("has_capital_equipment"):
            score += 30                      # financed device == budget + urgency
        score += min(15, len(detail.get("devices", [])) * 5)

    if signals.get("site_unreachable"):
        score -= 20

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_seed(args: argparse.Namespace) -> None:
    with open(args.csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as conn:
        inserted = 0
        for row in rows:
            if not row.get("business_name"):
                continue
            res = await conn.execute(
                """
                INSERT INTO agency.prospect
                  (niche, business_name, website, suburb, city, province,
                   public_phone, public_email)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (args.niche, row["business_name"].strip(), row.get("website"),
                 row.get("suburb"), row.get("city"), row.get("province"),
                 row.get("public_phone"), row.get("public_email") or None),
            )
            inserted += 1 if await res.fetchone() else 0
        await conn.commit()
    print(f"seeded {inserted} new prospects ({len(rows) - inserted} already known)")


async def cmd_audit(args: argparse.Namespace) -> None:
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, business_name, website, city
                FROM agency.prospect
                WHERE niche = %s
                  AND status IN ('new','audited')
                  AND (audited_at IS NULL OR audited_at < now() - interval '90 days')
                ORDER BY created_at
                LIMIT %s
                """,
                (args.niche, args.limit),
            )
        ).fetchall()

        if not rows:
            print("nothing to audit")
            return

        # Politeness matters: you are about to appear in these businesses'
        # analytics. Low concurrency, real user agent, no hammering.
        sem = asyncio.Semaphore(args.concurrency)

        async with httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": UA}
        ) as client:

            async def run(row: dict[str, Any]) -> None:
                async with sem:
                    if not row["website"]:
                        audit = Audit(url="", signals={"site_unreachable": True})
                    else:
                        audit = await audit_site(client, row["website"], args.niche)
                    places = await enrich_places(
                        client, row["business_name"], row["city"] or ""
                    )
                    pain = score_pain(audit.signals, places)
                    fit = score_fit(args.niche, audit.signals, audit.detail, places)

                    await conn.execute(
                        """
                        UPDATE agency.prospect
                           SET signals = %s, audit_raw = %s,
                               pain_score = %s, fit_score = %s,
                               google_rating = COALESCE(%s, google_rating),
                               google_review_count = COALESCE(%s, google_review_count),
                               last_review_at = COALESCE(%s::date, last_review_at),
                               listing_count = COALESCE(%s, listing_count),
                               audited_at = now(),
                               status = CASE WHEN status = 'new' THEN 'audited'
                                             ELSE status END
                         WHERE id = %s
                        """,
                        (Jsonb(audit.signals), Jsonb(audit.detail), pain, fit,
                         places.get("google_rating"), places.get("google_review_count"),
                         places.get("last_review_at"),
                         audit.detail.get("listing_links"), row["id"]),
                    )
                    print(f"  {row['business_name'][:40]:<42} pain={pain:>3} fit={fit:>3}")
                    await asyncio.sleep(args.delay)

            await asyncio.gather(*(run(r) for r in rows))
        await conn.commit()
    print(f"audited {len(rows)} prospects")


async def cmd_queue(args: argparse.Namespace) -> None:
    """Emit the call list. Highest priority first, never-approached only."""
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = await (
            await conn.execute(
                """
                SELECT business_name, suburb, city, public_phone, public_email,
                       website, pain_score, fit_score, priority,
                       google_review_count, google_rating,
                       (SELECT string_agg(k, ', ')
                          FROM jsonb_each_text(signals) AS s(k, v)
                         WHERE v = 'true') AS gaps
                FROM agency.prospect
                WHERE niche = %s
                  AND status = 'audited'
                  AND approach_count = 0
                  AND public_phone IS NOT NULL
                ORDER BY priority DESC
                LIMIT %s
                """,
                (args.niche, args.top),
            )
        ).fetchall()

    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()) if rows else ["empty"])
    w.writeheader()
    for r in rows:
        w.writerow(r)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    seed = sub.add_parser("seed", help="import a CSV of businesses")
    seed.add_argument("--niche", required=True, choices=["real_estate", "med_spa"])
    seed.add_argument("--csv", required=True)
    seed.set_defaults(fn=cmd_seed)

    aud = sub.add_parser("audit", help="crawl and score")
    aud.add_argument("--niche", required=True, choices=["real_estate", "med_spa"])
    aud.add_argument("--limit", type=int, default=50)
    aud.add_argument("--concurrency", type=int, default=4)
    aud.add_argument("--delay", type=float, default=1.0)
    aud.set_defaults(fn=cmd_audit)

    q = sub.add_parser("queue", help="emit the call list as CSV")
    q.add_argument("--niche", required=True, choices=["real_estate", "med_spa"])
    q.add_argument("--top", type=int, default=25)
    q.set_defaults(fn=cmd_queue)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
