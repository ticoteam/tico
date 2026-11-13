"""Growth's first run (backend/growth_setup.py): the four steps from an empty Growth to Acme's first numbers, the next
step every Growth read names, what each caller may do, and the dates the activation rate is read from."""

from backend import growth_setup as GS
from backend.tests.helpers.api import as_member, get, headers, post, put
from backend.tests.helpers.company import bot

READS = (
    "growth/channels",
    "growth/sources",
    "growth/cohorts",
    "growth/economics",
    "growth/numbers",
    "growth/kpis",
    "growth/settings",
)


def recorded(api):
    with api.app.state.store.read() as c:
        return GS.recorded(c)


def name_source(api, src, slug="search-ads"):
    r = api.patch(f"/api/v2/growth/channels/{slug}", json={"source_id": src["id"]}, headers=headers())
    assert r.status_code == 200, r.text


def set_up_to(api, step):
    """Acme done up to and including `step`: settings, a search-ads channel, its source, its first month."""
    order = list(GS.STEPS)
    upto = order.index(step) + 1 if step else 0
    if upto >= 1:
        put(api, "growth/settings", {"revenue_month": 80, "margin_pct": 70})
    if upto >= 2:
        post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    if upto >= 3:
        src = post(api, "growth/sources", {"truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp"})
        name_source(api, src)
    if upto >= 4:
        put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-10", "spend": 1800, "new_customers": 6})


def test_an_empty_growth_names_settings_next_on_every_read_with_its_command_and_tool(api):
    for path in READS:
        setup = get(api, path)["setup"]
        assert setup["done"] is False, path
        assert [s["done"] for s in setup["steps"]] == [False] * 4, path
        nxt = setup["next"]
        assert (nxt["step"], nxt["number"], nxt["tool"], nxt["you_may"]) == (
            "settings",
            1,
            "tico_growth_settings",
            True,
        ), path
        assert nxt["command"].startswith("tico growth settings --set revenue_month="), path
    put(api, "growth/settings", {"revenue_month": 80, "margin_pct": 70})
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    show = get(api, "growth/channels/search-ads")["setup"]
    assert show["next"]["step"] == "source" and show["next"]["tool"] == "tico_growth_source_add"


def test_the_next_step_follows_the_steps_in_order(api):
    expected = {
        "settings": ("channel", "tico_growth_channel_add"),
        "channel": ("source", "tico_growth_source_add"),
        "source": ("month", "tico_growth_cohort_set"),
    }
    set_up_to(api, "settings")
    setup = get(api, "growth/channels")["setup"]
    assert (setup["next"]["step"], setup["next"]["tool"]) == expected["settings"]
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    setup = get(api, "growth/channels")["setup"]
    assert (setup["next"]["step"], setup["next"]["tool"]) == expected["channel"]
    assert "search-ads" in setup["next"]["then"]
    src = post(api, "growth/sources", {"truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp"})
    # With one live source the command names it.
    assert get(api, "growth/sources")["setup"]["next"]["command"] == (
        f"tico growth channel update search-ads --source {src['id']}"
    )
    name_source(api, src)
    setup = get(api, "growth/channels")["setup"]
    assert (setup["next"]["step"], setup["next"]["tool"]) == expected["source"]
    assert f"--channel search-ads --month {GS.last_full_month()}" in setup["next"]["command"]
    # A month with only spend does not finish step 4; spend and new customers do.
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-10", "spend": 1800})
    assert get(api, "growth/channels")["setup"]["next"]["step"] == "month"
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-10", "new_customers": 6})
    for path in READS:
        assert get(api, path)["setup"] == {"done": True}, path


def test_unknown_a_retired_channel_and_a_retired_source_do_not_count(api):
    put(api, "growth/settings", {"revenue_month": 80, "margin_pct": 70})
    put(api, "growth/cohorts", {"channel": "unknown", "month": "2026-10", "new_customers": 9})
    assert get(api, "growth/channels")["setup"]["next"]["step"] == "channel"
    post(api, "growth/channels", {"slug": "old", "name": "Old"})
    post(api, "growth/channels/old/retire", {})
    assert get(api, "growth/channels")["setup"]["next"]["step"] == "channel"
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    src = post(api, "growth/sources", {"truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp"})
    name_source(api, src)
    post(api, f"growth/sources/{src['id']}/retire", {})
    assert get(api, "growth/channels")["setup"]["next"]["step"] == "source"


def test_a_bot_may_not_do_settings_and_is_told_who_may_and_may_record_the_first_month(api):
    ops = bot(api, "ops")
    nxt = get(api, "growth/channels", token=ops)["setup"]["next"]
    assert nxt["step"] == "settings" and nxt["you_may"] is False
    assert nxt["who_may"] == [{"ref": "human:ana", "name": "Ana"}, {"ref": "human:ben", "name": "Ben"}]
    set_up_to(api, "source")
    nxt = get(api, "growth/channels", token=ops)["setup"]["next"]
    assert (nxt["step"], nxt["you_may"]) == ("month", True) and "who_may" not in nxt


