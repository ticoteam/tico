"""Every reason the claim gives for queued work that does not start has words and a fix in one list
(backend/wait_reasons.py), and a reason without them is shown with its code and logged, never a generic fallback."""

import ast
import logging
from pathlib import Path

from backend import holds, queue_refusals, setup_drafts, wait_reasons

BACKEND = Path(__file__).resolve().parents[1]
# The functions whose answer is a claim's or a job's refusal code, by file.
REFUSERS = {
    "execution.py": {
        "claim_refusal",
        "_claim_refusal",
        "job_refusal",
        "job_reason",
        "queued_reasons",
        "bot_claimable",
    },
    "queue_refusals.py": {"current"},
}
CONSTANTS = {"setup_drafts.DRAFT_WAIT": setup_drafts.DRAFT_WAIT, "holds.TASK_QUARANTINE": holds.TASK_QUARANTINE}
CONSTANTS |= {"holds.CLAIM_HELD": holds.CLAIM_HELD}


def _codes(node):
    """The codes one returned expression can give: a string, a tuple's first item, both sides of an `if`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.Tuple) and node.elts:
        yield from _codes(node.elts[0])
    elif isinstance(node, ast.IfExp):
        yield from _codes(node.body)
        yield from _codes(node.orelse)
    elif isinstance(node, ast.Attribute):
        name = ast.unparse(node)
        assert name in CONSTANTS, f"{name} is returned as a refusal: add it to CONSTANTS so its words are checked"
        yield CONSTANTS[name]


def returned_codes():
    found = {}
    for name, functions in REFUSERS.items():
        tree = ast.parse((BACKEND / name).read_text())
        seen = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in functions:
                seen.add(fn.name)
                for node in ast.walk(fn):
                    if isinstance(node, ast.Return) and node.value is not None:
                        for code in _codes(node.value):
                            found.setdefault(code, f"{name}:{fn.name}")
        assert seen == functions, f"{name} no longer has {sorted(functions - seen)}"
    # A hold's kind is a refusal too (holds.why), and so are the waits the queue keeps on purpose.
    for code in (*holds.ORDER, *queue_refusals.ON_PURPOSE):
        found.setdefault(code, "holds.py")
    return found


def test_every_claim_and_job_refusal_code_has_words_and_a_fix():
    codes = returned_codes()
    assert {"payer_wait", "worktree_home", "continuation_home", "cooling_off", "not_ready"} <= set(codes)
    missing = sorted(f"{code} ({where})" for code, where in codes.items() if code not in wait_reasons.REASONS)
    assert not missing, "refusal codes with no words and fix in backend/wait_reasons.py: " + ", ".join(missing)
    for code in codes:
        reason = wait_reasons.REASONS[code]
        label, href, _ = reason.fix
        assert reason.words and reason.turns and label and href, code
        words, fix = wait_reasons.say(code, computer="Test Mac", home="Shared cloud machine")
        assert "{" not in words and "{" not in wait_reasons.turns(code, home="Shared cloud machine"), code
        assert fix == reason.fix


def test_no_reason_reads_as_a_generic_fallback():
    for code, reason in wait_reasons.REASONS.items():
        for text in (reason.words, reason.turns):
            for generic in ("pre-start requirements", "can't start it", "Waiting for the agent's Paid by"):
                assert generic not in text, (code, text)


def test_an_unmapped_code_shows_its_code_and_logs_one_error(caplog, monkeypatch):
    monkeypatch.setattr(wait_reasons, "_logged", set())
    with caplog.at_level(logging.ERROR, logger="tico.wait_reasons"):
        words, fix = wait_reasons.say("brand_new_wait", computer="Test Mac")
        again, _ = wait_reasons.say("brand_new_wait")
        turns = wait_reasons.turns("brand_new_wait")
    assert words == again == "Waiting (code: brand_new_wait)"
    assert turns == "(code: brand_new_wait)"
    assert fix == wait_reasons.RUNS
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "brand_new_wait" in errors[0].getMessage()


def test_the_checkout_words_name_the_computer_that_holds_it():
    words, fix = wait_reasons.say("worktree_home", computer="Builder's machine", home="Shared cloud machine")
    assert "Shared cloud machine" in words and fix == wait_reasons.COMPUTERS
    assert wait_reasons.turns("worktree_home", home="Shared cloud machine") == "checkout is on Shared cloud machine"
    assert wait_reasons.turns("continuation_home", home="Test Mac") == "session saved on Test Mac"


def test_a_usage_limit_with_no_paid_by_names_the_provider_and_reset_never_out_of_budget(api, monkeypatch):
    """AC-3: cooling_off (the bot's own login at its limit) reads as a usage limit with the provider and its reset."""
    from backend import execution, login_holds, thread_bot_status
    from backend.model_subscriptions import _when
    from backend.store import H

    store = api.app.state.store
    monkeypatch.setattr(execution, "_limits_login", lambda *args: ("box", "", "antigravity"))
    with store.transaction() as c:
        held = login_holds.record(c, "box", "", "antigravity", H.now(), reset=H.shift(H.now(), hours=2))
        limit = queue_refusals.login_limit(c, store.settings, "box", "ops")
        assert limit == {"plan": "Antigravity", "until": held["until"]}
        location = {"online": True, "machine": {"runner_id": "box", "label": "Test Mac"}, "readiness": {}}
        detail, fix, code = thread_bot_status._blocked(
            c, "ops", location, {"reason": "cooling_off", "runner_id": "box", "payer": limit}
        )
    assert detail == f"It hit its Antigravity usage limit (resets {_when(held['until'])})"
    assert thread_bot_status.OUT_OF_BUDGET not in detail
    assert (fix["label"], code) == ("Switch model", "cooling_off")
