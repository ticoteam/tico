"""What a save refuses (backend/automation_flow.py `check`): the flow's shape, 10 paths per fork, 3 levels, paths end
their list (no rejoin), one find, and each step type's fields."""
import copy

import pytest

from backend.automation_flow import Invalid, check

QUESTIONS = {"bucket": {"type": "choice", "instructions": "Where does it go?",
                        "criteria": {"reply": "needs an answer", "archive": "nothing to do"}}}


def flow(*steps, **extra):
    return {"name": "Inbox · ana", "owner": "person:ana", "trigger": {"type": "message", "inbox": "ana@acme.example"},
            "steps": list(steps), **extra}


def fork(sid, n=1, inner=None, otherwise=None):
    """A fork of `n` paths; `inner` goes on the first path (ids are unique in a flow)."""
    out = {"id": sid, "type": "paths", "enabled": True,
           "paths": [{"name": f"p{i}", "when": [], "steps": list(inner or []) if i == 0 else []} for i in range(n)]}
    if otherwise is not None:
        out["otherwise"] = {"steps": otherwise}
    return out


def act(sid, do="note"):
    return {"id": sid, "type": "action", "enabled": True, "do": do, "args": {"text": "hi"}}


def refused(value, words):
    with pytest.raises(Invalid) as caught:
        check(value)
    assert words in str(caught.value), str(caught.value)


def test_every_step_type_saves_and_defaults_fill():
    saved = check(flow(
        {"id": "gate", "type": "filter", "rules": [{"when": [{"field": "message.from", "op": "contains", "value": "news"}],
                                                    "match": "any", "then": [{"do": "archive", "args": {}}], "stop": True}]},
        {"id": "d1", "type": "decide", "questions": QUESTIONS, "level": "medium", "out": "d1"},
        {"id": "d2", "type": "decide", "set": "mail-triage", "out": "d2"},
        {"id": "find", "type": "find", "source": "mail", "query": "is:unread", "since": "last_run", "limit": 5},
        {"id": "draft", "type": "llm", "as": "assistant:ana", "prompt": "Draft a reply to {{message.subject}}", "out": "draft"},
        {"id": "tell", "type": "bot_turn", "bot": "coo", "prompt": "Look"},
        act("note"),
        fork("route", 2, [act("inner")], otherwise=[act("other")]),
        owner="human:ana", spend_cap={"per_30m": 2}, batch={"times": ["08:00", "17:30"], "tz": "Europe/Paris"}))
    assert saved["owner"] == "person:ana" and saved["enabled"] is True and saved["builtin"] is False


@pytest.mark.parametrize("trigger", [
    {"type": "message", "inbox": "ana@acme.example", "send": False},
    {"type": "task_received", "to": "bot:coo"},
    {"type": "schedule", "cron": "0 9 * * 1-5", "tz": "America/New_York"},
    {"type": "webhook", "id": "trg_1"},
    {"type": "listening", "destination": "acme-mentions"},
    {"type": "event", "name": "release.published", "filters": [{"field": "release.tag", "op": "matches", "value": "^v"}]},
])
def test_each_trigger_type_saves(trigger):
    check({**flow(act("a")), "trigger": trigger})


@pytest.mark.parametrize("trigger, words", [
    ({"type": "carrier-pigeon"}, "type is one of"),
    ({"type": "message"}, "inbox"),
    ({"type": "schedule", "cron": "every day"}, "cron"),
    ({"type": "schedule", "cron": "0 9 * * *", "tz": "Mars/Base"}, "time zone"),
    ({"type": "event", "name": "x", "filters": [{"field": "a", "op": "near", "value": 1}]}, "op is one of"),
    ({"type": "event", "name": "x", "filters": [{"field": "a", "op": "matches", "value": "("}]}, "pattern"),
    ({"type": "event", "name": "x", "filters": [{"field": "a", "op": "is", "value": "b", "min": 2}]}, "min"),
])
def test_bad_triggers_are_refused(trigger, words):
    refused({**flow(act("a")), "trigger": trigger}, words)


def test_ten_paths_save_and_eleven_are_refused():
    check(flow(fork("f", 10)))
    refused(flow(fork("f", 11)), "1 to 10 paths")


