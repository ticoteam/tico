"""A bot's question to a person (Tasks spec "Bots ask people a question with choices"): its shape is checked in plain
code before any model call (backend/ask_shape.py, AC-1, AC-2), the built-in rule "Asks to a person" is added once and
judged only for such a question, in the one call the check already makes (org_rules.ensure_builtin, AC-7 to AC-9), and
a bot's task for a person that is really a decision is refused (backend/ask_ingest.py, AC-6)."""

import pytest

from backend import ask_ingest as A
from backend import ask_shape as S
from backend import org_rules as OR
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
