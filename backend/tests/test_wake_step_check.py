"""A wake rule names a stage its pipeline must have (backend/redesign_migration.py check_wakes, wake_problems): an
edit that names a missing stage is refused with the pipeline's real stages; a rule that already names one, or names a
stage since renamed or removed, is flagged in every automation payload, not silently idle."""

import copy
import json

import pytest

from backend.tests.helpers.api import get, headers, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.redesign_migration import db, first_start

STAGES = ["Ready", "QA/PR Rejected"]


def build_pipeline(api, stages=STAGES):
    """A Build pipeline with these stages (and its own Done and Closed), as its record reads back."""
    general = get(api, "pipelines/general")["pipeline"]
    [todo] = general["stages"]
    record = {
        "name": "Build",
        "goal": "Ship what the team builds.",
        "stages": [{**{k: v for k, v in todo.items() if k not in ("id", "requirements")}, "name": n} for n in stages],
    }
    return post(api, "pipelines/build", record)["pipeline"]


def stage_id(pipe, name):
    return next(s["id"] for s in pipe["stages"] if s["name"] == name)


def convert(api, pipe, rules):
    """Write wake rules the old way, naming a stage each, and run the cutover that moves them into a What wakes
    automation (as an install's rules move at upgrade)."""
    with db(api).transaction() as c:
        for position, (rid, stage) in enumerate(rules):
            c.execute(
                "INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
                "created_by,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,1)",
                (
                    rid,
                    "pipeline",
                    pipe["id"],
                    "task_turn",
                    json.dumps({"stage": stage}),
                    "wake",
                    "{}",
                    position,
                    "human:ana",
                    "2026-10-01T00:00:00Z",
                    "2026-10-01T00:00:00Z",
                ),
            )
    first_start(api)


def wake_id(api, pipe):
    [found] = [
        a
        for a in get(api, "automations?limit=500")["automations"]
        if (a.get("wakes") or {}).get("scope_id") == pipe["id"]
    ]
    return found["id"]


def detail(api, aid):
    return get(api, f"automations/{aid}")


def rule_stages(flow):
    """[(rule id, the rule's stage condition)] for a What wakes automation's rules, as the editor shows them."""
    out = []
    for step in flow["steps"]:
        for rule in step["rules"]:
            cond = [c for c in rule["when"] if c["field"] == "task.stage"]
            out.append((rule["then"][0]["args"]["rule"]["id"], cond[0]["value"] if cond else None))
    return out


def set_stage(flow, index, value):
    """The automation steps with rule `index` naming stage `value`, as the editor saves them (the condition and the rule's own record)."""
    steps = copy.deepcopy(flow["steps"])
    rule = steps[0]["rules"][index]
    [cond] = [c for c in rule["when"] if c["field"] == "task.stage"]
    cond["value"] = value
    rule["then"][0]["args"]["rule"]["when"]["stage"] = value
    return steps


def patch(api, aid, body, expected=200):
    r = api.patch(f"/api/v2/automations/{aid}", json=body, headers=headers("ana-test"))
    assert r.status_code == expected, r.text
    return r


def test_a_rule_that_names_no_stage_is_flagged_in_the_list_and_the_detail_and_still_converted(api):
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_qa", "QA Fixes")])
    aid = wake_id(api, pipe)
    listed = [a for a in get(api, "automations?limit=500")["automations"] if a["id"] == aid][0]
    [problem] = listed["problems"]
    assert problem["rule"] == "rule_qa" and problem["stage"] == "QA Fixes"
    assert "QA/PR Rejected" in problem["why"] and "Ready" in problem["why"]
    assert detail(api, aid)["problems"] == listed["problems"]


def test_an_edit_that_names_a_stage_the_pipeline_does_not_have_is_refused_with_its_real_stages(api):
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_ok", "Ready")])
    aid = wake_id(api, pipe)
    flow = detail(api, aid)
    r = patch(api, aid, {"steps": set_stage(flow, 0, "Pending Review")}, expected=422)
    assert "Pending Review" in r.text and "QA/PR Rejected" in r.text and "Ready" in r.text
    assert detail(api, aid)["problems"] == [], "a refused edit saves nothing"


