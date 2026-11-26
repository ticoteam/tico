"""The runner wire contract (contracts/runner, docs/runner-wire-contract.md) is what the server really sends and takes.

Each test drives the real endpoints with the bodies the Rust runner sends, checks every request and answer against
the contract, and keeps each exchange as a golden fixture (contracts/runner/fixtures) that runner-rs/src/wire.rs
parses too. A fixture whose shape no longer matches fails here; after a deliberate change, rewrite them with
TICO_WRITE_WIRE_FIXTURES=1 and keep the diff in review. The skew tests replay each kept release's fixtures against
this server (old runner, new server) and check this contract reads each release's answers (new runner, old server).
"""

import copy
import json
import os
import re
import uuid


from backend import models as M
from backend.store import H
from backend.tests.helpers.api import assign, headers, post, runner  # noqa: F401  (the api fixture)
from backend.tests.helpers.events import live  # noqa: F401  (a listening server for the event stream)
from backend.tests.helpers.org_rule_check import Judge, color_rule, ruled  # noqa: F401  (ruled: a fixture)
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES
from scripts import check_wire_contract as W

CONTRACT = W.load()
WIRE, REQUESTS = CONTRACT["wire"], CONTRACT["requests"]
FIXTURES = W.CONTRACT / W.FIXTURES
WRITE = os.environ.get("TICO_WRITE_WIRE_FIXTURES") == "1"
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def ref(name):
    return {"$ref": "#/$defs/" + name}


def normalized(value):
    """`value` with what changes on every run replaced: ids by stable placeholders, times by one time, the turn's
    bearer token by a placeholder. The same id keeps the same placeholder, so references between fields hold."""
    ids = {}

    def walk(item, key=None):
        if isinstance(item, dict):
            return {k: walk(v, k) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v, key) for v in item]
        if isinstance(item, str):
            if key == "token":
                return "turn-token"
            item = TIME.sub("2026-01-01T00:00:00.000000Z", item)
            return UUID.sub(
                lambda m: ids.setdefault(m.group(0), "00000000-0000-4000-8000-%012d" % (len(ids) + 1)), item
            )
        return item

    return walk(value)


def shape_differences(golden, actual, schema, root, where="$"):
    """How `actual` differs from `golden` in what the contract fixes: the fields of fully declared objects, the JSON
    type of each value, and true/false/null. Values that change from run to run (counts, times, text) and objects the
    contract leaves open are not compared."""
    schema = W.resolve(schema, root)
    if json_kind(golden) != json_kind(actual):
        return ["%s: %s became %s" % (where, json_kind(golden), json_kind(actual))]
    if isinstance(golden, bool) or golden is None:
        return [] if golden == actual else ["%s: %r became %r" % (where, golden, actual)]
    for key in ("oneOf", "anyOf"):
        if key in schema:
            options = [o for o in schema[key] if not W.errors(actual, o, root)]
            return shape_differences(golden, actual, options[0], root, where) if options else []
    if isinstance(golden, dict) and schema.get("additionalProperties") is False:
        found = []
        if set(golden) != set(actual):
            found.append("%s: fields %s became %s" % (where, sorted(golden), sorted(actual)))
        for key in set(golden) & set(actual):
            found += shape_differences(
                golden[key], actual[key], schema.get("properties", {}).get(key, {}), root, where + "." + key
            )
        return found
    if isinstance(golden, dict):
        # An open object may gain fields, never lose one: a runner in the window may still read it (a renamed
        # column keeps its old wire name, backend/runner_wire.py).
        missing = sorted(set(golden) - set(actual))
        found = ["%s: lost fields %s" % (where, missing)] if missing else []
        for key in set(golden) & set(actual):
            found += missing_fields(golden[key], actual[key], where + "." + key)
        return found
    if isinstance(golden, list) and golden and actual and isinstance(schema.get("items"), dict):
        return shape_differences(golden[0], actual[0], schema["items"], root, where + "[0]")
    return missing_fields(golden, actual, where)


def missing_fields(golden, actual, where):
    """The fields of `golden`'s objects, at any depth, that `actual` no longer has."""
    if isinstance(golden, dict) and isinstance(actual, dict):
        missing = sorted(set(golden) - set(actual))
        found = ["%s: lost fields %s" % (where, missing)] if missing else []
        for key in set(golden) & set(actual):
            found += missing_fields(golden[key], actual[key], where + "." + key)
        return found
    if isinstance(golden, list) and isinstance(actual, list) and golden and actual:
        return missing_fields(golden[0], actual[0], where + "[0]")
    return []


