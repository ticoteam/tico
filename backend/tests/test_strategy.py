"""The team's strategy (backend/strategy.py): the owner writes a draft and publishes numbered versions; bets carry what
they rule out and what would show them wrong; goals serve bets; a bot proposes and the owner confirms. Examples use
Acme."""

import json

from backend import strategy as S
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post, put  # noqa: F401
from backend.tests.test_company import with_strategy_bot
from backend.tests.test_kpis import goal_with_kpi, log

WRONG = {"kind": "text", "text": "Three landlords in a row say no", "check_by": "2099-01-01"}


def draft(api, token="ana-test"):
    return get(api, "strategy", token=token)["draft"]


def write(api, token="ana-test", expected=200, **fields):
    revision = fields.pop("revision", None)
    if revision is None:
        revision = draft(api)["revision"]
    return put(api, "strategy/draft", {"revision": revision, **fields}, token=token, expected=expected)


def add_bet(api, statement="Small landlords pay for bots", expected=200, token="ana-test", **more):
    body = {"statement": statement, "why": "They have no staff.", "rules_out": "Enterprise sales",
            "wrong_if": WRONG, "status": "active", **more}
    return post(api, "strategy/bets", body, token=token, expected=expected)


def publish(api, note="First", expected=200):
    return post(api, "strategy/publish", {"note": note, "revision": draft(api)["revision"]}, expected=expected)


def ready(api, **bet):
    """A draft that may be published: a won't-do and one active bet."""
    write(api, summary="Acme runs rentals with bots.", sections={"not": "No enterprise deals."})
    return add_bet(api, **bet)["bet"]


def test_draft_merges_only_what_is_sent_and_a_stale_revision_gets_409(api):
    first = draft(api)
    assert first["guide"] == "default" and set(first["sections"]) == {*S.FIXED_KEYS, "extra"}
    saved = write(api, sections={"serve": "Landlords with 1-20 units.",
                                 "extra": {"brand": {"title": "Brand and voice", "body": "Plain."}}})["draft"]
    assert saved["revision"] == first["revision"] + 1
    saved = write(api, sections={"sell": "Bots at $99 a month."})["draft"]
    assert saved["sections"]["serve"] == "Landlords with 1-20 units." and saved["sections"]["extra"]["brand"]
    stale = write(api, revision=first["revision"], summary="Mine.", expected=409)
    assert stale["error"]["code"] == "conflict"
    removed = write(api, sections={"extra": {"brand": ""}})["draft"]
    assert removed["sections"]["extra"] == {}
    write(api, summary="x" * (S.SUMMARY_MAX + 1), expected=422)


def test_publish_needs_a_wont_and_an_active_bet_rules_out_and_wrong_if_optional(api):
    write(api, summary="Acme runs rentals with bots.")
    proposed = post(api, "strategy/bets", {"statement": "Landlords pay"})["bet"]
    write(api, sections={"not": "No enterprise deals."})
    publish(api, expected=422)                                                          # no active bet
    # Rules out and Wrong if are optional: a bare statement is active, and publishes.
    bare = post(api, "strategy/bets/" + proposed["id"], {"status": "active"})["bet"]
    assert bare["status"] == "active" and bare["rules_out"] == "" and bare["wrong_if"] is None
    post(api, "strategy/bets/" + proposed["id"], {"status": "proposed"})
    write(api, sections={"not": ""})
    add_bet(api)
    publish(api, expected=422)                                                          # no "What we won't do"
    write(api, sections={"not": "No enterprise deals."})
    first = publish(api)
    assert first["n"] == 1 and first["status"] == "published" and [b["status"] for b in first["bets"]] == ["proposed", "active"]
    fresh = draft(api)
    assert fresh["n"] is None and fresh["summary"] == "Acme runs rentals with bots." and fresh["revision"] > 0
    publish(api, expected=422)                                                          # nothing changed
    write(api, summary="Acme runs small rentals with bots.")
    assert publish(api, "Second")["n"] == 2
    assert [v["n"] for v in get(api, "strategy/versions")] == [2, 1]
    assert get(api, "strategy/versions/1")["summary"] == "Acme runs rentals with bots."


