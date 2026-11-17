"""A bot's question to a person (Tasks spec "Bots ask people a question with choices"): its shape is checked in plain
code before any model call (backend/ask_shape.py, AC-1, AC-2), the built-in rule "Asks to a person" is added once and
judged only for such a question, in the one call the check already makes (org_rules.ensure_builtin, AC-7 to AC-9), and
a bot's task for a person that is really a decision is refused (backend/ask_ingest.py, AC-6)."""

import importlib

import pytest

from backend import ask_ingest as A
from backend import ask_shape as S
from backend import org_rules as OR
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.counsel import store_file
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    color_rule,
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

WHY = "The launch note goes out under the company's name on Friday."
GOOD = {
    "line": "Which tone should the launch note take?",
    "why": WHY,
    "choices": ["Plain", "Playful"],
    "details": ["Reads like our docs; safe for customers.", "Stands out; one customer complained last time."],
    "recommend": "Plain",
}


def ask(api, task, body, to="ana", token="ops-test"):
    return api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": to, "version": task["version"], **body},
        headers=headers(token),
    )


def builtin(api):
    return sql(api, "SELECT * FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,))


def version(api, task):
    return sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"]


def refusals(api):
    return sql(api, "SELECT actor, rule, detail FROM refusals WHERE rule='task_ask_shape' ORDER BY rowid")


# ---- the shape, plain code (AC-1, AC-2) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change,said",
    [
        ({"why": None}, S.NO_WHY),
        ({"why": "   "}, S.NO_WHY),
        ({"choices": list("ABCDE"), "details": list("abcde"), "recommend": "A"}, S.COUNT),
        ({"details": None}, S.NO_DETAIL),
        ({"details": ["Safe.", ""]}, S.NO_DETAIL),
        ({"details": ["Safe."]}, S.NO_DETAIL),
        ({"recommend": None}, S.NO_RECOMMEND),
        ({"recommend": "Loud"}, S.NOT_A_CHOICE),
        ({"choices": None, "details": None}, S.STRAY),
        ({"choices": ["Plain", "plain"], "recommend": "plain"}, S.SAME),
    ],
)
def test_a_badly_shaped_question_to_a_person_is_refused_before_any_call_and_writes_nothing(ruled, change, said):
    api, judge = ruled
    color_rule(api)  # a rule in force, so a call would be made if the ask got that far
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, **change})
    assert r.status_code == 409, r.text
    error = r.json()
    assert error["error"] == "task_ask_shape" and error["detail"].startswith(S.NEEDS) and said in error["detail"], (
        r.text
    )
    assert judge.calls == [], "refused in plain code: no model call"
    assert version(api, task) == task["version"], "nothing written"
    assert sql(api, "SELECT count(*) AS n FROM requirements WHERE line=?", (GOOD["line"],))[0]["n"] == 0
    [refused] = refusals(api)
    assert refused["actor"] == "bot:ops"


def test_one_refusal_names_every_part_an_ask_is_missing(ruled):
    """A bare ask is fixed in one rewrite, not refused once per missing part."""
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {"line": GOOD["line"], "choices": GOOD["choices"]})
    detail = r.json()["detail"]
    assert r.status_code == 409 and all(fix in detail for fix in (S.NO_WHY, S.NO_DETAIL, S.NO_RECOMMEND)), detail


@pytest.mark.parametrize(
    "ref",
    [
        "reports/launch-note.md",
        "bot-ops/drafts/note.md",
        "the draft in my repo",
        # A path dressed as an id (Review 1): only a page, file or task that is there opens.
        "file:reports/reviews/x.html",
        "page:reports/x.html",
        "task:../x",
        "file:/etc/passwd",
        "page:file-not-there",
        "task:no-such-task",
        "task:9223372036854775808",
        "task:99999999999999999999999",
    ],
)
def test_a_question_that_shows_what_a_person_cannot_open_is_refused(ruled, ref):
    api, judge = ruled
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, "show": [ref]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail.endswith("Publish it as a page and show that page.") and ref in detail, detail
    assert judge.calls == []


@pytest.mark.parametrize(
    "ref", ["page:{file}", "page:{file}@1", "file:{file}@1", "task:{task}", "https://example.com/a"]
)
def test_a_question_may_show_a_page_a_file_a_task_or_a_web_address(ruled, ref):
    api, _ = ruled
    task = task_for_ops(api)
    file_id, _ = store_file(api, "The launch note, both tones.", title="Launch note drafts")
    r = ask(api, task, {**GOOD, "show": [ref.format(file=file_id, task=task["id"])]})
    assert r.status_code == 200, r.text