def json_kind(value):
    return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else W.json_type(value)


def keep(name, case, request, status, answer, schema):
    """Write or check the golden fixture `<name>.<case>.json`: the exchange as the runner sees it."""
    path = FIXTURES / ("%s.%s.json" % (name, case))
    fixture = normalized({"endpoint": name, "request": request, "status": status, "answer": answer})
    if WRITE:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
        return
    assert path.exists(), "no golden fixture %s: run with TICO_WRITE_WIRE_FIXTURES=1" % path.name
    golden = json.loads(path.read_text())
    assert golden["status"] == status, (path.name, golden["status"], status)
    end = WIRE["endpoints"][name]
    if "request" in end and request is not None:
        problems = shape_differences(golden["request"], fixture["request"], ref(end["request"]), REQUESTS, "request")
        assert not problems, (path.name, problems)
    problems = shape_differences(golden["answer"], fixture["answer"], schema, WIRE, "answer")
    assert not problems, (path.name + " changed shape; rewrite with TICO_WRITE_WIRE_FIXTURES=1 if deliberate", problems)


def exchange(api, name, token, body, case, aid="", key=None, expected=200):
    """POST the runner's `body` to endpoint `name`; check both sides against the contract and keep the fixture."""
    end = WIRE["endpoints"][name]
    problems = W.errors(body, ref(end["request"]), REQUESTS)
    # The schema and the server agree: a body the server refuses as invalid is one the schema refuses too.
    assert bool(problems) == (expected == 422), (name, "request", problems)
    response = api.post(end["path"].replace("{aid}", aid), json=body, headers=headers(token, key))
    assert response.status_code == expected, (name, response.status_code, response.text[:600])
    answer = response.json()
    schema = ref(end["answer"] if expected == 200 else WIRE["error"])
    problems = W.errors(answer, schema, WIRE)
    assert not problems, (name, case, problems, json.dumps(answer)[:600])
    keep(name, case, body, response.status_code, answer, schema)
    return answer


def readiness(bot):
    """The structured readiness report runner-rs/src/readiness.rs sends, for one ready bot on the fake runtime."""
    return {
        "schema_version": 1,
        "worktrees": True,
        "runtimes": {"fake": {"installed": True, "authenticated": "ready", "version": "1.0.0"}},
        "bots": {
            bot: {
                "ready": True,
                "runtime": "fake",
                "repository_present": True,
                "configuration_valid": True,
                "published": True,
            }
        },
    }


def heartbeat_body(bot):
    """A heartbeat as runner-rs/src/runner.rs `maintain` builds it on a Docker runner following a release (rc.1:
    `version` is the protocol version, `release` the build's, sent while following the server's release)."""
    return {
        "version": "0.5.4",
        "platform": "Linux-6.8-x86_64",
        "capacity": 4,
        "capabilities": list(RUNNER_CAPABILITIES),
        "readiness": readiness(bot),
        "mail_agent_instructions": {},
        "agent_instructions": {},
        "release": "0.6.0",
        "kind": "docker",
        "update": {"state": "idle", "target": "", "error": ""},
        "profiles": [],
        "worktrees": [],
    }


CLAIM = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
SNAPSHOT = {"at": 1767225600.0, "cpu_pct": 12.5, "mem_pct": 40.0, "running_turns": 1}


def test_the_retired_packet_capability_is_still_accepted_on_a_heartbeat(api):
    computer = runner(api)
    assign(api, computer, "ops")
    body = heartbeat_body("ops")
    # The retired Nightly memory tidy pilot's: a runner of the previous release in the window still sends it.
    body["capabilities"].append("memory_tidy_packets_v1")
    assert not W.errors(body, ref("Heartbeat"), REQUESTS)
    post(api, "runners/heartbeat", body, token=computer["token"])


def test_claim_keeps_attempt_credentials_behind_the_advertised_capability(api):
    from backend.tests.helpers.api import put
    from backend.tests.helpers.credentials import setup
    from backend.tests.helpers.model_subscriptions import add, company, on

    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    computer = runner(api)
    assign(api, computer, "ops")
    body = heartbeat_body("ops")
    body["capabilities"].append("attempt_credentials_v1")
    body["readiness"]["runtimes"]["claude"] = {"installed": True, "authenticated": "missing"}
    body["readiness"]["bots"]["ops"]["runtime"] = "claude"
    assert not W.errors(body, ref("Heartbeat"), REQUESTS)
    post(api, "runners/heartbeat", body, token=computer["token"])
    sid = add(api)["id"]
    put(api, "bots/ops/subscriptions", {"subscriptions": {"claude": {"kind": "plan", "subscription_id": sid}}})
    post(api, "chat/ops", {"text": "Check the provider."})
    answer = post(api, "jobs/claim", CLAIM, token=computer["token"])
    assert not W.errors(answer, ref("ClaimAnswer"), WIRE)
    assert answer["attempt"]["attempt_credential_required"]
    assert answer["attempt"]["attempt_credential"]["id"] == sid