def test_a_valid_stage_saves_by_name_ignoring_case_or_by_id(api):
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_ok", "Ready")])
    aid = wake_id(api, pipe)
    flow = detail(api, aid)
    patch(api, aid, {"steps": set_stage(flow, 0, "qa/pr rejected")})
    assert detail(api, aid)["problems"] == []
    patch(api, aid, {"steps": set_stage(detail(api, aid), 0, stage_id(pipe, "Ready"))})
    assert rule_stages(detail(api, aid)) == [("rule_ok", stage_id(pipe, "Ready"))]
    assert detail(api, aid)["problems"] == []


def test_a_flagged_rule_does_not_block_an_unrelated_edit(api):
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_qa", "QA Fixes")])
    aid = wake_id(api, pipe)
    patch(api, aid, {"name": "Renamed wakes"})
    patch(api, aid, {"steps": detail(api, aid)["steps"]})
    assert [p["rule"] for p in detail(api, aid)["problems"]] == ["rule_qa"]


def test_a_new_rule_naming_a_missing_stage_is_refused(api):
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_ok", "Ready")])
    aid = wake_id(api, pipe)
    flow = detail(api, aid)
    added = copy.deepcopy(flow["steps"][0]["rules"][0])
    added["when"] = [
        {"field": "event", "op": "is", "value": "task_turn"},
        {"field": "task.stage", "op": "is", "value": "In Review"},
    ]
    added["then"][0]["args"]["rule"] = {}
    flow["steps"][0]["rules"].append(added)
    r = patch(api, aid, {"steps": flow["steps"]}, expected=422)
    assert "In Review" in r.text and "Ready" in r.text


@pytest.mark.parametrize("edit", ["to", "next", "destinations"])
def test_a_wake_rule_takes_no_destination_to_or_next_so_an_edit_adding_one_is_refused(api, edit):
    """The wake executor runs one action per rule and no destination: an edit that adds one is refused (422, as every
    refused wake edit is), and nothing changes."""
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_ok", "Ready")])
    aid = wake_id(api, pipe)
    before = detail(api, aid)
    steps = copy.deepcopy(before["steps"])
    rule = steps[0]["rules"][0]
    dests = [
        {
            "id": "legal",
            "name": "Legal",
            "color": "red",
            "then": [{"do": "note", "args": {"bot": "assistant", "text": "Hi"}}],
        }
    ]
    body = {"steps": steps}
    if edit == "to":
        rule["to"], body["destinations"] = "legal", dests
    elif edit == "next":
        rule["next"] = "end"
    else:
        body["destinations"] = dests
    r = patch(api, aid, body, expected=422)
    assert "wake rule" in r.text, r.text
    after = detail(api, aid)
    assert after["steps"] == before["steps"] and not after.get("destinations"), "a refused edit saves nothing"


def test_renaming_or_removing_a_stage_a_wake_rule_names_flags_the_rule(api):
    """A pipeline's stages are its owner's to change; a rule left naming a stage that is gone says so."""
    pipe = build_pipeline(api)
    convert(api, pipe, [("rule_rej", "QA/PR Rejected")])
    aid = wake_id(api, pipe)
    assert detail(api, aid)["problems"] == []
    record = {
        **pipe,
        "stages": [{**s, "name": "Rejected" if s["name"] == "QA/PR Rejected" else s["name"]} for s in pipe["stages"]],
    }
    record.pop("may_change", None)
    post(api, "pipelines/" + pipe["id"], record)
    [problem] = detail(api, aid)["problems"]
    assert (problem["rule"], problem["stage"]) == ("rule_rej", "QA/PR Rejected")
    assert "Rejected" in problem["why"] and "Ready" in problem["why"]
