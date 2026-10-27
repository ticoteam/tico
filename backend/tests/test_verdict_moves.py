"""The verdict moves the ticket by its steps' flow, as Tico, with no builder turn spent on routing
(backend/verdict_moves.py, backend/step_flow.py)."""
import pytest

from backend import hubdb as H, rules, verdict_moves as V
from backend.tests.test_github import PR, _suite, api, hook, post, pr_event  # noqa: F401

HEAD, NEW, BASE = "a" * 40, "d" * 40, "c" * 40
STEPS = [("On Deck", "open"), ("In Progress", "doing"), ("Review 1", "review"), ("Review 1 Fixes", "doing"),
         ("Review 2", "review"), ("Review 2 Fixes", "doing"), ("In QA", "review"), ("QA Fixes", "doing"),
         ("Pending Release", "review"), ("Post Release QA", "review"), ("Done", "done")]


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


def flow(api, task, step_name, **settings):
    """Set one step's flow the way Settings > Tasks does: the whole type saved with its steps."""
    from backend.tests.test_github import get, headers
    kind = get(api, "task-types/" + task["type_id"])["type"]
    steps = [{**{k: s[k] for k in ("id", "name", "status")},
              **({"flow": {**s["flow"], **settings}} if s["name"] == step_name else {})} for s in kind["steps"]]
    r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": steps})
    assert r.status_code == 200, r.text


def checks_of(api, task):
    with api.app.state.store.read() as c:
        return [t for t in (H.task(c, i) for i in H.TR.child_ids(c, task["id"])) if t["status"] in H.ACTIVE_STATUSES]


def builder_jobs(api):
    with api.app.state.store.read() as c:
        return [H.message(c, r[0]) for r in c.execute(
            "SELECT message_id FROM jobs WHERE bot='cpo' AND state='queued' ORDER BY rowid")]


def step_events(api, task):
    with api.app.state.store.read() as c:
        return [(r["actor"], r["new"]) for r in c.execute(
            "SELECT actor,new FROM task_events WHERE task_id=? AND field='step' ORDER BY rowid", (task["id"],))]


def test_a_review_is_feedback_its_approval_goes_back_to_the_builder_once(api):
    task = ticket(api)
    review = check(api, task)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 1 Fixes", "a Dev review is feedback: the builder moves on when it is in"
    assert step_events(api, task)[-1][0] == H.KEEPER, "Tico moved it, not the reviewer"
    woke = builder_jobs(api)
    assert len(woke) == 1 and woke[0]["refs"][V.HANDOFF] == "verdict", woke
    # The builder's own move to the same step is the same move: nothing new is written.
    before = step_events(api, task)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], step="Review 1 Fixes")
    assert step_events(api, task) == before


def test_a_board_whose_review_approves_straight_to_the_next_check_moves_quietly(api):
    task = ticket(api)
    flow(api, task, "Review 1", on_pass="Review 2")
    verdict(api, check(api, task), "approve")
    assert step(api, task) == "Review 2"
    assert not builder_jobs(api), "an approval Tico moved on buys no builder turn"


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


def test_post_release_qa_verdict_does_not_run_the_pre_release_step_mover(api):
    task = ticket(api, step="Post Release QA")
    review = check(api, task, title="Post Release QA: PR #412")
    verdict(api, review, "pass")
    assert step(api, task) == "Post Release QA", "Post Launch QA owns the parent ticket's disposition"


def test_duplicate_delivery_of_the_same_eligible_verdict_moves_only_once(api):
    task = ticket(api)
    review = check(api, task)
    before = step_events(api, task)
    verdict(api, review, "approve")
    with api.app.state.store.transaction() as c:
        once = V.after_verdict(c, H.task(c, review["id"]))
    assert once is None, "the ticket has already left the step the verdict judged"
    assert step(api, task) == "Review 1 Fixes"
    assert len(step_events(api, task)) == len(before) + 1


def test_configured_other_family_checker_fallback_verdict_still_moves(api):
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
    assert step(api, task) == "Review 1 Fixes"
    with api.app.state.store.read() as c:
        check_row = H.task(c, review["id"])
        assert check_row["verdict"] == "approve" and check_row["verdict_head"] == HEAD
        eligibility = V.delivery(c, check_row)["eligibility"]
        assert not eligibility["eligible"] and eligibility["known"]
        assert eligibility["builder_families"] == ["openai"]
        assert eligibility["checker_families"] == ["openai"]
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='verdict_wait'",
                         (task["id"],)).fetchone()[0] == 1


