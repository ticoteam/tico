"""A bot's ask of a person (Tasks spec "Bots ask people a question with choices"): a question is a message marked ❓
(POST /api/v2/tasks/{tid}/questions, backend/question_marks.py), an OK is an ask on the task that shows what it
approves. The words of either are checked in plain code (backend/ask_shape.py line_refusal, AC-1, AC-2), the built-in
rule "Asks to a person" is added once and judged only for a bot's ask of a person, in the one call the check already
makes (org_rules.ensure_builtin, AC-7 to AC-9), a bot's task for a person that is really a decision is refused
(backend/ask_ingest.py, AC-6), and a reply to a bot's question goes to the bot that asked (AC-13)."""

import importlib

import pytest

from backend import ask_ingest as A
from backend import ask_shape as S
from backend import org_rules as OR
from backend import question_marks as Q
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.counsel import store_file
from backend.tests.helpers.org_rule_check import (  # noqa: F401 - fixtures
    COLOR,
    color_rule,
    not_a_friday,
    ruled,
    sql,
    task_for_ops,
)

LINE = "Which tone should the launch note take?"
OK = {"line": "OK the launch note?", "kind": "ok"}


def ask(api, task, body, to="ana", token="ops-test"):
    """An ask on the task (POST /asks): an OK. One that names nothing shows a page of ops' (a bot's OK approves what
    it shows)."""
    if body.get("kind") == "ok" and "show" not in body:
        file_id, _ = store_file(api, "The launch note, both tones.", title="Launch note")
        body = {**body, "show": [f"page:{file_id}"]}
    return api.post(
        f"/api/v2/tasks/{task['id']}/asks",
        json={"to": to, "version": task["version"], **body},
        headers=headers(token),
    )


def question(api, task, text=LINE, to="ana", token="ops-test"):
    """A bot's question to a person: a ❓ on the task's chat."""
    return api.post(f"/api/v2/tasks/{task['id']}/questions", json={"to": to, "text": text}, headers=headers(token))


def builtin(api):
    return sql(api, "SELECT * FROM org_rules WHERE text=?", (OR.ASKS_TO_A_PERSON,))


def version(api, task):
    return sql(api, "SELECT version FROM tasks WHERE id=?", (task["id"],))[0]["version"]


def refusals(api):
    """The refusals of an ask's words: an OK's at the asks route (`task_ask_shape`), a ❓'s when it is marked (`lint`)."""
    return sql(api, "SELECT actor, rule, detail FROM refusals WHERE rule IN ('task_ask_shape', 'lint') ORDER BY rowid")


def refusal_of(r):
    """(code, detail) of a refusal, flat (the engine's) or in the error envelope."""
    said = r.json()
    if isinstance(said.get("error"), dict):
        return said["error"]["code"], said["error"]["detail"]
    return said["error"], said["detail"]


# ---- what an OK shows ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "ok"),
    [
        ("page:{file}", True),
        ("page:{file}@1", True),
        ("file:{file}@1", True),
        ("task:{task}", False),
        ("https://example.com/a", False),
        ("page:{file}@9", False),
    ],
)
def test_a_bots_ok_shows_a_page_or_a_file_version_not_a_task_or_a_web_address(ruled, ref, ok):
    api, _ = ruled
    task = task_for_ops(api)
    file_id, _ = store_file(api, "The launch note, both tones.", title="Launch note drafts")
    r = ask(api, task, {**OK, "show": [ref.format(file=file_id, task=task["id"])]})
    assert r.status_code == (200 if ok else 409), r.text
    assert ok or r.json()["error"] == "task_requirement"


def test_the_shape_is_a_bots_ask_of_a_person_only(ruled):
    """A person asking anyone keeps asking as before: words a bot's ask could not use are a person's to choose."""
    api, _ = ruled
    other = post(api, "tasks", {"owner": "ana", "title": "Pick the launch tone", "body": "Two drafts."})
    r = ask(api, other, {"line": "See above: OK to send?", "kind": "ok"}, to="ben", token="ana-test")
    assert r.status_code == 200, r.text
    assert refusals(api) == []


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
    "text,said",
    [
        (UNSEEN[0], '"on this task"'),
        (UNSEEN[1], '"comment above"'),
        (
            "Which onboarding flow first? Background: knowledge/onboarding-in-flow.md",
            'has a file path: "knowledge/onboarding-in-flow.md"',
        ),
        ("Ship the fix in commit a7cf7af today?", 'has an id or hash: "a7cf7af"'),
        ("Which launch note tone? " + "x" * 120, S.SHORT_LINE),
    ],
)
def test_a_question_that_breaks_its_shape_is_refused_with_the_fix_before_any_call(ruled, text, said):
    api, judge = ruled
    color_rule(api)  # a rule in force, so a call would be made if the question got that far
    task = task_for_ops(api)
    r = question(api, task, text)
    assert 400 <= r.status_code < 500, r.text
    code, detail = refusal_of(r)
    assert code in ("task_ask_shape", "lint") and said in detail, r.text
    assert judge.calls == [], "refused in plain code: no model call"
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body=?", (text,))[0]["n"] == 0, "nothing written"
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0


