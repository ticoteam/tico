"""Growth fills its own numbers (backend/growth_sync.py, task #304): a synced file source, each number's origin, who wins
between typed and synced numbers, the months a run writes, and health. Acme, a home-cleaning company, keeps its
sign-ups per channel in a sheet it exports each week."""

import sqlite3
from datetime import datetime, timedelta, timezone

from backend import growth_sync as GS
from backend.tests.helpers.api import get, headers, post, put  # noqa: F401
from backend.tests.helpers.company import bot

NOW = GS.this_month()


def month(back):
    i = GS._index(NOW) - back
    return GS._month(i)


def channels(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search Ads"})
    post(api, "growth/channels", {"slug": "referrals", "name": "Referrals"})


def connect(api, fields=None, token="ana-test", expected=200, **more):
    body = {
        "truth_for": "new customers",
        "system": "Signups sheet",
        "sync_kind": "file",
        "sync_map": {"fields": fields or {"new_customers": "signups", "revenue_month": "revenue"}},
        **more,
    }
    return post(api, "growth/sources", body, token=token, expected=expected)


def push(api, sid, text, token="ana-test", expected=200):
    return post(api, f"growth/sources/{sid}/rows", {"csv": text}, token=token, expected=expected)


def cohort(api, slug, m):
    rows = get(api, f"growth/cohorts?channel={slug}&month={m}")["cohorts"]
    return rows[0] if rows else None


def test_a_csv_fills_known_channels_lists_the_unknown_one_and_marks_each_number_synced(api):
    """AC-4 and AC-10: 2 known channels fill, the unknown channel's row is listed as not written, and each number names
    its source, its run time and actual."""
    channels(api)
    src = connect(api)
    assert (src["kind"], src["fills"], src["health"]) == ("file", ["new_customers", "revenue_month"], "waiting")
    m = month(1)
    result = push(
        api,
        src["id"],
        f"month,channel,signups,revenue\n{m},search-ads,131,90\n{m},Referrals,12,85.5\n{m},podcast,4,70\n",
    )
    assert result["ok"] and result["written"] == 4 and result["held"] == 0
    assert result["not_written"] == [{"row": 4, "channel": "podcast", "month": m, "reason": "no such channel"}]
    search = cohort(api, "search-ads", m)
    assert (search["new_customers"], search["revenue_month"], search["quality"]) == (131, 90, "actual")
    origin = search["origins"]["new_customers"]
    assert (origin["origin"], origin["quality"], origin["source"], origin["source_id"]) == (
        "synced",
        "actual",
        "Signups sheet",
        src["id"],
    )
    assert origin["at"] and origin["conflict"] is None
    assert cohort(api, "referrals", m)["new_customers"] == 12
    assert get(api, "growth/cohorts")["cohorts"] and cohort(api, "unknown", m) is None
    listed = get(api, "growth/sources")["sources"][0]
    assert listed["health"] == "fresh" and listed["last_good_run"] and listed["last_error"] is None
    assert listed["has_file"] is True and "last_payload" not in listed
    numbers = get(api, "growth/numbers")
    search_series = next(ch for ch in numbers["channels"] if ch["slug"] == "search-ads")["series"][-1]
    assert search_series["origins"]["new_customers"]["source"] == "Signups sheet"


def test_typing_a_synced_number_is_refused_naming_the_source(api):
    """AC-11: a number a live synced source fills cannot be typed, by a person or a bot; once the source is retired it
    can."""
    channels(api)
    src = connect(api)
    m = month(0)
    push(api, src["id"], f"month,channel,signups,revenue\n{m},search-ads,40,\n")
    refused = put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 41}, expected=409)
    assert "Signups sheet" in refused["error"]["detail"]
    growth_bot = bot(api, "ops")
    put(
        api,
        "growth/cohorts",
        {"channel": "search-ads", "month": m, "new_customers": 41},
        token=growth_bot,
        expected=409,
    )
    assert cohort(api, "search-ads", m)["new_customers"] == 40
    # A number it does not fill is still typed, and says who typed it.
    typed = put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "spend": 900, "quality": "estimate"})
    assert typed["origins"]["spend"]["origin"] == "typed" and typed["origins"]["spend"]["by"] == "human:ana"
    assert typed["origins"]["spend"]["quality"] == "estimate"
    post(api, f"growth/sources/{src['id']}/retire", {})
    assert put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 41})["new_customers"] == 41


