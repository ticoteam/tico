"""Goal suggestion cards say what yes does (spec "Clear Strategy Bot suggestion cards", Dev 530): each proposal the page
draws carries `card`, what the server read to say it plainly (backend/goals.py card); an Add a KPI suggestion may be
confirmed with the person's own target; and the Strategy Bot's Needs you task uses the card's words, not "Confirm or
reject"."""

from backend import strategy_bot
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_goal_asks import asks, goal_manager, sync
from backend.tests.test_kpis import goal_with_kpi

IMPROVE = {"kind": "improve", "baseline": 10, "target": 25, "deadline": "2099-12-31"}


def propose(api, kind, payload, goal_id=None, kpi_id=None, token="ana-test"):
    return post(api, "goal-proposals", {"kind": kind, "goal_id": goal_id, "kpi_id": kpi_id, "payload": payload,
                                        "reason": "No measure yet"}, token=token)["proposal"]


def tree_card(api, proposal_id, token="ana-test"):
    return next(p for p in get(api, "goals/tree", token)["proposals"] if p["id"] == proposal_id)["card"]


def links(api, goal_id):
    return {k["name"]: k["link"] for k in get(api, f"goals/{goal_id}")["goal"]["kpis"]}


def test_an_add_a_kpi_card_names_the_measure_says_what_it_counts_and_that_it_has_no_target_or_source(api):
    goal_manager(api, level=2)          # above Passive, so Needs you lists suggestions not yet asked
    goal, activation = goal_with_kpi(api)
    new = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos", "definition": "Demos held, per week"}}, goal["id"])
    card = tree_card(api, new["id"])
    assert card["goal_title"] == "Raise activation"
    assert card["measure"] == {"name": "Weekly demos", "definition": "Demos held, per week", "source_note": "",
                               "unit": "", "direction": "up", "existing": False}
    assert card["target_label"] == ""                       # the card reads "No target yet"
    # One that reuses a measure names it and says what it counts, never "existing KPI".
    other = post(api, "goals", {"title": "Keep customers", "owner": "ana"})["goal"]
    reuse = propose(api, "goal_kpi", {"kpi_id": activation["id"], "target": IMPROVE}, other["id"])
    card = tree_card(api, reuse["id"])
    assert (card["measure"]["name"], card["measure"]["definition"], card["measure"]["existing"]) == (
        "Activation", "Signed-up accounts that finish setup", True)
    assert card["target_label"].startswith("→ 25%")
    # Needs you draws the same card.
    sync(api)
    [item] = [i for i in get(api, "goals/needs-you")["items"] if i.get("proposal", {}).get("id") == reuse["id"]]
    assert item["proposal"]["card"]["measure"]["name"] == "Activation"


def test_new_definition_and_new_wording_cards_carry_now_and_proposed(api):
    goal, kpi = goal_with_kpi(api)
    change = propose(api, "kpi_definition", {"definition": "Finish setup within 7 days"}, kpi_id=kpi["id"])
    assert get(api, f"kpis/{kpi['id']}")["proposals"][0]["card"]["changes"] == [
        {"label": "Definition", "now": "Signed-up accounts that finish setup", "proposed": "Finish setup within 7 days"}]
    wording = propose(api, "goal_wording", {"title": "Raise activation to 70%"}, goal["id"])
    assert tree_card(api, wording["id"])["changes"] == [
        {"label": "Title", "now": "Raise activation", "proposed": "Raise activation to 70%"}]
    target = propose(api, "kpi_target", {"kind": "maintain", "min": 50}, goal["id"], kpi["id"])
    card = tree_card(api, target["id"])
    assert card["now_label"].startswith("→ 70%") and card["target_label"] != card["now_label"]