def test_only_the_owner_writes_and_reads_the_draft_a_bot_proposes_at_any_level(api):
    strategy_token = with_strategy_bot(api, level=3)
    ready(api)
    for token in ("ben-test", "cara-test", strategy_token):
        assert "draft" not in get(api, "strategy", token=token)
        put(api, "strategy/draft", {"revision": 0, "summary": "Mine."}, token=token, expected=403)
        add_bet(api, token=token, expected=403)
        post(api, "strategy/publish", {"revision": 0}, token=token, expected=403)
    assert get(api, "strategy", token="cara-test")["current"] is None
    bet = get(api, "strategy")["bets"][0]
    proposal = post(api, "goal-proposals", {"kind": "strategy_draft", "reason": "The site says so.", "payload": {
        "summary": "Acme runs rentals for small landlords.",
        "bets": [{"id": bet["id"], "status": "retired"}, {"statement": "Landlords refer landlords"}]}},
        token=strategy_token)["proposal"]
    # The proposal carries the unpublished draft: the owner and the bot that made it read it, no one else.
    for token in ("ben-test", "cara-test"):
        assert not [p for p in get(api, "goal-proposals", token=token)["proposals"] if p["kind"] == "strategy_draft"]
        assert not [p for p in get(api, "goals/tree", token=token)["proposals"] if p["kind"] == "strategy_draft"]
    assert [p["id"] for p in get(api, "goal-proposals", token=strategy_token)["proposals"]] == [proposal["id"]]
    assert [p["id"] for p in get(api, "goal-proposals")["proposals"]] == [proposal["id"]]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"}, token=strategy_token, expected=403)
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})
    view = get(api, "strategy")
    assert view["draft"]["summary"] == "Acme runs rentals for small landlords." and view["current"] is None
    assert {(b["statement"], b["status"]) for b in view["bets"]} == {
        ("Small landlords pay for bots", "retired"), ("Landlords refer landlords", "proposed")}


def test_two_pending_strategy_proposals_apply_in_the_order_confirmed(api):
    strategy_token = with_strategy_bot(api)
    ids = [post(api, "goal-proposals", {"kind": "strategy_draft", "reason": "r", "payload": {"summary": text}},
                token=strategy_token)["proposal"]["id"] for text in ("First.", "Second.")]
    for pid in reversed(ids):
        post(api, f"goal-proposals/{pid}/decide", {"decision": "confirm"})
    assert draft(api)["summary"] == "First."


def test_how_we_sell_is_a_section_that_publishes_with_the_rest_and_stays_out_of_the_run_summary(api):
    ready(api)
    write(api, sections={"how": "Referrals from landlords first, then a door-to-door pilot."})
    assert draft(api)["sections"]["how"].startswith("Referrals from landlords")
    publish(api)
    assert get(api, "strategy")["current"]["sections"]["how"] == "Referrals from landlords first, then a door-to-door pilot."
    run_text = api.get("/api/v2/strategy/summary", headers={"Authorization": "Bearer cara-test"}).text
    assert "Referrals" not in run_text and "How we sell" not in run_text


def test_proof_and_why_now_sit_after_what_we_sell_and_are_optional_for_publishing(api):
    assert S.FIXED_KEYS == ("serve", "sell", "proof", "now", "know", "how", "not")
    # A draft stored before the two sections existed reads them as empty and publishes without them.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE strategy_versions SET sections=? WHERE status='draft'",
                  (json.dumps({"serve": "Landlords.", "not": "No enterprise deals."}),))
    old = draft(api)["sections"]
    assert old["proof"] == old["now"] == "" and old["serve"] == "Landlords."
    add_bet(api)
    assert publish(api)["sections"]["proof"] == ""
    write(api, sections={"proof": "Forty landlords book weekly. (Growth, October)", "now": "Bots can do the office work."})
    second = publish(api, "Proof")
    assert second["sections"]["now"] == "Bots can do the office work."
    assert get(api, "strategy/diff?from=1&to=2")["fields"] == [
        {"key": "sections.proof", "from": "", "to": "Forty landlords book weekly. (Growth, October)"},
        {"key": "sections.now", "from": "", "to": "Bots can do the office work."}]
    write(api, sections={"why": "x"}, expected=422)