def test_unknown_executed_checker_family_is_recorded_but_does_not_block_verdict(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        c.execute("UPDATE turn_usage_segments SET model='synthetic-unknown-model',provider='unknown' WHERE turn_id=?",
                  (turn,))
        from backend.check_independence import capture_attempt
        capture_attempt(c, task["id"], review["id"], HEAD, turn)
    verdict(api, review, "approve")
    assert step(api, task) == "Review 1 Fixes"
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
        assert moved and moved["moved"] == "Review 1 Fixes"
        assert V.after_capture(c, review["id"], HEAD, turn) is None
        assert V.step_of(c, H.task(c, task["id"])) == "Review 1 Fixes"


def test_post_capture_callback_ignores_a_different_or_obsolete_attempt(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (review["id"],)).fetchone()[0]
        H.task_update(c, review["owner"], review["id"], status="done", note="Reviewed.",
                      verdict="approve", verdict_head=HEAD)
        assert V.after_capture(c, review["id"], "b" * 40, turn) is None
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
    task = ticket(api, step="In QA")
    qa = check(api, task, "QA: PR #412")
    verdict(api, qa, "fail")
    assert step(api, task) == "QA Fixes"
    woke = builder_jobs(api)
    assert len(woke) == 1 and woke[0]["refs"][V.HANDOFF] == "verdict", woke


def test_a_merge_at_in_qa_moves_the_ticket_on_and_a_later_qa_verdict_moves_nothing(api):
    task = ticket(api, step="In QA")
    qa = check(api, task, "QA: PR #412")
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    assert step(api, task) == "Pending Release"
    verdict(api, qa, "pass")
    assert step(api, task) == "Pending Release"


def test_qa_pass_before_the_merge_wakes_the_builder_to_merge_and_the_merge_moves_it_on(api):
    task = ticket(api, step="In QA")
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    qa = check(api, task, "QA: PR #412")
    verdict(api, qa, "pass")
    assert step(api, task) == "In QA", "Tico does not merge; the builder does"
    woke = builder_jobs(api)
    assert len(woke) == 1 and woke[0]["refs"][V.HANDOFF] == "merge" and "--match-head-commit " + HEAD in woke[0]["body"]
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    assert step(api, task) == "Pending Release"


def test_a_board_without_qa_merges_after_its_last_review_once_ci_is_green(api):
    task = ticket(api, step="Review 2")
    flow(api, task, "Review 2", on_pass="merge")
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


def test_a_ticket_tagged_with_a_steps_skip_tag_passes_it_by_and_the_skip_is_recorded(api):
    task = ticket(api, step="QA Fixes", labels=["skip-qa"])
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:cpo", task["id"], step="In QA")
    assert step(api, task) == "In QA", "the merge is next, and Tico does not merge"
    assert not checks_of(api, task), "a skipped step files no check"
    assert [m["refs"].get(V.HANDOFF) for m in builder_jobs(api)] == ["merge"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT new FROM task_events WHERE task_id=? AND field='step_skipped'",
                         (task["id"],)).fetchone()[0] == "In QA"


def test_a_green_new_head_at_a_step_with_after_green_moves_on_to_the_next_check(api):
    task = ticket(api)
    flow(api, task, "Review 1 Fixes", after_green="Review 2")
    review = check(api, task)
    verdict(api, review, "changes")
    assert step(api, task) == "Review 1 Fixes"
    # A re-run on the judged head is not a fix.
    _suite(api, "github-actions", "completed", "success", sha=HEAD)
    assert step(api, task) == "Review 1 Fixes"
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    receipt(api, task, NEW)
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "Review 2"
    assert step_events(api, task)[-1][0] == H.KEEPER


def test_on_the_dev_board_the_builder_moves_on_from_fixes_not_ci(api):
    task = ticket(api)
    verdict(api, check(api, task), "changes")
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    receipt(api, task, NEW)
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "Review 1 Fixes", "only the builder knows when the feedback is in"


def test_a_check_finished_with_no_verdict_is_feedback_and_goes_to_on_fail(api):
    task = ticket(api, step="In QA")
    qa = check(api, task, "QA: PR #412")
    with api.app.state.store.transaction() as c:
        H.task_update(c, qa["owner"], qa["id"], status="done", note="Looked at it; the login form is odd.")
        from backend import check_pools
        turn = c.execute("SELECT id FROM turns WHERE task_id=?", (qa["id"],)).fetchone()[0]
        check_pools.capture_attempt(c, turn)
    assert step(api, task) == "QA Fixes"
    assert [m["refs"].get(V.HANDOFF) for m in builder_jobs(api)] == ["verdict"], [(m["refs"], m["body"][:200]) for m in builder_jobs(api)]


def test_a_ticket_past_its_check_steps_timeout_has_its_check_closed_and_the_round_skipped(api):
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        assert V.timeouts(c) == [], "not an hour yet"
        later = H.parse_ts(H.now()) + __import__("datetime").timedelta(minutes=61)
        assert V.timeouts(c, later.strftime("%Y-%m-%dT%H:%M:%S.%fZ")) == [task["id"]]
        assert H.task(c, review["id"])["status"] == "closed"
    assert step(api, task) == "Review 1 Fixes", "the round is skipped as if it passed"


def test_a_check_timeout_keeps_unrelated_human_and_bot_subtasks_open(api):
    from backend import step_flow
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        children = [H.task_create(c, "bot:cpo", title, "Independent work.", owner,
                                 parent_id=task["id"], lint=False) for title, owner in (
            ("Approve the legal wording", "human:ana"), ("Draft the release copy", "bot:cmo"))]
        later = H.shift(H.now(), minutes=61)
        assert V.timeouts(c, later) == [task["id"]]
        assert H.task(c, review["id"])["status"] == "closed"
        for child in children:
            assert H.task(c, child["id"])["status"] == "open"
            assert step_flow.check_step(c, child) is None


@pytest.mark.parametrize("unavailable", [
    {"state": "quarantined"}, {"state": "planned"}, {"state": "paused"}, {"state": "archived"},
    {"state": ""}, {"state": None},
    {"onboarding_state": "needs_setup"}, {"onboarding_state": "needs_onboarding"},
    {"config": {"profile": "responder"}}, {"config": {"harness": "dots"}},
])
def test_allocator_skips_inactive_pool_members_and_assigns_remaining_checks(api, unavailable):
    from backend import task_allocator
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Check queue", [
            {"name": "Inbox", "status": "open", "flow": {"assign": {"pool": ["cmo", "cpo"], "slots": 4}}},
            {"name": "Check", "status": "doing"}])
        parents = [H.task_create(c, "human:ana", f"Improve messages {i}", "Company work.", "human:ana", lint=False)
                   for i in range(2)]
        checks = [H.task_create(c, "human:ana", f"Review 1: synthetic check {i}", "Check messages.", "human:ana",
                                type=typ["id"], parent_id=parent["id"], private=False, lint=False)
                  for i, parent in enumerate(parents)]
        if "state" in unavailable:
            c.execute("UPDATE bots SET state=? WHERE slug='cmo'", (unavailable["state"],))
        if "onboarding_state" in unavailable:
            c.execute("UPDATE bot_config SET onboarding_state=? WHERE bot='cmo'", (unavailable["onboarding_state"],))
        for key, value in unavailable.get("config", {}).items():
            c.execute("UPDATE bot_config SET config_json=json_set(config_json, ?, ?) WHERE bot='cmo'",
                      ("$." + key, value))

        assert task_allocator.allocate(c) == [(check["id"], "cpo") for check in checks]
        assert all(H.task(c, check["id"])["owner"] == "bot:cpo" for check in checks)
        assert task_allocator.allocate(c) == [], "already assigned checks are not allocated twice"