def test_add_measure_with_the_persons_target_uses_it_and_blank_adds_it_with_none(api):
    goal, _ = goal_with_kpi(api)
    with_target = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos"}}, goal["id"])
    post(api, f"goal-proposals/{with_target['id']}/decide", {"decision": "confirm", "target": IMPROVE})
    link = links(api, goal["id"])["Weekly demos"]
    assert (link["kind"], link["target"], link["deadline"]) == ("improve", 25, "2099-12-31")
    blank = propose(api, "goal_kpi", {"kpi": {"name": "Trials started"}}, goal["id"])
    post(api, f"goal-proposals/{blank['id']}/decide", {"decision": "confirm"})
    assert links(api, goal["id"])["Trials started"]["kind"] == "none"


def test_a_target_is_checked_as_a_links_target_and_refused_on_any_other_kind_or_a_reject(api):
    goal, kpi = goal_with_kpi(api)
    add = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos", "direction": "down"}}, goal["id"])
    # The measure should go down: a target above its baseline is wrong, as it is on a link; nothing is made.
    wrong = post(api, f"goal-proposals/{add['id']}/decide", {"decision": "confirm", "target": IMPROVE}, expected=422)
    assert "wrong side" in str(wrong)
    post(api, f"goal-proposals/{add['id']}/decide", {"decision": "confirm", "target": {"kind": "improve", "target": 5}},
         expected=422)                                       # no deadline
    assert "Weekly demos" not in links(api, goal["id"])
    post(api, f"goal-proposals/{add['id']}/decide", {"decision": "reject", "target": {"kind": "maintain", "max": 3}},
         expected=422)
    target = propose(api, "kpi_target", {"kind": "maintain", "min": 50}, goal["id"], kpi["id"])
    post(api, f"goal-proposals/{target['id']}/decide", {"decision": "confirm", "target": {"kind": "maintain", "min": 60}},
         expected=422)
    # Still pending, both: a refused decision decides nothing.
    assert {p["id"] for p in get(api, "goals/tree")["proposals"]} >= {add["id"], target["id"]}
    post(api, f"goal-proposals/{add['id']}/decide", {"decision": "confirm", "target": {"kind": "maintain", "max": 3}})
    assert links(api, goal["id"])["Weekly demos"]["max"] == 3


def test_a_flag_confirmed_changes_nothing_else(api):
    goal, _ = goal_with_kpi(api)
    flag = propose(api, "flag", {"issue": "duplicate", "note": "Same words as Keep customers"}, goal["id"])
    before = get(api, f"goals/{goal['id']}")["goal"]
    done = post(api, f"goal-proposals/{flag['id']}/decide", {"decision": "confirm"})["proposal"]
    after = get(api, f"goals/{goal['id']}")["goal"]
    assert done["status"] == "confirmed"
    assert (after["title"], after["body"], after["status"], links(api, goal["id"])) == (
        before["title"], before["body"], before["status"], {k["name"]: k["link"] for k in before["kpis"]})


