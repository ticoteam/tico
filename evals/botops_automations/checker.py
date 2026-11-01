"""Does the automation BotOps saved have the structure its request asks for?

Pure: no model, no server. Each request in requests.yaml states its expected structure. A `new` request must leave one
new automation, off unless the request said to turn it on; an `edit` request must leave its change as the live version,
with no proposal, since BotOps has the asking person's rights. The checker validates that flow with
backend/automation_flow.py (the same check a save runs) and then matches the structure. It never reads wording: neither
the request nor BotOps' replies are compared with anything.

The offline layer (backend/tests/test_botops_automations_evals.py) and the live runner (run.py) score with this module,
so a saved answer and a real model's answer are judged by the same rules.
"""
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))   # the repo root, for backend.automation_flow
from backend import automation_flow as F  # noqa: E402

REQUESTS = HERE / "requests.yaml"
SAVED = HERE / "saved"
OWNER = "person:dana"
ANY = "*"


def load_cases(only=None):
    cases = yaml.safe_load(REQUESTS.read_text())
    return [case for case in cases if not only or case["id"] in only]


def load_saved(case_id):
    """A saved answer. `new`: {"created": [automation, ...]}, each with its name, owner and enabled. `edit`:
    {"live": flow, "pending": [flow, ...]}, the newest active version and any proposals."""
    path = SAVED / f"{case_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def before_of(case):
    """The live flow an edit starts from (its seed); None for a new request."""
    return case.get("seed")


def canon(flow):
    """The parts a request can change, as one string: equal before and after means the live flow did not move."""
    return json.dumps({"trigger": flow.get("trigger"), "steps": flow.get("steps") or [], "batch": flow.get("batch")},
                      sort_keys=True, default=str)


def validate(flow, owner, name):
    """None when the flow passes the save check, else the reason it is refused."""
    try:
        F.check({"name": name, "owner": owner, "trigger": flow.get("trigger"), "steps": flow.get("steps") or [],
                 "batch": flow.get("batch")})
    except Exception as exc:   # a malformed answer is a failed check, never a crash
        return str(exc) or type(exc).__name__
    return None


# ------------------------------------------------------------------------------------------------ matching
def _number(value):
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same(want, got):
    if isinstance(want, str) and want == ANY:
        return got not in (None, "", [], {})
    if isinstance(want, bool) or isinstance(got, bool):
        return str(want).casefold() == str(got).casefold()
    if isinstance(want, (int, float)):
        return _number(got) == float(want)
    return str(want).strip().casefold() == str(got).strip().casefold()


def _cond(want, got):
    if want.get("field") != got.get("field") or want.get("op") != got.get("op"):
        return False
    if "value" in want and not _same(want["value"], got.get("value")):
        return False
    if "min" in want:
        low = _number(got.get("min"))
        return low is not None and low >= float(want["min"])
    return True


def _conds(wants, gots):
    """Each expected condition is among the flow's; a flow may carry more."""
    return all(any(_cond(want, got) for got in gots or []) for want in wants or [])


def _rule(want, got):
    return (_conds(want.get("when"), got.get("when"))
            and [action.get("do") for action in got.get("then") or []] == list(want.get("then") or [])
            and ("match" not in want or want["match"] == (got.get("match") or "all")))


def _path(want, got):
    return (_conds(want.get("when"), got.get("when")) and _steps(want.get("steps"), got.get("steps"))
            and ("match" not in want or want["match"] == (got.get("match") or "all")))


def _branches(wants, gots, same):
    return len(wants or []) == len(gots or []) and all(same(w, g) for w, g in zip(wants or [], gots or []))


def _step(want, got):
    if "any_of" in want:
        return any(_step(alternative, got) for alternative in want["any_of"])
    kind = want.get("type")
    if got.get("type") != kind:
        return False
    if kind == "decide":
        return "set" not in want or str(got.get("set") or "").partition("@")[0] == want["set"]
    if kind == "filter":
        return _branches(want.get("rules"), got.get("rules"), _rule)
    if kind == "paths":
        if not _branches(want.get("paths"), got.get("paths"), _path):
            return False
        if "otherwise" in want:
            wanted, actual = want["otherwise"], got.get("otherwise")
            return actual is None if wanted is None else actual is not None and _steps(wanted.get("steps"), actual.get("steps"))
        return True
    if kind == "action":
        return _same(want.get("do"), got.get("do"))
    if kind == "find":
        return _same(want.get("source", ANY), got.get("source"))
    if kind == "bot_turn":
        return _same(want.get("bot", ANY), str(got.get("bot") or "").removeprefix("bot:"))
    if kind == "llm":
        return _same(want.get("as", ANY), got.get("as"))
    return True