def test_an_ok_that_breaks_its_shape_is_refused_with_the_fix_before_any_call(ruled):
    api, judge = ruled
    color_rule(api)  # a rule in force, so a call would be made if the ask got that far
    task = task_for_ops(api)
    r = ask(api, task, {"line": "See above: OK to send?", "kind": "ok"})
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "task_ask_shape" and '"See above"' in r.json()["detail"], r.text
    assert judge.calls == [], "refused in plain code: no model call"
    assert version(api, task) == task["version"], "nothing written"


@pytest.mark.parametrize("line", ["Is a price above $50 OK?", LIST])
def test_a_question_that_stands_on_its_own_is_asked_and_a_list_may_follow_its_first_line(ruled, line):
    api, _ = ruled
    task = task_for_ops(api)
    r = question(api, task, line)
    assert r.status_code == 200, r.text
    assert r.json()["question"]["line"] == line.split("\n", 1)[0]
    assert refusals(api) == []


@pytest.mark.parametrize("text", ["x", "❓", "42", "x\nShould the launch note go out on Friday?"])
def test_a_placeholder_question_is_refused_and_nothing_is_marked(ruled, text):
    """A line of a character or two, a lone ❓ or a number asks nothing: a ❓ on it would sit in a Needs you as "x"."""
    api, judge = ruled
    task = task_for_ops(api)
    r = question(api, task, text)
    assert 400 <= r.status_code < 500, r.text
    assert refusal_of(r)[0] in ("task_ask_shape", "lint"), r.text
    assert judge.calls == []
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            Q.ask(c, "bot:ops", task["id"], "human:ana", text)
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 0


def test_an_older_clients_question_on_the_asks_route_is_asked_as_a_mark_with_its_parts_in_its_words(ruled):
    """A computer's `tico` from before ❓ marks sends a question to the asks route with its why and choices: it is
    asked as a ❓, never refused with the command the bot just ran. An OK takes no choices, and a person asks no ❓."""
    api, _ = ruled
    task = task_for_ops(api)
    parts = {
        "line": LINE,
        "why": "It goes out on Friday.",
        "choices": ["Plain", "Playful"],
        "details": ["Reads like a memo", ""],
        "recommend": "Plain",
    }
    r = ask(api, task, parts)
    assert r.status_code == 200, r.text
    mark = r.json()["question"]
    assert (mark["line"], mark["person_id"]) == (LINE, "human:ana")
    [said] = sql(api, "SELECT body FROM messages WHERE id=?", (mark["message_id"],))
    assert said["body"] == f"{LINE}\n\nIt goes out on Friday.\n\n- Plain: Reads like a memo\n- Playful\nI'd pick Plain."

    assert ask(api, task, {"line": "x"}).status_code in (409, 422), "its words are checked as any ❓'s are"
    assert ask(api, task, {**OK, "choices": ["Yes", "No"]}).status_code == 422
    other = post(api, "tasks", {"owner": "ana", "title": "Pick the launch tone", "body": "Two drafts."})
    assert ask(api, other, {"line": LINE}, to="ben", token="ana-test").status_code == 403
    assert sql(api, "SELECT count(*) AS n FROM question_marks")[0]["n"] == 1


def test_every_path_that_asks_a_person_checks_the_shape_whatever_the_bot_was_told(ruled):
    """H.task_ask is where every ask on a task is made (the asks routes, OKs, browser help, built-in bots) and
    question_marks.ask where every ❓ on a task's chat is, so a path that skips the route's early check is refused
    there too."""
    api, _ = ruled
    task = task_for_ops(api)
    with api.app.state.store.transaction() as c:
        shown = X.shown(c, bot="ops")
        for line, why in (
            (UNSEEN[1], None),
            ("Which onboarding flow first?", "Background: knowledge/onboarding-in-flow.md"),
        ):
            words = " ".join(filter(None, (line, why)))
            with pytest.raises(H.Refused) as refused:
                H.task_ask(c, "bot:ops", task["id"], "human:ana", words, shows=shown, seen_version=task["version"])
            assert refused.value.rule == "lint"
            with pytest.raises(H.Refused) as refused:
                Q.ask(c, "bot:ops", task["id"], "human:ana", words)
            assert refused.value.rule == "lint"
        assert Q.on_task(c, task["id"]) == []
        made = Q.ask(c, "bot:ops", task["id"], "human:ana", LIST)
        assert [m["id"] for m in Q.on_task(c, task["id"])] == [made["id"]]


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


