"""The inbox templates a new inbox starts from: founder, support, sales and blank, kept as flow JSON beside this module.

A template names the people its destinations reach by role (`{"role": "legal_lead"}`). `fill` resolves each role to an
active bot on the org chart, takes out what the owner may not use and returns the flow as the hub would save it:
`enabled` off, nothing written. The caller checks it with automations._checked, which also tests the inbox and the
owner's rights, and POST /api/v2/automations saves it.
"""
import copy
import json
from pathlib import Path

from .. import automation_flow as F
from ..store import H, Problem

HERE = Path(__file__).resolve().parent
ORDER = ("founder", "support", "sales", "blank")
# A role is a catalog template (backend/company_file/roles.json). The first active bot made from one of its templates fills
# it, the bots in chart order by id.
ROLES = {
    "legal_lead": ("general-counsel",),
    "engineering_manager": ("engineering-manager", "engineering-lead"),
    "finance_lead": ("finance-lead",),
    "marketing_lead": ("marketing-lead",),
    "support_lead": ("support-lead",),
    "sales_lead": ("sales-lead",),
}


def load(tid):
    """One template's flow JSON, or None for an id that is not one."""
    if tid not in ORDER:
        return None
    return json.loads((HERE / f"{tid}.json").read_text(encoding="utf-8"))


def listing():
    """The templates in the order New inbox shows them: id, name and the names of their destinations."""
    out = []
    for tid in ORDER:
        tpl = load(tid)
        out.append({"id": tid, "name": tpl["name"], "destinations": [dest["name"] for dest in tpl["destinations"]]})
    return out


def _active_by_template(c, chart):
    """{catalog template: [bot ids]}: the bots on the chart that are active (not archived, held or planned)."""
    states = {row["id"]: row.get("state") for row in H.bots(c)}
    found = {}
    for bot, config in sorted(chart.employees.items()):
        if bot in chart.archived or states.get(bot) in H.INACTIVE_STATES or not config.get("template"):
            continue
        found.setdefault(config["template"], []).append(bot)
    return found


def _role_bot(found, role):
    for template in ROLES[role]:
        if found.get(template):
            return found[template][0]
    return None


def _fill_actions(c, A, owner, found, dest, assistant_bot):
    """The destination's actions for this owner, or None when one of them has no bot to reach (the destination is dropped).
    A wake a bot the owner may not tell becomes a task for that bot: a task may be opened for any active bot."""
    then = []
    for action in dest.get("then") or []:
        do, args = action["do"], dict(action.get("args") or {})
        if do == "create_task" and isinstance(args.get("owner"), dict):
            bot = _role_bot(found, args["owner"]["role"])
            if bot is None:
                return None
            args["owner"] = "bot:" + bot
        elif do == "wake_bot" and isinstance(args.get("bot"), dict):
            bot = _role_bot(found, args["bot"]["role"])
            if bot is None:
                return None
            if A.may_tell(c, owner, bot, assistant_bot):
                args = {"bot": "bot:" + bot, "text": args.get("text", "")}
            else:
                do = "create_task"
                args = {"title": args.pop("title", dest["name"]), "body": args.pop("text", ""), "owner": "bot:" + bot}
        then.append({"do": do, "args": args})
    return then


def _asked(rules, out):
    """The question ids a filter's rules read from a decide step's answers (`<out>.<question id>`)."""
    prefix = f"{out}."
    return {str(cond.get("field"))[len(prefix):] for rule in rules for cond in rule.get("when") or []
            if str(cond.get("field", "")).startswith(prefix)}


def _pair(decide, follow):
    """A decide step with the filter that reads its answers, both pruned. [] when none of its questions is asked any more.
    A choice needs two options: with one destination left, the filter sends every thread there."""
    questions = {qid: q for qid, q in decide["questions"].items() if qid in _asked(follow["rules"], decide.get("out"))}
    if not questions:
        return []
    reached = list(dict.fromkeys(rule["to"] for rule in follow["rules"] if rule.get("to")))
    for question in questions.values():
        if question["type"] == "choice":
            question["criteria"] = {opt: text for opt, text in question["criteria"].items() if opt in reached}
    if any(q["type"] == "choice" and len(q["criteria"]) < 2 for q in questions.values()):
        last = next((rule["to"] for rule in follow["rules"] if not rule.get("when") and rule.get("to")),
                    reached[0] if reached else None)
        if last is None:
            return []
        return [{"id": follow["id"], "type": "filter", "rules": [{"when": [], "to": last, "next": "end", "stop": True}]}]
    decide["questions"] = questions
    return [decide, follow]