def test_todays_runner_no_longer_advertises_or_gates_the_retired_packet_capability():
    """The packet path is gone from runner-rs: its capability is neither advertised (lanes.rs CAPABILITIES) nor listed
    under the contract's `gated` (what today's runner may send), so the server accepts it only from older runners."""
    lanes = (W.ROOT / "runner-rs" / "src" / "lanes.rs").read_text()
    advertised = re.search(r"pub const CAPABILITIES: &\[&str\] = &\[(.*?)\n\];", lanes, re.S)
    assert advertised and '"task_lanes_v2"' in advertised.group(1)
    assert '"memory_tidy_packets_v1"' not in lanes
    assert not [k for k in WIRE["gated"] if "memory_tidy_packets_v1" in k]
    manifests = sorted((W.ROOT / "runner-rs" / "harnesses").glob("*.toml"))
    assert manifests
    assert not [m.name for m in manifests if "packet_only" in m.read_text()]


def turn(api, bot="ops"):
    computer = runner(api)
    assign(api, computer, bot)
    exchange(api, "heartbeat", computer["token"], heartbeat_body(bot), "ok")
    exchange(api, "claim", computer["token"], CLAIM, "idle", key=str(uuid.uuid4()))
    post(api, "chat/" + bot, {"text": "Please summarize the current work."})
    attempt = exchange(api, "claim", computer["token"], CLAIM, "attempt", key=str(uuid.uuid4()))["attempt"]
    assert attempt and attempt["bot"] == bot
    return computer, attempt


def test_the_turn_path_sends_and_takes_what_the_contract_says(api):
    computer, attempt = turn(api)
    token, aid = computer["token"], attempt["id"]
    assert exchange(api, "start_place", token, {}, "reserved", aid=aid)["reserved"] is True
    exchange(
        api, "started", token, {"thread_id": "thread-1", "load_snapshot": SNAPSHOT}, "ok", aid=aid, key="started:" + aid
    )
    events = {
        "events": [
            {"seq": 1, "kind": "status", "payload": {"waiting": ""}},
            {"seq": 2, "kind": "tool", "payload": {"name": "Read", "tool": "Read", "phase": "tool_execution_start"}},
            {"seq": 3, "kind": "delta", "payload": {"text": "Acme's summary", "delta_kind": "text"}},
            {"seq": 4, "kind": "tokens", "payload": {"input": 120, "output": 30, "total": 150}},
            {"seq": 5, "kind": "diagnostic", "payload": {"text": "Compacting the saved session before this turn."}},
            {"seq": 6, "kind": "message", "payload": {"text": "Acme's summary is ready", "final": False}},
        ]
    }
    assert exchange(api, "events", token, events, "ok", aid=aid, key="events:%s:1:6" % aid)["ack_seq"] == 6
    exchange(api, "renew", token, {}, "ok", aid=aid)
    completion = {
        "outcome": "completed",
        "text": "Acme's summary is ready",
        "last_seq": 6,
        "load_snapshot": SNAPSHOT,
        "usage": {
            "input_tokens": 120,
            "cached_tokens": 0,
            "output_tokens": 30,
            "model": "fake-1",
            "runtime": "fake",
            "harness": "fake",
            "billing": "api",
        },
        "session": {"kind": "new", "why": "first"},
    }
    answer = exchange(api, "complete", token, completion, "ok", aid=aid, key="complete:" + aid)
    assert answer["outcome"] == "completed" and answer["message"]


def test_refusals_on_the_turn_path_carry_the_error_envelope(api):
    computer, attempt = turn(api)
    token, aid = computer["token"], attempt["id"]
    exchange(api, "started", token, {"thread_id": "thread-1"}, "ok-bare", aid=aid, key="started:" + aid)
    # A second start of the same attempt under a new key, a gap in the event sequence, and a renewal a Stop answers.
    exchange(api, "started", token, {"thread_id": "thread-1"}, "already-started", aid=aid, expected=409)
    gap = {"events": [{"seq": 3, "kind": "delta", "payload": {"text": "late"}}]}
    assert exchange(api, "events", token, gap, "gap", aid=aid, expected=409)["error"]["code"] == "event_gap"
    post(api, "bots/ops/stop", {})
    assert exchange(api, "renew", token, {}, "stop", aid=aid)["stop"] is True
    # The runner's own refusal ladder reads 422 details: an unknown field is named in them.
    refused = exchange(
        api, "heartbeat", token, {**heartbeat_body("ops"), "from_the_future": True}, "unknown-field", expected=422
    )
    assert refused["error"]["code"] == "validation" and "from_the_future" in refused["error"]["detail"]
    # A lease that lapsed: the runner stops the turn (lease.rs, a definite answer).
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), aid))
    assert exchange(api, "renew", token, {}, "stale", aid=aid, expected=409)["error"]["code"] == "stale_lease"


