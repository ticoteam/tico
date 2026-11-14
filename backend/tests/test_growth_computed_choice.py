"""Growth computed retention part 4 (spec page growth-computed-cohorts v2, AC-14 second half): when a typed actual
differs from the figure computed from customer facts, the channel's owner is asked once per channel, month and figure,
chooses through #304's resolve flow (keep typed or keep computed), and the choice sticks across later loads. A computed
figure's origin names the customers source(s) it came from.

Synthetic customers only: a search-ads September 2026 cohort of 40, 30 still paying on day 30 (75%), read 2026-11-13."""

import pytest

from backend import growth_computed as GX
from backend.tests.helpers.api import get, headers, post, put
from backend.tests.helpers.company import bot
from backend.tests.helpers.growth_computed import TODAY, as_csv, september

SEPT = "2026-09"
CHOOSE = {"channel": "search-ads", "month": SEPT, "field": "kept_d30"}


@pytest.fixture(autouse=True)
def on_the_13th(monkeypatch):
    monkeypatch.setattr(GX, "today", lambda conf: TODAY)


def load(api, rows):
    return post(api, "growth/customers", {"csv": as_csv(rows)})


def owned_channel(api):
    """search-ads, owned by the ops bot; returns the bot's token."""
    ops = bot(api, "ops")
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads", "owner_ref": "ops"})
    return ops


def sept(api):
    return next(r for r in get(api, f"growth/cohorts?month={SEPT}")["cohorts"] if r["channel"] == "search-ads")


def asks(api, to="bot:ops"):
    with api.app.state.store.read() as c:
        return [
            r[0]
            for r in c.execute("SELECT body FROM messages WHERE kind='notice' AND recipient_id=? ORDER BY seq", (to,))
        ]


def card(api, template, metric=None):
    view = get(api, f"places/growth?month={SEPT}")
    return next(
        p["card"]["data"]
        for p in view["pins"]
        if p["card"]["template"] == template and (metric is None or p["card"]["settings"].get("metric") == metric)
    )


def test_a_computed_figure_names_the_customers_source_it_came_from(api):
    owned_channel(api)
    load(api, september())
    row = sept(api)
    origin = row["origins"]["kept_d30"]
    assert origin["origin"] == "computed" and origin["computed_from"] == 40
    assert origin["sources"] == [{"source_id": None, "source": "Upload in Growth", "at": origin["sources"][0]["at"]}]
    assert origin["sources"][0]["at"]
    # A typed figure keeps #304's typed origin, and still says typed.
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "spend": 900})
    row = sept(api)
    assert row["origins"]["spend"]["origin"] == "typed" and row["computed_or_typed"]["spend"] == "typed"
    assert row["origins"]["new_customers"]["origin"] == "computed"


def test_a_differing_typed_actual_asks_the_channels_owner_once(api):  # AC-14
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    row = sept(api)
    assert row["kept_d30"] == 75.0 and row["typed"] == {"kept_d30": 70}
    assert row["origins"]["kept_d30"]["differs"] == {"typed": 70, "chosen": None}
    said = asks(api)
    assert len(said) == 1 and "Search ads" in said[0] and "Sep 2026" in said[0] and "70" in said[0] and "75" in said[0]
    load(api, september())  # the next load: asked already, no second notice
    assert len(asks(api)) == 1
    # The Number card lists it with Keep and Use for the owner, as a source's conflict is.
    conflicts = card(api, "growth_number", "kept_d30")["origin"]["conflicts"]
    assert conflicts == [
        {
            "channel": "search-ads",
            "channel_name": "Search ads",
            "month": SEPT,
            "field": "kept_d30",
            "typed": 70,
            "value": 75.0,
            "source": "40 customers",
            "against": "computed",
            "may": True,  # ana, the owner
        }
    ]


def test_only_the_channels_owner_or_an_admin_chooses(api):
    ops = owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    refused = post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "computed"}, token="cara-test", expected=403)
    assert "Search ads" in refused["error"]["detail"]
    chosen = post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "typed", "against": "computed"}, token=ops)
    assert chosen == {**chosen, "field": "kept_d30", "kept": "typed", "value": 70}


def test_keeping_the_typed_number_sticks_across_later_loads(api):
    ops = owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "typed", "against": "computed"}, token=ops)
    load(api, september(kept=32))  # a later load changes the computed figure to 80%
    row = sept(api)
    assert row["kept_d30"] == 70 and row["computed_or_typed"]["kept_d30"] == "typed" and row["typed"] == {}
    assert row["origins"]["kept_d30"]["differs"] == {"typed": 70, "chosen": "typed", "computed": 80.0}
    assert card(api, "growth_number", "kept_d30")["origin"]["conflicts"] == []
    assert len(asks(api)) == 1
    # The other computed figures of the month are still computed.
    assert row["computed_or_typed"]["new_customers"] == "computed"


def test_keeping_the_computed_figure_sticks_and_drops_the_typed_one_from_beside_it(api):
    ops = owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "computed"}, token=ops)
    load(api, september(kept=32))
    row = sept(api)
    assert row["kept_d30"] == 80.0 and row["computed_or_typed"]["kept_d30"] == "computed" and row["typed"] == {}
    assert row["origins"]["kept_d30"]["differs"] == {"typed": 70, "chosen": "computed"}
    assert len(asks(api)) == 1
    # With every customer gone, the typed number shows again (AC-16b): the choice was about this typed number.
    post(api, "growth/customers/delete", {})
    row = sept(api)
    assert row["kept_d30"] == 70 and row["computed_or_typed"]["kept_d30"] == "typed"