def test_an_archived_file_a_deleted_page_or_a_missing_version_cannot_be_shown_and_a_task_number_can(ruled):
    """Review 2: what is shown must open now, at the version it pins; a task may be named by its number."""
    api, _ = ruled
    task = task_for_ops(api)
    archived, _ = store_file(api, "Old draft.", title="Old draft")
    deleted, _ = store_file(api, "Gone page.", title="Gone page")
    kept, _ = store_file(api, "The launch note.", title="Launch note")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET archived=1 WHERE id=?", (archived,))
        c.execute(
            "INSERT INTO pages(file_id, deleted_at, created_at, updated_at) VALUES (?,?,?,?)",
            (deleted, "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z", "2026-11-13T00:00:00Z"),
        )
        c.execute("UPDATE tasks SET number=? WHERE id=?", (9350, task["id"]))
    for ref in (f"file:{archived}", f"page:{deleted}", f"file:{kept}@99"):
        r = ask(api, task, {**GOOD, "show": [ref]})
        assert r.status_code == 409 and r.json()["detail"].endswith("show that page."), (ref, r.text)
    assert ask(api, task, {**GOOD, "show": ["task:9350"]}).status_code == 200


def test_a_well_shaped_question_is_asked_and_its_why_lines_and_recommendation_are_on_the_ask(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, GOOD)
    assert r.status_code == 200, r.text
    rid = r.json()["requirement_id"]
    view = get(api, f"tasks/{task['id']}")
    view = view.get("task", view)
    [entry] = [e for e in view["to_leave"] if e["requirement_id"] == rid]
    assert entry["choices"] == GOOD["choices"]
    assert entry["brief"] == {"why": WHY, "details": GOOD["details"], "recommend": "Plain"}
    assert refusals(api) == []


def test_a_question_with_no_choices_needs_only_its_why(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {"line": "What is this team for, in a sentence or two?", "why": "Every bot plans from it."})
    assert r.status_code == 200, r.text


# ---- an ask's short title (decision view part 2, task #390) --------------------------------------------------------


def needs_you_ask(api, rid, token="ana-test"):
    return next(
        i["ask"] for i in get(api, "needs-you", token)["items"] if (i.get("ask") or {}).get("requirement_id") == rid
    )


def test_an_asks_short_title_is_on_its_entry_and_in_needs_you(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, "title": "  Launch note tone  "})
    assert r.status_code == 200, r.text
    rid = r.json()["requirement_id"]
    view = get(api, f"tasks/{task['id']}")
    view = view.get("task", view)
    [entry] = [e for e in view["to_leave"] if e["requirement_id"] == rid]
    assert entry["title"] == "Launch note tone"
    assert entry["line"] == GOOD["line"], "the full question stays the line"
    assert needs_you_ask(api, rid)["title"] == "Launch note tone"
    post(api, f"tasks/{task['id']}/asks/{rid}/answer", {"value": "Plain", "version": version(api, task)})
    # The bot's next ask replaces it; the answered one stays on the task as decided, title and all.
    again = ask(api, {**task, "version": version(api, task)}, {**GOOD, "line": "Which day should it go out?"})
    assert again.status_code == 200, again.text
    view = get(api, f"tasks/{task['id']}")
    view = view.get("task", view)
    [answered] = [e for e in view["to_leave"] if e["requirement_id"] == rid]
    assert answered.get("replaced") and answered["title"] == "Launch note tone", "a decided ask keeps its title"


def test_an_asks_title_over_60_characters_is_refused_and_nothing_is_asked(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, "title": "x" * 61})
    assert r.status_code == 422, r.text
    assert sql(api, "SELECT count(*) AS n FROM requirements WHERE line=?", (GOOD["line"],))[0]["n"] == 0
    assert ask(api, task, {**GOOD, "title": "x" * 60}).status_code == 200


@pytest.mark.parametrize("title", [None, "   "])
def test_an_ask_without_a_title_is_asked_as_before_and_carries_none(ruled, title):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, **({} if title is None else {"title": title})})
    assert r.status_code == 200, r.text
    rid = r.json()["requirement_id"]
    assert "title" not in needs_you_ask(api, rid)