def _prune(steps, gone):
    """The steps with what points at a destination the owner may not use taken out: its rules, the questions only those
    rules asked, a choice's options that no rule reaches, and an llm step's choices. A step left with nothing goes."""
    steps = [copy.deepcopy(step) for step in steps]
    for step in steps:
        if step["type"] == "filter":
            step["rules"] = [rule for rule in step.get("rules") or [] if rule.get("to") not in gone]
    out, skip = [], set()
    for n, step in enumerate(steps):
        if n in skip:
            continue
        follow = steps[n + 1] if n + 1 < len(steps) and steps[n + 1]["type"] == "filter" else None
        if step["type"] == "decide" and follow is not None:
            out.extend(_pair(step, follow))
            skip.add(n + 1)
            continue
        if step["type"] == "llm" and step.get("choose") is not None:
            to = [dest for dest in step["choose"]["to"] if dest not in gone]
            if not to:
                continue
            choose = {**step["choose"], "to": to}
            if choose["default"] not in to:
                choose["default"] = "me" if "me" in to else to[0]
            step["choose"] = choose
        out.append(step)
    return out


def _as_sort(step, tpl):
    """A group has no turn to answer a choose with, so its llm step with choose becomes Sort: a choice over the same
    destinations, then a filter that sends the thread to the one chosen (the default when the answer is missing). Every
    option has its own route, the default's too, which is the shape the board reads (ui/app/automations-flow.js)."""
    choose = step.get("choose")
    if step["type"] != "llm" or choose is None:
        return [step]
    to, default = choose["to"], choose["default"]
    if len(to) < 2:
        return [{"id": step["id"], "type": "filter", "rules": [{"when": [], "to": default, "next": "end", "stop": True}]}]
    out = step.get("out") or step["id"]
    spec = tpl.get("sort") or {}
    names = {dest["id"]: dest["name"] for dest in tpl["destinations"]}
    criteria = {dest: (spec.get("criteria") or {}).get(dest) or names.get(dest, dest) for dest in to}
    rules = [{"when": [{"field": f"{out}.route", "op": "is", "value": dest, "min": 0.5}], "to": dest,
              "next": "end", "stop": True} for dest in to]
    rules.append({"when": [], "to": default, "next": "end", "stop": True})
    return [{"id": step["id"], "type": "decide", "level": "high", "out": out, "questions": {"route": {
                "type": "choice", "instructions": spec.get("instructions") or "Where does this message belong? Pick the one that fits best.",
                "criteria": criteria}}},
            {"id": f"{step['id']}_route", "type": "filter", "rules": rules}]


def fill(c, tid, owner, inbox, settings=None):
    """The template `tid` filled in for `owner` on `inbox`: a flow as the hub would save it, `enabled` off. Not checked
    here (automations._checked does that, with the inbox and the rights); unknown templates and owners are refused."""
    from .. import automations as A
    tpl = load(tid)
    if tpl is None:
        raise Problem("not_found", "No such template", 404)
    owner = F.owner_ref(owner)
    kind, _, ident = owner.partition(":")
    if kind not in ("person", "bot", "group") or not ident:
        raise Problem("invalid", "owner is person:<id>, bot:<slug> or group:<id>", 422)
    if kind == "bot" and H.resolve_bot(c, ident):
        ident = H.resolve_bot(c, ident)["id"]
        owner = f"bot:{ident}"
    chart = A.Chart(c)
    found = _active_by_template(c, chart)
    assistant_bot = getattr(settings, "assistant_bot", None)
    destinations, gone = [], set()
    for dest in tpl["destinations"]:
        then = _fill_actions(c, A, owner, found, dest, assistant_bot)
        if then is None:
            gone.add(dest["id"])
        else:
            destinations.append({**{k: v for k, v in dest.items() if k != "then"}, "then": then})
    steps = _prune(tpl["steps"], gone)
    if kind == "group":
        steps = [kept for step in steps for kept in _as_sort(step, tpl)]
    else:
        for step in steps:
            if step["type"] == "llm":
                step["as"] = ("assistant:" if kind == "person" else "bot:") + ident
    return {"name": tpl["name"], "owner": owner, "enabled": False,
            "trigger": {"type": "message", "inbox": str(inbox or "").strip()}, "steps": steps,
            "destinations": destinations}
