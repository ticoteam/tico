"""Every reason the claim gives for queued work that does not start has words and a fix in one list
(backend/wait_reasons.py), and a reason without them is shown with its code and logged, never a generic fallback."""

import ast
import logging
from pathlib import Path

from backend import holds, queue_refusals, setup_drafts, wait_reasons

BACKEND = Path(__file__).resolve().parents[1]
# The functions whose answer is a claim's or a job's refusal code, by file, with the code's place in a tuple answer
# (queued_reasons yields `(job, why)`). A refusal one returns from another (`return job_refusal(...)`) is that one's.
REFUSERS = {
    "execution.py": {
        "claim_refusal": 0,
        "_claim_refusal": 0,
        "job_refusal": 0,
        "job_reason": 0,
        "queued_reasons": 1,
        "bot_claimable": 0,
    },
    "queue_refusals.py": {"current": 0},
}
CONSTANTS = {"setup_drafts.DRAFT_WAIT": setup_drafts.DRAFT_WAIT, "holds.TASK_QUARANTINE": holds.TASK_QUARANTINE}
CONSTANTS |= {"holds.CLAIM_HELD": holds.CLAIM_HELD}
# Calls whose answer is no refusal code of its own: what they give is a refusal another refuser already said, or a
# claim's other answer (its config, its job). Anything else returned is an error until listed here or in CONSTANTS.
PASSED_ON = {"bot_claimable", "claim_refusal", "_claim_refusal", "job_refusal", "job_reason", "queued_reasons"}
PASSED_ON |= {"holds.why", "_hold_reason", "cast"}


class Unchecked(AssertionError):
    pass