def test_allocator_never_grants_a_pool_bot_access_to_an_unreadable_private_task(api):
    from backend import task_allocator
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Studio queue", [
            {"name": "Inbox", "status": "open", "flow": {"assign": {"pool": ["cmo"], "slots": 4}}},
            {"name": "Build", "status": "doing"}])
        secret = H.task_create(c, "bot:cpo", "Review the sensitive plan", "Private details.", "bot:cpo",
                               type=typ["id"], private=True, lint=False)
        public = H.task_create(c, "human:ana", "Draft the public plan", "Company work.", "bot:cpo",
                               type=typ["id"], private=False, lint=False)
        assert not H.task_private_readable(c, "bot:cmo", secret)
        assert task_allocator.allocate(c) == [(public["id"], "cmo")]
        secret = H.task(c, secret["id"])
        assert secret["owner"] == "bot:cpo"
        assert not H.task_private_readable(c, "bot:cmo", secret)
        assert H.task(c, public["id"])["owner"] == "bot:cmo"


def test_a_merge_outside_the_flow_moves_the_ticket_to_the_merged_step_from_any_earlier_step(api):
    task = ticket(api, step="Review 1")
    hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="e" * 40, head={"sha": HEAD}))
    assert step(api, task) == "Pending Release"


def test_a_green_head_the_gate_refuses_stays_and_the_builder_hears_what_clears_it(api):
    task = ticket(api)
    flow(api, task, "Review 1 Fixes", after_green="Review 2")
    verdict(api, check(api, task), "changes")
    hook(api, "pull_request", pr_event("synchronize", head={"sha": NEW}))
    _suite(api, "github-actions", "completed", "success", sha=NEW)
    assert step(api, task) == "Blocked", "no receipt at the new head keeps the ticket blocked"
    with api.app.state.store.read() as c:
        told = c.execute("SELECT body FROM messages WHERE conversation_id=? AND json_extract(refs_json,'$.review_gate') "
                         "IS NOT NULL", (task["conversation_id"],)).fetchall()
    assert len(told) == 1 and "receipt" in told[0][0]
    # Tico remembers the refused move and makes it itself once the receipt names the new head.
    from backend import move_retry
    assert move_retry.tick(api.app.state.github_app, force=True) == []
    receipt(api, task, NEW)
    assert move_retry.tick(api.app.state.github_app, force=True) == [task["id"]]
    assert step(api, task) == "Review 2"


