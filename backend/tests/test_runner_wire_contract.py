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
from backend.tests.test_api import api, assign, headers, post, runner  # noqa: F401  (the api fixture)
from backend.tests.test_events import live  # noqa: F401  (a listening server for the event stream)
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
            return UUID.sub(lambda m: ids.setdefault(m.group(0), "00000000-0000-4000-8000-%012d" % (len(ids) + 1)), item)
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
            found += shape_differences(golden[key], actual[key], schema.get("properties", {}).get(key, {}), root,
                                       where + "." + key)
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
    return {"schema_version": 1, "worktrees": True,
            "runtimes": {"fake": {"installed": True, "authenticated": "ready", "version": "1.0.0"}},
            "bots": {bot: {"ready": True, "runtime": "fake", "repository_present": True, "configuration_valid": True,
                           "published": True}}}


def heartbeat_body(bot):
    """A heartbeat as runner-rs/src/runner.rs `maintain` builds it on a Docker runner following a release."""
    return {"version": "0.5.0", "platform": "Linux-6.8-x86_64", "capacity": 4,
            "capabilities": ["task_lanes_v2", "session_roots_v1", "task_goals_v1", "capacity_continue_v1",
                             "source_continue_v1", "model_sign_out_v1", "model_keys_v1", "model_switch_type_v1",
                             "model_remove_v1", "model_switch_to_key_v1", "turns_at_once_v1", "private_lanes_v1"],
            "readiness": readiness(bot), "mail_agent_instructions": {}, "agent_instructions": {},
            "release": "0.5.0", "kind": "docker", "update": {"state": "idle", "target": "", "error": ""},
            "profiles": [], "worktrees": []}


CLAIM = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
SNAPSHOT = {"at": 1767225600.0, "cpu_pct": 12.5, "mem_pct": 40.0, "running_turns": 1}


def turn(api, bot="ops"):
    computer = runner(api)
    assign(api, computer, bot)
    exchange(api, "heartbeat", computer["token"], heartbeat_body(bot), "ok")
    exchange(api, "claim", computer["token"], CLAIM, "idle", key=str(uuid.uuid4()))
    post(api, "chat/" + bot, {"text": "Please summarize the current work."})
    attempt = exchange(api, "claim", computer["token"], CLAIM, "attempt", key=str(uuid.uuid4()))["attempt"]
    assert attempt and attempt["bot_id"] == bot
    return computer, attempt


def test_the_turn_path_sends_and_takes_what_the_contract_says(api):
    computer, attempt = turn(api)
    token, aid = computer["token"], attempt["id"]
    assert exchange(api, "start_place", token, {}, "reserved", aid=aid)["reserved"] is True
    exchange(api, "started", token, {"thread_id": "thread-1", "load_snapshot": SNAPSHOT}, "ok", aid=aid,
             key="started:" + aid)
    events = {"events": [
        {"seq": 1, "kind": "status", "payload": {"waiting": ""}},
        {"seq": 2, "kind": "tool", "payload": {"name": "Read", "tool": "Read", "phase": "tool_execution_start"}},
        {"seq": 3, "kind": "delta", "payload": {"text": "Acme's summary", "delta_kind": "text"}},
        {"seq": 4, "kind": "tokens", "payload": {"input": 120, "output": 30, "total": 150}},
        {"seq": 5, "kind": "diagnostic", "payload": {"text": "Compacting the saved session before this turn."}},
        {"seq": 6, "kind": "message", "payload": {"text": "Acme's summary is ready", "final": False}},
    ]}
    assert exchange(api, "events", token, events, "ok", aid=aid, key="events:%s:1:6" % aid)["ack_seq"] == 6
    exchange(api, "renew", token, {}, "ok", aid=aid)
    completion = {"outcome": "completed", "text": "Acme's summary is ready", "last_seq": 6, "load_snapshot": SNAPSHOT,
                  "usage": {"input_tokens": 120, "cached_tokens": 0, "output_tokens": 30, "model": "fake-1",
                            "runtime": "fake", "harness": "fake", "billing": "api"},
                  "session": {"kind": "new", "why": "first"}}
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
    refused = exchange(api, "heartbeat", token, {**heartbeat_body("ops"), "from_the_future": True}, "unknown-field",
                       expected=422)
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
    assert W.stripped(REQUESTS) == W.stripped(W.generate_requests()), \
        "contracts/runner/requests.json is stale: run python scripts/check_wire_contract.py --write-requests"


def test_every_fixture_request_is_one_the_server_model_takes():
    """The schema and the server agree: a fixture request is valid under both requests.json and backend/models.py."""
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text())
        end = WIRE["endpoints"][fixture["endpoint"]]
        if fixture["request"] is None or fixture["status"] == 422:
            continue
        assert not W.errors(fixture["request"], ref(end["request"]), REQUESTS), path.name
        getattr(M, end["request"]).model_validate(fixture["request"])


# --- version skew --------------------------------------------------------------------------------------------------

def kept():
    return [path for _, path in W.window(W.releases())]


def test_the_contract_is_compatible_both_ways_with_every_release_in_the_window():
    assert kept(), "seed a release copy: python scripts/check_wire_contract.py --snapshot <version>"
    for path in kept():
        assert W.breaks(CONTRACT, W.load(path), path.name) == [], path.name


# The recorded requests of one turn, in the order a runner sends them.
TURN = [("heartbeat", "ok"), ("claim", "attempt"), ("start_place", "reserved"), ("started", "ok"), ("events", "ok"),
        ("renew", "ok"), ("complete", "ok")]


def test_an_old_runners_requests_still_drive_this_server(api):
    """Old runner, new server: each kept release's recorded requests, replayed in turn order, are still taken, and
    what this server answers satisfies that release's contract as its runner reads it."""
    for path in kept():
        old = W.load(path)["wire"]
        computer = runner(api, label="Runner " + path.name)
        assign(api, computer, "ops", generation=current_generation(api, "ops"))
        aid = ""
        for name, case in TURN:
            recorded = json.loads((path / W.FIXTURES / ("%s.%s.json" % (name, case))).read_text())
            if name == "claim":
                post(api, "chat/ops", {"text": "Please summarize the current work."})
            response = api.post(WIRE["endpoints"][name]["path"].replace("{aid}", aid), json=recorded["request"],
                                headers=headers(computer["token"], "%s:%s:%s" % (name, aid, uuid.uuid4())))
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
        return {k: (opened(v, root, depth + 1) if k not in W.CHILD_MAPS else
                    {n: opened(s, root, depth + 1) for n, s in v.items()}) for k, v in schema.items()}
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
    assert any(p.endswith('outcome="yielded": the reader does not know this value') for p in changed(new_closed_enum_value))

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