def test_a_member_is_told_the_owner_and_admins_by_name(api):
    as_member(api, "ben@acme.example")
    put(api, "growth/settings", {"revenue_month": 80, "margin_pct": 70})
    nxt = get(api, "growth/channels", token="cara-test")["setup"]["next"]
    assert (nxt["step"], nxt["you_may"]) == ("channel", False)
    assert [p["ref"] for p in nxt["who_may"]] == ["human:ana"]


def test_every_read_gives_one_answer_per_action_for_the_caller(api):
    ops = bot(api, "ops")
    want = {
        "ana-test": {"settings": True, "channels": True, "sources": True, "months": True},
        "cara-test": {"settings": False, "channels": False, "sources": False, "months": False},
        ops: {"settings": False, "channels": False, "sources": False, "months": True},
    }
    for token, may in want.items():
        for path in READS:
            assert get(api, path, token=token)["you_may"] == may, (token, path)
    # may_edit keeps its meaning per read: changing channels, sources, settings; recording months in numbers.
    assert get(api, "growth/settings", token=ops)["may_edit"] is False
    assert get(api, "growth/numbers", token=ops)["may_edit"] is True


def test_a_team_set_up_before_release_sees_no_setup_and_keeps_its_records(api):
    set_up_to(api, "month")
    before = get(api, "growth/cohorts")["cohorts"]
    with api.app.state.store.transaction() as c:  # as at release: no dates written yet
        c.execute("DELETE FROM growth_setup")
    assert get(api, "growth/economics")["setup"] == {"done": True}
    assert get(api, "growth/cohorts")["cohorts"] == before


def test_the_dates_are_written_once_each(api):
    get(api, "growth/kpis", token="cara-test")
    first = recorded(api)
    assert first["first_opened_at"] and first["first_opened_by"] == "human:cara"
    assert not first["settings_at"] and not first["numbers_seen_at"]
    set_up_to(api, "settings")
    assert recorded(api)["settings_at"]  # written by the write itself
    post(api, "growth/channels", {"slug": "search-ads", "name": "Search ads"})
    assert recorded(api)["channel_at"] and not recorded(api)["source_at"]
    # Reading numbers before setup is done is not "numbers seen".
    get(api, "growth/economics")
    assert not recorded(api)["numbers_seen_at"]
    src = post(api, "growth/sources", {"truth_for": "ad spend", "system": "Google Ads", "reach_kind": "mcp"})
    name_source(api, src)
    put(api, "growth/cohorts", {"channel": "search-ads", "month": "2026-10", "spend": 1800, "new_customers": 6})
    done = recorded(api)
    assert done["source_at"] and done["month_at"] and done["done_at"] and not done["numbers_seen_at"]
    get(api, "growth/settings")  # not a numbers read
    assert not recorded(api)["numbers_seen_at"]
    get(api, "places/growth", token="ben-test")
    seen = recorded(api)
    assert seen["numbers_seen_at"] and seen["numbers_seen_by"] == "human:ben"
    for path in READS:
        get(api, path)
    get(api, "places/growth")
    assert recorded(api) == seen


def test_an_agent_alone_is_the_first_open_and_a_retired_channel_keeps_setup_done(api):
    ops = bot(api, "ops")
    get(api, "growth/economics", token=ops)
    assert recorded(api)["first_opened_by"].startswith("bot:")
    set_up_to(api, "month")
    post(api, "growth/channels/search-ads/retire", {})
    assert get(api, "growth/channels")["setup"] == {"done": True}


def test_the_setup_read_lists_each_step_with_its_date(api):
    empty = get(api, "growth/setup", token="cara-test")
    assert empty["done"] is False and empty["next"]["step"] == "settings" and empty["first_opened_at"]
    assert [s["step"] for s in empty["steps"]] == list(GS.STEPS) and all(s["at"] is None for s in empty["steps"])
    set_up_to(api, "month")
    get(api, "growth/numbers")
    full = get(api, "growth/setup")
    assert full["done"] is True and full["next"] is None and full["numbers_seen_at"]
    assert all(s["done"] and s["at"] for s in full["steps"]) and full["channel"] == {
        "slug": "search-ads",
        "name": "Search ads",
    }


def test_last_full_month():
    from datetime import datetime, timezone

    assert GS.last_full_month(datetime(2026, 11, 13, tzinfo=timezone.utc)) == "2026-10"
    assert GS.last_full_month(datetime(2027, 1, 2, tzinfo=timezone.utc)) == "2026-12"


def test_a_read_whose_record_is_refused_still_answers(api, monkeypatch):
    from backend.store import Problem

    def refuse(who, fn, **kw):
        raise Problem("forbidden", "This sign-in writes nothing", 403)

    monkeypatch.setattr(api.app.state.store, "write", refuse)
    assert get(api, "growth/channels")["setup"]["next"]["step"] == "settings"
    assert not recorded(api).get("first_opened_at")