def test_a_typed_actual_is_held_beside_the_source_value_and_its_owner_chooses(api):
    """AC-12: 120 typed as actual; the source brings 131. 120 stays with a conflict; keeping 120 is logged and the same
    131 is not raised again; a later different value is; using the source's value makes it synced."""
    channels(api)
    m = month(1)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 120, "quality": "actual"})
    ops = bot(api, "ops")
    src = connect(api, fields={"new_customers": "signups"}, owner_ref="ops")
    text = f"month,channel,signups\n{m},search-ads,131\n"
    result = push(api, src["id"], text)
    assert result["ok"] and (result["written"], result["held"]) == (0, 1)
    row = cohort(api, "search-ads", m)
    assert row["new_customers"] == 120
    origin = row["origins"]["new_customers"]
    assert origin["origin"] == "typed" and origin["conflict"]["value"] == 131
    assert origin["conflict"]["source"] == "Signups sheet"
    body = {"channel": "search-ads", "month": m, "field": "new_customers", "keep": "typed"}
    post(api, "growth/cohorts/conflicts", body, token="cara-test", expected=403)
    kept = post(api, "growth/cohorts/conflicts", body, token=ops)
    assert kept == {"cohort": row["id"], "field": "new_customers", "kept": "typed", "value": 120}
    assert cohort(api, "search-ads", m)["origins"]["new_customers"]["conflict"] is None
    assert push(api, src["id"], text)["held"] == 0  # the same 131 again: already declined
    assert push(api, src["id"], f"month,channel,signups\n{m},search-ads,133\n")["held"] == 1
    used = post(api, "growth/cohorts/conflicts", {**body, "keep": "source"})
    assert used["value"] == 133
    row = cohort(api, "search-ads", m)
    assert row["new_customers"] == 133 and row["origins"]["new_customers"]["origin"] == "synced"
    with api.app.state.store.read() as c:
        kinds = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'growth.cohort.conflict%'")]
    assert kinds.count("growth.cohort.conflict.resolve") == 2 and kinds.count("growth.cohort.conflict") == 2


def test_an_old_typed_actual_with_no_origin_is_held_too(api):
    """A number typed before origins were kept counts as typed with its row's quality (spec: old typed data stays)."""
    channels(api)
    m = month(1)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 120, "quality": "actual"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM growth_origins")
    src = connect(api, fields={"new_customers": "signups"})
    assert push(api, src["id"], f"month,channel,signups\n{m},search-ads,131\n")["held"] == 1
    assert cohort(api, "search-ads", m)["new_customers"] == 120


def test_a_typed_estimate_is_replaced_by_the_synced_actual(api):
    """AC-13."""
    channels(api)
    m = month(1)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 100, "quality": "estimate"})
    src = connect(api, fields={"new_customers": "signups"})
    result = push(api, src["id"], f"month,channel,signups\n{m},search-ads,131\n")
    assert (result["written"], result["held"]) == (1, 0)
    row = cohort(api, "search-ads", m)
    assert row["new_customers"] == 131 and row["quality"] == "actual"
    assert row["origins"]["new_customers"]["quality"] == "actual"


def test_the_first_run_fills_twelve_months_back_and_later_runs_only_the_last_three(api):
    """AC-3."""
    channels(api)
    src = connect(api, fields={"new_customers": "signups"})
    lines = ["month,channel,signups"] + [f"{month(b)},search-ads,{100 + b}" for b in range(15)]
    lines.append(f"{month(-1)},search-ads,5")
    first = push(api, src["id"], "\n".join(lines))
    assert first["written"] == 13
    reasons = sorted((r["month"], r["reason"]) for r in first["not_written"])
    assert reasons == [
        (month(14), "before the months a run writes"),
        (month(13), "before the months a run writes"),
        (month(-1), "a future month"),
    ]
    later = ["month,channel,signups"] + [f"{month(b)},search-ads,{200 + b}" for b in range(15)]
    second = push(api, src["id"], "\n".join(later))
    assert second["written"] == 3 and second["not_written_count"] == 12
    assert cohort(api, "search-ads", month(2))["new_customers"] == 202
    assert cohort(api, "search-ads", month(3))["new_customers"] == 103


def test_a_failed_run_changes_nothing_shows_failing_and_tells_the_owner_once_each_way(api):
    """AC-7 and AC-8 for a file: a missing column fails the run, every number stays, the owner gets one notice when it
    starts failing and one when it recovers."""
    channels(api)
    src = connect(api, fields={"new_customers": "signups"}, owner_ref="human:ben")
    m = month(0)
    push(api, src["id"], f"month,channel,signups\n{m},search-ads,40\n")
    bad = push(api, src["id"], f"month,source,signups\n{m},search-ads,99\n")
    assert bad == {**bad, "ok": False, "written": 0, "error": 'column "channel" not found'}
    push(api, src["id"], f"month,channel,signups\n{m},search-ads,abc\n")  # a second failure: no second notice
    listed = get(api, "growth/sources")["sources"][0]
    assert listed["health"] == "failing" and listed["last_error"] == "row 2: new_customers 'abc' is not a number"
    assert listed["health_since"]
    assert cohort(api, "search-ads", m)["new_customers"] == 40
    good = push(api, src["id"], f"month,channel,signups\n{m},search-ads,41\n")
    assert good["ok"] and get(api, "growth/sources")["sources"][0]["health"] == "fresh"
    with api.app.state.store.read() as c:
        notices = [
            r[0]
            for r in c.execute(
                "SELECT body FROM messages WHERE kind='notice' AND recipient_id='human:ben' ORDER BY seq"
            )
        ]
    assert len(notices) == 2 and "stopped syncing" in notices[0] and "syncs again" in notices[1]


