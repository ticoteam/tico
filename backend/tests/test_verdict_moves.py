"""The verdict moves the Dev ticket, as Tico, with no builder turn spent on routing (backend/verdict_moves.py)."""
import json

from backend import hubdb as H, rules, verdict_moves as V
from backend.tests.test_github import PR, _suite, api, hook, post, pr_event  # noqa: F401

HEAD, NEW, BASE = "a" * 40, "d" * 40, "c" * 40
STEPS = [("On Deck", "open"), ("In Progress", "doing"), ("Review 1", "review"), ("Review 1 Fixes", "doing"),
         ("Review 2", "review"), ("Review 2 Fixes", "doing"), ("In QA", "review"), ("QA Fixes", "doing"),
         ("Pending Release", "review"), ("Done", "done")]


def receipt(api, task, head=HEAD):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET note=? WHERE id=?", (f"Tests: base {BASE} head {head}: 9 passed, 0 failed", task["id"]))


def usage(c, task_id, bot, model="claude-sonnet-5-5", provider="anthropic"):
    turn = H.new_id()
    c.execute("INSERT INTO turns(id,bot,started,finished,task_id) VALUES(?,?,?,?,?)",
              (turn, bot, H.now(), H.now(), task_id))
    c.execute("INSERT INTO turn_usage_segments(turn_id,position,input_tokens,output_tokens,model,provider,runtime,"
              "harness,effort) VALUES(?,0,20,20,?,?,? ,?,'high')",
              (turn, model, provider, "claude" if provider == "anthropic" else "codex",
               "claude" if provider == "anthropic" else "codex"))
    return turn


def ticket(api, step="Review 1", labels=()):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Dev tickets", [{"name": n, "status": s} for n, s in STEPS])
        rules.seed_dev(c)
    task = post(api, "tasks", {"owner": "cpo", "title": "Improve messages", "body": "Build messages.",
                               "type": typ["id"], "step": "In Progress", "links": [PR]})
    hook(api, "pull_request", pr_event("opened", head={"sha": HEAD}))
    receipt(api, task)
    with api.app.state.store.transaction() as c:
        if labels:
            H.task_update(c, "human:ana", task["id"], labels=list(labels))
        usage(c, task["id"], "cpo", model="gpt-6.1-sol", provider="openai")
        H.task_update(c, "bot:cpo", task["id"], step=step)
        c.execute("DELETE FROM jobs")
    return task


def check(api, task, title="Review 1: PR #412", owner="bot:cmo"):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:cpo", title, f"Check PR #412 head {HEAD}", owner, parent_id=task["id"], lint=False)
        turn = usage(c, row["id"], H.actor_id(owner))
        from backend.check_independence import capture_attempt
        capture_attempt(c, task["id"], row["id"], HEAD, turn)
        c.execute("DELETE FROM jobs")
    return row


def verdict(api, row, value, head=HEAD):
    with api.app.state.store.transaction() as c:
        H.task_update(c, row["owner"], row["id"], status="done", note="Reviewed.", verdict=value, verdict_head=head)
        turn = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1",
                         (row["id"],)).fetchone()
        if turn:
            V.after_capture(c, row["id"], head, turn["id"])


def step(api, task):
    with api.app.state.store.read() as c:
        return V.step_of(c, H.task(c, task["id"]))


def builder_jobs(api):
    with api.app.state.store.read() as c:
        return [H.message(c, r[0]) for r in c.execute(
            "SELECT message_id FROM jobs WHERE bot='cpo' AND state='queued' ORDER BY rowid")]


def step_events(api, task):
    with api.app.state.store.read() as c:
        return [(r["actor"], r["new"]) for r in c.execute(
            "SELECT actor,new FROM task_events WHERE task_id=? AND field='step' ORDER BY rowid", (task["id"],))]


def test_review_1_approve_at_the_current_head_moves_to_review_2_as_tico_with_no_builder_turn(api):
    task = ticket(api)
    review = check(api, task)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 2"
    assert step_events(api, task)[-1][0] == H.KEEPER, "Tico moved it, not the reviewer"
    assert not builder_jobs(api), "an approval Tico moved on buys no builder turn"
    # The builder's own move to the same step is the same move: nothing new is written.
    before = step_events(api, task)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], step="Review 2")
    assert step_events(api, task) == before


def test_a_verdict_on_an_old_head_moves_nothing(api):
    task = ticket(api)
    review = check(api, task)
    verdict(api, review, "approve", head="b" * 40)
    assert step(api, task) == "Review 1"
    assert builder_jobs(api), "a verdict Tico did not act on still reaches the builder"


