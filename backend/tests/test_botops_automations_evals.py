"""BotOps' automation eval without a model (evals/botops_automations/): every request is well formed, each saved answer
passes its checks, and the checker refuses the answers it should. The live runner (evals/botops_automations/run.py) asks
a real model and never runs here.
"""
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals" / "botops_automations"
sys.path.insert(0, str(EVALS))
import checker  # noqa: E402
from backend import automation_actions  # noqa: E402
from backend import automation_flow as F  # noqa: E402

CASES = checker.load_cases()
BY_ID = {case["id"]: case for case in CASES}


def saved(case_id):
    observed = checker.load_saved(case_id)
    assert observed is not None, f"{case_id} has no saved answer"
    return copy.deepcopy(observed)


def failed(verdict):
    return {check["name"] for check in verdict["checks"] if not check["held"]}


def test_the_requests_are_well_formed_and_fictional():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 14
    for case in CASES:
        assert case.get("prompt") and case.get("expect"), case["id"]
        assert case.get("mode", "new") in ("new", "edit"), case["id"]
        assert case.get("mode") != "edit" or case.get("seed"), f"{case['id']} edits a seed it does not have"
        if case.get("seed"):
            assert F.check({"name": "Seed", **case["seed"]}), f"{case['id']}: the seed is not a valid flow"
    text = (EVALS / "requests.yaml").read_text()
    # A letter starts a domain; `mail-triage@1` names a question set's version, not an address.
    domains = {match.group(1) for match in re.finditer(r"[\w.+-]+@([a-z][\w.-]*\.[a-z]+)", text)}
    assert domains, "the requests name no address at all, so the check above proves nothing"
    assert domains <= {"acme.example"}, "requests name real domains"


@pytest.mark.parametrize("case_id", [case["id"] for case in CASES])
def test_each_saved_answer_passes_its_checks(case_id):
    verdict = checker.check_case(BY_ID[case_id], saved(case_id))
    assert verdict["passed"], [check for check in verdict["checks"] if not check["held"]]


def test_a_wrong_inbox_fails_the_trigger():
    observed = saved("investor-drafts")
    observed["created"][0]["trigger"]["inbox"] = "priya@acme.example"
    assert failed(checker.check_case(BY_ID["investor-drafts"], observed)) == {"trigger"}


def test_the_first_matching_path_is_checked_in_order():
    """Money must come first: with the reply path first, a money mail would be drafted instead of left."""
    observed = saved("investor-drafts")
    paths = observed["created"][0]["steps"][1]["paths"]
    paths.reverse()
    assert failed(checker.check_case(BY_ID["investor-drafts"], observed)) == {"steps in order"}


def test_a_money_threshold_below_the_expected_one_fails():
    observed = saved("investor-drafts")
    observed["created"][0]["steps"][1]["paths"][0]["when"][0]["min"] = 0.5
    assert failed(checker.check_case(BY_ID["investor-drafts"], observed)) == {"steps in order"}


def test_a_disabled_step_does_not_count():
    observed = saved("legal-leave")
    observed["created"][0]["steps"].insert(0, {"id": "old", "type": "find", "enabled": False, "source": "mail"})
    assert checker.check_case(BY_ID["legal-leave"], observed)["passed"]


def test_an_either_bot_step_is_accepted():
    observed = saved("nightly-summary")
    observed["created"][0]["steps"][0] = {"id": "summarize", "type": "llm", "enabled": True,
                                          "as": "assistant:dana", "prompt": "Summarize.", "out": "summary"}
    assert checker.check_case(BY_ID["nightly-summary"], observed)["passed"]


def test_a_schedule_at_the_wrong_hour_fails():
    observed = saved("summary-7pm")
    observed["live"]["trigger"]["cron"] = "0 17 * * 1-5"
    assert failed(checker.check_case(BY_ID["summary-7pm"], observed)) == {"trigger"}


def test_an_invalid_condition_fails_the_save_check():
    """The save check's own refusal is the failure: the checker names it and does not crash on it."""
    observed = saved("newsletters-archive")
    observed["created"][0]["steps"][1]["rules"][0]["when"][1]["op"] = "like"
    verdict = checker.check_case(BY_ID["newsletters-archive"], observed)
    assert "valid flow (the save check)" in failed(verdict)
    assert "op is one of" in next(c["why"] for c in verdict["checks"] if c["name"].startswith("valid flow"))


