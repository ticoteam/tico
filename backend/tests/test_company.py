"""The company at the top of Goals and each goal's Why (backend/company.py), where the CEO Bot switch is on: the owner
and admins write the Vision and the Description, everyone reads them, and every change is kept with who and when. The
CEO Bot proposes a goal's Why; a person accepts it, edits it or writes their own. No bot writes either, the CEO Bot
included (spec: Head of Product, CEO Bot, tests 1, 2 and 4)."""

from backend import goal_asks as GA
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_goal_asks import goal_manager

CEO = "bot:goal-manager"


def bot(api, slug):
    """The bot itself, as an agent credential (backend/tests/test_ceo_bot.py as_bot)."""
    token = slug + "-test"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created,created_by) "
                  "VALUES(?,'hermes',?,?,'human:ana')", (slug, "x-" + slug, H.now()))
    api.app.state.store.settings.test_identities[token] = Identity("bot:" + slug, "bot", agent="hermes")
    return token


def switched_on(api):
    goal_manager(api)
    assert post(api, "ceo-bot", {"on": True})["on"] is True
    return bot(api, "goal-manager")


def propose_why(api, goal_id, why, token, reason="The vision is about rental owners."):
    return post(api, "goal-proposals", {"kind": "goal_why", "goal_id": goal_id, "payload": {"why": why},
                                        "reason": reason}, token=token)["proposal"]


def goal(api, goal_id):
    return get(api, f"goals/{goal_id}")["goal"]


def test_off_there_is_no_company_section_to_write(api):
    """Spec test 17's guard: with the switch off the record reads empty and nothing about it can be written."""
    goal_manager(api)
    ceo = bot(api, "goal-manager")
    shown = get(api, "company")
    assert shown["on"] is False and shown["company"]["vision"] == "" and shown["may_edit"] is True
    post(api, "company", {"vision": "Every landlord runs on Tico."}, expected=409)
    gid = post(api, "goals", {"title": "Grow rentals", "owner": "company"})["goal"]["id"]
    post(api, "goal-proposals", {"kind": "goal_why", "goal_id": gid, "payload": {"why": "Landlords first."}},
         token=ceo, expected=409)
    post(api, f"goals/{gid}/why", {"why": "Landlords first."}, expected=409)
    # Off wins over every other refusal: a bot, the Assistant acting for the owner, a member, an unknown goal.
    api.app.state.store.settings.test_identities["ana-via"] = Identity("human:ana", "owner", "ana@acme.example",
                                                                       via="assistant", confirmed=True)
    for token in (ceo, "ana-via", "cara-test"):
        post(api, "company", {"vision": "Said for her."}, token=token, expected=409)
        post(api, f"goals/{gid}/why", {"why": "Said for her."}, token=token, expected=409)
    post(api, "goals/no-such-goal/why", {"why": "Landlords first."}, expected=409)
    post(api, "goal-proposals", {"kind": "goal_why", "goal_id": "no-such-goal", "payload": {"why": "Landlords first."}},
         token=ceo, expected=409)
    # A Why left waiting from when it was on can't be decided while off, by anyone.
    assert post(api, "ceo-bot", {"on": True})["on"] is True
    waiting = propose_why(api, gid, "Landlords first.", ceo)
    assert post(api, "ceo-bot", {"on": False})["on"] is False
    for token in (ceo, "ana-via", "cara-test", "ana-test"):
        post(api, f"goal-proposals/{waiting['id']}/decide", {"decision": "confirm"}, token=token, expected=409)
    assert goal(api, gid)["why"] == ""
    assert get(api, "company/history")["events"] == []