def test_the_strategy_bots_task_uses_the_cards_words_and_an_old_one_is_reworded_once(api):
    goal_manager(api, level=2)
    goal, kpi = goal_with_kpi(api)
    other = post(api, "goals", {"title": "Keep customers", "owner": "ana"})["goal"]
    made = {
        propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos"}}, goal["id"])["id"]: "Add the measure Weekly demos to Raise activation?",
        propose(api, "goal_kpi", {"kpi_id": kpi["id"]}, other["id"])["id"]: "Add the measure Activation to Keep customers?",
        propose(api, "kpi_target", {"kind": "maintain", "min": 50}, goal["id"], kpi["id"])["id"]: "Use a new Activation target on Raise activation?",
        propose(api, "kpi_definition", {"definition": "Within 7 days"}, kpi_id=kpi["id"])["id"]: "Use a new definition for Activation?",
        propose(api, "goal_wording", {"title": "Raise activation to 70%"}, goal["id"])["id"]: "Use new wording for Raise activation?",
    }
    sync(api)
    tasks = {a["ref"]: a for a in asks(api) if a["kind"] == "proposal"}
    assert {ref: tasks[ref]["title"] for ref in made if ref in tasks} == {ref: t for ref, t in made.items() if ref in tasks}
    assert tasks, "the Strategy Bot asked"
    for task in tasks.values():
        assert "Confirm or reject" not in task["title"] + task["body"] and "existing KPI" not in task["body"]
        assert task["body"].endswith("Decide it under Needs you on the Goals page.")
    # A task filed before, in the old words, takes the card's words on the next pass, once.
    ref, task = next(iter(tasks.items()))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title=?, body=? WHERE id=?", ("Confirm or reject a new KPI for Raise activation",
                                                                    "Confirm or reject it.", task["id"]))
    sync(api)
    again = next(a for a in asks(api) if a["ref"] == ref)
    assert (again["id"], again["title"], again["body"]) == (task["id"], made[ref], task["body"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", ("My own words", task["id"]))
    sync(api)
    assert next(a for a in asks(api) if a["ref"] == ref)["title"] == "My own words"     # a person's title stays


def test_a_flags_task_says_what_the_strategy_bot_noticed(api):
    goal_manager(api, level=2)
    goal, _ = goal_with_kpi(api)
    flag = propose(api, "flag", {"issue": "vague"}, goal["id"])
    sync(api)
    [task] = [a for a in asks(api) if a["ref"] == flag["id"]]
    with api.app.state.store.read() as c:
        name = strategy_bot.name(c)
    assert task["title"] == f"The {name} noticed something on Raise activation"


def test_a_persons_target_replaces_the_one_proposed_and_none_clears_it(api):
    goal, kpi = goal_with_kpi(api)
    proposed = {"kind": "improve", "baseline": 10, "target": 40, "deadline": "2099-06-30"}
    new = propose(api, "goal_kpi", {"kpi": {"name": "Weekly demos"}, "target": proposed}, goal["id"])
    post(api, f"goal-proposals/{new['id']}/decide", {"decision": "confirm", "target": IMPROVE})
    link = links(api, goal["id"])["Weekly demos"]
    assert (link["target"], link["deadline"]) == (25, "2099-12-31")
    # Reusing a measure, the person's "no target" clears the one the Strategy Bot proposed.
    other = post(api, "goals", {"title": "Keep customers", "owner": "ana"})["goal"]
    reuse = propose(api, "goal_kpi", {"kpi_id": kpi["id"], "target": IMPROVE}, other["id"])
    post(api, f"goal-proposals/{reuse['id']}/decide", {"decision": "confirm", "target": {"kind": "none"}})
    assert links(api, other["id"])["Activation"]["kind"] == "none"


def test_an_empty_body_in_new_wording_shows_the_description_it_clears(api):
    goal, _ = goal_with_kpi(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE goals SET body=? WHERE id=?", ("Half of sign-ups finish setup", goal["id"]))
    wording = propose(api, "goal_wording", {"title": "Raise activation to 70%", "body": ""}, goal["id"])
    assert tree_card(api, wording["id"])["changes"] == [
        {"label": "Title", "now": "Raise activation", "proposed": "Raise activation to 70%"},
        {"label": "Description", "now": "Half of sign-ups finish setup", "proposed": ""}]
    post(api, f"goal-proposals/{wording['id']}/decide", {"decision": "confirm"})
    assert get(api, f"goals/{goal['id']}")["goal"]["body"] == ""      # what the card said yes does


# A member may not read the inbox bot (the base fixture: it is Ana's alone), so nothing of its goals or measures reaches
# her through a suggestion: not its name, what it counts, its source, its unit, nor the target it has now.
SECRETS = ("NDAs waiting", "Unsigned NDAs in the mail", "Mailbox count", "Close all NDAs")


def inbox_measure(api):
    hidden = post(api, "goals", {"title": "Close all NDAs by Q4", "owner": "bot:inbox", "top": True})["goal"]
    kpi = post(api, "kpis", {"name": "NDAs waiting", "unit": "docs", "direction": "down", "cadence": "daily",
                             "definition": "Unsigned NDAs in the mail", "source_note": "Mailbox count",
                             "goal_id": hidden["id"]})["kpi"]
    return hidden, kpi


def leaks(response):
    return [s for s in SECRETS if s in str(response)]


def test_a_suggestion_about_a_hidden_bots_measure_or_goal_shows_none_of_it(api):
    hidden, secret = inbox_measure(api)
    definition = propose(api, "kpi_definition", {"definition": "Within 3 days"}, kpi_id=secret["id"])
    assert definition["id"] in {p["id"] for p in get(api, "goals/tree")["proposals"]}        # Ana reads it
    tree = get(api, "goals/tree", "cara-test")
    assert definition["id"] not in {p["id"] for p in tree["proposals"]} and leaks(tree) == []
    # A measure she reads, on a goal she may not: its target suggestion there is that goal's.
    goal, kpi = goal_with_kpi(api)
    post(api, f"goals/{hidden['id']}/kpis", {"kpi_id": kpi["id"], "kind": "maintain", "min": 30})
    target = propose(api, "kpi_target", {"kind": "maintain", "min": 50}, hidden["id"], kpi["id"])
    [mine] = [p for p in get(api, f"kpis/{kpi['id']}")["proposals"] if p["id"] == target["id"]]
    assert mine["card"]["goal_title"] == "Close all NDAs by Q4" and mine["card"]["now_label"]
    detail = get(api, f"kpis/{kpi['id']}", "cara-test")
    assert target["id"] not in {p["id"] for p in detail["proposals"]} and leaks(detail) == []


def test_needs_you_does_not_name_a_hidden_goal_when_the_reader_can_edit_its_kpi(api):
    goal_manager(api, level=2)  # Above Passive, where unfiled proposals are shown in Needs you.
    visible, kpi = goal_with_kpi(api, owner="cara", token="cara-test")
    hidden = post(api, "goals", {"title": "Close all NDAs by Q4", "owner": "bot:inbox", "top": True})["goal"]
    post(api, f"goals/{hidden['id']}/kpis", {"kpi_id": kpi["id"], "kind": "maintain", "min": 30})
    secret = propose(api, "kpi_target", {"kind": "maintain", "min": 50}, hidden["id"], kpi["id"])
    visible_proposal = propose(api, "kpi_target", {"kind": "maintain", "min": 60}, visible["id"], kpi["id"])

    needs_you = get(api, "goals/needs-you", "cara-test")
    proposal_ids = {item.get("proposal", {}).get("id") for item in needs_you["items"]}
    assert visible_proposal["id"] in proposal_ids
    assert secret["id"] not in proposal_ids and "Close all NDAs by Q4" not in str(needs_you)


def test_task_filing_skips_a_hidden_goal_when_its_decider_cannot_read_the_owner_bot(api):
    goal_manager(api, level=2)
    hidden = post(api, "goals", {"title": "Close all NDAs by Q4", "owner": "bot:cpo", "top": True})["goal"]
    proposal = propose(api, "goal_wording", {"title": "Close all NDAs this month"}, hidden["id"])
    # CPO's nearest person is Ben. He is a regular member and cannot read CPO's records.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
        from backend import goal_asks as GA
        assert GA._person(c, "bot:cpo", api.app.state.auth.owner_id(c), GA._org(c)) == "human:ben"
        # Model the per-person Read grant: the assigned person has no Read on CPO.
        GA.sync(c, api.app.state.auth.owner_id(c), reads=lambda person, owner: False)

    assert not [task for task in asks(api) if task["ref"] == proposal["id"]]
    assert "Close all NDAs by Q4" not in str(asks(api))


def sync_reading(api):
    """The Strategy Bot's pass as the app runs it: told whose measures each person may read."""
    from backend import goal_asks as GA
    with api.app.state.store.transaction() as c:
        return GA.sync(c, api.app.state.auth.owner_id(c), reads=GA.readers(c, api.app.state.auth))


def test_reusing_a_hidden_bots_measure_on_her_goal_neither_names_nor_describes_it(api):
    goal_manager(api, level=2)
    _, secret = inbox_measure(api)
    hers = post(api, "goals", {"title": "Keep customers", "owner": "cara"}, token="cara-test")["goal"]
    reuse = propose(api, "goal_kpi", {"kpi_id": secret["id"], "target": {"kind": "maintain", "max": 3}}, hers["id"])
    [item] = [i for i in get(api, "goals/needs-you", "cara-test")["items"] if i.get("proposal", {}).get("id") == reuse["id"]]
    measure = item["proposal"]["card"]["measure"]
    assert measure["hidden"] and (measure["unit"], measure["definition"], measure["source_note"]) == ("", "", "")
    assert leaks(item) == [] and "docs" not in str(item["proposal"]["card"])
    # Her task says a measure is to be added, not which; Ana's, for her own goal, names it and is hers alone.
    anas = post(api, "goals", {"title": "Sign faster", "owner": "ana"})["goal"]
    named = propose(api, "goal_kpi", {"kpi_id": secret["id"]}, anas["id"])
    sync_reading(api)
    tasks = {a["ref"]: a for a in asks(api)}
    assert reuse["id"] not in tasks            # she may not add it, so she is not asked to
    assert tasks[named["id"]]["title"] == "Add the measure NDAs waiting to Sign faster?"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT private FROM tasks WHERE id=?", (tasks[named["id"]]["id"],)).fetchone()[0] == 1


def test_a_hidden_bots_measure_suggested_for_her_goal_stays_hidden_however_it_is_decided(api):
    goal_manager(api, level=2)          # Needs you lists suggestions and the Strategy Bot's tasks
    hidden, secret = inbox_measure(api)
    post(api, f"goals/{hidden['id']}/kpis/{secret['id']}", {"kind": "maintain", "max": 3})
    hers = post(api, "goals", {"title": "Keep customers", "owner": "cara"}, token="cara-test")["goal"]
    reuse = propose(api, "goal_kpi", {"kpi_id": secret["id"], "target": {"kind": "maintain", "max": 3}}, hers["id"])
    # She may not add a measure she may not read, as she may not link it by hand.
    post(api, f"goal-proposals/{reuse['id']}/decide", {"decision": "confirm"}, token="cara-test", expected=403)
    post(api, f"goals/{hers['id']}/kpis", {"kpi_id": secret["id"], "kind": "none"}, token="cara-test", expected=404)
    # Nor may the owner, who reads it: everyone reads her goal, its KPI lines and its colour's reason with them.
    post(api, f"goal-proposals/{reuse['id']}/decide", {"decision": "confirm"}, expected=403)
    post(api, f"goals/{hers['id']}/kpis", {"kpi_id": secret["id"], "kind": "maintain", "max": 3}, expected=403)
    post(api, "kpis", {"name": "Inbox depth", "owner": "inbox", "goal_id": hers["id"]}, expected=403)
    anas = post(api, "goals", {"title": "Sign faster", "owner": "ana"})["goal"]
    post(api, f"goals/{anas['id']}/kpis", {"kpi_id": secret["id"], "kind": "none"}, expected=403)
    assert "NDAs waiting" not in links(api, hers["id"])
    assert reuse["id"] in {p["id"] for p in get(api, "goals/tree")["proposals"]}      # still waiting, undecided
    # A fresh reading past the target writes the inbox goal's colour reason; nothing of it reaches her.
    import datetime
    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    post(api, f"kpis/{secret['id']}/readings", {"value": 7, "period_start": yesterday, "period_end": yesterday})
    with api.app.state.store.read() as c:
        assert "NDAs waiting" in str(c.execute("SELECT status_note FROM goals WHERE id=?", (hidden["id"],)).fetchone()[0])
    sync(api)
    for path in ("goals", "goals?all=true", "goals/tree", f"goals/{hers['id']}", "goals/needs-you"):
        assert leaks(get(api, path, "cara-test")) == [], path
    assert [a for a in asks(api) if a.get("owner") == "human:cara" and leaks(a)] == []
    # A link made before links were checked shows no line of the KPI to her either.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO goal_kpis (id, goal_id, kpi_id, kind, created_at, created_by, updated_at, updated_by) "
                  "VALUES ('old-link', ?, ?, 'none', ?, 'human:ana', ?, 'human:ana')",
                  (hers["id"], secret["id"], today, today))
    [mine] = [g for g in get(api, "goals/tree", "cara-test")["goals"] if g["id"] == hers["id"]]
    assert mine["kpis"] == [] and get(api, f"goals/{hers['id']}", "cara-test")["goal"]["kpis"] == []
    # She may still say no to one waiting.
    post(api, f"goal-proposals/{reuse['id']}/decide", {"decision": "reject"}, token="cara-test")


def test_a_bots_measure_goes_on_its_own_goal_and_not_with_a_goal_handed_to_a_person(api):
    hidden, secret = inbox_measure(api)                     # made on the inbox bot's goal: allowed
    assert "NDAs waiting" in links(api, hidden["id"])
    post(api, f"goals/{hidden['id']}", {"owner": "cara"}, expected=403)
    assert get(api, f"goals/{hidden['id']}")["goal"]["owner"] == "bot:inbox"


def test_a_target_suggestion_does_not_link_a_hidden_bots_measure_to_her_goal(api):
    _, secret = inbox_measure(api)
    hers = post(api, "goals", {"title": "Keep customers", "owner": "cara"}, token="cara-test")["goal"]
    target = propose(api, "kpi_target", {"kind": "maintain", "max": 3}, hers["id"], secret["id"])
    post(api, f"goal-proposals/{target['id']}/decide", {"decision": "confirm"}, expected=403)
    assert "NDAs waiting" not in links(api, hers["id"])
    post(api, f"goal-proposals/{target['id']}/decide", {"decision": "reject"})


def old_link(api, goal_id, kpi_id, **target):
    """A link made before links were checked: nothing makes one now (see exposes in backend/goal_routes.py)."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO goal_kpis (id, goal_id, kpi_id, kind, max, created_at, created_by, updated_at, updated_by) "
                  "VALUES (?, ?, ?, 'maintain', ?, ?, 'human:ana', ?, 'human:ana')",
                  ("old-" + goal_id, goal_id, kpi_id, target.get("max"), now, now))


def test_an_old_link_to_a_hidden_measure_names_it_in_no_colour_reason_or_suggestion_she_reads(api):
    _, secret = inbox_measure(api)
    auto = post(api, "goals", {"title": "Keep customers", "owner": "cara"}, token="cara-test")["goal"]
    manual = post(api, "goals", {"title": "Grow referrals", "owner": "cara"}, token="cara-test")["goal"]
    post(api, f"goals/{manual['id']}/status", {"status": "green", "note": "On track"}, token="cara-test")
    for goal in (auto, manual):
        old_link(api, goal["id"], secret["id"], max=3)
    import datetime
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    post(api, f"kpis/{secret['id']}/readings", {"value": 7, "period_start": yesterday, "period_end": yesterday})
    post(api, "goals/refresh", {"goal_ids": [auto["id"], manual["id"]]})
    with api.app.state.store.read() as c:
        said = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, status_note, suggest_note FROM goals")}
    assert "NDAs waiting" in str(said[auto["id"]][0]) and "NDAs waiting" in str(said[manual["id"]][1])
    for path in ("goals", "goals?all=true", "goals/tree", f"goals/{auto['id']}", f"goals/{manual['id']}"):
        assert leaks(get(api, path, "cara-test")) == [], path
    assert "NDAs waiting" in str(get(api, "goals/tree"))          # the owner reads it