def test_late_output_is_historical_and_a_late_result_is_still_taken(api):
    computer, attempt = turn(api)
    token, aid = computer["token"], attempt["id"]
    exchange(api, "started", token, {"thread_id": "thread-1"}, "ok-bare", aid=aid, key="started:" + aid)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (H.shift(H.now(), seconds=-1), aid))
    late = {"events": [{"seq": 1, "kind": "message", "payload": {"text": "done after all"}}]}
    assert exchange(api, "events", token, late, "historical", aid=aid)["historical"] is True
    # The next claim's sweep expires the lapsed lease; the finished turn's result is then still taken.
    exchange(api, "claim", token, CLAIM, "idle-after-lapse", key=str(uuid.uuid4()))
    done = {"outcome": "completed", "text": "done after all", "last_seq": 1}
    exchange(api, "complete", token, done, "late", aid=aid, key="complete:" + aid)


# Route-level settlement characterization (task E-1, before any execution.complete extraction): the completion route
# (fleet_runner_routes.register_completion, app._mutation, store.mutate) as the runner meets it, under its own
# `complete:{aid}` Idempotency-Key. Plain posts, not `exchange`: these pin behaviour, not new golden fixtures.


def complete(api, token, aid, body, key):
    return api.post("/api/v2/attempts/%s/complete" % aid, json=body, headers=headers(token, key))


def settlement(api, aid, key):
    """What one completion leaves behind: the run, its job, the bot's replies, the event log, the saved receipt."""
    with api.app.state.store.read() as c:
        run = c.execute("SELECT state, exit, summary, finished_at, job_id FROM attempts WHERE id=?", (aid,)).fetchone()
        return {
            "run": tuple(run[:4]),
            "job": c.execute("SELECT state FROM jobs WHERE id=?", (run["job_id"],)).fetchone()[0],
            "replies": [
                tuple(r) for r in c.execute("SELECT id, body FROM messages WHERE sender_id='bot:ops' ORDER BY rowid")
            ],
            "events": c.execute("SELECT count(*) FROM events").fetchone()[0],
            "receipts": c.execute(
                "SELECT count(*) FROM idempotency WHERE operation=? AND key=?",
                ("/api/v2/attempts/%s/complete" % aid, key),
            ).fetchone()[0],
        }


def started_with_one_event(api):
    computer, attempt = turn(api)
    token, aid = computer["token"], attempt["id"]
    post(api, "attempts/%s/started" % aid, {"thread_id": "thread-1"}, token, key="started:" + aid)
    first = {"events": [{"seq": 1, "kind": "message", "payload": {"text": "Acme's summary is ready"}}]}
    post(api, "attempts/%s/events" % aid, first, token, key="events:%s:1:1" % aid)
    return computer, attempt


def test_a_completion_whose_answer_was_lost_replays_its_receipt_and_settles_once(api):
    """Invariant 1. The runner never heard the answer to its completion and sends it again under the same
    `complete:{aid}` key: it gets the saved receipt back, and nothing is settled, posted or logged a second time."""
    computer, attempt = started_with_one_event(api)
    token, aid, key = computer["token"], attempt["id"], "complete:" + attempt["id"]
    body = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 1}
    first = complete(api, token, aid, body, key)
    assert first.status_code == 200, first.text
    after_first = settlement(api, aid, key)
    assert after_first["run"][:3] == ("completed", "completed", "Acme's summary is ready")
    assert after_first["job"] == "completed" and after_first["receipts"] == 1
    assert [text for _, text in after_first["replies"]] == ["Acme's summary is ready"]

    replay = complete(api, token, aid, body, key)
    assert replay.status_code == 200, replay.text
    assert replay.json()["attempt_id"] == aid and replay.json()["outcome"] == "completed"
    assert replay.json()["message"]["id"] == first.json()["message"]["id"] == after_first["replies"][0][0]
    assert settlement(api, aid, key) == after_first

    # The key binds this body: other content under it is refused, and nothing moves.
    changed = complete(api, token, aid, {**body, "text": "A different answer"}, key)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    # A new key on the settled run is not a second completion: the runner is told to stop it.
    fresh = complete(api, token, aid, body, "complete:%s:again" % aid)
    assert fresh.status_code == 409, fresh.text
    assert fresh.json()["error"]["code"] == "stale_lease" and fresh.json()["error"]["retryable"] is False
    assert settlement(api, aid, key) == after_first


