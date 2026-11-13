"""Growth synced sources part 5 (task #310): a file source's signed address, and bot cost. Acme, a home-cleaning
company, has a script that posts its weekly sign-ups sheet, and a bot that works its Referrals channel."""

import hashlib
import hmac

from backend import growth_sync as GS
from backend import hubdb as H
from backend import usage_facts
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.company import bot

NOW = GS.this_month()
LAST = GS._month(GS._index(NOW) - 1)
CSV = f"month,channel,signups\n{LAST},search-ads,131\n{LAST},referrals,12\n"


def setup(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"})
    return post(
        api,
        "growth/sources",
        {
            "truth_for": "new customers",
            "system": "Signups sheet",
            "sync_kind": "file",
            "sync_map": {"fields": {"new_customers": "signups"}},
        },
        token="ana-test",
    )


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send(api, sid, body, signature, expected=200):
    r = api.post(f"/api/v2/growth/in/{sid}", content=body, headers={"X-Signature": signature})
    assert r.status_code == expected, r.text
    return r.json()


def cohorts(api, slug):
    return get(api, f"growth/cohorts?channel={slug}&month={LAST}")["cohorts"]


def test_a_script_posting_the_csv_to_the_signed_address_fills_like_an_upload_and_a_bad_signature_writes_nothing(api):
    """AC-5."""
    src = setup(api)
    made = post(api, f"growth/sources/{src['id']}/address", {}, token="ana-test")
    assert made["address"].endswith(f"/api/v2/growth/in/{src['id']}") and made["signing_secret"]
    body = CSV.encode()
    send(api, src["id"], body, "sha256=" + "0" * 64, expected=401)
    send(api, src["id"], body, "", expected=401)
    send(api, src["id"], body, sign("another-secret", body), expected=401)
    assert cohorts(api, "search-ads") == []
    assert get(api, "growth/sources")["sources"][0]["last_good_run"] is None
    result = send(api, src["id"], body, sign(made["signing_secret"], body))
    assert result["ok"] and result["written"] == 2
    assert cohorts(api, "search-ads")[0]["new_customers"] == 131
    listed = get(api, "growth/sources")["sources"][0]
    assert listed["has_address"] is True and "signing_secret" not in listed and listed["health"] == "fresh"


def test_a_new_address_secret_stops_the_old_one_and_a_source_without_one_refuses(api):
    src = setup(api)
    body = CSV.encode()
    send(api, src["id"], body, sign("x", body), expected=404)
    old = post(api, f"growth/sources/{src['id']}/address", {}, token="ana-test")["signing_secret"]
    new = post(api, f"growth/sources/{src['id']}/address", {}, token="ana-test")["signing_secret"]
    send(api, src["id"], body, sign(old, body), expected=401)
    assert send(api, src["id"], body, sign(new, body))["ok"]


def test_bot_cost_is_a_bots_run_cost_on_its_one_channel_and_unknown_when_it_owns_several(api):
    """AC-16."""
    bot(api, "ops")
    bot(api, "cpo")
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals", "owner_ref": "bot:ops"})
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search", "owner_ref": "bot:cpo"})
    post(api, "growth/channels", {"slug": "podcast", "name": "Podcast", "owner_ref": "bot:cpo"})
    src = post(
        api,
        "growth/sources",
        {"truth_for": "bot cost", "system": "Bot run cost", "sync_kind": "bot_cost", "sync_map": {"fields": {}}},
        token="ana-test",
    )
    assert src["fills"] == ["bot_cost"]
    at = f"{LAST}-10T12:00:00Z"
    with api.app.state.store.transaction() as c:
        for who, usd in (("ops", 1.25), ("ops", 2.0), ("cpo", 4.0)):
            usage_facts.record_run(
                c, {"id": H.new_id(), "bot": who, "task_id": None, "started_at": at}, {"est_cost_usd": usd}, at
            )
    result = post(api, f"growth/sources/{src['id']}/sync", {}, token="ana-test")
    assert result["ok"] and result["written"] == 2
    assert cohorts(api, "referrals")[0]["bot_cost"] == 3.25
    assert cohorts(api, "search-ads") == [] and cohorts(api, "podcast") == []
    assert cohorts(api, "unknown")[0]["bot_cost"] == 4.0
    assert "cpo owns 2 channels" in result["notes"][0]


def test_the_first_bot_cost_run_reaches_back_a_year_and_later_runs_only_three_months(api):
    bot(api, "ops")
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals", "owner_ref": "bot:ops"})
    src = post(
        api,
        "growth/sources",
        {"truth_for": "bot cost", "system": "Bot run cost", "sync_kind": "bot_cost", "sync_map": {"fields": {}}},
        token="ana-test",
    )
    old, edge = GS._month(GS._index(NOW) - 4), GS._month(GS._index(NOW) - 2)
    with api.app.state.store.transaction() as c:
        for m in (old, edge):
            at = f"{m}-10T12:00:00Z"
            usage_facts.record_run(
                c, {"id": H.new_id(), "bot": "ops", "task_id": None, "started_at": at}, {"est_cost_usd": 1.5}, at
            )
    first = post(api, f"growth/sources/{src['id']}/sync", {}, token="ana-test")
    assert first["written"] == 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE growth_cohorts SET bot_cost=NULL")
        c.execute("DELETE FROM growth_origins")
    later = post(api, f"growth/sources/{src['id']}/sync", {}, token="ana-test")
    assert later["written"] == 1
    rows = {r["month"]: r["bot_cost"] for r in get(api, "growth/cohorts?channel=referrals")["cohorts"]}
    assert rows.get(edge) == 1.5 and rows.get(old) is None


def test_a_bot_cost_run_clears_the_amounts_it_wrote_that_no_longer_apply(api):
    """AC-16: after ownership changes, or a month's cost falls to zero, a month's cost sits on one channel or on
    Unknown, never both."""
    bot(api, "ops")
    bot(api, "cpo")
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals", "owner_ref": "bot:ops"})
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search", "owner_ref": "bot:cpo"})
    post(api, "growth/channels", {"slug": "events", "name": "Events", "owner_ref": "bot:cpo"})
    src = post(
        api,
        "growth/sources",
        {"truth_for": "bot cost", "system": "Bot run cost", "sync_kind": "bot_cost", "sync_map": {"fields": {}}},
        token="ana-test",
    )
    at = f"{LAST}-10T12:00:00Z"
    with api.app.state.store.transaction() as c:
        for who, usd in (("ops", 4.0), ("cpo", 2.0)):
            usage_facts.record_run(
                c, {"id": H.new_id(), "bot": who, "task_id": None, "started_at": at}, {"est_cost_usd": usd}, at
            )
    sync = lambda: post(api, f"growth/sources/{src['id']}/sync", {}, token="ana-test")  # noqa: E731
    assert sync()["written"] == 2
    assert cohorts(api, "referrals")[0]["bot_cost"] == 4.0 and cohorts(api, "unknown")[0]["bot_cost"] == 2.0

    # ops gets a second channel: its $4.00 moves to Unknown and leaves Referrals.
    post(api, "growth/channels", {"slug": "podcast", "name": "Podcast", "owner_ref": "bot:ops"})
    sync()
    assert cohorts(api, "referrals")[0]["bot_cost"] == 0
    assert cohorts(api, "unknown")[0]["bot_cost"] == 6.0

    # Events is retired: cpo owns one channel again, so its $2.00 moves from Unknown to Search.
    post(api, "growth/channels/events/retire", {})
    sync()
    assert cohorts(api, "search-ads")[0]["bot_cost"] == 2.0 and cohorts(api, "unknown")[0]["bot_cost"] == 4.0

    # Podcast is retired: ops owns one channel again, so its cost goes back to Referrals and Unknown is cleared.
    post(api, "growth/channels/podcast/retire", {})
    sync()
    assert cohorts(api, "referrals")[0]["bot_cost"] == 4.0
    assert cohorts(api, "unknown")[0]["bot_cost"] == 0
    rows = {r["channel"]: r["bot_cost"] for r in get(api, f"growth/cohorts?month={LAST}")["cohorts"]}
    assert sum(v or 0 for v in rows.values()) == 6.0

    # Every cost falls to zero (run cost is append-only, so the run is given no rows): nothing it wrote is left.
    with api.app.state.store.transaction() as c:
        source = c.execute("SELECT * FROM gtm_sources WHERE id=?", (src["id"],)).fetchone()
        result = GS.run(c, H.KEEPER, dict(source), rows=[])
    assert result["ok"] and result["written"] == 2
    rows = {r["channel"]: r["bot_cost"] for r in get(api, f"growth/cohorts?month={LAST}")["cohorts"]}
    assert sum(v or 0 for v in rows.values()) == 0
