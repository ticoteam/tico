"""Every remote MCP tool, once, through the same door test_mcp.py uses (`/api/v2/mcp`, as the owner).

A rename or a moved schema shows up here as a server error, a tool that stopped answering, or a refusal that changed.
Each tool gets one call built from its own input schema: required arguments filled with seeded, valid values, and an
enum's first choice, so most calls reach the tool's body. A clean refusal (a 4xx) is fine, but only when it is the one
recorded in e2e/mcp/expected-refusals.json, so a new refusal (or a fixed one) makes the test say so.
"""

import json
import os
import re
from pathlib import Path

import pytest

from backend.tests.live import block_outside_network
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_automation_api import NOTE_STEP
from backend.tests.test_mcp import call
from clients import hubtools

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = json.loads((ROOT / "e2e" / "mcp" / "expected-refusals.json").read_text())
SMOKE = "Acme smoke"
# A tool's call needs state that nothing seeds: its refusal is recorded, and its argument set is listed here.
# Each entry sets arguments the schema alone cannot pick (a value that must exist or match a rule).
OVERRIDES = {}
# Values a string must match when its schema gives a pattern (the first candidate that matches is used).
PATTERN_SAMPLES = ("1h", "30m", "never", "2d", "1", SMOKE)
# Set to a file path to write every tool's outcome there instead of checking it: the way the recorded list is
# regenerated after a deliberate change (review the diff of e2e/mcp/expected-refusals.json before committing it).
RECORD = os.environ.get("TICO_E2E_RECORD_REFUSALS", "")


@pytest.fixture
def no_outside_network(monkeypatch):
    """External effects off: no tool can reach a provider."""
    block_outside_network(monkeypatch, "the MCP smoke test has no network")


@pytest.fixture
def statuses(monkeypatch):
    """The HTTP status of each refusal the in-process API raises (the MCP reply carries only the code)."""
    from backend import mcp
    seen = []
    real = mcp.problem

    def record(payload, status, key):
        seen.append(status)
        return real(payload, status, key)
    monkeypatch.setattr(mcp, "problem", record)
    return seen