def test_an_event_gap_is_not_saved_under_the_key_and_the_same_key_settles_once_the_events_are_in(api):
    """Invariant 2, the gap. A completion that names events the server has not got is refused before anything is
    written or saved: the run stays live, and the same key settles it once the missing event is uploaded."""
    computer, attempt = started_with_one_event(api)
    token, aid, key = computer["token"], attempt["id"], "complete:" + attempt["id"]
    body = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 2}
    before = settlement(api, aid, key)
    gap = complete(api, token, aid, body, key)
    assert gap.status_code == 409 and gap.json()["error"]["code"] == "event_gap"
    assert "idempotency_refused" not in gap.json()["error"]
    assert settlement(api, aid, key) == before and before["run"][0] == "running" and before["receipts"] == 0
    assert complete(api, token, aid, body, key).json()["error"]["code"] == "event_gap"  # still missing: same answer

    second = {"events": [{"seq": 2, "kind": "status", "payload": {"state": "idle"}}]}
    post(api, "attempts/%s/events" % aid, second, token, key="events:%s:2:2" % aid)
    done = complete(api, token, aid, body, key)
    assert done.status_code == 200 and done.json()["outcome"] == "completed", done.text
    after = settlement(api, aid, key)
    assert after["run"][0] == "completed" and after["receipts"] == 1 and len(after["replies"]) == 1


def test_a_permanent_refusal_is_the_same_answer_on_every_retry_and_settles_nothing(api):
    """Invariant 2, the permanent kind. A completion from a computer that does not own the run is refused on every
    retry, under the run's key or a new one: nothing is settled or saved, and the owner's own completion still settles
    the run afterwards."""
    computer, attempt = started_with_one_event(api)
    token, aid, key = computer["token"], attempt["id"], "complete:" + attempt["id"]
    body = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 1}
    intruder = runner(api, "ana", label="Another Mac")
    before = settlement(api, aid, key)
    for retry_key in (key, key, "complete:%s:again" % aid):
        foreign = complete(api, intruder["token"], aid, body, retry_key)
        assert foreign.status_code == 403, foreign.text
        assert foreign.json()["error"]["retryable"] is False and "idempotency_refused" not in foreign.json()["error"]
    assert settlement(api, aid, key) == before
    assert before["run"][0] == "running" and before["replies"] == [] and before["receipts"] == 0
    owned = complete(api, token, aid, body, key)
    assert owned.status_code == 200 and owned.json()["outcome"] == "completed", owned.text
    assert settlement(api, aid, key)["receipts"] == 1


class JudgeThatNarrowsAccess(Judge):
    """The rule-check model, answering as usual; while it is asked, `then` runs as a concurrent write would: after the
    completion's rule check began and before its settlement write."""

    def __init__(self, store, then):
        super().__init__(store)
        self.then = then

    def __call__(self, state, questions, label=None):
        answer = super().__call__(state, questions, label)
        if not str(label or "").startswith("rule-part@") and self.then:
            then, self.then = self.then, None
            with self.store.transaction() as c:
                then(c)
        return answer


def test_a_run_that_loses_access_to_what_it_read_during_the_rule_check_is_refused_before_it_settles(ruled):
    """Invariant 3. A private task the bot cannot read joins the run's inputs while its reply's rule check is out (the
    check passed). The settlement write checks access again under its lock: it refuses, writes nothing, saves no
    receipt, and the same key gets the same refusal. The runner is told to stop."""
    api, _ = ruled
    color_rule(api)
    computer, attempt = started_with_one_event(api)
    token, aid, key = computer["token"], attempt["id"], "complete:" + attempt["id"]

    def read_a_private_task(c):
        plan = H.task_create(c, "human:ben", "Private plan", "Secret.", "human:ben", lint=False, private=True)
        room = c.execute(
            "SELECT m.conversation_id FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.id=?",
            (attempt["job_id"],),
        ).fetchone()[0]
        note = H._write_message(
            c,
            H.TICO_ACTOR,
            "bot:ops",
            "Plan notes",
            H.conversation(c, room),
            "notice",
            {"task": plan["id"], "quiet": True},
            None,
            None,
        )
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)", (aid, note["id"]))

    judge = api.app.state.judge = JudgeThatNarrowsAccess(api.app.state.store, read_a_private_task)
    body = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 1}
    before = settlement(api, aid, key)
    refused = complete(api, token, aid, body, key)
    assert len(judge.calls) == 1 and judge.then is None  # checked once, and the access changed meanwhile
    assert refused.status_code == 409, refused.text
    expected = {"code": "stale_lease", "detail": "Task access expired; stop this attempt", "retryable": False}
    assert refused.json()["error"] == expected
    again = complete(api, token, aid, body, key)
    assert again.status_code == 409 and again.json()["error"] == expected
    after = settlement(api, aid, key)
    assert after["run"] == before["run"] and after["run"][0] == "running" and after["job"] == "running"
    assert after["replies"] == [] and after["receipts"] == 0