def _codes(node, fn, at, seen=frozenset()):
    """Every code expression `node` can give, as a refuser answers: a string, the code's place in a tuple, both sides
    of an `if`, a constant, or a local variable's every assignment. A shape it can't read raises Unchecked, naming
    the line, so a new way of answering never slips past the test."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node.value
        elif node.value is not None:
            raise Unchecked(f"line {node.lineno}: {ast.unparse(node)}")
    elif isinstance(node, ast.Tuple):
        if node.elts:
            yield from _codes(node.elts[at] if at < len(node.elts) else node.elts[0], fn, 0, seen)
    elif isinstance(node, ast.IfExp):
        yield from _codes(node.body, fn, at, seen)
        yield from _codes(node.orelse, fn, at, seen)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _codes(value, fn, at, seen)
    elif isinstance(node, ast.Attribute):
        name = ast.unparse(node)
        if name not in CONSTANTS:
            raise Unchecked(f"line {node.lineno}: {name} is returned as a refusal: add it to CONSTANTS")
        yield CONSTANTS[name]
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _nested(fn) and node.args:
        yield from _codes(node.args[0], fn, 0, seen)  # a local helper that answers with its first argument
    elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and isinstance(node.slice, ast.Constant):
        if isinstance(node.slice.value, int):  # an item of a local tuple: that item of its every assignment
            for value, _ in _assignments(fn, node.value.id):
                yield from _codes(value, fn, node.slice.value, seen | {node.value.id})
        else:
            raise Unchecked(f"line {node.lineno}: {ast.unparse(node)} is a shape the refusal test can't read")
    elif isinstance(node, ast.Call):
        name = ast.unparse(node.func)
        if name not in PASSED_ON and name.rsplit(".", 1)[-1] not in PASSED_ON:
            raise Unchecked(
                f"line {node.lineno}: {name}(...) is returned as a refusal: add it to REFUSERS or PASSED_ON"
            )
    elif isinstance(node, ast.Name):
        if node.id in seen:
            return
        assigned = list(_assignments(fn, node.id))
        if not assigned and node.id not in {a.arg for a in fn.args.args + fn.args.kwonlyargs}:
            raise Unchecked(f"line {node.lineno}: {node.id} has no assignment in {fn.name}")
        for value, place in assigned:
            yield from _codes(value, fn, place if place is not None else at, seen | {node.id})
    elif isinstance(node, ast.Dict):
        # queue_refusals.current's state: its refusal is its 'reason'.
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "reason":
                yield from _codes(value, fn, 0, seen)
    else:
        raise Unchecked(f"line {node.lineno}: {ast.unparse(node)} is a shape the refusal test can't read")


def _nested(fn):
    return {node.name for node in ast.walk(fn) if isinstance(node, ast.FunctionDef) and node is not fn}


def _assignments(fn, name):
    """(value, place) for each assignment to `name` in `fn`; `place` is its position when unpacked from a tuple."""
    for node in _own(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name and node.value is not None:
                    yield node.value, None
                elif isinstance(target, ast.Tuple):
                    for place, item in enumerate(target.elts):
                        if isinstance(item, ast.Name) and item.id == name and node.value is not None:
                            yield node.value, place
        elif isinstance(node, ast.NamedExpr) and node.target.id == name:
            yield node.value, None
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            for item in node.target.elts:
                if isinstance(item, ast.Name) and item.id == name:
                    yield node.iter, None


def _own(fn):
    """The nodes of `fn` itself, not of a function defined in it (a local helper answers through its caller)."""
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(node))


def answers(fn):
    """Each expression `fn` answers with: its returns and its yields."""
    for node in _own(fn):
        if isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:
            yield node.value
        elif isinstance(node, ast.YieldFrom):
            yield node.value


def refusal_codes(source, functions, where=""):
    """{code: "file:function"} of every code these functions of `source` answer with."""
    found, seen = {}, set()
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, ast.FunctionDef) and fn.name in functions:
            seen.add(fn.name)
            for value in answers(fn):
                try:
                    for code in _codes(value, fn, functions[fn.name]):
                        found.setdefault(code, f"{where}:{fn.name}")
                except Unchecked as error:
                    raise Unchecked(f"{where}:{fn.name} {error}") from None
    assert seen == set(functions), f"{where} no longer has {sorted(set(functions) - seen)}"
    return found


def returned_codes():
    found = {}
    for name, functions in REFUSERS.items():
        for code, where in refusal_codes((BACKEND / name).read_text(), functions, name).items():
            found.setdefault(code, where)
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


def test_the_collector_finds_a_new_code_in_every_shape_a_refuser_answers_with():
    """#3967 review: a code returned through a local variable or yielded with its job slipped past the collector.
    Each shape a refuser answers with is read, and one it cannot read fails the test instead of passing silently."""
    shapes = {
        "literal": "def refuse(c):\n    return 'brand_new'\n",
        "alias": "def refuse(c):\n    why = 'brand_new'\n    return why, {}\n",
        "yield": "def refuse(c):\n    for job in c:\n        why = 'brand_new' if job else None\n        yield job, why\n",
        "helper": "def refuse(c):\n    def result(reason):\n        return reason, None\n    return result('brand_new')\n",
        "walrus": "def refuse(c):\n    if (why := 'brand_new'):\n        return why\n",
    }
    for shape, source in shapes.items():
        place = 1 if shape == "yield" else 0
        assert refusal_codes(source, {"refuse": place}, shape) == {"brand_new": f"{shape}:refuse"}, shape
    for unreadable in ("return f'{c}_wait'", "return some_module.pick(c)", "return c.reason"):
        try:
            refusal_codes(f"def refuse(c):\n    {unreadable}\n", {"refuse": 0}, "unreadable")
        except Unchecked:
            continue
        raise AssertionError(f"{unreadable} passed the refusal test unread")


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


def test_a_capacity_or_payment_hold_on_the_login_is_never_called_a_usage_limit(api, monkeypatch):
    """#3967 review: only a usage-limit hold names the plan and a reset; a busy provider or an unpaid plan does not."""
    from backend import execution, login_holds
    from backend.store import H

    store = api.app.state.store
    monkeypatch.setattr(execution, "_limits_login", lambda *args: ("box", "", "antigravity"))
    for error in ("529 overloaded_error: the provider is at capacity", "Your payment is past due"):
        with store.transaction() as c:
            c.execute("DELETE FROM holds")
            hold = login_holds.record(c, "box", "", "antigravity", H.now(), error=error)
            assert hold["until"], error
            assert queue_refusals.login_limit(c, store.settings, "box", "ops") == {}, error
