"""The Growth page's fixtures (ui/tests/fixtures/growth-read-*.json) are the real read (backend/growth_core.py) of three
company shapes, so the page's browser test (ui/tests/growth-page.cts) draws what the server sends:

- TIDY-like: two revenue segments, paid search per segment with budgets, a no-spend referral channel and Unknown.
- Tico-like: no segments, no paid spend: bot cost and hours only.
- HotMic-like: one retention segment, promotions paid in cash and credits.

A change to the read's shape fails here; TICO_WRITE_FIXTURES=1 writes the files again for the page test to follow."""

import itertools
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import growth_computed as GX
from backend import hubdb
from backend.tests.helpers.api import get, post
from backend.tests.helpers.growth_computed import TODAY

FIXTURES = Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures"
AS_OF = (TODAY - timedelta(days=1)).isoformat()
MONTHS = [f"{2025 + (10 + k) // 12}-{(10 + k) % 12 + 1:02d}" for k in range(12)]  # 2025-11 .. 2026-10
DETAIL = "detail=series,retention"


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    """The read's day and every write's time are fixed, so the fixtures name a push time that does not move."""
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)
    start = datetime(TODAY.year, TODAY.month, TODAY.day, 9, tzinfo=timezone.utc)
    ticks = itertools.count()  # one microsecond apart: writes still sort and never collide, as hubdb.now promises
    monkeypatch.setattr(
        hubdb, "now", lambda: (start + timedelta(microseconds=next(ticks))).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    )


def _old_enough(month: str, days: int) -> bool:
    y, m = map(int, month.split("-"))
    end = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
    return end + timedelta(days=days) <= TODAY - timedelta(days=1)


def _kept(month: str, entered: int, rate: float, days: int) -> tuple[int, int]:
    """(kept, eligible) for a window: none eligible until the whole cohort is that old."""
    return (round(entered * rate), entered) if _old_enough(month, days) else (0, 0)


def _push(api, what: str, source: str, header: list[str], rows: list[list[object]]) -> None:
    csv = "\n".join([",".join(header), *(",".join("" if v is None else str(v) for v in r) for r in rows)]) + "\n"
    post(api, f"growth/{what}", {"source": source, "csv": csv})


def _kpi(api, name: str, unit: str, readings: list[tuple[float, str]]) -> str:
    kpi = post(api, "kpis", {"name": name, "unit": unit})["kpi"]["id"]
    for value, end in readings:
        post(api, f"kpis/{kpi}/readings", {"value": value, "period_end": end, "quality": "measured"})
    return kpi


def _bot(api, bot_id: str, slug: str, name: str) -> None:
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (bot_id, slug, name))


def _shape(api, scopes: list[str]) -> dict[str, object]:
    reads = {scope: get(api, f"growth/read?scope={scope}&{DETAIL}") for scope in scopes}
    ids: dict[str, str] = {}
    for out in reads.values():  # KPI ids are random: the fixture names them in order
        for m in out["metrics"]:
            m["id"] = ids.setdefault(m["id"], f"kpi-{len(ids) + 1}")
    return {"segments": get(api, "growth/segments")["segments"], "reads": reads}


def _matches(name: str, shape: dict[str, object]) -> None:
    path = FIXTURES / f"growth-read-{name}.json"
    text = json.dumps(shape, indent=1, sort_keys=True) + "\n"
    if os.environ.get("TICO_WRITE_FIXTURES"):
        path.write_text(text)
    assert path.read_text() == text, f"{path.name} is not the read: TICO_WRITE_FIXTURES=1 writes it again"