def test_a_replayed_completion_is_checked_for_who_may_read_it_now_not_when_it_was_saved(api):
    """Invariant 3, the answer's own check. The completion settled and its answer was lost; then ops moved to
    another computer. The same key does not hand the saved answer (the reply's words) back to the old computer: it
    is refused, and the reply that was posted stays the only one."""
    computer, attempt = started_with_one_event(api)
    token, aid, key = computer["token"], attempt["id"], "complete:" + attempt["id"]
    body = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 1}
    assert complete(api, token, aid, body, key).status_code == 200
    settled = settlement(api, aid, key)
    assign(api, runner(api, "ana", label="Another Mac"), "ops", generation=1)
    replay = complete(api, token, aid, body, key)
    assert replay.status_code == 403, replay.text
    assert replay.json()["error"]["code"] == "privacy" and "Acme's summary" not in replay.text
    assert replay.json()["error"]["detail"] == "This execution is no longer assigned to this computer"
    after = settlement(api, aid, key)
    assert {k: after[k] for k in ("run", "job", "replies", "receipts")} == {
        k: settled[k] for k in ("run", "job", "replies", "receipts")
    }


def test_a_too_old_runner_is_told_why_in_the_claim_answer(api):
    """The server's floor (runner_versions.MIN_RUNNER_RELEASE) pauses an older runner through the claim answer."""
    from backend import runner_versions

    computer = runner(api)
    assign(api, computer, "ops")
    exchange(api, "heartbeat", computer["token"], {**heartbeat_body("ops"), "release": "0.0.9"}, "old-release")
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    paused = exchange(api, "claim", computer["token"], CLAIM, "paused", key=str(uuid.uuid4()))
    assert paused["attempt"] is None and runner_versions.MIN_RUNNER_RELEASE in paused["paused"]


def test_the_runner_event_stream_frames_follow_the_contract(api, live):
    computer = runner(api)
    assign(api, computer, "ops")
    stream = live(computer["token"], path="/api/v2/runners/me/events")
    ready = stream.until(lambda e: e["event"] == "ready")
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    work = stream.until(lambda e: e["event"] == "runner" and e["data"].get("kind") == "work")
    frames = WIRE["endpoints"]["runner_events"]["stream"]
    for name, frame in (("ready", ready), ("work", work)):
        schema = ref(frames[frame["event"]])
        assert not W.errors(frame["data"], schema, WIRE), (name, W.errors(frame["data"], schema, WIRE))
        keep("runner_events", name, None, 200, frame["data"], schema)


def test_the_committed_requests_are_the_servers_models():
    assert W.stripped(REQUESTS) == W.stripped(W.generate_requests()), (
        "contracts/runner/requests.json is stale: run python scripts/check_wire_contract.py --write-requests"
    )


# --- version skew --------------------------------------------------------------------------------------------------


def kept():
    # A copy below the minimum runner release is not served any more (runner_versions.MIN_RUNNER_RELEASE, 0.6.0).
    return [path for version, path in W.window(W.releases()) if version >= (0, 6, 0)]


def test_the_contract_is_compatible_both_ways_with_every_release_in_the_window():
    assert kept(), "seed a release copy: python scripts/check_wire_contract.py --snapshot <version>"
    for path in kept():
        assert W.breaks(CONTRACT, W.load(path), path.name) == [], path.name


# The recorded requests of one turn, in the order a runner sends them.
TURN = [
    ("heartbeat", "ok"),
    ("claim", "attempt"),
    ("start_place", "reserved"),
    ("started", "ok"),
    ("events", "ok"),
    ("renew", "ok"),
    ("complete", "ok"),
]