def test_the_owner_writes_it_a_member_reads_it_and_the_history_says_who_and_when(api):
    """Spec tests 1 and 2: empty at first; the owner (or an admin) saves it, a member reads it and cannot change it,
    and the history keeps every change with who made it."""
    switched_on(api)
    first = get(api, "company")
    assert first == {**first, "on": True, "may_edit": True}
    assert first["company"]["vision"] == "" and first["company"]["description"] == ""
    assert get(api, "company", token="cara-test")["may_edit"] is False
    assert get(api, "company", token="ben-test")["may_edit"] is True                    # an admin
    saved = post(api, "company", {"vision": "Every small landlord runs  their rentals on Tico.",
                                  "description": "Tico runs a rental business with bots.\nFor landlords with 1-20 units."})
    assert saved["company"]["vision"] == "Every small landlord runs their rentals on Tico."   # one line
    assert saved["company"]["description"].endswith("For landlords with 1-20 units.")
    assert saved["company"]["updated_by"] == "human:ana" and saved["company"]["updated"]
    # A member reads both, and may not change them.
    read = get(api, "company", token="cara-test")
    assert read["company"]["vision"] == saved["company"]["vision"] and read["may_edit"] is False
    post(api, "company", {"vision": "Mine now."}, token="cara-test", expected=403)
    # An admin changes the description only; the same words again change nothing.
    post(api, "company", {"description": "Bots that run rentals."}, token="ben-test")
    post(api, "company", {"description": "Bots that run rentals."}, token="ben-test")
    post(api, "company", {"vision": "x" * 401}, expected=422)
    events = get(api, "company/history", token="cara-test")["events"]
    assert [(e["field"], e["actor"]) for e in events] == [
        ("description", "human:ben"), ("description", "human:ana"), ("vision", "human:ana")]
    newest = events[0]
    assert newest["old"].startswith("Tico runs a rental business") and newest["new"] == "Bots that run rentals."
    assert newest["actor_name"] and newest["ts"]
    assert get(api, "company")["company"]["updated_by"] == "human:ben"


def test_no_bot_writes_the_company_or_accepts_a_why_the_ceo_bot_included(api):
    """The API refuses a Vision or Description edit and a Why accept or write from any bot; the CEO Bot proposes."""
    ceo = switched_on(api)
    ops = bot(api, "ops")
    for token in (ceo, ops):
        post(api, "company", {"vision": "Bots rule."}, token=token, expected=403)
    # Not the Assistant acting for the owner either, even on a card she confirmed (which gets past the Assistant's own
    # gate in backend/app.py): only her own click here. The CEO Bot's proposal is the one way words reach it.
    api.app.state.store.settings.test_identities["ana-via"] = Identity("human:ana", "owner", "ana@acme.example",
                                                                       via="assistant", confirmed=True)
    post(api, "company", {"vision": "Said for her."}, token="ana-via", expected=403)
    assert get(api, "company")["company"]["vision"] == ""
    # A bot's own goal: still no Why written by the bot, and no accept.
    gid = post(api, "goals", {"title": "Answer every ticket in an hour", "owner": "ops"})["goal"]["id"]
    post(api, f"goals/{gid}/why", {"why": "Support is the vision."}, token=ops, expected=403)
    post(api, f"goals/{gid}/why", {"why": "Support is the vision."}, token=ceo, expected=403)
    # Nor the Assistant writing it straight onto the owner's goal for her, with a Why waiting there: nothing changes.
    company_goal = post(api, "goals", {"title": "Grow rentals 30%", "owner": "company"})["goal"]["id"]
    waiting = propose_why(api, company_goal, "More rentals is the vision.", ceo)
    post(api, f"goals/{company_goal}/why", {"why": "Said for her."}, token="ana-via", expected=403)
    unchanged = goal(api, company_goal)
    assert unchanged["why"] == "" and not [e for e in unchanged["events"] if e["field"] == "why"]
    assert [x["status"] for x in get(api, "goal-proposals?status=all")["proposals"] if x["id"] == waiting["id"]] == ["pending"]
    # Her own click does it.
    assert post(api, f"goals/{company_goal}/why", {"why": "Her own line."})["goal"]["why"] == "Her own line."
    proposal = propose_why(api, gid, "Fast answers keep landlords on Tico.", ceo)
    assert proposal["status"] == "pending" and proposal["proposed_by"] == CEO
    for token in (ceo, ops, "ana-via"):
        post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token=token, expected=403)
    assert goal(api, gid)["why"] == ""


