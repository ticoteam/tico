"""Which model actually worked a task (GET /api/v2/tasks/{id} `runs[].ran_on`, Runs `ran_on`): a fallback turn names
the model that ran it, never the bot's configured one, and a maker Tico cannot know stays unknown."""
from backend.tests.test_api import assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.test_api import api  # noqa: F401

CLAUDE = {"model": "claude-sonnet-5-5", "runtime": "claude", "harness": "claude", "billing": "subscription"}
CODEX = {"model": "gpt-6.1-sol", "runtime": "codex", "harness": "codex", "billing": "subscription"}
CURSOR = {"model": "cursor-auto", "runtime": "cursor", "harness": "cursor-agent", "billing": "subscription"}


def work(api, machine, title, segments, **extra):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Build it."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": title}, machine["token"])
    parts = [{**s, "input_tokens": tokens, "output_tokens": tokens} for s, tokens in segments]
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": "Built.", "usage": {**parts[-1], "segments": parts}, **extra},
         machine["token"])
    return task["id"], attempt["id"]


def ran_on(api, tid):
    runs = get(api, f"tasks/{tid}")["runs"]
    assert len(runs) == 1
    return runs[0]["ran_on"]


def test_a_claude_bot_that_fell_back_to_codex_shows_codex_and_unknown_stays_unknown(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])

    # Claude hit its limit before spending a token (the runner drops that segment); Codex did the work.
    fell, aid = work(api, machine, "fell back", [(CLAUDE, 0), (CODEX, 50)], fallback="codex", primary_limited=True)
    made = ran_on(api, fell)
    assert made["family"] == "openai" and made["fallback"] == "codex"
    assert [s["model"] for s in made["segments"]] == ["gpt-6.1-sol"]
    with api.app.state.store.read() as c:
        detail = c.execute("SELECT detail_json FROM events WHERE action='attempt.fallback' AND target=?",
                           (aid,)).fetchone()[0]
    assert '"model": "gpt-6.1-sol"' in detail and '"primary_runtime"' in detail
    assert [r["ran_on"]["family"] for r in get(api, "bots/ops/turns") if r["id"] == aid] == ["openai"]

    # A genuine Claude run is Claude's.
    own = ran_on(api, work(api, machine, "own", [(CLAUDE, 40)])[0])
    assert own["family"] == "anthropic" and own["fallback"] is None

    # Both models spent tokens: no single maker did the work.
    mixed = ran_on(api, work(api, machine, "mixed", [(CLAUDE, 10), (CODEX, 30)], fallback="codex")[0])
    assert mixed["family"] is None
    assert [s["family"] for s in mixed["segments"]] == ["anthropic", "openai"]

    # Cursor on auto picks its own model; nothing counted at all is unknown too.
    assert ran_on(api, work(api, machine, "cursor", [(CURSOR, 20)])[0])["family"] is None
    task = post(api, "tasks", {"owner": "ops", "title": "silent", "body": "Build it."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "silent"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Built."},
         machine["token"])
    assert ran_on(api, task["id"]) == {"segments": [], "fallback": None, "family": None}