def test_tidy_two_revenue_segments_with_paid_channels(api):
    _bot(api, "b-ads", "ads-owner", "Ads Owner")
    post(api, "growth/segments", {"slug": "consumer", "name": "Consumer", "min_cohort": 10, "customer_rule": "booked"})
    post(api, "growth/segments", {"slug": "rental", "name": "Rental", "min_cohort": 20, "customer_rule": "booked"})
    bookings = _kpi(api, "Rental bookings", "bookings", [(40, "2026-09-30"), (46, "2026-10-31")])
    for slug, name, segment, owner, budget, kpi in (
        ("search-consumer", "Search · Consumer", "consumer", "ads-owner", 3000, None),
        ("search-rental", "Search · Rental", "rental", "ads-owner", 1500, bookings),
        ("referrals", "Referrals", "consumer", "human:ana", None, None),
        ("outbound", "Outbound", "rental", None, None, None),
    ):
        post(
            api,
            "growth/channels",
            {"slug": slug, "name": name, "segment": segment, "owner_ref": owner, "budget_month": budget}
            | ({"input_kpi_id": kpi} if kpi else {}),
        )
    rows, costs = [], []
    for i, month in enumerate(MONTHS):
        for channel, segment, base, kept, net in (
            ("search-consumer", "consumer", 30, 0.72, 140),
            ("referrals", "consumer", 14, 0.85, 160),
            ("", "consumer", 6, 0.7, 120),
            ("search-rental", "rental", 6, 0.9, 900),
            ("outbound", "rental", 2, 0.95, 1100),
            ("referrals", "", 2, 0.8, 100),
        ):
            entered = base + (i * base) // 8
            d30, d90 = _kept(month, entered, kept, 30), _kept(month, entered, kept - 0.08, 90)
            d180 = _kept(month, entered, kept - 0.15, 180)
            n90 = entered if _old_enough(month, 90) else 0
            n180 = entered if _old_enough(month, 180) else 0
            nets = [net * n90 or None, n90 or None, net * 2 * n180 or None, n180 or None]
            rows.append(["customer", month, segment, channel, AS_OF, entered, *d30, *d90, *d180, *nets])
        costs += [
            [month, "search-consumer", "money", 2400 + 90 * i],
            [month, "search-rental", "money", 1200 + 40 * i],
            [month, "outbound", "bot", 80],
            [month, "outbound", "hours", 300],
        ]
    header = ["anchor", "period", "segment", "channel", "as_of", "entered", "kept_d30", "kept_d30_eligible"]
    header += ["kept_d90", "kept_d90_eligible", "kept_d180", "kept_d180_eligible"]
    header += ["net_d90_sum", "net_d90_eligible", "net_d180_sum", "net_d180_eligible"]
    _push(api, "rows", "warehouse", header, rows)
    signups = [
        ["signup", m, seg, ch, AS_OF, 90 + 3 * i, 40 + i, 90 + 3 * i, 18 + i, 90 + 3 * i]
        for i, m in enumerate(MONTHS)
        for seg, ch in (("consumer", "search-consumer"), ("rental", "search-rental"))
    ]
    _push(
        api,
        "rows",
        "app-signups",
        ["anchor", "period", "segment", "channel", "as_of", "entered", "activated", "activated_eligible"]
        + ["paid", "paid_eligible"],
        signups,
    )
    _push(api, "costs", "finance", ["period", "channel", "kind", "amount"], costs)
    shape = _shape(api, ["all", "segment:rental"])
    top = shape["reads"]["all"]["headline"]
    assert top["cac"]["status"] == "ok" and top["ltv"]["status"] == "ok"
    rental = shape["reads"]["segment:rental"]["headline"]
    assert rental["kept"]["status"] == "too_few"
    _matches("tidy", shape)


def test_tico_no_segments_and_no_spend(api):
    weekly = _kpi(api, "Weekly active teams", "teams", [(31, "2026-09-30"), (36, "2026-10-31")])
    for slug, name, kpi in (
        ("content", "Content", weekly),
        ("outbound", "Outbound", None),
        ("community", "Community", None),
    ):
        post(api, "growth/channels", {"slug": slug, "name": name} | ({"input_kpi_id": kpi} if kpi else {}))
    rows, costs = [], []
    for i, month in enumerate(MONTHS):
        for channel, base in (("content", 20), ("outbound", 6), ("community", 4), ("", 3)):
            signups = base + i * base // 6
            paid = signups // 4
            rows.append(["signup", month, channel, AS_OF, signups, paid, signups, None, None])
            rows.append(["customer", month, channel, AS_OF, paid, None, None, *_kept(month, paid, 0.8, 90)])
        costs += [[month, "outbound", "bot", 120], [month, "outbound", "hours", 600], [month, "content", "bot", 60]]
    header = [
        "anchor",
        "period",
        "channel",
        "as_of",
        "entered",
        "paid",
        "paid_eligible",
        "kept_d90",
        "kept_d90_eligible",
    ]
    _push(api, "rows", "hq", header, rows)
    _push(api, "costs", "effort", ["period", "channel", "kind", "amount"], costs)
    shape = _shape(api, ["all"])
    assert shape["segments"] == [] and shape["reads"]["all"]["headline"]["cost"]["status"] == "no_spend"
    _matches("tico", shape)


def test_hotmic_a_retention_segment_with_promo_costs(api):
    post(
        api,
        "growth/segments",
        {"slug": "viewers", "name": "Viewers", "value_kind": "retention", "min_cohort": 10, "customer_rule": "watched"},
    )
    retained = _kpi(api, "Day-7 viewers", "%", [(41, "2026-09-30"), (44, "2026-10-31")])
    post(api, "growth/channels", {"slug": "promotions", "name": "Promotions", "segment": "viewers"})
    post(api, "growth/channels", {"slug": "organic", "name": "Organic", "input_kpi_id": retained})
    rows, costs = [], []
    for i, month in enumerate(MONTHS):
        for channel, base, kept in (("promotions", 60, 0.38), ("organic", 90, 0.46)):
            signups = base * 3 + i * 12
            active = signups // 3
            rows.append(["signup", month, "viewers", channel, AS_OF, signups, active, signups, None, None])
            rows.append(
                ["customer", month, "viewers", channel, AS_OF, active, None, None, *_kept(month, active, kept, 30)]
            )
        costs += [
            [month, "promotions", "promo_cash", "acquisition", 900 + 30 * i],
            [month, "promotions", "promo_credits", "acquisition", 400],
            [month, "promotions", "promo_cash", "retention", 250],
        ]
    header = ["anchor", "period", "segment", "channel", "as_of", "entered", "activated", "activated_eligible"]
    _push(api, "rows", "hotmic-db", [*header, "kept_d30", "kept_d30_eligible"], rows)
    _push(api, "costs", "promotions", ["period", "channel", "kind", "purpose", "amount"], costs)
    shape = _shape(api, ["all"])
    assert "value" in shape["reads"]["all"]["headline"]
    _matches("hotmic", shape)
