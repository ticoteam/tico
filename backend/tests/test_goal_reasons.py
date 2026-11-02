"""An optional reason after turning down a Strategy Bot suggestion (spec "Clear Strategy Bot suggestion cards", follow-up:
criteria 12 and 14): "No, leave it" rejects at once; the person who rejected it may then tell the Strategy Bot why, in one
line, once (POST /api/v2/goal-proposals/{pid}/note), and a reader who may not read a bot the suggestion is about
never reads that reason."""

from backend import goals as G
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_goal_cards import inbox_measure, leaks, propose
from backend.tests.test_kpis import goal_with_kpi

REASON = "no billing yet, don't track revenue"


def rejected(api, token="ana-test", **query):
    q = "&".join(f"{k}={v}" for k, v in query.items())
    return {p["id"]: p for p in get(api, "goal-proposals?status=rejected" + (f"&{q}" if q else ""), token)["proposals"]}


def stored(api, proposal_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT status, decision_note FROM goal_proposals WHERE id=?", (proposal_id,)).fetchone()


def test_no_leave_it_rejects_at_once_and_its_decider_adds_the_reason_afterwards(api):
    goal, _ = goal_with_kpi(api)
    p = propose(api, "goal_kpi", {"kpi": {"name": "Monthly Services revenue"}}, goal["id"])
    done = post(api, f"goal-proposals/{p['id']}/decide", {"decision": "reject"})["proposal"]
    assert (done["status"], done["decision_note"], done["may_reason"]) == ("rejected", "", True)
    # One line: spaces and line breaks fold into single spaces.
    saved = post(api, f"goal-proposals/{p['id']}/note", {"note": "  no billing yet,\n don't track   revenue "})["proposal"]
    assert (saved["decision_note"], saved["may_reason"]) == (REASON, False)
    listed = rejected(api, goal_id=goal["id"])[p["id"]]           # what hub proposal list --status rejected prints
    assert (listed["decision_note"], listed["may_reason"]) == (REASON, False)
    # Through the new name of the route as well (backend/route_renames.py).
    other = propose(api, "goal_wording", {"title": "Raise activation to 70%"}, goal["id"])
    post(api, f"proposals/{other['id']}/decide", {"decision": "reject"})
    assert post(api, f"proposals/{other['id']}/note", {"note": "Keep it short"})["proposal"]["decision_note"] == "Keep it short"


def test_leaving_the_box_changes_nothing_more(api):
    goal, _ = goal_with_kpi(api)
    p = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos"}}, goal["id"])
    post(api, f"goal-proposals/{p['id']}/decide", {"decision": "reject"})
    assert tuple(stored(api, p["id"])) == ("rejected", "")
    assert rejected(api)[p["id"]]["may_reason"] is True


def test_a_second_reason_anyone_else_a_waiting_or_confirmed_one_and_an_empty_one_are_refused(api):
    goal, _ = goal_with_kpi(api)
    waiting = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos"}}, goal["id"])
    refused = post(api, f"goal-proposals/{waiting['id']}/note", {"note": REASON}, expected=422)
    assert "turned down" in str(refused) and tuple(stored(api, waiting["id"])) == ("pending", "")

    yes = propose(api, "goal_kpi", {"kpi": {"name": "Trials started"}}, goal["id"])
    post(api, f"goal-proposals/{yes['id']}/decide", {"decision": "confirm"})
    post(api, f"goal-proposals/{yes['id']}/note", {"note": REASON}, expected=422)
    assert tuple(stored(api, yes["id"])) == ("confirmed", "")

    no = propose(api, "goal_kpi", {"kpi": {"name": "Monthly Services revenue"}}, goal["id"])
    post(api, f"goal-proposals/{no['id']}/decide", {"decision": "reject"})
    for empty in ("", "   ", "\n\t"):
        post(api, f"goal-proposals/{no['id']}/note", {"note": empty}, expected=422)
    long = post(api, f"goal-proposals/{no['id']}/note", {"note": "x" * (G.REASON_MAX + 1)}, expected=422)
    assert f"at most {G.REASON_MAX}" in str(long)
    # Ben and Cara read the goal too; only Ana turned it down.
    for token in ("ben-test", "cara-test"):
        assert "Only the person who turned" in str(post(api, f"goal-proposals/{no['id']}/note", {"note": REASON},
                                                       token=token, expected=403))
        assert rejected(api, token)[no["id"]]["may_reason"] is False
    assert tuple(stored(api, no["id"])) == ("rejected", "")

    post(api, f"goal-proposals/{no['id']}/note", {"note": REASON})
    again = post(api, f"goal-proposals/{no['id']}/note", {"note": "changed my mind"}, expected=422)
    assert "added once" in str(again) and tuple(stored(api, no["id"])) == ("rejected", REASON)

    # A reason typed with the reject is that reason: none is added after it.
    typed = propose(api, "goal_wording", {"title": "Raise activation to 70%"}, goal["id"])
    done = post(api, f"goal-proposals/{typed['id']}/decide", {"decision": "reject", "note": "Too narrow"})["proposal"]
    assert done["may_reason"] is False
    post(api, f"goal-proposals/{typed['id']}/note", {"note": REASON}, expected=422)
    post(api, "goal-proposals/no-such/note", {"note": REASON}, expected=404)


def test_a_reader_who_may_not_read_the_measures_bot_reads_no_reason_and_gets_no_box(api):
    # Cara may not read the inbox bot (test_goal_cards). A suggestion to reuse its measure on her goal still shows to
    # her, as "a measure you cannot see"; the reason Ana gave for turning it down does not.
    hidden, secret = inbox_measure(api)
    hers = post(api, "goals", {"title": "Keep customers", "owner": "cara"}, token="cara-test")["goal"]
    reuse = propose(api, "goal_kpi", {"kpi_id": secret["id"], "target": {"kind": "maintain", "max": 3}}, hers["id"])
    post(api, f"goal-proposals/{reuse['id']}/decide", {"decision": "reject", "note": "Inbox only, not churn"})
    definition = propose(api, "kpi_definition", {"definition": "Within 3 days"}, kpi_id=secret["id"])
    post(api, f"goal-proposals/{definition['id']}/decide", {"decision": "reject"})
    post(api, f"goal-proposals/{definition['id']}/note", {"note": "Inbox only, three days is too long"})
    assert rejected(api)[reuse["id"]]["decision_note"] == "Inbox only, not churn"        # Ana reads both
    assert rejected(api)[definition["id"]]["decision_note"] == "Inbox only, three days is too long"

    for path in ("goal-proposals?status=rejected", "goal-proposals?status=all", f"goal-proposals?status=rejected&goal_id={hers['id']}",
                 f"goals/{hers['id']}", "goals/tree", "goals"):
        seen = get(api, path, "cara-test")
        assert "Inbox only" not in str(seen) and leaks(seen) == [], path
    mine = rejected(api, "cara-test")
    assert mine[reuse["id"]]["decision_note"] == "" and definition["id"] not in mine
    # Her goal's history still says a suggestion was turned down, without its words.
    [line] = [e for e in get(api, f"goals/{hers['id']}", "cara-test")["goal"]["events"]
              if e["field"] == "proposal" and e["new"] == "rejected"]
    assert line["note"] == ""
    assert any(e.get("note") == "Inbox only, not churn" for e in get(api, f"goals/{hers['id']}")["goal"]["events"])

    # She may turn such a suggestion down herself, but gets no box: its reason is not hers to read or add.
    again = propose(api, "goal_kpi", {"kpi_id": secret["id"]}, hers["id"])
    done = post(api, f"goal-proposals/{again['id']}/decide", {"decision": "reject"}, token="cara-test")["proposal"]
    assert done["may_reason"] is False
    refused = post(api, f"goal-proposals/{again['id']}/note", {"note": "No"}, token="cara-test", expected=403)
    assert "may not read" in str(refused) and tuple(stored(api, again["id"])) == ("rejected", "")
    # A measure a new suggestion would give to the inbox bot is the same.
    named = propose(api, "goal_kpi", {"kpi": {"name": "Replies sent"}, "owner": "inbox"}, hers["id"])
    post(api, f"goal-proposals/{named['id']}/decide", {"decision": "reject", "note": "Inbox again"})
    assert rejected(api, "cara-test")[named["id"]]["decision_note"] == ""
    assert "Inbox again" not in str(get(api, f"goals/{hers['id']}", "cara-test"))
    # A note typed with a yes is kept from her the same way. Confirming this one is refused now (it would show her the
    # inbox bot's measure), so it stands for a yes given before she lost the inbox bot, history line included.
    yes = propose(api, "goal_kpi", {"kpi": {"name": "Replies read"}, "owner": "inbox"}, hers["id"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE goal_proposals SET status='confirmed', decision_note='Inbox wants it', decided_by='human:ana' "
                  "WHERE id=?", (yes["id"],))
        G._event(c, hers["id"], "human:ana", "proposal", "goal_kpi", "confirmed", "Inbox wants it")
    for path in ("goal-proposals?status=confirmed", "goal-proposals?status=all", f"goals/{hers['id']}"):
        assert "Inbox wants it" not in str(get(api, path, "cara-test")), path
    assert "Inbox wants it" in str(get(api, "goal-proposals?status=confirmed"))
