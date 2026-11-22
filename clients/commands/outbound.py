"""The outbound gate (backend/outbound_actions.py, docs/outbound-actions.md): queue a send or an outside write, read the
queue, cancel, and the company's policy per channel and audience.

clients/commands/__init__.py says how the table is put together. An OK is a person's own click in Tico, never a tool:
`tico_outbound_show` says what it waits on."""

import json
from urllib.parse import quote

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import TASK_ID, _key, _s

STATES = ["waiting_approval", "queued", "attempting", "done", "failed", "unknown", "blocked", "canceled"]


@tool(
    "tico_outbound_send",
    "Queue an outbound action through Tico's gate (today: channel slack, an internal post as a bot to a listed "
    "channel). The company's policy decides: queued (goes now), waiting_approval (a person OKs it) or blocked (the "
    "reason in last_error). The same occurrence_key always answers the action it made, so a retry never sends twice. "
    "Never route around a block.",
    {
        "channel": _s("The channel: slack"),
        "destination": _s("Where it goes: a Slack channel id or #name on the channel list"),
        "occurrence_key": _s("Stable key for this one action, e.g. release:v1.2:slack-reply"),
        "payload": {"type": "object", "description": "What is sent; Slack: {bot, text, thread_ts?}"},
        "operation": _s("What it does, e.g. post or reply (default send)"),
        "metadata": {"type": "object", "description": "Kept after the payload is wiped: what it is for"},
        "connection_id": _s("The connection (tool) it goes through, when there is one"),
        "task": TASK_ID,
        "send_after": _s("Not before this ISO time"),
    },
    required=("channel", "destination", "occurrence_key", "payload"),
    writes=True,
)
def outbound_send(api, args):
    body = {k: args[k] for k in ("channel", "destination", "occurrence_key", "payload") if k in args}
    body.update({k: args[k] for k in ("operation", "metadata", "connection_id", "send_after") if args.get(k)})
    if args.get("task"):
        body["task_id"] = args["task"]
    return api.post("outbound-actions", body, key=_key(args))


@tool(
    "tico_outbound_list",
    "Outbound actions you queued (all of them for the owner or an admin), newest first: state, policy decision, "
    "receipt and last error.",
    {
        "state": _s("Only this state", enum=STATES),
        "channel": _s("Only this channel"),
        "cursor": _s("next_cursor from the page before"),
        "limit": {"type": "integer", "description": "1-100, default 50"},
    },
)
def outbound_list(api, args):
    return api.get("outbound-actions", **{k: args[k] for k in ("state", "channel", "cursor", "limit") if k in args})


@tool(
    "tico_outbound_show",
    "One outbound action with its frozen payload (wiped once it ended) and the OK it waits on.",
    {"id": _s("Outbound action id")},
    required=("id",),
)
def outbound_show(api, args):
    return api.get("outbound-actions/" + quote(str(args["id"]), safe=""))


@tool(
    "tico_outbound_cancel",
    "Cancel an outbound action that has not gone: waiting for an OK, queued, blocked, or unknown (it may have gone; "
    "cancel says it must not be tried again). Whoever queued it, the owner or an admin.",
    {"id": _s("Outbound action id")},
    required=("id",),
    writes=True,
)
def outbound_cancel(api, args):
    return api.post(f"outbound-actions/{quote(str(args['id']), safe='')}/cancel", {}, key=_key(args))


@tool(
    "tico_outbound_policy",
    "Read or set the company's outbound policy by '<channel>:<audience>' (audience internal or external): "
    "on_their_own, ask_first or never; null goes back to the default (on_their_own inside, ask_first outside). "
    "Setting it is the owner's or an admin's, as themselves.",
    {"settings": {"type": "object", "description": "Settings to change; omit to read"}},
    writes=True,
)
def outbound_policy(api, args):
    if args.get("settings") is not None:
        return api.call("PUT", "outbound-actions/policy", {"settings": args["settings"]}, key=_key(args))
    return api.get("outbound-actions/policy")


# `tico outbound ...` (clients/cli_spec.py): each leaf's flags come from its tool's schema.
CLI = (
    Group("outbound", "the outbound gate: queued sends and outside writes, and the policy that decides them"),
    Command(
        "outbound send",
        "queue an outbound action through the gate",
        args=(
            Arg("channel", "--channel"),
            Arg("destination", "--to"),
            Arg("occurrence_key", "--key"),
            Arg("payload", "--payload", type=json.loads, metavar="JSON"),
            Arg("metadata", "--metadata", type=json.loads, metavar="JSON"),
        ),
        example='tico outbound send --channel slack --to "#release" --key v1.2:reply '
        '--payload \'{"bot": "ops", "text": "Shipped"}\'',
    ),
    Command("outbound list", "outbound actions, newest first", example="tico outbound list --state unknown"),
    Command("outbound show", "one outbound action", example="tico outbound show 0b5e"),
    Command("outbound cancel", "cancel an action that has not gone", example="tico outbound cancel 0b5e"),
    Command(
        "outbound policy",
        "read or set the company's outbound policy",
        args=(Arg("settings", type=json.loads, metavar="JSON"),),
        example='tico outbound policy --settings \'{"slack:external": "never"}\'',
    ),
)