def _enabled(steps):
    """A disabled step never runs, so it does not count toward the structure."""
    return [step for step in steps or [] if step.get("enabled", True) is not False]


def _steps(wants, gots):
    live = _enabled(gots)
    return len(wants or []) == len(live) and all(_step(want, got) for want, got in zip(wants or [], live))


def describe(steps):
    """The structure in a few words, for a failed check."""
    names = []
    for step in _enabled(steps):
        if "any_of" in step:
            names.append("one of " + " or ".join(describe([alternative]) for alternative in step["any_of"]))
        elif step.get("type") == "paths":
            branches = [describe(path.get("steps")) or "nothing" for path in step.get("paths") or []]
            names.append("paths[" + "; ".join(branches) + "]")
        elif step.get("type") == "action":
            names.append("action " + str(step.get("do")))
        else:
            names.append(str(step.get("type")))
    return ", ".join(names)


def _trigger(want, got):
    if want.get("type") != got.get("type"):
        return False
    for key, value in want.items():
        if key == "type":
            continue
        if key == "at":
            if not _at(value, got.get("cron")):
                return False
        elif key == "filters":
            if not _conds(value, got.get("filters")):
                return False
        elif not _same(value, got.get(key)):
            return False
    return True


def _at(want, cron):
    """A five-field cron whose minute and hour are the given HH:MM."""
    fields = str(cron or "").split()
    hour, _, minute = str(want).partition(":")
    return len(fields) == 5 and fields[0] == str(int(minute)) and fields[1] == str(int(hour))


def _batch(want, got):
    return bool(got) and sorted(want.get("times") or []) == sorted(got.get("times") or [])


def _held(fn, *args):
    """A structure that is not shaped as a flow fails its check; it does not stop the others."""
    try:
        return bool(fn(*args))
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


# ------------------------------------------------------------------------------------------------ a case
def _check(name, held, why):
    return {"name": name, "held": bool(held), **({} if held else {"why": why})}


def check_case(case, observed):
    """The verdict for one request: {id, passed, checks}. `observed` is a saved answer (load_saved)."""
    expect = case.get("expect") or {}
    before = before_of(case)
    created = list(observed.get("created") or [])
    live = observed.get("live") or before
    pending = list(observed.get("pending") or [])
    checks = [_check("no proposal", not pending, f"{len(pending)} pending version(s): a person's request is applied")]
    if expect.get("outcome") == "refuse":
        checks.append(_check("nothing saved", not created and (before is None or canon(live) == canon(before)),
                             "an automation was saved or changed"))
        forbidden = str(expect.get("forbidden") or "").lower()
        named = any(forbidden in json.dumps(flow).lower() for flow in [*created, *pending, *([live] if live else [])])
        checks.append(_check("forbidden inbox not named", not named, "a saved flow reads the forbidden inbox"))
        return _verdict(case, checks)
    if before is None:
        checks.append(_check("one automation saved", len(created) == 1, f"{len(created)} automation(s) saved"))
        if len(created) != 1:
            return _verdict(case, checks)
        flow = created[0]
        want_on = bool(expect.get("enabled", False))
        checks.append(_check("on only when asked", flow.get("enabled") is want_on,
                             f"saved {'on' if flow.get('enabled') else 'off'}; the request wants it "
                             f"{'on' if want_on else 'off'}"))
        owner, name = flow.get("owner") or OWNER, flow.get("name") or "New"
    else:
        changed = canon(live) != canon(before)
        checks.append(_check("saved as the live version", changed, "the live flow did not change"))
        if not changed:
            return _verdict(case, checks)
        flow, owner, name = live, before.get("owner", OWNER), before.get("name", "Seed")
    error = validate(flow, owner, name)
    checks.append(_check("valid flow (the save check)", error is None, error or ""))
    if "trigger" in expect:
        checks.append(_check("trigger", _held(_trigger, expect["trigger"], flow.get("trigger") or {}),
                             f"wanted {expect['trigger']}, got {flow.get('trigger')}"))
    if "steps" in expect:
        checks.append(_check("steps in order", _held(_steps, expect["steps"], flow.get("steps") or []),
                             f"wanted {describe(expect['steps'])}; got {describe(flow.get('steps') or [])}"))
    if "batch" in expect:
        checks.append(_check("batch times", _held(_batch, expect["batch"], flow.get("batch")),
                             f"wanted {expect['batch']}, got {flow.get('batch')}"))
    return _verdict(case, checks)


def _verdict(case, checks):
    return {"id": case["id"], "passed": all(check["held"] for check in checks), "checks": checks}