def seeded(api):
    """A task, a chat with its bot, an incoming trigger and an automation: what the arguments below name."""
    task = post(api, "tasks", {"owner": "ops", "title": "Acme smoke task", "body": "Seeded for the MCP smoke test."})
    message = post(api, "chat/ops", {"text": "Acme smoke message"})
    trigger = post(api, "triggers", {"name": "Acme smoke hook"})["trigger"]
    automation = post(api, "automations", {"name": "Acme smoke automation", "owner": "person:ana", "steps": [NOTE_STEP],
                                           "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}})
    return {"task": task["id"], "conversation": message["conversation_id"], "message": message["id"],
            "trigger": trigger["id"], "automation": automation["id"]}


def seeds_for(tool, seeds):
    """The value a string argument gets, by its name and by the kind of object the tool is about."""
    if tool.startswith("hub_task_") and tool != "hub_task_list":
        return {"id": seeds["task"], "task_id": seeds["task"], "parent": seeds["task"]}
    if tool.startswith(("hub_trigger_", "hub_relay_")):
        return {"id": seeds["trigger"], "trigger": seeds["trigger"]}
    if tool.startswith("hub_automation_"):
        return {"id": seeds["automation"]}
    if tool.startswith(("hub_message_", "hub_conversation_")):
        return {"id": seeds["conversation"], "conversation_id": seeds["conversation"], "in_reply_to": seeds["message"],
                "to": "ops", "bot": "ops"}
    return {"to": "ops", "owner": "ops", "bot": "ops", "slug": "ops", "name": SMOKE, "title": SMOKE, "text": SMOKE,
            "body": SMOKE, "note": SMOKE, "subject": SMOKE}


def value(schema, name, strings, depth=0):
    """A valid value for `schema`: enums take their first choice, required object fields are filled, and a bounded
    string takes its seeded value cut to fit."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or (kind is None and "properties" in schema):
        if depth > 4:
            return {}
        props = schema.get("properties", {})
        return {key: value(props[key], key, strings, depth + 1) for key in schema.get("required", []) if key in props}
    if kind == "array":
        if depth > 4:
            return []
        return [value(schema.get("items", {}), name, strings, depth + 1) for _ in range(max(1, schema.get("minItems", 0)))]
    if kind == "integer" or kind == "number":
        low = schema.get("minimum", 1)
        return min(max(1, low), schema.get("maximum", low))
    if kind == "boolean":
        return name == "dry_run"
    if "pattern" in schema:
        return next(c for c in PATTERN_SAMPLES if re.fullmatch(schema["pattern"], c))
    text = strings.get(name, SMOKE)
    high = schema.get("maxLength", len(text))
    return text[:high] if len(text) >= schema.get("minLength", 0) else text.ljust(schema.get("minLength", 0), "x")


def generated(tool, seeds):
    """One call's arguments for `tool` (a listing entry): its required fields, plus `dry_run` where it has one."""
    schema = tool["inputSchema"]
    strings = seeds_for(tool["name"], seeds)
    args = {key: value(schema["properties"][key], key, strings) for key in schema.get("required", [])}
    if "dry_run" in schema["properties"]:
        args["dry_run"] = True
    return {**args, **OVERRIDES.get(tool["name"], {})}


def outcome(api, name, args, statuses):
    """`ok`, or "<status> <code>" for a refusal the API answered with a status (a protocol refusal such as a missing
    argument has none, so it is its code alone)."""
    statuses.clear()
    is_error, payload = call(api, name, args)
    if not is_error:
        return "ok", None
    code = payload.get("error", "unknown")
    return (f"{statuses[-1]} {code}" if statuses else code), payload


@pytest.mark.slow
def test_every_remote_tool_answers_a_generated_call_as_its_recorded_refusal_or_success(api, no_outside_network,
                                                                                       statuses):
    seeds = seeded(api)
    problems, observed = [], {}
    tools = hubtools.listing(kind="owner")
    assert len(tools) > 400, "the owner's tool list shrank: the smoke test would not cover the surface"
    for tool in tools:
        name = tool["name"]
        try:
            result, payload = outcome(api, name, generated(tool, seeds), statuses)
        except Exception as exc:       # a tool that raises instead of answering is the failure this test is for
            problems.append(f"{name}: raised {type(exc).__name__}: {exc}")
            continue
        if result != "ok":
            observed[name] = result
        if RECORD:
            continue
        if result == "ok":
            if name in EXPECTED:
                problems.append(f"{name}: was refused as {EXPECTED[name]!r} and now succeeds; update expected-refusals.json")
        elif EXPECTED.get(name) != result:
            # A 5xx is a server error unless it is recorded as the server's own answer (a service it needs is not set up).
            problems.append(f"{name}: answered {result!r} (recorded: {EXPECTED.get(name)!r}): {json.dumps(payload)[:200]}")
    if RECORD:
        Path(RECORD).write_text(json.dumps(dict(sorted(observed.items())), indent=1, ensure_ascii=False) + "\n")
        assert not [p for p in problems if "raised" in p], "\n".join(problems)
        return
    stale = sorted(set(EXPECTED) - {t["name"] for t in tools})
    if stale:
        problems.append(f"expected-refusals.json names tools that no longer exist: {', '.join(stale)}")
    assert not problems, "\n".join(problems)


def test_a_scripted_task_runs_over_mcp_from_create_to_done(api):
    """A multi-step task as an external agent would run it: create, comment, assign, complete, then read it back."""
    is_error, made = call(api, "hub_task_create", {"owner": "ben", "title": "Smoke: scripted MCP task",
                                                   "body": "Check the release notes and say what changed."})
    assert not is_error, made
    task_id = made["task"]["id"]

    is_error, comment = call(api, "hub_task_comment", {"id": task_id, "text": "Started on the release notes."})
    assert not is_error, comment

    is_error, moved = call(api, "hub_task_update", {"id": task_id, "owner": "ana", "status": "doing"})
    assert not is_error, moved
    assert moved["task"]["owner_id"] == "human:ana" and moved["task"]["status"] == "doing"

    is_error, done = call(api, "hub_task_done", {"id": task_id, "note": "Release notes checked."})
    assert not is_error, done

    is_error, shown = call(api, "hub_task_show", {"id": task_id})
    assert not is_error, shown
    assert shown["task"]["status"] == "done"
    assert any("Started on the release notes." in str(c) for c in shown.get("comments", [shown]))
