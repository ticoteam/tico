"""Message routines written in Assistant chat: off until a person's card click, with a readable plan."""
import copy
import json
import re

from .store import H, Problem, digest


CREATE = "/api/v2/automations"
EDIT = re.compile(r"/api/v2/automations/([^/]+)")


def message(flow):
    trigger = flow.get("trigger") if isinstance(flow, dict) else None
    return isinstance(trigger, dict) and trigger.get("type") == "message"


def defaults(fields):
    """A new chooser without an explicit mode drafts; Sort flows need no chooser at all."""
    fields = copy.deepcopy(fields)

    def visit(steps):
        # The route has not validated the flow yet; leave malformed nodes for its normal 422.
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "llm" and isinstance(step.get("choose"), dict):
                step["choose"].setdefault("mode", "draft")
            paths = step.get("paths")
            for path in paths if isinstance(paths, list) else []:
                if isinstance(path, dict):
                    visit(path.get("steps"))
            if isinstance(step.get("otherwise"), dict):
                visit(step["otherwise"].get("steps"))

    visit(fields.get("steps"))
    return fields


def gate(store, method, path, fields):
    """True/False for a Message write; None leaves other automations to the ordinary Assistant gate."""
    from . import automations as A
    if method == "POST" and path == CREATE and message(fields):
        # The create route forces it off and puts its Turn on on a card in the same transaction.
        return fields.get("enabled") is True
    match = re.fullmatch(r"/api/v2/automations/([^/]+)(?:/run|/versions/\d+/restore)?", path)
    if not match or method not in ("PATCH", "POST"):
        return None
    with store.read() as c:
        row = A._row(c, match.group(1))
        before = A.flow_of(row) if row else {}
    if not (message(before) or message(fields)):
        return None
    # Stopping is always possible. Any edit, enable, restore or manual run needs the person's click.
    return not (method == "PATCH" and fields.get("enabled") is False
                and set(fields) <= {"enabled", "summary"})


def mode(flow):
    from . import automations as A
    from .automation_flow import choosing
    modes = {step["choose"].get("mode", "draft") for step in choosing(flow.get("steps"))}
    actions = {do for do, _ in A._actions(flow)}
    if "reply" in modes or "send_reply" in actions:
        return "Reply"
    if "draft" in modes or "draft_reply" in actions:
        return "Draft"
    return "Sort"


def require_send(c, flow):
    """A Reply card promises sending, so refuse it when the inbox or company does not allow that."""
    from .automation_actions import send_on
    from .google_workspace import permissions
    if not message(flow) or not flow.get("enabled") or mode(flow) != "Reply":
        return
    inbox = flow["trigger"]["inbox"]
    if not send_on(c, inbox) or (not inbox.startswith("slack:") and not permissions(c)["send_mail"]):
        raise Problem("send_permission", "Reply needs this inbox's send permission; use Draft", 403)


def _name(c, actor):
    if str(actor).startswith("person:") or str(actor).startswith("human:"):
        return (H.human(c, actor.split(":", 1)[1]) or {}).get("name") or actor
    if str(actor).startswith("bot:"):
        row = H.resolve_bot(c, actor) or {}
        return row.get("display_name") or row.get("slug") or actor
    return str(actor or "")


def _action(c, action):
    do, args = action.get("do"), action.get("args") or {}
    if do == "create_task":
        return "Task for " + _name(c, args.get("owner"))
    if do == "wake_bot":
        return "Tell " + _name(c, args.get("bot"))
    if do == "forward":
        return "Forward to " + str(args.get("to") or args.get("webhook") or "")
    if do == "label":
        return "Label " + str(args.get("name") or args.get("tag") or "")
    return {"leave_for_you": "Leave in inbox", "archive": "Archive", "draft_reply": "Draft reply",
            "send_reply": "Send reply", "call_webhook": "Call webhook " + str(args.get("webhook") or "")}.get(
                do, str(do or "").replace("_", " ").capitalize())


def describe(c, flow, verb, revision=None):
    """Only server-derived words on the card; the full reviewed request remains under Details."""
    lines = [{"field": "Inbox", "new": flow["trigger"]["inbox"], "routine": True},
             {"field": "Mode", "new": mode(flow)}]
    if revision:
        lines[0]["routine_revision"] = revision
    for dest in flow.get("destinations") or []:
        lines.append({"field": dest["name"], "new": "; ".join(_action(c, a) for a in dest.get("then") or [])
                      or "Ignore"})
    # A blank hand-written flow may use actions directly rather than named destinations.
    from . import automations as A
    for do, args in A._step_actions(flow.get("steps")):
        lines.append({"field": "Action", "new": _action(c, {"do": do, "args": args})})
    return f'{verb} routine “{flow["name"]}”', lines


def prepare(c, settings, who, method, path, body):
    """Validate and freeze what a Message card reviews, including an existing routine's unchanged destinations."""
    from . import automations as A
    match = EDIT.fullmatch(path)
    if not (method == "POST" and path == CREATE or method == "PATCH" and match):
        return body, None
    before, row = {}, None
    if match:
        row = A.found(c, who, match.group(1), "change this automation", rule=A.may_change)
        before = A.flow_of(row)
    if not (message(before) or message(body)):
        return body, None
    fields = defaults(body)
    fields.pop("summary", None)
    stopping = method == "PATCH" and fields == {"enabled": False}
    if method == "POST":
        fields["enabled"] = True
    flow = A._checked(c, {**before, **fields}, settings, access=not stopping, saved=before or None,
                      aid=row["id"] if row else None)
    A._require(c, who, flow)
    require_send(c, flow)
    if not message(flow):
        return body, None
    if stopping:
        return body, describe(c, flow, "Turn off", digest(row["flow"]))
    # A full snapshot prevents a pending enable from silently approving different steps edited in between.
    model = A.AutomationIn if method == "POST" else A.AutomationPatch
    sent = {k: v for k, v in flow.items() if k in model.model_fields}
    if method == "PATCH":
        sent.update({key: flow.get(key) for key in ("group", "spend_cap", "batch", "destinations")})
        if "summary" in body:
            sent["summary"] = body["summary"]
    verb = "Turn on" if flow["enabled"] and not before.get("enabled") else "Update" if flow["enabled"] else "Save"
    return sent, describe(c, flow, verb, digest(row["flow"]) if row else None)


def check_confirmed(c, request, before, flow):
    """Recheck the reviewed revision and send permission inside the save's transaction."""
    who = request.state.identity
    if who.via != "assistant" or not who.confirmed:
        return
    aid = request.headers.get("x-tico-assistant-action", "").partition(".")[0]
    row = c.execute("SELECT diff FROM assistant_actions WHERE id=?", (aid,)).fetchone()
    lines = json.loads(row["diff"] or "[]") if row else []
    revision = next((line.get("routine_revision") for line in lines if line.get("routine_revision")
                     and line.get("routine_path", request.url.path) == request.url.path), None)
    if revision and (not before or digest(before["flow"]) != revision):
        raise Problem("routine_changed", "This routine changed; ask for a new card", 409)
    require_send(c, flow)


def propose_saved(c, settings, who, row, routes):
    """The create and its off routine's approval card commit together, or neither does."""
    from . import assistant
    return assistant.create_proposal(c, settings, who, assistant.chat_room(c, settings, who), "assistant",
                                     "Turn on " + row["name"], "PATCH", CREATE + "/" + row["id"],
                                     {"enabled": True}, routes=routes)
