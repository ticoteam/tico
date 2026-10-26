"""Goal suggestion cards say what yes does (spec "Clear CEO Bot suggestion cards", Dev 530): each proposal the page
draws carries `card`, what the server read to say it plainly (backend/goals.py card); an Add a KPI suggestion may be
confirmed with the person's own target; and the CEO Bot's Needs you task uses the card's words, not "Confirm or reject"."""

from backend import ceo_bot
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


def test_the_ceo_bots_task_uses_the_cards_words_and_an_old_one_is_reworded_once(api):
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
    assert tasks, "the CEO Bot asked"
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


def test_a_flags_task_says_what_the_ceo_bot_noticed(api):
    goal_manager(api, level=2)
    goal, _ = goal_with_kpi(api)
    flag = propose(api, "flag", {"issue": "vague"}, goal["id"])
    sync(api)
    [task] = [a for a in asks(api) if a["ref"] == flag["id"]]
    with api.app.state.store.read() as c:
        name = ceo_bot.name(c)
    assert task["title"] == f"The {name} noticed something on Raise activation"
