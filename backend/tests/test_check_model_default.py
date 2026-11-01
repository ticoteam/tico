"""The company's check model as the default (backend/check_models.py `auto`, `review_default`): a check named with no
model, and a review handed to a bot set to `review_model: check`, run on another provider than the work's author."""

from backend.store import encode
from backend.tests.test_api import assign, claim, get, post  # noqa: F401
from backend.tests.test_api import api  # noqa: F401  (the fixture)
from backend.tests.test_check_models import GROK, finish, heartbeat, parent_turn

CLAUDE = {"model": "claude-sonnet-5-5", "runtime": "claude", "harness": "claude", "billing": "api"}


def check_row(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT runtime,model,role FROM task_check_models WHERE task_id=?", (task_id,)).fetchone()


def computer(api, machine, grok="ready"):
    """Every runtime installed and signed in, Grok as given."""
    runtimes = {name: {"installed": True, "authenticated": "ready"} for name in ("claude", "codex", "cursor")}
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1, "runtimes": {**runtimes, "grok": {"installed": True, "authenticated": grok}},
                      "bots": {"ops": {"runtime": "claude", "ready": True}}}}, token=machine["token"])


def earlier_run(api, task_id, model, provider, runtime):
    """A finished run on the task by another model: the parent's work as a fallback wrote it."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO turns(id,bot,started_at,finished_at,task_id) VALUES('earlier','ops','2026-01-01T00:00:00Z',"
                  "'2026-01-01T00:05:00Z',?)", (task_id,))
        c.execute("INSERT INTO turn_usage_segments(turn_id,position,input_tokens,output_tokens,model,provider,runtime,"
                  "harness) VALUES('earlier',0,10,10,?,?,?,?)", (model, provider, runtime, runtime))


def test_a_check_with_no_model_skips_every_maker_of_the_parents_work(api):
    machine, parent, turn = parent_turn(api)
    earlier_run(api, parent["id"], "gpt-6-luna", "openai", "codex")
    with api.app.state.store.transaction() as c:
        # The order puts Codex and Claude first: Codex wrote part of the work as a fallback, Claude is the bot's own.
        c.execute("UPDATE registry_metadata SET value=? WHERE key='providers'",
                  (encode({"revision": 1, "enabled": ["anthropic", "xai", "openai", "cursor"], "runtime": "claude",
                           "model": "claude-sonnet-5-5", "check_models": ["codex", "claude", "grok", "cursor"]}),))
    computer(api, machine)
    sub = post(api, "tasks", {"owner_id": "ops", "title": "Check the import fix", "body": "Read the PR.",
                              "check_model": "auto", "relations": [{"task": parent["id"], "kind": "parent"}]},
               turn["token"])
    assert tuple(check_row(api, sub["id"])) == ("grok", "grok-4.6", "check")
    # Grok signed out on the bot's computer: the next in order.
    computer(api, machine, grok="missing")
    later = post(api, "tasks", {"owner_id": "ops", "title": "Check it on another", "body": "Read the PR.",
                                "check_model": "auto", "relations": [{"task": parent["id"], "kind": "parent"}]},
                 turn["token"])
    assert tuple(check_row(api, later["id"]))[:2] == ("cursor", "cursor-auto")
    # With only the author's provider on, it runs there rather than leaving the review unfiled, and says why.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='providers'",
                  (encode({"revision": 2, "enabled": ["anthropic"], "runtime": "claude", "model": "claude-sonnet-5-5"}),))
    alone = post(api, "tasks", {"owner_id": "ops", "title": "Check it again", "body": "Read the PR.", "check_model": "auto",
                                "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    assert tuple(check_row(api, alone["id"]))[:2] == ("claude", "claude-sonnet-5-5")
    with api.app.state.store.read() as c:
        why = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='check_model'", (alone["id"],)).fetchone()
    assert why and "other than Anthropic" in why[0] and "author's own" in why[0], why


def test_a_review_for_a_bot_set_to_review_on_the_check_model_runs_there_and_says_which(api):
    machine, parent, turn = parent_turn(api)
    finish(api, machine, turn, CLAUDE)          # the parent's work ran on Claude
    plain = post(api, "tasks", {"owner_id": "cpo", "title": "Review PR 700", "body": "https://github.com/ticoteam/tico/pull/700",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    assert check_row(api, plain["id"]) is None, "a bot not set to review on the check model keeps its own"
    def review_model(value):
        return post(api, "bots/cpo/definition", {"review_model": value, "expected_revision":
                    next(b for b in get(api, "bots") if b["slug"] == "cpo")["revision"]})
    review_model("check")
    review = post(api, "tasks", {"owner_id": "cpo", "title": "Review PR 701", "body": "https://github.com/ticoteam/tico/pull/701",
                                 "relations": [{"task": parent["id"], "kind": "parent"}]})
    assert tuple(check_row(api, review["id"])) == ("grok", "grok-4.6", "review")
    # Its own top-level work is no review of someone else's: its own model.
    own = post(api, "tasks", {"owner_id": "cpo", "title": "Write the weekly notes", "body": "As every week."})
    assert check_row(api, own["id"]) is None
    # A subtask under its own task is no one else's work either.
    mine = post(api, "tasks", {"owner_id": "cpo", "title": "Split the notes", "body": "Two parts.",
                               "relations": [{"task": own["id"], "kind": "parent"}]})
    assert check_row(api, mine["id"]) is None
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='check_model'", (mine["id"],)).fetchone()
    # A person's task with no runs has no known author: its own model, and the history says why.
    person = post(api, "tasks", {"owner_id": "human:ana", "title": "Draft the launch post", "body": "Mine."})
    unknown = post(api, "tasks", {"owner_id": "cpo", "title": "Review my draft", "body": "Please.",
                                  "relations": [{"task": person["id"], "kind": "parent"}]})
    assert check_row(api, unknown["id"]) is None
    with api.app.state.store.read() as c:
        why = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='check_model'", (unknown["id"],)).fetchone()
    assert why and "author is unknown" in why[0]

    assign(api, machine, "cpo")
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"},
                                                         "grok": {"installed": True, "authenticated": "ready"}},
                      "bots": {"cpo": {"runtime": "grok", "ready": True}}}}, token=machine["token"])
    run = claim(api, machine)
    while run["task"]["id"] != review["id"]:     # the bot's other two tasks, on its own model
        assert run["config"]["runtime"] != "grok"
        finish(api, machine, run)
        run = claim(api, machine)
    assert run["task"]["id"] == review["id"] and (run["config"]["runtime"], run["config"]["model"]) == ("grok", "grok-4.6")
    finish(api, machine, run, GROK, during=lambda: post(
        api, "tasks/" + review["id"], {"status": "done", "note": "Verdict: ok to merge.", "verdict": "approve", "verdict_head": "a" * 40, "version": run["task"]["version"]},
        run["token"]))
    shown = get(api, "tasks/" + review["id"])
    assert shown["check_model"]["model"] == "grok-4.6" and shown["runs"][0]["ran_on"]["family"] == "xai"
    with api.app.state.store.read() as c:
        notices = [r[0] for r in c.execute("SELECT body FROM messages WHERE recipient_id='bot:ops' AND kind='notice' "
                                           "AND body LIKE '%Review PR 701%'")]
    assert len(notices) == 1 and "Review done (ran on grok-4.6)" in notices[0], notices
    review_model("own")
    after = post(api, "tasks", {"owner_id": "cpo", "title": "Review PR 702", "body": "https://github.com/ticoteam/tico/pull/702",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    assert check_row(api, after["id"]) is None, "turned off, its reviews run on its own model again"
    # Set again, with only the author's provider on: still reviewed on the check model, and the history says why.
    review_model("check")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='providers'",
                  (encode({"revision": 9, "enabled": ["anthropic"], "runtime": "claude", "model": "claude-sonnet-5-5"}),))
    alone = post(api, "tasks", {"owner_id": "cpo", "title": "Review PR 703", "body": "https://github.com/ticoteam/tico/pull/703",
                                "relations": [{"task": parent["id"], "kind": "parent"}]})
    assert tuple(check_row(api, alone["id"]))[:2] == ("claude", "claude-sonnet-5-5")
    with api.app.state.store.read() as c:
        why = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='check_model'", (alone["id"],)).fetchone()
    assert why and "other than Anthropic" in why[0] and "author's own" in why[0], why