def test_the_rule_judges_a_bots_question_to_a_person_with_all_its_lines_in_one_call(ruled):
    api, judge = ruled
    judge.needles = {OR.ASKS_TO_A_PERSON: "**"}  # it reads Markdown as breaking the rule
    task = task_for_ops(api)
    four = "Four tones for the launch note, which one?\n1. Plain\n2. Playful\n3. Formal\n4. Short"
    r = question(api, task, four)
    assert r.status_code == 200, r.text
    [call] = judge.calls  # AC-9: one question, whatever its lines, is 1 rule-check call
    assert [rule_row["text"] for rule_row in call["state"]["rules"]] == [OR.ASKS_TO_A_PERSON]
    output = call["state"]["output"]
    assert "1. Plain" in output and "4. Short" in output

    task = task_for_ops(api, "Write the second launch note")
    r = question(api, task, "Which tone should it take? It goes out **Friday** under our name.")
    assert r.status_code == 200 and r.json()["held"]["state"] == "held", r.text  # AC-8: held by the rule, not asked
    assert "question" not in r.json()
    assert sql(api, "SELECT count(*) AS n FROM question_marks WHERE task_id=?", (task["id"],))[0]["n"] == 0


def test_the_rule_is_not_read_for_any_other_write(ruled):
    """Met only by a bot's ask of a person: its comments and done notes make no call, and no bot's live reply is
    hidden for it."""
    api, judge = ruled
    task = task_for_ops(api)
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
    assert question(api, task).status_code == 200
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
    said = A.message("decision", "Cara", 0, "Ben", "cara")
    assert said == (
        'This asks Cara to decide, so ask it on your own task: tico task ask <task> --to cara "<question>". Say why, '
        "the options and your pick in the message."
    )


# ---- writing to the bot that asked (AC-13) --------------------------------------------------------------------------


def jobs_for(api, bot, message_id):
    return sql(api, "SELECT state FROM jobs WHERE bot_id=? AND message_id=?", (bot, message_id))


def test_a_reply_to_a_bots_question_reaches_that_bot_on_the_task_and_wakes_it_and_clears_the_question(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = question(api, task).json()["question"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Why not both, one per audience?", "ask": mark["id"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    said = r.json()["comment"]
    assert said["recipient_id"] == "bot:ops" and said["body"] == "Why not both, one per audience?"
    assert [j["state"] for j in jobs_for(api, "ops", said["id"])] == ["queued"], "the bot that asked is woken"
    [cleared] = sql(api, "SELECT cleared_reason, cleared_message_id FROM question_marks WHERE id=?", (mark["id"],))
    assert cleared == {"cleared_reason": "replied", "cleared_message_id": said["id"]}, "her reply answers it"


@pytest.mark.parametrize("which", ["missing", "a person's", "a bot's OK"])
def test_a_reply_names_a_bots_question_on_that_task(ruled, which):
    """Only a bot's open ❓: an OK, a person's or a bot's, is answered with OK or Send back, never named by a reply."""
    api, _ = ruled
    task = task_for_ops(api)
    rid = "nope"
    if which == "a person's":
        rid = ask(api, task, {"line": "OK the tone?", "kind": "ok"}, to="ben", token="ana-test").json()[
            "requirement_id"
        ]
    if which == "a bot's OK":
        rid = ask(api, task, OK).json()["requirement_id"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "Hello", "ask": rid}, headers=headers("ana-test")
    )
    assert r.status_code == 422, r.text
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body='Hello'")[0]["n"] == 0


def test_a_question_once_replied_to_cannot_be_named_again(ruled):
    api, _ = ruled
    task = task_for_ops(api)
    mark = question(api, task).json()["question"]
    first = api.post(
        f"/api/v2/tasks/{task['id']}/comments", json={"text": "Plain", "ask": mark["id"]}, headers=headers("ana-test")
    )
    assert first.status_code == 200, first.text
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "One more thing", "ask": mark["id"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 422, r.text
    assert sql(api, "SELECT count(*) AS n FROM messages WHERE body='One more thing'")[0]["n"] == 0


def test_the_person_a_bots_question_waits_on_may_wake_that_bot_and_no_one_else_may(ruled):
    """Ben is not on Ana's task: the question to him is his way to wake the bot; Cara, not asked, cannot."""
    api, _ = ruled
    task = task_for_ops(api)
    mark = question(api, task, to="ben").json()["question"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Playful", "ask": mark["id"], "delivery": "steer"},
        headers=headers("cara-test"),
    )
    assert r.status_code == 403, r.text
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Plain, but warmer", "ask": mark["id"], "delivery": "steer"},
        headers=headers("ben-test"),
    )
    assert r.status_code == 200, r.text
    assert [j["state"] for j in jobs_for(api, "ops", r.json()["comment"]["id"])] == ["queued"]


def test_a_reply_reaches_the_bot_that_asked_when_it_does_not_own_the_task(ruled):
    """Finance asked for the task Ops owns, and asks Ana on it: her reply goes to Finance, not to the owner."""
    api, _ = ruled
    task = post(api, "tasks", {"owner": "ops", "title": "Price the launch", "body": "Set it."}, token="finance-test")
    mark = question(api, task, token="finance-test").json()["question"]
    r = api.post(
        f"/api/v2/tasks/{task['id']}/comments",
        json={"text": "Plain, please", "ask": mark["id"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    said = r.json()["comment"]
    assert said["recipient_id"] == "bot:finance"
    assert [j["state"] for j in jobs_for(api, "finance", said["id"])] == ["queued"]
    assert jobs_for(api, "ops", said["id"]) == []
