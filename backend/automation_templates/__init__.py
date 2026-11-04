"""The inbox templates a new inbox starts from: founder, support, sales and blank, kept as flow JSON beside this module.

A template names the people its destinations reach by role (`{"role": "legal_lead"}`). `fill` resolves each role to an
active bot on the org chart that can take the work it is given, takes out what the owner may not use and returns the
flow as the hub would save it: `enabled` off, nothing written. The caller checks it with automations._checked, which also
tests the inbox and the owner's rights, and POST /api/v2/automations saves it.
"""
import copy
import json
from pathlib import Path

from .. import automation_flow as F
from ..store import H, Problem

HERE = Path(__file__).resolve().parent
ORDER = ("founder", "support", "sales", "blank")
# A role is a catalog template (backend/company_file/roles.json). The bots made from its templates fill it: each
# template's bots in chart order by id, the first template's before the next's. The work goes to the first of them that
# can take it (_pick), so a bot that cannot is skipped, not failed.
ROLES = {
    "legal_lead": ("general-counsel",),
    "engineering_manager": ("engineering-manager", "engineering-lead"),
    "finance_lead": ("finance-lead",),
    "marketing_lead": ("marketing-lead",),
    "support_lead": ("support-lead",),
    "sales_lead": ("sales-lead",),
}
# The actions that act as someone: a task is opened as the owner's actor, a reply is drafted as it (automation_actions
# _create_task and _actor). A group with no lead to act as cannot run them.
NEEDS_ACTOR = ("create_task", "draft_reply", "send_reply")
# The actions a Slack channel shows nothing for (automation_actions._slack): a group hears only Slack channels.
NO_OP_IN_SLACK = ("archive", "leave_for_you", "label", "forward")
# The actions a draft is filed with: in Slack automation_actions._slack files a draft as a task under the owner's actor,
# so a bot actor that cannot take a non-routine task cannot draft there (fill takes these out).
DRAFTS = ("draft_reply", "send_reply")
DRAFT_TITLE = "Draft reply"
# The message a choose step's turn is asked about, and that each task body and wake text hands on, named by the fields
# the trigger carries (automations.on_mail and on_slack). A prompt's {{field}} is quoted as data after the instructions
# (automation_flow.render_prompt). A task body is rendered as plain text (automation_engine._act: render_args), so there
# the message is substituted unquoted; a wake text is quoted like the prompt.
MESSAGE_PART = {
    "mail": ("\n\nThe message to handle: id {{message.id}} in thread {{message.thread}}, from {{message.from}} "
             "({{message.from_name}}), subject {{message.subject}}. Its body:\n{{message.body}}"),
    "slack": "\n\nThe message to handle: from {{message.from}} in {{message.inbox}}. Its text:\n{{message.text}}",
}


def _is_slack(inbox):
    return str(inbox or "").strip().lower().startswith("slack:")


def _message_part(inbox):
    """The part that names the message: Slack for a `slack:` inbox, mail otherwise."""
    return MESSAGE_PART["slack" if _is_slack(inbox) else "mail"]


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


def _candidates(found, role):
    """The bots that could fill `role`, in the order _active_by_template gives them, per template in ROLES' order."""
    return [slug for template in ROLES[role] for slug in found.get(template, [])]


def _takes_task(c, actor, bot, title, routine=False):
    """Whether H.task_create would take a task from `actor` for bot `bot` (a bot id), asked without recording a refusal.
    The gates are task_create's own, in its order: the actor is not an onboarding bot (_learner); the bot is active
    (_reach) and not onboarding (_learning); and no task rule refuses it, a synced harness or a profile that takes no such
    task (task_taking_refusal). With no actor at all (a group with no lead) none is taken. `title` is the template's, its
    placeholders not yet filled in, so a Reviewer, whose profile takes only review and QA requests, is skipped here.
    `routine` asks as a routine's own run does (automation_actions.start_turn files a turn that way): a Reviewer or a
    Monitor then takes it, and a Responder or a synced bot still does not."""
    if not actor or (H.is_bot(actor) and H._onboarding(c, H.actor_id(actor))):
        return False
    if (H.bot(c, bot) or {}).get("state") != "active" or H._onboarding(c, bot):
        return False
    return H.task_taking_refusal(c, actor, "bot:" + bot, title, None, routine) is None