def test_a_daily_source_with_no_good_run_in_48_hours_is_stale():
    """AC-9's state: twice the refresh time without a good run."""
    at = datetime(2026, 11, 13, 9, tzinfo=timezone.utc)
    good = (at - timedelta(hours=49)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    row = {"sync_kind": "db_query", "refresh_hours": 24, "last_good_run_at": good, "sync_map": "{}"}
    assert GS.health(row, at)["health"] == "stale"
    assert GS.health({**row, "last_good_run_at": at.isoformat()[:19] + "Z"}, at)["health"] == "fresh"
    assert GS.health({**row, "last_error": "timeout"}, at)["health"] == "failing"
    assert GS.health({"sync_kind": None}, at)["health"] == "not_synced"


def test_only_the_owner_and_admins_connect_remap_or_retire_and_two_sources_never_fill_one_number(api):
    """AC-14 (owner and admins) and the one-source-per-number rule."""
    channels(api)
    ops = bot(api, "ops")
    connect(api, token="cara-test", expected=403)
    connect(api, token=ops, expected=403)
    src = connect(api, token="ben-test", owner_ref="ops")
    r = api.patch(
        f"/api/v2/growth/sources/{src['id']}",
        json={"sync_map": {"fields": {"spend": "cost"}}},
        headers=headers("cara-test"),
    )
    assert r.status_code == 403
    post(api, f"growth/sources/{src['id']}/retire", {}, token=ops, expected=403)
    taken = connect(api, fields={"new_customers": "n"}, system="Warehouse", expected=409)
    assert "Signups sheet" in taken["error"]["detail"]
    # The source's own owner posts its rows, which is how a bot's daily run will reach it; another member may not.
    m = month(0)
    assert push(api, src["id"], f"month,channel,signups,revenue\n{m},search-ads,3,\n", token=ops)["ok"]
    push(api, src["id"], f"month,channel,signups,revenue\n{m},search-ads,3,\n", token="cara-test", expected=403)
    # A source that only names a system does not run.
    plain = post(api, "growth/sources", {"truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp"})
    assert plain["kind"] is None and plain["health"] == "not_synced" and plain["fills"] == []
    push(api, plain["id"], "month,channel\n", expected=409)


def test_sync_runs_the_last_file_again_after_a_remap(api):
    """AC-17's sync for a file source: it runs its last file again (here after the revenue column is mapped)."""
    channels(api)
    src = connect(api, fields={"new_customers": "signups"})
    post(api, f"growth/sources/{src['id']}/sync", {}, expected=409)  # no file yet
    m = month(0)
    push(api, src["id"], f"month,channel,signups,rev\n{m},search-ads,7,88\n")
    r = api.patch(
        f"/api/v2/growth/sources/{src['id']}",
        json={"sync_map": {"fields": {"new_customers": "signups", "revenue_month": "rev"}}},
        headers=headers(),
    )
    assert r.status_code == 200 and r.json()["fills"] == ["new_customers", "revenue_month"]
    again = post(api, f"growth/sources/{src['id']}/sync", {})
    assert again["ok"] and again["written"] == 1
    assert cohort(api, "search-ads", m)["revenue_month"] == 88


def test_a_number_remapped_away_from_its_source_can_be_typed_again(api):
    """AC-11 holds only while the source fills the number: once an owner remaps a field away, typing it is accepted and
    the number becomes typed, while the source keeps the numbers it still fills."""
    channels(api)
    src = connect(api)
    m = month(1)
    push(api, src["id"], f"month,channel,signups,revenue\n{m},search-ads,131,90\n")
    r = api.patch(
        f"/api/v2/growth/sources/{src['id']}",
        json={"sync_map": {"fields": {"revenue_month": "revenue"}}},
        headers=headers(),
    )
    assert r.status_code == 200 and r.json()["fills"] == ["revenue_month"]
    typed = put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "new_customers": 120})
    assert typed["new_customers"] == 120 and typed["origins"]["new_customers"]["origin"] == "typed"
    put(api, "growth/cohorts", {"channel": "search-ads", "month": m, "revenue_month": 95}, expected=409)


def test_the_source_list_columns_leave_out_the_stored_file():
    """The list selects every gtm_sources column but the stored file (up to 1 MB a source), and says whether one is
    kept."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE gtm_sources (id TEXT, system TEXT, last_payload TEXT, retired_at TEXT)")
    c.execute("CREATE TABLE growth_source_secrets (source_id TEXT, signing_secret TEXT)")
    c.execute("INSERT INTO gtm_sources VALUES ('s1', 'Signups sheet', 'month,channel\n', NULL)")
    cols = GS.listed(c)
    assert "last_payload," not in cols + "," and "id" in cols and "retired_at" in cols
    row = c.execute(f"SELECT {cols} FROM gtm_sources").fetchone()
    assert row == ("s1", "Signups sheet", None, 1, 0)
    assert GS.listed(c, "s").startswith("s.id, s.system")