def test_the_shape_is_a_bots_question_to_a_person_only(ruled):
    """A bot asking a bot, and a person asking anyone, keep asking as before."""
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(
        api,
        task,
        {"line": "Which tone should the launch note take?", "choices": ["A", "B", "C", "D", "E"]},
        to="finance",
    )
    assert r.status_code == 200, r.text
    other = post(api, "tasks", {"owner": "ana", "title": "Pick the launch tone", "body": "Two drafts."})
    r = ask(api, other, {"line": "Which tone should the launch note take?"}, to="ben", token="ana-test")
    assert r.status_code == 200, r.text


# ---- the shape of every ask a bot puts to a person (Flow simplified, change 6) ---------------------------------------

# Real asks that pointed at text the person could not see from Flow, or put a path in front of them.
UNSEEN = (
    "Can I go ahead with my suggested answer to each of the five Growth questions on this task?",
    "Five Listening questions are in the comment above. Yes to all, or answer by number?",
)
LIST = (
    "Three things before I write the spec:\n1. Guest checkout, yes or no?\n2. Keep carts 30 days?\n3. Email a reminder?"
)


@pytest.mark.parametrize(
    "change,said",
    [
        ({"line": UNSEEN[0]}, '"on this task"'),
        ({"line": UNSEEN[1]}, '"comment above"'),
        (
            {"why": "Background: knowledge/onboarding-in-flow.md"},
            '--why has a file path: "knowledge/onboarding-in-flow.md"',
        ),
        ({"line": "Ship the fix in commit a7cf7af today?"}, 'has an id or hash: "a7cf7af"'),
        ({"line": "Which launch note tone? " + "x" * 120}, S.SHORT_LINE),
        ({"why": "It goes out Friday.\nUnder our name."}, S.WHY_ONE_LINE),
        (
            {
                "line": "See above: OK to send?",
                "kind": "ok",
                "why": None,
                "choices": None,
                "details": None,
                "recommend": None,
            },
            '"See above"',
        ),
    ],
)
def test_an_ask_that_breaks_its_shape_is_refused_with_the_fix_before_any_call(ruled, change, said):
    api, judge = ruled
    color_rule(api)  # a rule in force, so a call would be made if the ask got that far
    task = task_for_ops(api)
    r = ask(api, task, {**GOOD, **change})
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "task_ask_shape" and said in r.json()["detail"], r.text
    assert judge.calls == [], "refused in plain code: no model call"
    assert version(api, task) == task["version"], "nothing written"


@pytest.mark.parametrize("line", ["Is a price above $50 OK?", LIST])
def test_an_ask_that_stands_on_its_own_is_asked_and_a_list_may_follow_its_first_line(ruled, line):
    api, _ = ruled
    task = task_for_ops(api)
    r = ask(api, task, {"line": line, "why": WHY})
    assert r.status_code == 200, r.text
    assert refusals(api) == []


def test_every_path_that_asks_a_person_checks_the_shape_whatever_the_bot_was_told(ruled):
    """H.task_ask is where every ask is made (the asks routes, OKs, browser help, built-in bots), so a path that skips
    the route's early check is refused there too."""
    api, _ = ruled
    task = task_for_ops(api)
    with api.app.state.store.transaction() as c:
        for line, why in (
            (UNSEEN[1], None),
            ("Which onboarding flow first?", "Background: knowledge/onboarding-in-flow.md"),
        ):
            with pytest.raises(H.Refused) as refused:
                H.task_ask(
                    c, "bot:ops", task["id"], "human:ana", line, seen_version=task["version"], brief={"why": why}
                )
            assert refused.value.rule == "lint"
        made = H.task_ask(c, "bot:ops", task["id"], "human:ana", LIST, seen_version=task["version"], brief={"why": WHY})
        assert made["requirement_id"]


# ---- the built-in rule (AC-7, AC-8, AC-9) ---------------------------------------------------------------------------


def test_asks_to_a_person_is_on_once_and_a_team_that_turns_it_off_keeps_it_off(api):
    [found] = builtin(api)
    assert found["status"] == "active" and found["kind"] == "internal" and found["if_broken"] == "block"
    assert found["where_terms"] == OR.ASKS_ONLY
    with api.app.state.store.transaction() as c:
        assert OR.ensure_builtin(c) is None, "added once"
        c.execute("UPDATE org_rules SET status='retired', text='Edited by the team' WHERE id=?", (found["id"],))
    # A restart runs every ensure again.
    with api.app.state.store.transaction() as c:
        assert OR.ensure_builtin(c) is None
    assert builtin(api) == []
    [kept] = sql(api, "SELECT status, text FROM org_rules WHERE id=?", (found["id"],))
    assert kept == {"status": "retired", "text": "Edited by the team"}