def test_proof_since_is_when_the_published_proof_last_changed_and_every_reader_sees_it(api):
    strategy_token = with_strategy_bot(api)
    assert get(api, "strategy")["proof_since"] is None                         # nothing published
    ready(api)
    publish(api)
    assert get(api, "strategy", token=strategy_token)["proof_since"] is None   # published, but proof is empty
    # The Strategy Bot proposes proof from the evidence; the owner confirms; publishing dates it.
    proposal = post(api, "goal-proposals", {"kind": "strategy_draft", "reason": "October's numbers are in.",
                                            "payload": {"sections": {"proof": "Forty landlords book weekly."}}},
                    token=strategy_token)["proposal"]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm"})
    second = publish(api, "Proof")
    write(api, summary="Acme runs small rentals with bots.")
    publish(api, "Summary only")
    assert get(api, "strategy", token=strategy_token)["proof_since"] == second["published_at"]
    write(api, sections={"proof": "Fifty landlords book weekly."})
    fourth = publish(api, "New proof")
    assert get(api, "strategy", token="cara-test")["proof_since"] == fourth["published_at"] != second["published_at"]


def test_an_extra_section_stored_as_proof_or_now_before_they_were_fixed_reads_under_a_new_key_and_stays_editable(api):
    old = {"not": "No enterprise deals.", "extra": {"proof": {"title": "Proof", "body": "Ten pilots."},
                                                    "now": {"title": "Now", "body": "Rates fell."},
                                                    "now-2": {"title": "Later", "body": "A second office."}}}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE strategy_versions SET sections=? WHERE status='draft'", (json.dumps(old),))
    sections = draft(api)["sections"]
    assert sections["proof"] == sections["now"] == ""
    assert sections["extra"] == {"proof-2": {"title": "Proof", "body": "Ten pilots."},
                                 "now-3": {"title": "Now", "body": "Rates fell."},
                                 "now-2": {"title": "Later", "body": "A second office."}}
    # The owner edits one and removes another; the fixed sections are untouched.
    write(api, sections={"extra": {"proof-2": {"title": "Pilots", "body": "Twelve pilots."}, "now-3": ""}})
    write(api, sections={"proof": "Forty landlords book weekly."})
    sections = draft(api)["sections"]
    assert sections["proof"] == "Forty landlords book weekly." and sections["now"] == ""
    assert sections["extra"] == {"proof-2": {"title": "Pilots", "body": "Twelve pilots."},
                                 "now-2": {"title": "Later", "body": "A second office."}}
    add_bet(api)
    assert set(publish(api)["sections"]["extra"]) == {"proof-2", "now-2"}