def test_an_old_runners_requests_still_drive_this_server(api):
    """Old runner, new server: each kept release's recorded requests, replayed in turn order, are still taken, and
    what this server answers satisfies that release's contract as its runner reads it."""
    for path in kept():
        old = W.load(path)["wire"]
        computer = runner(api, label="Runner " + path.name)
        assign(api, computer, "ops", generation=current_generation(api, "ops"))
        # The computer reports its release first, as every runner does: a computer that never did is not served.
        post(api, "runners/heartbeat", heartbeat_body("ops"), token=computer["token"])
        aid = ""
        for name, case in TURN:
            recorded = json.loads((path / W.FIXTURES / ("%s.%s.json" % (name, case))).read_text())
            request = recorded["request"]
            if name == "heartbeat":
                # The copy's heartbeat was recorded on that line's release candidate (0.6.0's on 0.6.0-rc.1, below the
                # floor); the runner the copy stands for reports the release itself.
                request = {**request, "release": path.name}
            if name == "claim":
                post(api, "chat/ops", {"text": "Please summarize the current work."})
            response = api.post(
                WIRE["endpoints"][name]["path"].replace("{aid}", aid),
                json=request,
                headers=headers(computer["token"], "%s:%s:%s" % (name, aid, uuid.uuid4())),
            )
            assert response.status_code == 200, (path.name, name, response.text[:400])
            answer = response.json()
            problems = lenient_errors(answer, ref(old["endpoints"][name]["answer"]), old)
            assert not problems, (path.name, name, problems)
            if name == "claim":
                assert answer["attempt"], (path.name, answer)
                aid = answer["attempt"]["id"]


def current_generation(api, bot):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT generation FROM assignments WHERE bot=?", (bot,)).fetchone()
    return row[0] if row else 0


def lenient_errors(value, schema, root):
    """The errors a lenient reader (a runner) would have: unknown fields and unknown values of open enums pass."""
    return W.errors(value, opened(copy.deepcopy(W.resolve(schema, root)), root), root)


def opened(schema, root, depth=0):
    if isinstance(schema, dict):
        if "$ref" in schema and depth < 20:
            return opened(copy.deepcopy(W.resolve(schema, root)), root, depth + 1)
        if schema.get("additionalProperties") is False:
            schema["additionalProperties"] = True
        if schema.get("x-open-enum"):
            schema.pop("enum", None)
        return {
            k: (
                opened(v, root, depth + 1)
                if k not in W.CHILD_MAPS
                else {n: opened(s, root, depth + 1) for n, s in v.items()}
            )
            for k, v in schema.items()
        }
    if isinstance(schema, list):
        return [opened(s, root, depth + 1) for s in schema]
    return schema


def test_this_runner_reads_every_answer_a_kept_release_recorded():
    """New runner, old server: each kept release's recorded answers satisfy the current contract, read leniently."""
    for path in kept():
        for fixture_path in sorted((path / W.FIXTURES).glob("*.json")):
            fixture = json.loads(fixture_path.read_text())
            end = WIRE["endpoints"][fixture["endpoint"]]
            if fixture["endpoint"] == "runner_events":
                schema = ref(end["stream"][{"ready": "ready"}.get(fixture_path.stem.split(".")[1], "runner")])
            else:
                schema = ref(end["answer"] if fixture["status"] == 200 else WIRE["error"])
            assert not lenient_errors(fixture["answer"], schema, WIRE), (path.name, fixture_path.name)


def test_one_hour_cache_writes_are_an_optional_field_a_new_runner_sends_only_when_the_server_says_so(api):
    """`cache_write_1h_tokens` (RunUsagePart): an old runner never sends it and is still priced (the server guesses the
    split), and a new runner sends it only once the server's features name `usage_cache_write_1h` (runner-rs
    execute.rs `served`), so an older server never sees it. Without that gate the field would break 0.6.0."""
    for name in ("RunUsagePart", "RunUsage"):
        model = REQUESTS["$defs"][name]
        assert "cache_write_1h_tokens" in model["properties"] and "cache_write_1h_tokens" not in model.get(
            "required", []
        )
    gated = sorted(k for k in WIRE["gated"] if k.endswith("cache_write_1h_tokens"))
    assert gated == [
        "complete.request.usage.cache_write_1h_tokens",
        "complete.request.usage.segments[].cache_write_1h_tokens",
    ]
    ungated = copy.deepcopy(CONTRACT)
    for key in gated:
        del ungated["wire"]["gated"][key]
    assert any(
        p.startswith("new runner, old server") and "complete.request.usage" in p
        for p in W.breaks(ungated, W.load(kept()[0]))
    )
    computer, attempt = turn(api)
    features = api.get("/api/v2/runners/desired", headers=headers(computer["token"])).json()["features"]
    assert features["usage_cache_writes"] is True and features["usage_cache_write_1h"] is True
    token, aid = computer["token"], attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "thread-1"}, token=token)
    part = {"input_tokens": 1, "cache_write_tokens": 3, "cache_write_1h_tokens": 3, "output_tokens": 1}
    body = {"outcome": "completed", "text": "Done.", "last_seq": 0, "usage": {**part, "segments": [part]}}
    assert post(api, f"attempts/{aid}/complete", body, token=token)["outcome"] == "completed"