def test_the_stored_built_in_rule_stops_requiring_choices_unless_the_team_edited_it(api):
    migration = importlib.import_module("backend.migrations.20261116234252_asks_to_a_person_choices_optional")
    assert migration.NOW == OR.ASKS_TO_A_PERSON, "a team set up now gets the words the migration writes"
    [found] = builtin(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE org_rules SET text=? WHERE id=?", (migration.EARLIER, found["id"]))
        edited = dict(found, id=H.new_id(), text=migration.EARLIER + " Name the customer.")
        c.execute(
            f"INSERT INTO org_rules ({', '.join(edited)}) VALUES ({', '.join('?' * len(edited))})",
            tuple(edited.values()),
        )
        migration.up(c)
        migration.up(c)  # again: nothing more changes
    [now] = sql(api, "SELECT text, version FROM org_rules WHERE id=?", (found["id"],))
    assert now == {"text": OR.ASKS_TO_A_PERSON, "version": found["version"] + 1}
    [kept] = sql(api, "SELECT text, version FROM org_rules WHERE id=?", (edited["id"],))
    assert kept == {"text": edited["text"], "version": found["version"]}


def test_the_rule_judges_a_bots_question_to_a_person_with_all_its_choices_in_one_call(ruled):
    api, judge = ruled
    judge.needles = {OR.ASKS_TO_A_PERSON: "**"}  # it reads Markdown as breaking the rule
    task = task_for_ops(api)
    four = {
        **GOOD,
        "choices": ["Plain", "Playful", "Formal", "Short"],
        "details": ["Safe.", "Stands out.", "Reads like a contract.", "Two lines only."],
    }
    r = ask(api, task, four)
    assert r.status_code == 200, r.text
    [call] = judge.calls  # AC-9: one ask with 4 choices is 1 rule-check call
    assert [rule_row["text"] for rule_row in call["state"]["rules"]] == [OR.ASKS_TO_A_PERSON]
    output = call["state"]["output"]
    assert "Why: " + WHY in output
    assert "Plain (recommended): Safe." in output and "Short: Two lines only." in output

    task = task_for_ops(api, "Write the second launch note")
    r = ask(api, task, {**GOOD, "why": "It goes out **Friday** under our name."})
    assert r.status_code == 409 and r.json()["error"] == "task_rule", r.text
    assert OR.ASKS_TO_A_PERSON in r.json()["detail"]  # AC-8: refused with the rule's words
    assert version(api, task) == task["version"]


def test_the_rule_is_not_read_for_any_other_write(ruled):
    """Met only by a bot's question to a person: its comments, done notes and questions to bots make no call, and no
    bot's live reply is hidden for it."""
    api, judge = ruled
    task = task_for_ops(api)
    assert ask(api, task, {"line": "Which draft do you like?"}, to="finance").status_code == 200
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "**Draft one** is up."}, headers=headers("ops-test")
    )
    assert r.status_code in (200, 201), r.text
    assert judge.calls == []
    from backend import org_rule_check

    with api.app.state.store.read() as c:
        assert not org_rule_check.in_force(c) and org_rule_check.in_force(c, True)
        assert not org_rule_check.watched(c, "bot:ops")


def test_a_team_rule_and_the_built_in_one_are_judged_in_the_same_call(ruled):
    api, judge = ruled
    color_rule(api)
    task = task_for_ops(api)
    assert ask(api, task, GOOD).status_code == 200
    [call] = judge.calls
    assert sorted(r["text"] for r in call["state"]["rules"]) == sorted([COLOR, OR.ASKS_TO_A_PERSON])


# ---- a decision filed as a to-do (AC-6) ---------------------------------------------------------------------------