def test_proof_since_reaches_past_a_hundred_versions_with_the_same_proof(api):
    ready(api)
    write(api, sections={"proof": "Thirty landlords book weekly."})
    publish(api)
    write(api, sections={"proof": "Forty landlords book weekly."})
    changed = publish(api, "New proof")
    with api.app.state.store.transaction() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(strategy_versions)")]
        row = dict(zip(cols, c.execute("SELECT * FROM strategy_versions WHERE n=2").fetchone()))
        for n in range(3, S.VERSIONS_PAGE + 30):       # more unchanged versions than one page holds
            copy = {**row, "id": f"v-copy-{n}", "n": n, "published_at": f"2099-01-01T00:{n // 60:02d}:{n % 60:02d}Z"}
            c.execute(f"INSERT INTO strategy_versions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      [copy[k] for k in cols])
        assert S.proof_since(c) == changed["published_at"]


def test_the_default_guide_asks_in_the_style_of_yc_and_every_answer_lands_in_a_section(api):
    questions = S.guide("default")["questions"]
    keys = [q["key"] for q in questions]
    assert 12 <= len(questions) <= 14 and len(set(keys)) == len(keys)
    assert all(q["section"] in (*S.FIXED_KEYS, "bets") and q["ask"].strip() and q["claim"].strip() for q in questions)
    where = {q["key"]: q["section"] for q in questions}
    assert {k: where[k] for k in ("proof", "now", "acute", "next-100", "why-you")} == {
        "proof": "proof", "now": "now", "acute": "serve", "next-100": "how", "why-you": "know"}
    assert "not" in where.values()                                             # the one section publishing needs


def test_live_by_bet_counts_every_live_goal_past_the_sample_of_20(api):
    # 20 closed goals sort first (owner ana before cara), so the sample of 20 holds none of the 21 live ones.
    bet = ready(api)
    closed = [post(api, "goals", {"title": f"Old {i}", "owner": "ana", "bet_id": bet["id"]})["goal"]["id"] for i in range(20)]
    for i in range(21):
        post(api, "goals", {"title": f"Live {i}", "owner": "cara", "bet_id": bet["id"]})
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE goals SET status='done' WHERE id IN ({','.join('?' * len(closed))})", closed)
    view = get(api, "strategy")
    assert {g["status"] for g in view["goals_by_bet"][bet["id"]]} == {"done"}
    assert view["live_by_bet"][bet["id"]] == 21


def test_a_title_only_section_rename_from_a_proposal_renames_and_keeps_the_body(api):
    ready(api)
    write(api, sections={"extra": {"brand": {"title": "Brand", "body": "Plain."}}})
    publish(api)
    strategy_token = with_strategy_bot(api)
    pid = post(api, "goal-proposals", {"kind": "strategy_draft", "reason": "r", "payload": {
        "sections": {"extra": {"brand": {"title": "Positioning", "body": "Plain."}}}}},
        token=strategy_token)["proposal"]["id"]
    post(api, f"goal-proposals/{pid}/decide", {"decision": "confirm"})
    assert draft(api)["sections"]["extra"]["brand"] == {"title": "Positioning", "body": "Plain."}
    current = get(api, "strategy")["current"]
    assert current["n"] == 1 and current["sections"]["extra"]["brand"]["title"] == "Brand"


def test_goals_by_bet_hides_goals_the_reader_may_not_see_and_task_only_goals_are_gray(api):
    bet = ready(api)
    mine = post(api, "goals", {"title": "Sign 10 landlords", "owner": "ana", "bet_id": bet["id"]})["goal"]
    publish(api)
    view = get(api, "strategy")
    assert [g["id"] for g in view["goals_by_bet"][bet["id"]]] == [mine["id"]]
    assert view["health_by_bet"][bet["id"]] == "gray"
    assert view["website"] is None
    # A bot's goal is listed only to whoever may read that bot (goal_routes.readable_goals).
    theirs = post(api, "goals", {"title": "Answer every lead", "owner": "ana", "bet_id": bet["id"]})["goal"]

    class Auth:
        @staticmethod
        def unreadable_bots(c, who):
            return {"acme-helper"}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE goals SET owner_id='bot:acme-helper' WHERE id=?", (theirs["id"],))
        listed = S.goals_by_bet(c, Auth, Identity("human:cara", "human"), [bet["id"]])
    assert [g["id"] for g in listed[bet["id"]]] == [mine["id"]]
    post(api, "goals", {"title": "Nope", "owner": "ana", "bet_id": "missing"}, expected=422)
    goal, kpi = goal_with_kpi(api)
    post(api, f"goals/{goal['id']}", {"bet_id": bet["id"]})
    log(api, kpi["id"], 41)
    assert get(api, "strategy")["health_by_bet"][bet["id"]] in ("red", "yellow", "green")
    post(api, f"goals/{goal['id']}", {"bet_id": ""})
    assert get(api, f"goals/{goal['id']}")["goal"]["bet_id"] is None


def test_the_diff_says_what_changed_and_a_bet_back_to_proposed_is_not_retired(api):
    one = ready(api)
    two = add_bet(api, "Landlords refer landlords")["bet"]
    add_bet(api, "Landlords want one bill")
    publish(api)
    post(api, f"strategy/bets/{one['id']}", {"status": "proposed", "statement": "Small landlords pay $99"})
    post(api, f"strategy/bets/{two['id']}", {"status": "retired"})
    write(api, sections={"extra": {"brand": {"title": "Brand", "body": "Plain."}}})
    publish(api, "Second")
    out = get(api, "strategy/diff?from=1&to=2")
    assert [b["id"] for b in out["bets"]["retired"]] == [two["id"]] and out["bets"]["added"] == []
    assert [c["id"] for c in out["bets"]["changed"]] == [one["id"]]
    assert "sections.extra.brand" in [f["key"] for f in out["fields"]]


def test_retiring_a_bet_with_goals_files_one_realign_card_for_the_owner(api):
    with_strategy_bot(api)
    one = ready(api)
    two = add_bet(api, "Landlords refer landlords")["bet"]
    goal = post(api, "goals", {"title": "Sign 10 landlords", "owner": "cara", "bet_id": one["id"]})["goal"]
    publish(api)
    post(api, f"strategy/bets/{one['id']}", {"status": "retired"})
    publish(api, "Second")
    with api.app.state.store.read() as c:
        proposal = H._one(c, "SELECT * FROM goal_proposals WHERE kind=?", (S.REALIGN,))
        task = H._one(c, "SELECT t.* FROM goal_asks a JOIN tasks t ON t.id=a.task_id WHERE a.kind='proposal' AND "
                         "a.ref=?", (proposal["id"],))
    assert task and task["owner_id"] == "human:ana" and task["title"] == "Choose what happens to 1 goal after strategy v2"
    assert "Keep as it is, Retire goal, or Move to a bet" in task["body"] and "Confirm" not in task["body"]
    payload = json.loads(proposal["payload"])
    assert [g["goal_id"] for g in payload["goals"]] == [goal["id"]] and payload["bets"][0]["id"] == two["id"]
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "confirm", "moves": {goal["id"]: two["id"]}})
    assert get(api, f"goals/{goal['id']}")["goal"]["bet_id"] == two["id"]
    with api.app.state.store.read() as c:
        closed = H._one(c, "SELECT status, note FROM tasks WHERE id=?", (task["id"],))
        assert closed["status"] in ("done", "closed")
        assert not H._one(c, "SELECT 1 AS x FROM goal_asks WHERE ref=?", (proposal["id"],))
    assert closed["note"] == "Resolved: the goals were realigned with the strategy."