def test_a_new_typed_number_is_a_new_question(api):
    ops = owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "computed"}, token=ops)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 72})
    row = sept(api)
    assert row["typed"] == {"kept_d30": 72} and row["origins"]["kept_d30"]["differs"]["chosen"] is None
    load(api, september())
    said = asks(api)
    assert len(said) == 2 and "72" in said[1]


def test_nothing_to_choose_when_the_typed_number_matches(api):
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 75})
    load(api, september())
    assert sept(api)["typed"] == {} and asks(api) == []
    refused = post(api, "growth/cohorts/conflicts", {**CHOOSE, "keep": "computed"}, expected=404)
    assert refused["error"]["code"] == "not_found"


def test_the_retention_card_shows_both_values_where_they_differ(api):
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    computed = card(api, "growth_retention")["computed"]
    search = next(r for r in computed["cohorts"] if r["value"] == "search-ads")
    assert search["kept_d30"]["value"] == 75.0
    assert search["typed"] == {"kept_d30": {"typed": 70, "chosen": None, "may": True}}
    # The API's own read for another cut has no typed numbers: typed figures are per channel and month.
    by_segment = get(api, f"growth/computed?by=segment&month={SEPT}")
    assert all("typed" not in r for r in by_segment["cohorts"])


def test_a_synced_customers_source_is_named_with_its_id(api):
    owned_channel(api)
    src = post(
        api,
        "growth/sources",
        {
            "truth_for": "customers",
            "system": "Customers sheet",
            "sync_kind": "file",
            "sync_map": {"shape": "customers"},
        },
    )
    post(api, f"growth/sources/{src['id']}/rows", {"csv": as_csv(september())})
    sources = sept(api)["origins"]["kept_d30"]["sources"]
    assert [(s["source_id"], s["source"]) for s in sources] == [(src["id"], "Customers sheet")]


# ----------------------------------------------------------------------------- Review 1
def test_typing_an_actual_after_a_load_asks_the_owner_at_once(api):
    owned_channel(api)
    load(api, september())
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    said = asks(api)
    assert len(said) == 1 and "typed 70%, computed 75%" in said[0]
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70, "spend": 900})
    assert len(asks(api)) == 1  # the same typed number: asked already
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 75, "quality": "estimate"})
    assert len(asks(api)) == 1  # an estimate is replaced by the computed figure, nothing to ask


def test_a_channel_with_no_owner_is_asked_once_an_owner_is_set(api):
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    bot(api, "ops")
    r = api.patch("/api/v2/growth/channels/search-ads", json={"owner_ref": "ops"}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    load(api, september())
    said = asks(api)
    assert len(said) == 1 and "typed 70%" in said[0]


def test_a_member_sees_both_values_but_no_keep_or_use(api):
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70})
    load(api, september())
    view = get(api, f"places/growth?month={SEPT}", token="cara-test")
    number = next(
        p["card"]["data"]
        for p in view["pins"]
        if p["card"]["template"] == "growth_number" and p["card"]["settings"].get("metric") == "kept_d30"
    )
    assert [(c["typed"], c["may"]) for c in number["origin"]["conflicts"]] == [(70, False)]
    retention = next(p["card"]["data"] for p in view["pins"] if p["card"]["template"] == "growth_retention")
    search = next(r for r in retention["computed"]["cohorts"] if r["value"] == "search-ads")
    assert search["typed"] == {"kept_d30": {"typed": 70, "chosen": None, "may": False}}
    # The channel's owner may, though not an admin.
    ops = bot(api, "ops")
    mine = get(api, f"growth/computed?month={SEPT}", token=ops)
    assert next(r for r in mine["cohorts"] if r["value"] == "search-ads")["typed"]["kept_d30"]["may"] is True


def test_a_sources_held_value_stays_on_a_computed_figure(api):
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "new_customers": 120})
    src = post(
        api,
        "growth/sources",
        {
            "truth_for": "new customers",
            "system": "Signups sheet",
            "sync_kind": "file",
            "sync_map": {"fields": {"new_customers": "signups"}},
        },
    )
    assert (
        post(api, f"growth/sources/{src['id']}/rows", {"csv": f"month,channel,signups\n{SEPT},search-ads,131\n"})[
            "held"
        ]
        == 1
    )
    load(api, september())
    origin = sept(api)["origins"]["new_customers"]
    assert origin["origin"] == "computed" and origin["conflict"]["value"] == 131
    assert origin["differs"] == {"typed": 120, "chosen": None}


# ----------------------------------------------------------------------------- Review 2
def test_turning_a_typed_estimate_into_an_actual_asks_the_owner(api):
    owned_channel(api)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "kept_d30": 70, "quality": "estimate"})
    load(api, september())
    assert asks(api) == []  # an estimate gives way to the computed figure
    put(api, "growth/cohorts", {"channel": "search-ads", "month": SEPT, "quality": "actual"})
    said = asks(api)
    assert len(said) == 1 and "typed 70%, computed 75%" in said[0]
