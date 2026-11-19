"""The person's open app: the Assistant shows things on their screen (backend/screen_actions.py).

clients/commands/__init__.py says how the table is put together. No `tico` command: it moves an open app, which a
shell has none of."""

from clients.commands._registry import tool
from clients.commands._schemas import _s


@tool(
    "tico_screen_show",
    "Move the person's open Tico app with one action from its list: shell.go {path}, shell.openChat {chat: "
    "agent:<slug>|person:<id>|thread:<id>}, shell.closeChat, shell.fold or shell.unfold {sheet: tico|menu|chat}, "
    "tasks.open {task}, tasks.filter {pipeline, q, owner}; an unknown one is refused with the list. After a change you "
    "made with another tool, done=true only shows it. Waits up to 3 s for the app: say the screen moved only when "
    "status is shown; not_open, no_answer and failed mean it did not.",
    {
        "action": _s("Action id, such as tasks.open"),
        "input": {"type": "object", "description": "The action's input"},
        "done": {"type": "boolean", "description": "The change is already made: only show it"},
    },
    required=("action",),
)
def screen_show(api, args):
    body = {"action": args["action"], "input": args.get("input") or {}}
    if args.get("done"):
        body["done"] = True
    return api.post("screen-actions", body)