def realign_card(api):
    """A published bet reworded under two live goals: the realign card and the task it filed."""
    with_strategy_bot(api)
    one = ready(api)
    add_bet(api, "Landlords refer landlords")
    goals = [post(api, "goals", {"title": title, "owner": "cara", "bet_id": one["id"]})["goal"]
             for title in ("Sign 10 landlords", "Sign 20 landlords")]
    publish(api)
    post(api, f"strategy/bets/{one['id']}", {"statement": "Small landlords pay $99 a month"})
    publish(api, "Second")
    [card] = [p for p in get(api, "goal-proposals")["proposals"] if p["kind"] == S.REALIGN]
    with api.app.state.store.read() as c:
        task = H._one(c, "SELECT t.* FROM goal_asks a JOIN tasks t ON t.id=a.task_id WHERE a.ref=?", (card["id"],))
    return card, task, goals


def test_a_reworded_bet_carries_its_earlier_wording_and_keeping_every_goal_says_they_stay(api):
    card, task, goals = realign_card(api)
    assert [(g["change"], g["statement"], g["was"]) for g in card["payload"]["goals"]] == [
        ("rewritten", "Small landlords pay $99 a month", "Small landlords pay for bots")] * 2
    assert card["closed_goals"] == []
    assert task["title"] == "Choose what happens to 2 goals after strategy v2" and "reworded" in task["body"]
    post(api, f"goal-proposals/{card['id']}/decide", {"decision": "confirm", "moves": {g["id"]: "keep" for g in goals}})
    assert all(get(api, f"goals/{g['id']}")["goal"]["bet_id"] == card["payload"]["goals"][0]["bet_id"] for g in goals)
    with api.app.state.store.read() as c:
        assert H._one(c, "SELECT note FROM tasks WHERE id=?", (task["id"],))["note"] == \
            "Resolved: the goals stay as they are."


def test_a_goal_closed_since_the_card_was_filed_is_listed_closed_and_left_alone(api):
    card, task, goals = realign_card(api)
    post(api, f"goals/{goals[0]['id']}/status", {"status": "done", "note": "Signed"})
    [card] = [p for p in get(api, "goal-proposals")["proposals"] if p["id"] == card["id"]]
    assert card["closed_goals"] == [goals[0]["id"]]
    out = post(api, f"goal-proposals/{card['id']}/decide",
               {"decision": "confirm", "moves": {goals[0]["id"]: "retire"}})["proposal"]
    assert out["result"]["goals"] == {goals[0]["id"]: "closed", goals[1]["id"]: "kept"}
    assert get(api, f"goals/{goals[0]['id']}")["goal"]["status"] == "done"
    with api.app.state.store.read() as c:
        assert H._one(c, "SELECT note FROM tasks WHERE id=?", (task["id"],))["note"] == \
            "Resolved: the goals stay as they are."


def test_a_kpi_reading_past_the_wrong_if_trips_the_bet_once_per_version(api):
    with_strategy_bot(api)
    goal, kpi = goal_with_kpi(api)
    ready(api, wrong_if={"kind": "kpi", "kpi_id": kpi["id"], "direction": "below", "threshold": 30})
    publish(api)
    log(api, kpi["id"], 45)
    log(api, kpi["id"], 20)
    log(api, kpi["id"], 10)
    with api.app.state.store.read() as c:
        tasks = H._rows(c.execute("SELECT title, owner_id, body FROM tasks WHERE title LIKE 'Bet %'"))
    assert [(t["title"], t["owner_id"]) for t in tasks] == [("Bet 1: may be wrong", "bot:goal-manager")]
    assert "Evidence:" in tasks[0]["body"]