def test_a_verdict_after_the_builder_moved_on_moves_nothing(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], step="Review 2")
    verdict(api, review, "changes")
    assert step(api, task) == "Review 2", "the builder already moved it; Tico does not move it back"


def test_duplicate_delivery_of_the_same_eligible_verdict_moves_only_once(api):
    task = ticket(api)
    review = check(api, task)
    before = step_events(api, task)
    verdict(api, review, "approve")
    with api.app.state.store.transaction() as c:
        once = V.after_verdict(c, H.task(c, review["id"]))
    assert once is None, "the ticket has already left the step the verdict judged"
    assert step(api, task) == "Review 2"
    assert len(step_events(api, task)) == len(before) + 1


def test_configured_independent_checker_falling_back_to_builder_family_cannot_move(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM turn_usage_segments WHERE turn_id IN (SELECT id FROM turns WHERE task_id=?)",
                  (review["id"],))
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,input_tokens,output_tokens,model,provider,runtime,"
                  "harness,effort) VALUES(?,0,20,20,'gpt-6.1-sol','openai','codex','codex','high')", (turn,))
        from backend.check_independence import capture_attempt
        capture_attempt(c, task["id"], review["id"], HEAD, turn)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 1"
    with api.app.state.store.read() as c:
        check_row = H.task(c, review["id"])
        assert check_row["verdict"] == "approve" and check_row["verdict_head"] == HEAD
        eligibility = V.delivery(c, check_row)["eligibility"]
        assert not eligibility["eligible"] and eligibility["known"]
        assert eligibility["builder_families"] == ["openai"]
        assert eligibility["checker_families"] == ["openai"]
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='verdict_wait'",
                         (task["id"],)).fetchone()[0] == 1