def _title(args, dest):
    """The title a task is filed under, as automation_actions._create_task reads it."""
    return " ".join(str(args.get("title") or dest["name"]).split())[:300]


def _pick(c, A, owner, actor, found, dest, do, args, assistant_bot, role):
    """The action for one role-filled action and the bot it goes to, or None when no candidate takes it. A create_task
    goes to the first candidate that takes a task. A wake_bot goes to the first candidate the owner may tell: a wake is
    the runtime's _tell_bot, which checks only that the bot is active, and every candidate is. A candidate the owner may
    not tell gets a task instead, if it takes one."""
    title, text = _title(args, dest), args.get("text", "")
    for bot in _candidates(found, role):
        if do == "create_task":
            if _takes_task(c, actor, bot, title):
                return do, {**args, "owner": "bot:" + bot}
        elif A.may_tell(c, owner, bot, assistant_bot):
            return "wake_bot", {**args, "bot": "bot:" + bot}
        elif _takes_task(c, actor, bot, title):
            return "create_task", {"title": title, "body": text, "owner": "bot:" + bot}
    return None


def _fill_actions(c, A, owner, actor, found, dest, assistant_bot, part):
    """The destination's actions for this owner, or None when one of them has no bot that takes it (the destination is
    dropped). `part` (the message, _message_part) goes into each task body and wake text, so the hand-off carries it."""
    then = []
    for action in dest.get("then") or []:
        do, args = action["do"], dict(action.get("args") or {})
        if do == "create_task":
            args["body"] = str(args.get("body") or "") + part
        elif do == "wake_bot":
            args["text"] = str(args.get("text") or "") + part
        role = args.get("owner") if do == "create_task" else args.get("bot") if do == "wake_bot" else None
        if isinstance(role, dict):
            picked = _pick(c, A, owner, actor, found, dest, do, args, assistant_bot, role["role"])
            if picked is None:
                return None
            do, args = picked
        then.append({"do": do, "args": args})
    return then


def _asked(rules, out):
    """The question ids a filter's rules read from a decide step's answers (`<out>.<question id>`)."""
    prefix = f"{out}."
    return {str(cond.get("field"))[len(prefix):] for rule in rules for cond in rule.get("when") or []
            if str(cond.get("field", "")).startswith(prefix)}


def _pair(decide, follow):
    """A decide step with the filter that reads its answers, both pruned. When no rule reads an answer any more, the
    decide step goes and the filter stays, with the rules that never read one. A choice needs two options: with one
    destination left, the filter sends every thread there."""
    questions = {qid: q for qid, q in decide["questions"].items() if qid in _asked(follow["rules"], decide.get("out"))}
    if not questions:
        return [follow]
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


def _without_actions(rule, dos):
    """The rule without its actions in `dos`; None when it then has nothing to do and points nowhere."""
    then = [action for action in rule["then"] if action["do"] not in dos]
    if not then and not rule.get("to"):
        return None
    rule = {key: value for key, value in rule.items() if key != "then"}
    return {**rule, "then": then} if then else rule


def _no_drafts(step):
    """The step with its filter rules' draft and send actions taken out (_without_actions). A rule left with nothing to do
    and nowhere to go is gone. A step that is not a filter has no rules to take them from and stays as it is."""
    if step["type"] != "filter":
        return step
    rules = [_without_actions(rule, DRAFTS) if rule.get("then") else rule for rule in step.get("rules") or []]
    return {**step, "rules": [rule for rule in rules if rule is not None]}


def _prune(steps, gone, no_actor=False):
    """The steps with what points at a destination the owner may not use taken out: its rules, the questions only those
    rules asked, a choice's options that no rule reaches, and an llm step's choices. With `no_actor` (a group with no lead
    to act as) a rule's actions that need someone to act go too. A step left with nothing goes."""
    steps = [copy.deepcopy(step) for step in steps]
    for step in steps:
        if step["type"] == "filter":
            kept = []
            for rule in step.get("rules") or []:
                if rule.get("to") in gone:
                    continue
                if no_actor and rule.get("then"):
                    rule = _without_actions(rule, NEEDS_ACTOR)
                    if rule is None:
                        continue
                kept.append(rule)
            step["rules"] = kept
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
    """A group, or a bot that cannot run its own turn, has none to answer a choose with, so its llm step with choose becomes
    Sort: a choice over the same
    destinations, then a filter that sends the thread to the one chosen (the default when the answer is missing)."""
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