def test_an_open_bet_task_from_an_earlier_trip_holds_the_trip_until_it_closes(api):
    with_strategy_bot(api)
    goal, kpi = goal_with_kpi(api)
    ready(api, wrong_if={"kind": "kpi", "kpi_id": kpi["id"], "direction": "below", "threshold": 30})
    with api.app.state.store.transaction() as c:
        H.task_create(c, H.KEEPER, "Bet 1: may be wrong", "From strategy v0.", "bot:goal-manager", lint=False)
    publish(api)
    log(api, kpi["id"], 20)
    with api.app.state.store.transaction() as c:
        assert not c.execute("SELECT 1 FROM strategy_events WHERE kind=?", (S.TRIPPED,)).fetchone()
        c.execute("UPDATE tasks SET status='done' WHERE title='Bet 1: may be wrong'")
    log(api, kpi["id"], 15)
    with api.app.state.store.read() as c:
        tasks = H._rows(c.execute("SELECT status FROM tasks WHERE title='Bet 1: may be wrong' ORDER BY created_at"))
        assert [t["status"] for t in tasks][-1] != "done" and len(tasks) == 2
        assert c.execute("SELECT count(*) FROM strategy_events WHERE kind=?", (S.TRIPPED,)).fetchone()[0] == 1


def test_a_text_wrong_if_trips_after_its_check_by_and_nothing_trips_with_checks_off(api):
    ready(api)
    publish(api)
    with api.app.state.store.transaction() as c:
        assert S.watch(c, today="2099-01-02") == []                     # no Strategy Bot yet
    with_strategy_bot(api)
    with api.app.state.store.transaction() as c:
        assert S.watch(c, today="2098-12-31") == []
        assert len(S.watch(c, today="2099-01-02")) == 1
        assert S.watch(c, today="2099-01-03") == []


def test_the_summary_text_is_what_runs_carry(api):
    get(api, "strategy/summary", expected=404)
    ready(api)
    publish(api)
    text = api.get("/api/v2/strategy/summary", headers={"Authorization": "Bearer cara-test"}).text
    assert text.startswith("Acme runs rentals with bots.") and "1. Small landlords pay for bots" in text
    assert "We won't: No enterprise deals." in text and text.rstrip().endswith("version 1")
    assert len(json.dumps(get(api, "strategy"))) < 20_000


def test_a_group_serves_a_bet_and_only_the_owner_links_it(api):
    bet = ready(api)
    group = post(api, "groups", {"name": "Growth", "bet_id": bet["id"]})
    group = group.get("group", group)
    assert group["bet_id"] == bet["id"]


def test_trip_and_realign_cards_skip_the_daily_room(api):
    from backend import goal_asks as GA
    def card(kind, payload):
        return {"kind": "proposal", "proposal": {"kind": kind, "payload": payload}}
    assert GA.urgent(card(S.REALIGN, {}))
    assert GA.urgent(card("strategy_draft", {"source": S.TRIPPED}))
    assert not GA.urgent(card("strategy_draft", {}))