def test_three_levels_save_and_four_are_refused():
    three = fork("a", 1, [fork("b", 1, [fork("c", 1, [act("x")])])])
    check(flow(three))
    four = copy.deepcopy(three)
    four["paths"][0]["steps"][0]["paths"][0]["steps"][0]["paths"][0]["steps"] = [fork("d", 1, [act("y")])]
    refused(flow(four), "at most 3 levels")
    deep_otherwise = fork("a", 1, otherwise=[fork("b", 1, otherwise=[fork("c", 1, otherwise=[fork("d")])])])
    refused(flow(deep_otherwise), "at most 3 levels")


def test_paths_never_rejoin():
    refused(flow(fork("f", 2), act("after")), "paths end their list")
    refused(flow(fork("f", 1, [fork("g"), act("after")])), "paths end their list")


def test_one_otherwise_a_map():
    bad = fork("f", 1)
    bad["otherwise"] = [{"steps": []}, {"steps": []}]
    refused(flow(bad), "otherwise is a map")


def test_ids_are_unique_across_the_flow():
    refused(flow(act("a"), fork("f", 1, [act("a")])), "unique")
    refused(flow({"type": "action", "do": "note"}), "every step has an id")


def test_one_find_and_only_at_the_top():
    find = {"id": "f1", "type": "find", "source": "tasks"}
    refused(flow(find, {**find, "id": "f2"}), "one find")
    refused(flow(fork("x", 1, [{**find, "id": "f3"}])), "top-level")
    refused(flow({**find, "source": "fax"}), "source")
    refused(flow({**find, "limit": 0}), "limit")


@pytest.mark.parametrize("step, words", [
    ({"id": "s", "type": "teleport"}, "type is one of"),
    ({"id": "s", "type": "action", "do": "launch"}, "do is one of"),
    ({"id": "s", "type": "action", "do": "note", "enabled": "yes"}, "enabled"),
    ({"id": "s", "type": "decide", "out": "d"}, "not both"),
    ({"id": "s", "type": "decide", "set": "mail-triage", "questions": QUESTIONS, "out": "d"}, "not both"),
    ({"id": "s", "type": "decide", "set": "no-such-set", "out": "d"}, "no-such-set"),
    ({"id": "s", "type": "decide", "set": "mail-triage@999", "out": "d"}, "version"),
    ({"id": "s", "type": "decide", "questions": {"q": {"type": "choice"}}, "out": "d"}, "instructions"),
    ({"id": "s", "type": "decide", "questions": QUESTIONS, "level": "max", "out": "d"}, "level"),
    ({"id": "s", "type": "decide", "questions": QUESTIONS}, "out"),
    ({"id": "s", "type": "llm", "as": "person:ana", "prompt": "x", "out": "o"}, "assistant:"),
    ({"id": "s", "type": "llm", "as": "bot:coo", "out": "o"}, "prompt"),
    ({"id": "s", "type": "bot_turn", "bot": "Not A Bot!", "prompt": "x"}, "bot names"),
    ({"id": "s", "type": "filter", "rules": [{"when": [], "match": "most"}]}, "match"),
    ({"id": "s", "type": "filter", "rules": [{"then": [{"do": "explode"}]}]}, "do is one of"),
])
def test_bad_steps_are_refused(step, words):
    refused(flow(step), words)


@pytest.mark.parametrize("extra, words", [
    ({"owner": "team:x"}, "owner"),
    ({"group": "engineering"}, "group"),
    ({"spend_cap": {"per_30m": -1}}, "spend_cap"),
    ({"spend_cap": {"per_hour": 1}}, "spend_cap"),
    ({"batch": {"times": ["8am"]}}, "batch"),
    ({"batch": {"times": []}}, "batch"),
    ({"name": ""}, "name"),
])
def test_bad_automation_fields_are_refused(extra, words):
    refused({**flow(act("a")), **extra}, words)


@pytest.mark.parametrize("pattern", ["(a+)+$", "(a*)*", "(a|aa)+", "(x?)+b", "^(\\w+\\s?)+$", "a" * 201,
                                     "a*a*a*a*a*b", "x+.*y+.*z+"])
def test_patterns_that_backtrack_without_end_or_run_long_are_refused(pattern):
    refused({**flow(act("a")), "trigger": {"type": "event", "name": "x", "filters": [
        {"field": "a", "op": "matches", "value": pattern}]}}, "pattern")


def test_ordinary_patterns_still_save():
    for pattern in (r"invoice \d+", "^(re|fwd):", "(ab){2}", "[a-z]+@acme\\.example", "(foo)+bar", "a*b*c{0,20}d+"):
        check({**flow(act("a")), "trigger": {"type": "event", "name": "x", "filters": [
            {"field": "a", "op": "matches", "value": pattern}]}})