def _useful(destinations, steps):
    """Whether the flow does anything a Slack channel shows: an action other than the no-ops there."""
    dos = [action["do"] for dest in destinations for action in dest["then"]]
    dos += [action["do"] for step in steps if step["type"] == "filter" for rule in step.get("rules") or []
            for action in rule.get("then") or []]
    return any(do not in NO_OP_IN_SLACK for do in dos)


def fill(c, tid, owner, inbox, settings=None):
    """The template `tid` filled in for `owner` on `inbox`: a flow as the hub would save it, `enabled` off. Not checked
    here (automations._checked does that, with the inbox and the rights). Unknown templates and owners are refused, and so
    is a group with no lead to act as when nothing the template does would run there. In a Slack inbox a bot that cannot
    take a non-routine task (a draft is one) gets no drafts, and no choose either: Sort."""
    from .. import automation_actions as AA
    from .. import automations as A
    tpl = load(tid)
    if tpl is None:
        raise Problem("not_found", "No such template", 404)
    owner = F.owner_ref(owner)
    kind, _, ident = owner.partition(":")
    if kind not in ("person", "bot", "group") or not ident:
        raise Problem("invalid", "owner is person:<id>, bot:<slug> or group:<id>", 422)
    if kind == "bot" and (found_bot := H.resolve_bot(c, ident)):
        ident = found_bot["id"]
        owner = f"bot:{ident}"
    chart = A.Chart(c)
    found = _active_by_template(c, chart)
    actor = AA.acts_as(c, {"flow": {"owner": owner}})
    assistant_bot = getattr(settings, "assistant_bot", None)
    part = _message_part(inbox)
    # In Slack a draft is filed as a task under the actor (automation_actions._slack). A bot actor that cannot take a
    # non-routine task (task_create's gate, asked as the bot files one) cannot draft there, so its drafts go.
    actor_bot = H.actor_id(actor) if actor and H.is_bot(actor) else ""
    drafts = not (_is_slack(inbox) and actor_bot and not _takes_task(c, actor, actor_bot, DRAFT_TITLE))
    destinations, gone = [], set()
    for dest in tpl["destinations"]:
        then = _fill_actions(c, A, owner, actor, found, dest, assistant_bot, part)
        if then is None or (not actor and any(action["do"] in NEEDS_ACTOR for action in then)):
            gone.add(dest["id"])
        else:
            destinations.append({**{k: v for k, v in dest.items() if k != "then"}, "then": then})
    steps = _prune([step if drafts else _no_drafts(step) for step in tpl["steps"]], gone, no_actor=not actor)
    if not actor and not _useful(destinations, steps):
        raise Problem("invalid", f"{owner} has no lead to act as, so the {tpl['name']} template would do nothing in its "
                      "Slack channel. Give the group a lead first.", 422)
    # A group owns no turn. A bot owns one only if start_turn could file it: the gate a routine's task passes, asked as the
    # bot itself (onboarding, a Responder's or a synced bot's profile). Without a turn the choose becomes Sort (plan v2.1).
    # A bot that has a turn but no drafts (the Slack case above) gets Sort too: its choose would draft.
    turn = kind == "person" or (kind == "bot" and _takes_task(c, "bot:" + ident, ident, tpl["name"], routine=True))
    if not turn or not drafts:
        steps = [kept for step in steps for kept in _as_sort(step, tpl)]
    else:
        for step in steps:
            if step["type"] == "llm":
                step["as"] = ("assistant:" if kind == "person" else "bot:") + ident
                step["prompt"] = step["prompt"] + part
    return {"name": tpl["name"], "owner": owner, "enabled": False,
            "trigger": {"type": "message", "inbox": str(inbox or "").strip()}, "steps": steps,
            "destinations": destinations}