def test_the_old_company_vision_moves_into_the_first_draft(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM strategy_versions")
        c.execute("DELETE FROM strategy_events")
        # Upsert: a restarted hub (or a test database restored from a snapshot) already has the row.
        c.execute("INSERT INTO company_profile (id, vision, description, updated_at, updated_by) "
                  "VALUES ('company','Acme for every landlord.','Bots that run rentals.',?, 'human:ana') "
                  "ON CONFLICT(id) DO UPDATE SET vision=excluded.vision, description=excluded.description, "
                  "updated_at=excluded.updated_at, updated_by=excluded.updated_by", (H.now(),))
        S.ensure(c)
    assert draft(api)["vision"] == "Acme for every landlord."
    assert draft(api)["sections"]["sell"] == "Bots that run rentals."
    assert get(api, "company", token="cara-test")["company"]["vision"] == "Acme for every landlord."


def test_the_owner_runs_the_strategy_through_the_hub_tools(api):
    from backend.tests.test_mcp import call
    ok = lambda name, args=None, token="ana-test": call(api, name, args, token)  # noqa: E731
    err, out = ok("hub_strategy_draft", {"summary": "Acme runs rentals.", "section": ["not=No enterprise deals.",
                                                                                     "brand=Plain words."]})
    assert not err and out["sections"]["not"] == "No enterprise deals."
    assert out["sections"]["extra"]["brand"] == {"title": "Brand", "body": "Plain words."}
    err, out = ok("hub_strategy_bet_add", {"statement": "Landlords pay for bots", "rules_out": "Enterprise sales",
                                           "wrong_if_text": "Three landlords say no", "check_by": "2099-01-01",
                                           "status": "active"})
    assert not err and out["bet"]["wrong_if"]["kind"] == "text"
    err, out = ok("hub_strategy_publish", {"note": "First"})
    assert not err and out["n"] == 1
    err, out = ok("hub_strategy_summary", token="cara-test")
    assert not err and out["n"] == 1 and "Landlords pay for bots" in out["text"]
    err, _ = ok("hub_strategy_draft", {"summary": "Mine."}, token="cara-test")
    assert err
    err, out = ok("hub_goal_create", {"owner": "ana", "title": "Sign 10 landlords",
                                      "bet_id": get(api, "strategy")["bets"][0]["id"]})
    assert not err and out["bet_id"]


def test_an_unpublished_vision_stays_out_of_the_company_history_until_publish(api):
    ready(api)
    write(api, vision="Every landlord on Acme.")
    publish(api)
    seen = lambda: [e["new"] for e in get(api, "company/history", token="cara-test")["events"] if e["field"] == "vision"]
    assert seen() == ["Every landlord on Acme."]                     # before v1 the draft's vision was the team's
    write(api, vision="Every small landlord on Acme.")
    assert seen() == ["Every landlord on Acme."]
    assert get(api, "company", token="cara-test")["company"]["vision"] == "Every landlord on Acme."
    sql = lambda: post(api, "sql", {"sql": "SELECT vision FROM company_profile"}, token="cara-test")["rows"][0][0]
    assert sql() == "Every landlord on Acme."
    publish(api, "Second")
    assert seen()[0] == "Every small landlord on Acme." and sql() == "Every small landlord on Acme."


def test_company_profile_vision_follows_the_published_vision_after_publish_and_at_start(api):
    ready(api)
    write(api, vision="Every landlord on Acme.")
    sql = lambda: post(api, "sql", {"sql": "SELECT vision FROM company_profile"}, token="cara-test")["rows"][0][0]
    store = api.app.state.store
    with store.transaction() as c:                # what a release before this one left: the pre-strategy sentence
        c.execute("UPDATE company_profile SET vision='Rentals, simpler.'")
    publish(api)                                  # the first publish, the same vision as the draft
    assert sql() == "Every landlord on Acme."
    with store.transaction() as c:
        c.execute("UPDATE company_profile SET vision='Rentals, simpler.'")
    write(api, summary="Acme runs small rentals with bots.")
    publish(api, "Second")                        # a later publish, the vision unchanged
    assert sql() == "Every landlord on Acme."
    with store.transaction() as c:
        c.execute("UPDATE company_profile SET vision='Rentals, simpler.'")
        S.ensure(c)                               # every start (backend/store.py)
    assert sql() == "Every landlord on Acme."


def test_hub_sql_shows_strategy_proposals_only_to_the_owner_and_the_bot_that_proposed_them(api):
    strategy_token = with_strategy_bot(api)
    one = ready(api)
    add_bet(api, "Landlords refer landlords")
    post(api, "goals", {"title": "Sign 10 landlords", "owner": "cara", "bet_id": one["id"]})
    publish(api)
    post(api, f"strategy/bets/{one['id']}", {"status": "retired"})
    publish(api, "Second")                                              # files a strategy_realign card
    post(api, "goal-proposals", {"kind": "strategy_draft", "reason": "r", "payload": {
        "sections": {"not": "Unannounced: no franchise deals."}}}, token=strategy_token)
    query = {"sql": "SELECT kind, payload FROM goal_proposals WHERE kind LIKE 'strategy_%' ORDER BY kind"}
    assert [r[0] for r in post(api, "sql", query)["rows"]] == ["strategy_draft", "strategy_realign"]
    for token in ("ben-test", "cara-test"):
        assert post(api, "sql", query, token=token)["rows"] == []
    assert [r[0] for r in post(api, "sql", query, token=strategy_token)["rows"]] == ["strategy_draft"]