# --- the check itself ----------------------------------------------------------------------------------------------


def changed(edit):
    new = copy.deepcopy(CONTRACT)
    edit(new["wire"]["$defs"], new["requests"]["$defs"], new["wire"])
    return W.breaks(new, CONTRACT)


def test_the_check_refuses_what_breaks_a_runner_or_server_in_either_direction():
    def drop_answer_field(answers, requests, wire):
        answers["EventsAnswer"]["required"].remove("ack_seq")

    assert any(p.startswith("old runner, new server: events.answer") for p in changed(drop_answer_field))

    def require_new_answer_field(answers, requests, wire):
        answers["RenewAnswer"]["properties"]["renewed_at"] = {"type": "string"}
        answers["RenewAnswer"]["required"].append("renewed_at")

    assert any(p.startswith("new runner, old server: renew.answer") for p in changed(require_new_answer_field))

    def new_optional_answer_field(answers, requests, wire):
        answers["RenewAnswer"]["properties"]["renewed_at"] = {"type": "string"}

    assert changed(new_optional_answer_field) == []

    def new_closed_enum_value(answers, requests, wire):
        answers["CompleteAnswer"]["properties"]["outcome"]["enum"].append("yielded")

    assert any(
        p.endswith('outcome="yielded": the reader does not know this value') for p in changed(new_closed_enum_value)
    )

    def new_open_enum_value(answers, requests, wire):
        answers["RunnerFrame"]["properties"]["kind"]["enum"].append("moved")

    assert changed(new_open_enum_value) == []

    def answer_turns_nullable(answers, requests, wire):
        answers["EventsAnswer"]["properties"]["ack_seq"]["type"] = ["integer", "null"]

    assert any("may send null" in p for p in changed(answer_turns_nullable))

    def server_takes_a_new_request_field(answers, requests, wire):
        requests["Claim"]["properties"]["urgent_only"] = {"type": "boolean"}

    found = changed(server_takes_a_new_request_field)
    assert found and all(p.startswith("new runner, old server") and "urgent_only" in p for p in found)

    def gated_new_request_field(answers, requests, wire):
        requests["Claim"]["properties"]["urgent_only"] = {"type": "boolean"}
        wire["gated"]["claim.request.urgent_only"] = "sent once the heartbeat answer says urgent_only"

    assert changed(gated_new_request_field) == []

    # A field of an optional value (the heartbeat's readiness is `anyOf` object/null): gated by its own path, while a
    # field beside it that is not gated still breaks.
    def gated_readiness_field(answers, requests, wire):
        requests["StructuredReadiness"]["properties"]["sleepy"] = {"type": "boolean"}
        wire["gated"]["heartbeat.request.readiness.sleepy"] = "sent once the config answer says readiness_sleepy"

    assert changed(gated_readiness_field) == []

    def gated_and_ungated_readiness_fields(answers, requests, wire):
        gated_readiness_field(answers, requests, wire)
        requests["StructuredReadiness"]["properties"]["dozy"] = {"type": "boolean"}

    found = changed(gated_and_ungated_readiness_fields)
    assert found and all("readiness.dozy" in p for p in found), found

    def server_requires_a_request_field(answers, requests, wire):
        requests["Claim"]["properties"]["urgent_only"] = {"type": "boolean"}
        requests["Claim"].setdefault("required", []).append("urgent_only")

    assert any(p.startswith("old runner, new server: claim.request") for p in changed(server_requires_a_request_field))

    def server_drops_an_event_kind(answers, requests, wire):
        requests["Event"]["properties"]["kind"]["enum"].remove("goal")

    assert any(p.startswith("old runner, new server: events.request") for p in changed(server_drops_an_event_kind))

    def endpoint_removed(answers, requests, wire):
        del wire["endpoints"]["renew"]

    assert any("renew is gone" in p for p in changed(endpoint_removed))
