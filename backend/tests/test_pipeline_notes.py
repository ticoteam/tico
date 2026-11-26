"""Pipeline and stage notes (backend/pipeline_notes.py): who adds, edits, retires, proposes and decides them; what a
task's turn is handed; and the why a guard's refusal quotes.

The org (backend/tests/helpers/org_rules.py): Ana owns the company, Ben and Cara are plain members, Ops and Finance are
bots. Engineering is managed by the bot Finance; Product by nobody."""

from backend import pipeline_notes as N
from backend import run_triggers as RT
from backend.models import RunNotes
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import assign, claim, get, post, ready, runner
from backend.tests.helpers.org_rules import org
from backend.tests.helpers.run_triggers import USAGE, start

BUILD, QA = "eng-build", "eng-qa"
HEAVY = {
    "note": "Heavy checks run one at a time per machine; big test runs go to RunsOn.",
    "why": "A type check took the shared machine offline.",
    "since": "2026-11-18",
    "evidence": "#4232",
}
APPROVAL = {
    "stage": "QA",
    "note": "A pending approval is the ask: don't send the approver a separate question on the task.",
    "why": "Approvers got two cards for one decision.",
    "since": "2026-11-18",
    "evidence": "#4237",
    "guard": "approval_first",
}


def engineering(api):
    org(api)
    with api.app.state.store.transaction() as c:
        X.pipeline(
            c,
            "eng",
            "Engineering",
            stages=({"name": "Build", "owner": "assignee"}, {"name": "QA", "owner": "assignee"}),
            manager="bot:finance",
        )
        X.pipeline(c, "prod", "Product", stages=({"name": "Spec", "owner": "assignee"},))


def notes(api, pipeline="eng", state="active", token="cara-test"):
    return get(api, f"pipelines/{pipeline}/notes?state={state}", token=token)


def turn_notes(api, task_id):
    with api.app.state.store.read() as c:
        return N.for_task(c, X.E.task(c, task_id))


def test_a_person_who_may_change_the_pipeline_adds_edits_and_retires_notes_and_every_change_says_why(api):
    engineering(api)
    # A plain member may not, and a bot is told to propose instead.
    post(api, "pipelines/eng/notes", HEAVY, token="cara-test", expected=403)
    refused = post(api, "pipelines/eng/notes", HEAVY, token="ops-test", expected=403)
    assert "tico pipeline note propose" in refused["error"]["detail"]
    post(api, "pipelines/eng/notes", {**HEAVY, "why": " "}, expected=422)
    post(api, "pipelines/eng/notes", {**APPROVAL, "stage": "Spec"}, expected=422)
    heavy = post(api, "pipelines/eng/notes", HEAVY)
    approval = post(api, "pipelines/eng/notes", APPROVAL)
    assert (heavy["state"], heavy["stage_id"], approval["stage_id"]) == ("active", None, QA)

    # An edit needs its line on what changed and why, and keeps it under the note with what it was before.
    path = f"pipeline-notes/{heavy['id']}"
    loosened = {"note": "Heavy checks run one at a time per machine; runs over 100 tests go to RunsOn."}
    post(api, path, loosened, expected=422)
    # An edit keeps the note's scope: a stage sent with it is refused, never silently dropped.
    post(api, path, {**loosened, "line": "Moved to QA.", "stage": "QA"}, expected=422)
    post(api, path, {**loosened, "line": "Loosened: small runs stay local."}, token="cara-test", expected=403)
    post(api, path, {**loosened, "line": "Loosened: small runs stay local.", "revision": 2}, expected=409)
    edited = post(api, path, {**loosened, "line": "Loosened: small runs stay local.", "revision": 1})
    assert edited["revision"] == 2 and edited["why"] == HEAVY["why"]
    listed = notes(api)
    assert [n["id"] for n in listed["notes"]] == [heavy["id"]]
    assert [s["name"] for s in listed["stages"]][:2] == ["Build", "QA"]
    assert [n["id"] for n in listed["stages"][1]["notes"]] == [approval["id"]]
    assert [ch["line"] for ch in listed["notes"][0]["changes"]][1:] == ["Loosened: small runs stay local."]

    # Retired: it stays readable with its line and reaches no turn.
    post(api, f"{path}/retire", {"reason": ""}, expected=422)
    post(api, f"{path}/retire", {"reason": "RunsOn now takes every run."})
    assert notes(api)["notes"] == []
    assert [n["state"] for n in notes(api, state="retired")["notes"]] == ["retired"]
    post(api, path, {**loosened, "line": "again"}, expected=409)