def test_an_edit_left_as_a_proposal_fails():
    """A person's edit is applied: the change is the live version, not a pending one beside an unchanged flow."""
    observed = saved("receipts-label")
    observed = {"live": copy.deepcopy(BY_ID["receipts-label"]["seed"]), "pending": [observed["live"]]}
    assert failed(checker.check_case(BY_ID["receipts-label"], observed)) == {"no proposal", "saved as the live version"}


def test_a_new_request_that_saves_nothing_or_two_fails():
    assert failed(checker.check_case(BY_ID["billing-webhook"], {"created": []})) == {"one automation saved"}
    two = saved("billing-webhook")
    two["created"].append(copy.deepcopy(two["created"][0]))
    assert failed(checker.check_case(BY_ID["billing-webhook"], two)) == {"one automation saved"}


def test_a_new_automation_is_on_only_when_the_request_says_so():
    observed = saved("investor-drafts")
    observed["created"][0]["enabled"] = True
    assert failed(checker.check_case(BY_ID["investor-drafts"], observed)) == {"on only when asked"}
    observed = saved("receipts-archive-on")
    observed["created"][0]["enabled"] = False
    assert failed(checker.check_case(BY_ID["receipts-archive-on"], observed)) == {"on only when asked"}


def test_a_refusal_fails_when_a_saved_flow_names_the_forbidden_inbox():
    observed = {"created": [{"name": "Priya", "owner_id": "person:dana", "enabled": False,
                             "trigger": {"type": "message", "inbox": "priya@acme.example"}, "steps": []}]}
    assert failed(checker.check_case(BY_ID["priya-inbox-refuse"], observed)) == {"nothing saved",
                                                                                 "forbidden inbox not named"}


def _actions(node):
    """Every action step in a flow, nested paths and otherwise-branches included."""
    if isinstance(node, dict):
        if node.get("type") == "action":
            yield node
        for value in node.values():
            yield from _actions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _actions(value)


def _flows_in_evals():
    """Every flow the evals hold: the seeds, and the live and proposed flows of each saved answer."""
    flows = [case["seed"] for case in CASES if case.get("seed")]
    for path in sorted((EVALS / "saved").glob("*.json")):
        doc = json.loads(path.read_text())
        flows += doc.get("created", []) + doc.get("pending", []) + ([doc["live"]] if doc.get("live") else [])
    return flows


def test_the_bot_knows_every_action_the_hub_runs():
    """The flow shape the bot is given names exactly the actions the executor runs."""
    assert set(F.ACTION_ARGS) == set(automation_actions.ACTIONS) == set(F.ACTIONS)


def test_every_action_in_the_evals_uses_only_the_args_the_bot_is_told():
    """A seed or saved answer that names an arg the bot is never told would teach it the wrong thing (post_update's
    `to` was such a mismatch: the action reads `text`)."""
    seen = 0
    for flow in _flows_in_evals():
        for step in _actions(flow.get("steps", [])):
            do = step.get("do")
            assert do in F.ACTION_ARGS, f"{do} is not an action the bot knows"
            assert set(step.get("args") or {}) <= set(F.ACTION_ARGS[do]) | {"text"}, (do, step.get("args"))
            seen += 1
    assert seen >= 5, "the evals name too few actions to prove the check"


def test_the_flow_shape_names_every_trigger_step_and_action():
    text = F.shape_text()
    for word in [*F.TRIGGERS, *F.STEP_TYPES, *F.ACTIONS, *F.OPS]:
        assert word in text, word
    for do, args in F.ACTION_ARGS.items():
        assert do in text and all(arg in text for arg in args), do


def test_the_guide_carries_the_flow_shape_from_the_code():
    """BotOps reads `hub automation guide`: the shape is swapped in at read time, so it cannot go stale in a file."""
    assert F.GUIDE.count("{{flow_shape}}") == 1
    guide = F.guide_text()
    assert F.shape_text() in guide and "{{" not in guide.replace("{{summary}}", "")
    agent = (Path(__file__).resolve().parents[2] / "templates" / "catalog" / "botops" / "AGENT.md").read_text()
    assert "`hub automation guide`" in agent


def test_the_live_runner_lists_its_requests():
    spec = importlib.util.spec_from_file_location("botops_automations_run", EVALS / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.main(["--url", "http://127.0.0.1:1", "--owner-token", "x", "--list"]) == 0
