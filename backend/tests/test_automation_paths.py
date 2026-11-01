"""Paths in the executor (backend/automation_engine.py): in order, first match wins, else Otherwise, nested forks, and
no rejoin; each path keeps the outputs of the steps before the fork."""
from backend import automation_engine as E
from backend.tests.automation_fakes import Services


def note(sid):
    return {"id": sid, "type": "action", "do": "note", "args": {"text": sid}}


def when(field, op, value, **extra):
    return [{"field": field, "op": op, "value": value, **extra}]


DECIDE = {"id": "d", "type": "decide", "set": "mail-triage", "out": "d1"}


def fork(sid, paths, otherwise=None):
    out = {"id": sid, "type": "paths", "paths": [{"name": n, "when": w, "steps": s} for n, w, s in paths]}
    if otherwise is not None:
        out["otherwise"] = {"steps": otherwise}
    return out


def run_of(steps, payload=None, services=None):
    services = services or Services()
    run = E.execute({"name": "t", "owner_id": "person:ana", "trigger": {"type": "event", "name": "x"}, "steps": steps},
                    payload or {}, services)
    return run, [args["text"] for do, args in services.acted]


def test_first_matching_path_wins_in_order():
    run, done = run_of([DECIDE, fork("route", [
        ("archive", when("d1.bucket", "is", "archive"), [note("a")]),
        ("reply", when("d1.bucket", "is", "reply"), [note("r")]),
        ("any", [], [note("any")])], otherwise=[note("o")])])
    assert done == ["r"]
    assert run["steps"][1]["outputs"] == {"path": "reply"}


def test_min_moves_an_unsure_answer_to_otherwise():
    run, done = run_of([DECIDE, fork("route", [
        ("reply", when("d1.bucket", "is", "reply", min=0.9), [note("r")])], otherwise=[note("o")])])
    assert done == ["o"] and run["steps"][1]["outputs"] == {"path": "otherwise"}


def test_no_path_and_no_otherwise_ends_the_run_quietly():
    run, done = run_of([fork("route", [("never", when("x", "is", "y"), [note("n")])])])
    assert done == [] and run["status"] == "ok" and run["steps"][0]["outputs"] == {"path": None}


def test_an_empty_when_always_matches():
    _, done = run_of([fork("route", [("all", [], [note("a")])], otherwise=[note("o")])])
    assert done == ["a"]


def test_nested_forks_three_levels():
    inner = fork("c", [("c1", when("message.size", ">=", 10), [note("deep")])], otherwise=[note("c-o")])
    middle = fork("b", [("b1", when("message.from", "contains", "acme"), [note("b"), inner])])
    run, done = run_of([fork("a", [("a1", [], [middle])])], {"message": {"from": "x@acme.example", "size": 12}})
    assert done == ["b", "deep"]
    assert [r["id"] for r in run["steps"]] == ["a", "b", "b", "c", "deep"]


def test_the_otherwise_of_a_nested_fork():
    inner = fork("c", [("c1", when("message.size", ">=", 100), [note("deep")])], otherwise=[note("c-o")])
    _, done = run_of([fork("a", [("a1", [], [inner])])], {"message": {"size": 1}})
    assert done == ["c-o"]


def test_a_path_reads_outputs_from_before_the_fork():
    services = Services()
    run_of([DECIDE, note("before"), fork("route", [("r", when("d1.is_ask", "is", "yes", min=0.8),
                                                         [{"id": "x", "type": "action", "do": "note",
                                                           "args": {"text": "{{before.detail}}"}}])])],
           services=services)
    assert services.acted[-1] == ("note", {"text": "note done"})


def test_paths_after_find_route_each_item_on_its_own():
    services = Services(items=[{"kind": "bug"}, {"kind": "idea"}, {"kind": "bug"}])
    steps = [{"id": "f", "type": "find", "source": "tasks"},
             fork("route", [("bug", when("item.kind", "is", "bug"), [note("bug")])], otherwise=[note("other")])]
    run, done = run_of(steps, services=services)
    assert done == ["bug", "other", "bug"]
    assert [(r["id"], r.get("item")) for r in run["steps"] if r["id"] == "route"] == [("route", 0), ("route", 1),
                                                                                       ("route", 2)]


def test_a_fork_turned_off_ends_the_thread():
    _, done = run_of([fork("route", [("all", [], [note("a")])]) | {"enabled": False}])
    assert done == []


def test_an_llm_wait_inside_a_path_resumes_on_that_path():
    services = Services()
    flow = {"name": "t", "owner": "person:ana", "trigger": {"type": "event", "name": "x"}, "steps": [
        fork("route", [("p", [], [{"id": "w", "type": "llm", "as": "bot:coo", "prompt": "p", "out": "o"},
                                  {"id": "s", "type": "action", "do": "note", "args": {"text": "{{o.text}}"}}])])]}
    run = E.execute(flow, {}, services)
    assert run["status"] == "waiting"
    E.answer(run, "msg_1", "answered")
    E.advance(flow, run, services)
    assert services.acted == [("note", {"text": "answered"})] and run["status"] == "ok"