def test_the_ceo_bot_proposes_a_why_and_the_owner_accepts_or_edits_it(api):
    """Spec test 4: a goal with no Why is not tied to the vision; the CEO Bot proposes one, the owner accepts it as it
    is or edited, or writes their own. One open Why per goal, and none of them is a Needs you item of its own."""
    ceo = switched_on(api)
    a = post(api, "goals", {"title": "Grow rentals 30%", "owner": "company"})["goal"]["id"]
    b = post(api, "goals", {"title": "Ship the stats page", "owner": "ben"})["goal"]["id"]
    c = post(api, "goals", {"title": "Hire a support lead", "owner": "cara"})["goal"]["id"]
    assert goal(api, a)["why"] == "" and goal(api, a)["why_by"] is None                    # "Not tied to the vision"
    seen = propose_why(api, a, "More rentals is the vision's first step.", ceo)
    # The owner has the first line on screen; the CEO Bot replaces it before they click. Accept on what they saw
    # never confirms the newer words: it is refused, nothing is written, and the newer line waits for them.
    first = propose_why(api, a, "More landlords on Tico is the vision.", ceo)
    assert first["id"] != seen["id"] and first["payload"] == {"why": "More landlords on Tico is the vision."}
    for body in ({"decision": "confirm"}, {"decision": "confirm", "why": "My own line."}):
        refused = post(api, f"goal-proposals/{seen['id']}/decide", body, expected=422)
        assert "replaced by a newer one" in str(refused)
    assert goal(api, a)["why"] == "" and not [e for e in goal(api, a)["events"] if e["field"] == "why"]
    tree = get(api, "goals/tree")
    whys = [p for p in tree["proposals"] if p["kind"] == "goal_why"]
    assert [(p["id"], p["goal_id"], p["may_decide"]) for p in whys] == [(first["id"], a, True)]
    assert [p["may_decide"] for p in get(api, "goals/tree", token="cara-test")["proposals"] if p["kind"] == "goal_why"] == [False]
    # Never one Needs you item (or Goal Manager task) per goal: the CEO Bot bundles them (Dev 220).
    assert not [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "proposal"]
    with api.app.state.store.transaction() as conn:
        GA.sync(conn, api.app.state.auth.owner_id(conn))
        assert conn.execute("SELECT count(*) FROM goal_asks").fetchone()[0] == 0
    # Accepted as it is.
    decided = post(api, f"goal-proposals/{first['id']}/decide", {"decision": "confirm"})["proposal"]
    assert decided["status"] == "confirmed" and decided["result"] == {"why": "More landlords on Tico is the vision.",
                                                                     "edited": False}
    accepted = goal(api, a)
    assert accepted["why"] == "More landlords on Tico is the vision." and accepted["why_by"] == "human:ana"
    assert [(e["field"], e["actor"]) for e in accepted["events"] if e["field"] == "why"] == [("why", "human:ana")]
    # Accepted as edited, by the goal's own owner.
    p = propose_why(api, b, "The stats page shows landlords their money.", ceo)
    post(api, f"goal-proposals/{p['id']}/decide", {"decision": "confirm", "why": "  Landlords see   their money. "},
         token="cara-test", expected=403)
    edited = post(api, f"goal-proposals/{p['id']}/decide", {"decision": "confirm", "why": "  Landlords see   their money. "},
                  token="ben-test")["proposal"]
    assert edited["result"] == {"why": "Landlords see their money.", "edited": True}
    assert goal(api, b)["why"] == "Landlords see their money." and goal(api, b)["why_by"] == "human:ben"
    post(api, f"goal-proposals/{edited['id']}/decide", {"decision": "confirm"}, token="ben-test", expected=422)
    # An edited line is for a Why only, and an accepted Why is never empty.
    p2 = propose_why(api, c, "Support keeps landlords.", ceo)
    post(api, f"goal-proposals/{p2['id']}/decide", {"decision": "reject", "why": "x"}, expected=422)
    post(api, f"goal-proposals/{p2['id']}/decide", {"decision": "confirm", "why": "   "}, expected=422)
    # Written by hand: the waiting proposal is settled, so nobody is asked about it again.
    post(api, f"goals/{c}/why", {"why": "x" * 201}, expected=422)
    post(api, f"goals/{c}/why", {"why": "Ben's line."}, token="ben-test", expected=403)
    written = post(api, f"goals/{c}/why", {"why": "Good support keeps landlords on Tico."}, token="cara-test")["goal"]
    assert written["why"] == "Good support keeps landlords on Tico." and written["why_by"] == "human:cara"
    settled = get(api, "goal-proposals?status=all")["proposals"]
    assert {(x["id"], x["status"], x["decision_note"]) for x in settled if x["id"] == p2["id"]} == {
        (p2["id"], "rejected", "The Why was set")}
    # Cleared: not tied to the vision again, and that is a change in the history too.
    cleared = post(api, f"goals/{c}/why", {"why": ""}, token="cara-test")["goal"]
    assert cleared["why"] == "" and cleared["why_by"] is None
    assert [e["new"] for e in cleared["events"] if e["field"] == "why"] == ["Good support keeps landlords on Tico.", ""]