def test_unknown_executed_checker_family_is_not_proof_of_independence(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        c.execute("UPDATE turn_usage_segments SET model='synthetic-unknown-model',provider='unknown' WHERE turn_id=?",
                  (turn,))
        from backend.check_independence import capture_attempt
        capture_attempt(c, task["id"], review["id"], HEAD, turn)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 1"
    with api.app.state.store.read() as c:
        row = H.task(c, review["id"])
        assert row["verdict"] == "approve"
        eligibility = V.delivery(c, row)["eligibility"]
        assert not eligibility["eligible"] and not eligibility["known"]
        assert "unknown" in eligibility["reason"]


def test_verdict_waits_for_its_exact_attempt_capture_then_moves_once(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        c.execute("DELETE FROM check_head_provenance WHERE check_id=?", (review["id"],))
        H.task_update(c, review["owner"], review["id"], status="done", note="Reviewed.",
                      verdict="approve", verdict_head=HEAD)
        marker = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                           "ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
        assert H._json(marker["new"], {})["attempt_id"] == turn
        assert V.step_of(c, H.task(c, task["id"])) == "Review 1"
        from backend.check_independence import capture_attempt
        capture_attempt(c, task["id"], review["id"], HEAD, turn)
        moved = V.after_capture(c, review["id"], HEAD, turn)
        assert moved and moved["moved"] == "Review 2"
        assert V.after_capture(c, review["id"], HEAD, turn) is None
        assert V.step_of(c, H.task(c, task["id"])) == "Review 2"


def test_post_capture_callback_ignores_a_different_or_obsolete_attempt(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        H.task_update(c, review["owner"], review["id"], status="done", note="Reviewed.",
                      verdict="approve", verdict_head=HEAD)
        assert V.after_capture(c, review["id"], HEAD, "obsolete-attempt") is None
        assert V.step_of(c, H.task(c, task["id"])) == "Review 1"
        # A new attempt supersedes the old callback even if it has the same owner and head.
        usage(c, review["id"], H.actor_id(review["owner"]))
        assert V.after_capture(c, review["id"], HEAD, turn) is None
        assert V.step_of(c, H.task(c, task["id"])) == "Review 1"


def test_review_2_changes_moves_to_review_2_fixes_and_wakes_the_builder_once_with_verdict_and_head(api):
    task = ticket(api, step="Review 2")
    review = check(api, task, "Review 2: PR #412")
    verdict(api, review, "changes")
    assert step(api, task) == "Review 2 Fixes"
    woke = [m for m in builder_jobs(api)]
    assert len(woke) == 1, woke
    refs = woke[0]["refs"]
    assert (refs["verdict"], refs["verdict_head"], refs[V.HANDOFF]) == ("changes", HEAD, "verdict")
    assert "Review 2 Fixes" in woke[0]["body"] and HEAD in woke[0]["body"]


def test_qa_fail_moves_to_qa_fixes_and_the_step_rule_does_not_wake_the_builder_twice(api):
    task = ticket(api, step="In QA", labels=["qa"])
    qa = check(api, task, "QA: PR #412")
    verdict(api, qa, "fail")
    assert step(api, task) == "QA Fixes"
    woke = builder_jobs(api)
    assert len(woke) == 1 and woke[0]["refs"][V.HANDOFF] == "verdict", woke


def test_qa_pass_on_a_merged_ticket_goes_to_pending_release_with_no_builder_turn(api):
    task = ticket(api, step="In QA", labels=["qa"])
    qa = check(api, task, "QA: PR #412")
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    verdict(api, qa, "pass")
    assert step(api, task) == "Pending Release"
    assert not builder_jobs(api)


def test_qa_pass_before_the_merge_wakes_the_builder_to_merge_and_the_merge_moves_it_on(api):
    task = ticket(api, step="In QA", labels=["qa"])
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    qa = check(api, task, "QA: PR #412")
    verdict(api, qa, "pass")
    assert step(api, task) == "In QA", "Tico does not merge; the builder does"
    woke = builder_jobs(api)
    assert len(woke) == 1 and woke[0]["refs"][V.HANDOFF] == "merge" and "--match-head-commit " + HEAD in woke[0]["body"]
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    assert step(api, task) == "Pending Release"


def test_review_2_approve_without_qa_waits_for_green_ci_then_wakes_the_merge_once(api):
    task = ticket(api, step="Review 2")
    review = check(api, task, "Review 2: PR #412")
    verdict(api, review, "approve")
    assert step(api, task) == "Review 2"
    assert not builder_jobs(api), "nothing to do until CI is green"
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    merges = [m for m in builder_jobs(api) if m["refs"].get(V.HANDOFF) == "merge"]
    assert len(merges) == 1 and merges[0]["refs"]["pr_head"] == HEAD
    _suite(api, "github-actions", "completed", "success", sha=HEAD, id=7)
    assert len([m for m in builder_jobs(api) if m["refs"].get(V.HANDOFF) == "merge"]) == 1, "once per head"
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    assert step(api, task) == "Pending Release"


def test_review_1_only_makes_review_1_the_last_review(api):
    task = ticket(api, labels=[V.REVIEW_1_ONLY])
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    review = check(api, task)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 1"
    assert [m["refs"].get(V.HANDOFF) for m in builder_jobs(api)] == ["merge"]


def test_a_green_new_head_at_a_fixes_step_goes_back_to_the_review_that_asked_for_the_fix(api):
    task = ticket(api)
    review = check(api, task)
    verdict(api, review, "changes")
    assert step(api, task) == "Review 1 Fixes"
    # A re-run on the judged head is not a fix.
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    assert step(api, task) == "Review 1 Fixes"
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    receipt(api, task, NEW)
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "Review 1", "Review 2 waits until Review 1 approves the fixed head"
    assert step_events(api, task)[-1][0] == H.KEEPER


def test_a_green_head_the_gate_refuses_stays_and_the_builder_hears_what_clears_it(api):
    task = ticket(api)
    verdict(api, check(api, task), "changes")
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "Review 1 Fixes", "no receipt at the new head: the gate keeps it"
    with api.app.state.store.read() as c:
        told = c.execute("SELECT body FROM messages WHERE conversation_id=? AND json_extract(refs_json,'$.review_gate') "
                         "IS NOT NULL", (task["conversation_id"],)).fetchall()
    assert len(told) == 1 and "receipt" in told[0][0]
    with api.app.state.store.read() as c:
        kept = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='move_retry'", (task["id"],)).fetchall()
    assert [json.loads(r[0])["step"] for r in kept] == ["Review 1"], "Tico retries the refused move once it clears"


def test_qa_fixes_green_new_head_goes_back_to_in_qa(api):
    task = ticket(api, step="In QA", labels=["qa"])
    verdict(api, check(api, task, "QA: PR #412"), "fail")
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "In QA"


def test_a_failed_check_at_a_review_step_is_a_builder_hand_off(api):
    task = ticket(api)
    _suite(api, "github-actions", "completed", "failure", sha=HEAD)
    from backend import github
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value_json=json_set(value_json,'$.due','2000-01-01T00:00:00Z') "
                  "WHERE key LIKE 'github-task-wake:%'")
        github.flush_wakes(c)
    failed = [m for m in builder_jobs(api) if m["refs"].get("pr_result") == "fail"]
    assert len(failed) == 1 and failed[0]["refs"][V.HANDOFF] == "failed_check"
    assert step(api, task) == "Review 1"