def test_a_task_is_handed_its_pipeline_and_stage_notes_and_nothing_when_there_are_none(api):
    engineering(api)
    with api.app.state.store.transaction() as c:
        task = X.new_task(c, "human:ana", "Fix the smoke test", "eng", assignee="bot:ops")
        other = X.new_task(c, "human:ana", "Write the spec", "prod", assignee="bot:ops")
        loose = X.new_task(c, "human:ana", "Tidy up", assignee="bot:ops")
    assert turn_notes(api, task["id"]) == {} and turn_notes(api, loose["id"]) == {}
    heavy = post(api, "pipelines/eng/notes", HEAVY)
    post(api, "pipelines/eng/notes", APPROVAL)
    got = turn_notes(api, task["id"])
    # In Build: the pipeline's notes, with why, since and evidence; QA's are not Build's.
    assert set(got) == {"pipeline"}
    assert got["pipeline"].startswith("Notes for Engineering (what we learned; follow them):\n- Heavy checks")
    assert "Why: A type check took the shared machine offline. Since 2026-11-18 (#4232)." in got["pipeline"]
    assert "tico pipeline note propose eng" in got["pipeline"]
    with api.app.state.store.transaction() as c:
        X.E.move(c, task["id"], QA, "human:ana", "Ready for QA")
    got = turn_notes(api, task["id"])
    assert got["stage"].startswith("Notes for QA") and "A guard refuses it." in got["stage"]
    assert '--stage "QA"' in got["stage"]
    # Another pipeline's task gets none of them; a proposed note reaches no turn.
    post(api, "pipelines/prod/notes/propose", {**HEAVY, "stage": "Spec"}, token="ops-test")
    assert turn_notes(api, other["id"]) == {}
    post(api, f"pipeline-notes/{heavy['id']}/retire", {"reason": "Done with it."})
    assert set(turn_notes(api, task["id"])) == {"stage"}


def test_the_claim_carries_the_notes_beside_the_task_and_none_when_there_are_none(api):
    engineering(api)
    r = runner(api)
    for bot in ("ops", "cpo"):
        assign(api, r, bot)
    ready(api, r, ["ops", "cpo"])

    def task_for(bot, title):
        with api.app.state.store.transaction() as c:
            return H.task_create(c, H.TICO_ACTOR, title, "Do it.", f"bot:{bot}", lint=False)

    plain = task_for("ops", "Tidy the export")
    first = claim(api, r)
    assert first["task"]["id"] == plain["id"] and "pipeline_notes" not in first
    post(api, f"pipelines/{plain['pipeline_id']}/notes", HEAVY)
    noted = task_for("cpo", "Fix the smoke test")
    second = claim(api, r)
    assert second["task"]["id"] == noted["id"]
    assert second["pipeline_notes"] == {"pipeline": turn_notes(api, noted["id"])["pipeline"]}
    assert second["pipeline_notes"]["pipeline"].startswith("Notes for ")
    # The notes ride beside the task, never in it, so the task's digest and its printed form stay as they were.
    assert "notes" not in second["task"] and "pipeline_notes" not in second["task"]


def test_a_run_records_the_lists_its_claim_offered_and_what_the_runner_says_its_prompt_did_with_them(api):
    # The run's stored input is only its message, so this is how anyone checks the notes reached the bot.
    engineering(api)
    r = runner(api)
    for bot in ("ops", "cpo"):
        assign(api, r, bot)
    ready(api, r, ["ops", "cpo"])

    def task_for(bot, title):
        with api.app.state.store.transaction() as c:
            return H.task_create(c, H.TICO_ACTOR, title, "Do it.", f"bot:{bot}", lint=False)

    def run(attempt, sent=None):
        start(api, r, attempt)
        body = {"outcome": "completed", "last_seq": 0, "text": "", "usage": USAGE}
        post(api, f"attempts/{attempt['id']}/complete", {**body, **({"notes": sent} if sent else {})}, r["token"])
        with api.app.state.store.read() as c:
            row = H.turn(c, attempt["id"])
        return row["notes_offered"], row["notes_sent"]

    plain = task_for("ops", "Tidy the export")
    first = claim(api, r)
    assert first["task"]["id"] == plain["id"]
    assert run(first) == (None, None)
    post(api, f"pipelines/{plain['pipeline_id']}/notes", HEAVY)
    task_for("cpo", "Fix the smoke test")
    second = claim(api, r)
    assert run(second, {"pipeline": "given"}) == ("pipeline", '{"pipeline": "given"}')