def test_qa_fixes_green_new_head_goes_back_to_in_qa(api):
    task = ticket(api, step="In QA")
    flow(api, task, "QA Fixes", after_green="In QA")
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


@pytest.mark.parametrize("hold", ["own subtask", "ask"])
def test_a_verdict_given_while_the_ticket_was_held_moves_it_once_the_hold_clears(api, hold):
    """A person's open subtask holds the ticket in Blocked while its review is out: her own check-titled one, or an
    ask its builder put to her. Once she finishes it, the ticket is back at Review 1, not On Deck, and the verdict
    given meanwhile moves it on."""
    task = ticket(api)
    review = check(api, task)
    with api.app.state.store.transaction() as c:
        if hold == "ask":
            H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="human:ana",
                          note="Is the vendor contract signed?")
            ask = next(H.task(c, i) for i in H.TR.child_ids(c, task["id"]) if H.task(c, i)["owner"] == "human:ana")
        else:
            ask = H.task_create(c, "human:ana", "Security: confirm the vendor contract", "Confirm it.", "human:ana",
                                parent_id=task["id"], lint=False)
            H.task_update(c, "bot:cpo", task["id"], status="waiting", note="Waiting on the vendor contract.")
    assert step(api, task) == "Blocked"
    verdict(api, review, "changes")
    assert step(api, task) == "Blocked", "a held ticket is not at the check's step: the verdict moves nothing yet"
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        H.task_update(c, "human:ana", ask["id"], status="done", note="Yes, it is signed.")
        if hold == "own subtask":
            from backend import task_blocked
            task_blocked.sweep(c, H.now(), [task["id"]])
    assert step(api, task) == "Review 1 Fixes", "back at Review 1, the verdict given meanwhile moves it on"
    handoffs = [m for m in builder_jobs(api) if m["refs"].get(V.HANDOFF) == "verdict"]
    assert len(handoffs) == 1 and handoffs[0]["refs"]["verdict"] == "changes", builder_jobs(api)


def test_a_private_review_is_not_replayed_into_its_wider_parent_when_a_hold_clears(api):
    from backend.check_independence import capture_attempt
    from backend.tests.test_api import unrelated_task_managers

    task = ticket(api)
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c, "cmo")
        review = H.task_create(c, "human:ana", "Review 1: PR #412", f"Check head {HEAD}", "bot:cmo",
                               parent_id=task["id"], private=True, lint=False)
        turn = usage(c, review["id"], "cmo")
        capture_attempt(c, task["id"], review["id"], HEAD, turn)
        assert not H.task_private(c, H.task(c, task["id"]))
        assert not H.task_private_readable(c, task["owner"], review)
        assert H._subtask_result_parent(c, review) is None
        H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="human:ana",
                      note="Is the vendor contract signed?")
        ask = next(H.task(c, i) for i in H.TR.child_ids(c, task["id"])
                   if H.task(c, i)["owner"] == "human:ana")
    assert step(api, task) == "Blocked"
    verdict(api, review, "changes")
    assert step(api, task) == "Blocked"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", ask["id"], status="done", note="Yes, it is signed.")
        history = H.task_history(c, task["id"])
        assert not any(e["field"] in ("verdict_wait", "verdict_move") or "gave changes" in (e["note"] or "")
                       for e in history), history
    assert step(api, task) == "Review 1"