@pytest.mark.parametrize("urgency", [1, 3])
def test_a_decision_filed_for_a_person_is_refused_with_the_ask_it_as_a_question_line(urgency):
    answers = {
        "clear": True,
        "kind": A.DECISION,
        "meets": "line-1",
        "urgency": urgency,
        "recommends": True,
        "covered": "new",
    }
    assert A.decide(answers, 10, 0) == (A.SENT_BACK, "decision")
    assert A.decide({**answers, "kind": A.CHORE}, 10, 0)[0] == A.THROUGH, "a chore still files as today"
    said = A.message("decision", "Chris", 0, "Ben", "chris")
    assert said == (
        "This asks Chris to decide, so ask it as a question on your own task: tico task ask <task> --to chris, with "
        "why it matters, your choices and your recommendation."
    )


# ---- writing to the bot that asked (AC-13) --------------------------------------------------------------------------


def jobs_for(api, bot, message_id):
    return sql(api, "SELECT state FROM jobs WHERE bot_id=? AND message_id=?", (bot, message_id))


def test_a_reply_to_a_bots_question_reaches_that_bot_on_the_task_and_wakes_it_and_the_ask_stays_open(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    rid = ask(api, task, GOOD).json()["requirement_id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Why not both, one per audience?", "ask": rid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    said = r.json()["comment"]
    assert said["recipient_id"] == "bot:ops" and said["body"] == "Why not both, one per audience?"
    assert [j["state"] for j in jobs_for(api, "ops", said["id"])] == ["queued"], "the bot that asked is woken"
    view = get(api, f"tasks/{task['id']}")
    view = view.get("task", view)
    [entry] = [e for e in view["to_leave"] if e["requirement_id"] == rid]
    assert not entry["met"] and entry["answer"] is None, "the ask stays open"


@pytest.mark.parametrize("which", ["missing", "a person's"])
def test_a_reply_names_a_bots_question_on_that_task(ruled, which):
    api, _ = ruled
    task = task_for_ops(api)
    rid = "nope"
    if which == "a person's":
        rid = ask(api, task, {"line": "Which tone?"}, to="ben", token="ana-test").json()["requirement_id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "Hello", "ask": rid}, headers=headers("ana-test")
    )
    assert r.status_code == 422, r.text
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body='Hello'")[0]["n"] == 0


def test_a_reply_cannot_name_a_question_already_answered(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    rid = ask(api, task, GOOD).json()["requirement_id"]
    answered = api.post(
        f"/api/v2/tasks/{task['id']}/asks/{rid}/answer",
        json={"value": "Plain", "version": version(api, task)},
        headers=headers("ana-test"),
    )
    assert answered.status_code == 200, answered.text
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "One more thing", "ask": rid}, headers=headers("ana-test")
    )
    assert r.status_code == 422, r.text
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body='One more thing'")[0]["n"] == 0
    # Undone, the question is open again, and a reply may name it.
    [answer] = sql(api, "SELECT id FROM answers WHERE requirement_id=?", (rid,))
    undone = api.post(f"/api/v2/answers/{answer['id']}/undo", json={}, headers=headers("ana-test"))
    assert undone.status_code == 200, undone.text
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "One more thing", "ask": rid}, headers=headers("ana-test")
    )
    assert r.status_code == 200, r.text


def test_the_person_a_bots_question_waits_on_may_wake_that_bot_and_no_one_else_may(ruled):
    """Ben is not on Ana's task: the question to him is his way to wake the bot; Cara, not asked, cannot."""
    api, _ = ruled
    task = task_for_ops(api)
    rid = ask(api, task, GOOD, to="ben").json()["requirement_id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Plain, but warmer", "ask": rid, "delivery": "steer"},
        headers=headers("ben-test"),
    )
    assert r.status_code == 200, r.text
    assert [j["state"] for j in jobs_for(api, "ops", r.json()["comment"]["id"])] == ["queued"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Playful", "ask": rid, "delivery": "steer"},
        headers=headers("cara-test"),
    )
    assert r.status_code == 403, r.text


def test_a_reply_reaches_the_bot_that_asked_when_it_does_not_own_the_task(ruled):
    """Finance asked for the task Ops owns, and asks Ana on it: her reply goes to Finance, not to the owner."""
    api, _ = ruled
    task = post(api, "tasks", {"owner": "ops", "title": "Price the launch", "body": "Set it."}, token="finance-test")
    rid = ask(api, task, GOOD, token="finance-test").json()["requirement_id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Plain, please", "ask": rid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    said = r.json()["comment"]
    assert said["recipient_id"] == "bot:finance"
    assert [j["state"] for j in jobs_for(api, "finance", said["id"])] == ["queued"]
    assert jobs_for(api, "ops", said["id"]) == []