def test_a_command_turn_is_offered_no_notes_and_a_late_result_still_records_its_report(api):
    engineering(api)
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, H.TICO_ACTOR, "Fix the smoke test", "Do it.", "bot:ops", lint=False)
    post(api, f"pipelines/{task['pipeline_id']}/notes", HEAVY)
    assert turn_notes(api, task["id"])  # the task has notes a plain turn would be offered
    with api.app.state.store.transaction() as c:
        # The person typed a command (/compact) to the bot on its task: its prompt is only that.
        c.execute(
            "UPDATE messages SET refs=? WHERE conversation_id=?", ('{"command": "compact"}', task["conversation_id"])
        )
    typed = claim(api, r)
    assert typed["task"]["id"] == task["id"] and "pipeline_notes" not in typed
    start(api, r, typed)
    with api.app.state.store.transaction() as c:
        assert H.turn(c, typed["id"])["notes_offered"] is None
        # A result filed after its lease expired settled the run: its report is still kept.
        c.execute("UPDATE attempts SET settled_at=? WHERE id=?", (H.now(), typed["id"]))
        assert RT.notes(c, typed["id"], RunNotes(pipeline="held")) == 1
        assert H.turn(c, typed["id"])["notes_sent"] == '{"pipeline": "held"}'


def test_a_bot_proposes_with_why_and_evidence_and_the_manager_decides_never_its_own(api):
    engineering(api)
    proposal = {k: v for k, v in APPROVAL.items() if k != "evidence"}
    refused = post(api, "pipelines/eng/notes/propose", proposal, token="ops-test", expected=422)
    assert "evidence" in refused["error"]["detail"]
    refused = post(api, "pipelines/eng/notes/propose", {**APPROVAL, "why": ""}, token="ops-test", expected=422)
    assert "why" in refused["error"]["detail"]
    made = post(api, "pipelines/eng/notes/propose", APPROVAL, token="ops-test")
    assert made["state"] == "proposed" and made["created_by"] == "bot:ops"
    assert [n["id"] for n in notes(api, state="proposed")["stages"][1]["notes"]] == [made["id"]]
    # The manager is woken in its chat with the proposer: the message flags it as needing action.
    with api.app.state.store.read() as c:
        woke = c.execute(
            "SELECT body, refs FROM messages WHERE sender_id='bot:ops' AND body LIKE 'Proposed note for%'"
        ).fetchall()
    assert len(woke) == 1 and made["id"] in woke[0]["body"] and "needs_action" in woke[0]["refs"]

    # A bot never edits, retires or accepts its own; a plain member never decides; the reason is required.
    path = f"pipeline-notes/{made['id']}"
    post(api, f"{path}/accept", {"reason": "Mine."}, token="ops-test", expected=403)
    post(api, f"{path}/accept", {"reason": "Fine."}, token="cara-test", expected=403)
    post(api, f"{path}/accept", {"reason": ""}, token="finance-test", expected=422)
    accepted = post(api, f"{path}/accept", {"reason": "Two refusals this week back it."}, token="finance-test")
    assert accepted["state"] == "active"
    post(api, f"{path}/decline", {"reason": "late"}, token="finance-test", expected=409)
    refused = post(api, path, {"note": "Looser", "line": "too strict"}, token="ops-test", expected=403)
    assert "propose" in refused["error"]["detail"]
    post(api, f"{path}/retire", {"reason": "done"}, token="finance-test", expected=403)
    with api.app.state.store.read() as c:
        told = c.execute(
            "SELECT body FROM messages WHERE sender_id='bot:finance' AND body LIKE 'Accepted your note%'"
        ).fetchall()
    assert len(told) == 1 and "Two refusals this week back it." in told[0]["body"]

    # A declined proposal reaches no turn, and a person may decide one too.
    other = post(api, "pipelines/eng/notes/propose", HEAVY | {"note": "Merge at night."}, token="ops-test")
    declined = post(api, f"pipeline-notes/{other['id']}/decline", {"reason": "No evidence it helps."})
    assert declined["state"] == "declined"
    assert [n["id"] for n in notes(api)["notes"]] == []


def test_a_guard_refusal_quotes_the_note_that_names_it_and_still_says_why_without_one(api):
    engineering(api)
    with api.app.state.store.transaction() as c:
        task = X.new_task(c, "human:ana", "Fix the smoke test", "eng", assignee="bot:ops")
        X.E.move(c, task["id"], QA, "human:ana", "Ready for QA")
    with api.app.state.store.read() as c:
        row = X.E.task(c, task["id"])
        assert N.guard_why(c, row, "approval_first") == "Why: " + N.GUARDS["approval_first"]
    note = post(api, "pipelines/eng/notes", APPROVAL)
    with api.app.state.store.read() as c:
        why = N.guard_why(c, row, "approval_first")
    assert why == f"Why: Approvers got two cards for one decision. QA note {note['id']} (2026-11-18, #4237)."
    post(api, "pipelines/eng/notes", {**APPROVAL, "guard": "no_such_guard"}, expected=422)
