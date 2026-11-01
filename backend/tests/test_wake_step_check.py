"""A wake rule names a step its task type must have (backend/redesign_migration.py check_wakes, wake_problems): an edit
that names a missing step is refused with the type's real steps; a rule that already names one is flagged in every
automation payload, not silently idle; renaming a step moves the rules that name it; deleting a step a rule names is
refused and says which rules."""
import copy
import json

from backend import redesign_migration as M
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (api is a fixture)
from backend.tests.test_redesign_migration import db, first_start

STEPS = [("Ready", "open"), ("QA/PR Rejected", "open"), ("Done", "done")]


def build_type(api):
    typ = post(api, "task-types", {"name": "Build", "steps": [{"name": n, "status": s} for n, s in STEPS]})["type"]
    return typ


def step_id(typ, name):
    return next(s["id"] for s in typ["steps"] if s["name"] == name)


def convert(api, typ, rules):
    """Write wake rules the old way, naming a step each, and run the cutover that moves them into a What wakes
    automation (as an install's rules move at upgrade)."""
    with db(api).transaction() as c:
        for position, (rid, step) in enumerate(rules):
            c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
                      "created_by,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,1)",
                      (rid, "type", typ["id"], "step_entered", json.dumps({"step": step}), "wake", "{}", position,
                       "human:ana", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
    first_start(api)


def wake_id(api, typ):
    [found] = [a for a in get(api, "automations")["automations"] if (a.get("wakes") or {}).get("scope_id") == typ["id"]]
    return found["id"]


def detail(api, aid):
    return get(api, f"automations/{aid}")


def rule_steps(flow):
    """[(rule id, the rule's step condition)] for a What wakes automation's rules, as the editor shows them."""
    out = []
    for step in flow["steps"]:
        for rule in step["rules"]:
            cond = [c for c in rule["when"] if c["field"] == "task.step"]
            out.append((rule["then"][0]["args"]["rule"]["id"], cond[0]["value"] if cond else None))
    return out


def set_step(flow, index, value):
    """The steps with rule `index` naming `value`, as the editor saves them (the condition and the rule's own record)."""
    steps = copy.deepcopy(flow["steps"])
    rule = steps[0]["rules"][index]
    [cond] = [c for c in rule["when"] if c["field"] == "task.step"]
    cond["value"] = value
    rule["then"][0]["args"]["rule"]["when"]["step"] = value
    return steps


def patch(api, aid, body, expected=200):
    r = api.patch(f"/api/v2/automations/{aid}", json=body, headers=headers("ana-test"))
    assert r.status_code == expected, r.text
    return r


def test_a_rule_that_names_no_step_is_flagged_in_the_list_and_the_detail_and_still_converted(api):
    typ = build_type(api)
    convert(api, typ, [("rule_qa", "QA Fixes")])
    aid = wake_id(api, typ)
    listed = [a for a in get(api, "automations")["automations"] if a["id"] == aid][0]
    [problem] = listed["problems"]
    assert problem["rule"] == "rule_qa" and problem["step"] == "QA Fixes"
    assert "QA/PR Rejected" in problem["why"] and "Ready" in problem["why"]
    assert detail(api, aid)["problems"] == listed["problems"]


def test_an_edit_that_names_a_step_the_type_does_not_have_is_refused_with_its_real_steps(api):
    typ = build_type(api)
    convert(api, typ, [("rule_ok", "Ready")])
    aid = wake_id(api, typ)
    flow = detail(api, aid)
    r = patch(api, aid, {"steps": set_step(flow, 0, "Pending Review")}, expected=422)
    assert "Pending Review" in r.text and "QA/PR Rejected" in r.text and "Done" in r.text
    assert detail(api, aid)["problems"] == [], "a refused edit saves nothing"


def test_a_valid_step_saves_by_name_ignoring_case_or_by_id(api):
    typ = build_type(api)
    convert(api, typ, [("rule_ok", "Ready")])
    aid = wake_id(api, typ)
    flow = detail(api, aid)
    patch(api, aid, {"steps": set_step(flow, 0, "qa/pr rejected")})
    assert detail(api, aid)["problems"] == []
    patch(api, aid, {"steps": set_step(detail(api, aid), 0, step_id(typ, "Done"))})
    assert rule_steps(detail(api, aid)) == [("rule_ok", step_id(typ, "Done"))]
    assert detail(api, aid)["problems"] == []


def test_a_flagged_rule_does_not_block_an_unrelated_edit(api):
    typ = build_type(api)
    convert(api, typ, [("rule_qa", "QA Fixes")])
    aid = wake_id(api, typ)
    patch(api, aid, {"name": "Renamed wakes"})
    patch(api, aid, {"steps": detail(api, aid)["steps"]})
    assert [p["rule"] for p in detail(api, aid)["problems"]] == ["rule_qa"]


def test_a_new_rule_naming_a_missing_step_is_refused(api):
    typ = build_type(api)
    convert(api, typ, [("rule_ok", "Ready")])
    aid = wake_id(api, typ)
    flow = detail(api, aid)
    added = copy.deepcopy(flow["steps"][0]["rules"][0])
    added["when"] = [{"field": "event", "op": "is", "value": "step_entered"},
                     {"field": "task.step", "op": "is", "value": "In Review"}]
    added["then"][0]["args"]["rule"] = {}
    flow["steps"][0]["rules"].append(added)
    r = patch(api, aid, {"steps": flow["steps"]}, expected=422)
    assert "In Review" in r.text and "Ready" in r.text


def test_renaming_a_step_moves_the_rules_that_name_it(api):
    typ = build_type(api)
    convert(api, typ, [("rule_rej", "QA/PR Rejected")])
    aid = wake_id(api, typ)
    renamed = [{"id": s["id"], "name": "Rejected" if s["name"] == "QA/PR Rejected" else s["name"], "status": s["status"]}
               for s in typ["steps"]]
    r = api.post("/api/v2/task-types/" + typ["id"], headers=headers(), json={"steps": renamed})
    assert r.status_code == 200, r.text
    assert rule_steps(detail(api, aid)) == [("rule_rej", "Rejected")]
    assert detail(api, aid)["problems"] == []


def test_deleting_a_step_a_wake_rule_names_is_refused_and_an_unnamed_step_goes(api):
    typ = build_type(api)
    convert(api, typ, [("rule_rej", "QA/PR Rejected")])
    keep = [s for s in typ["steps"] if s["name"] != "QA/PR Rejected"]
    r = api.post("/api/v2/task-types/" + typ["id"], headers=headers(),
                 json={"steps": [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in keep]})
    assert r.status_code >= 400 and "QA/PR Rejected" in r.text and "wake rules" in r.text, r.text
    assert "QA/PR Rejected" in [s["name"] for s in get(api, "task-types/" + typ["id"])["type"]["steps"]]

    keep = [s for s in typ["steps"] if s["name"] != "Ready"]
    r = api.post("/api/v2/task-types/" + typ["id"], headers=headers(),
                 json={"steps": [{"id": s["id"], "name": s["name"], "status": s["status"]} for s in keep]})
    assert r.status_code == 200, r.text
    assert "Ready" not in [s["name"] for s in get(api, "task-types/" + typ["id"])["type"]["steps"]]


def test_the_migration_helper_names_the_rules_a_step_is_named_by(api):
    typ = build_type(api)
    convert(api, typ, [("rule_rej", "QA/PR Rejected")])
    with db(api).read() as c:
        named = M.wakes_naming(c, typ["id"], [(step_id(typ, "QA/PR Rejected"), "QA/PR Rejected")])
        assert named and named[0].endswith(": QA/PR Rejected")
        assert M.wakes_naming(c, typ["id"], [(step_id(typ, "Done"), "Done")]) == []
