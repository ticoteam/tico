"""Computer tools: restart a computer's runner, which release each computer runs, this installation's own update, the
shared Tico Cloud machine and the company's AI subscriptions. Each is a `tico` command and an MCP tool of the same name
(`tico computer restart` is `tico_computer_restart`), registered into the one table like any other module listed in
clients/commands/__init__.py. UPKEEP is their command lines as specs (clients/cli_spec.py).

A computer is the machine (a Mac, a server, a cloud machine); its runner is the Tico software on it. Every tool composes
the ordinary routes with the caller's own token (backend/computers_routes.py, backend/cloud_machines.py,
backend/model_subscriptions.py, the system/update routes). Pure stdlib, like hubtools.
"""

import argparse
from urllib.parse import quote

from clients.cli_spec import Arg, Command, Group
from clients.tico import APIError

from clients.commands._registry import TOOLS, tool
from clients.commands._requester import PEOPLE, REQUESTER, _as_person
from clients.commands._schemas import _key, _s

_FIRST = len(TOOLS)  # the tools below are TOOLS[_FIRST:]

# As in clients/commands/connections.py. Nothing is imported from it at load: either module may be imported first.
ADMINS = REQUESTER
OWNER_ONLY = PEOPLE


def _bots(api):
    rows = api.get("bots")
    return rows.get("bots", []) if isinstance(rows, dict) else rows or []


def _path(value):
    return quote(str(value), safe="")


# ----------------------------------------------------------------------------- computers and their runners
# A computer is the machine (a Mac, a server, a cloud machine); its runner is the Tico software on it. Renaming one,
# Inbox only, Takes member bots, Turns at once and Revoke are Settings controls: tico settings set computer.label ...
def _computers(api):
    return (api.get("computers") or {}).get("computers") or []


def _computer(rows, wanted):
    """One computer by id or label, as tico computer list names it."""
    wanted = str(wanted or "").strip()
    match = [r for r in rows if r.get("id") == wanted] or [
        r for r in rows if str(r.get("label") or "").strip().lower() == wanted.lower()
    ]
    if not match:
        raise APIError("not_found", f"No computer named {wanted!r}; tico computer list names them", 404)
    if len(match) > 1:
        raise APIError("ambiguous", f"More than one computer is called {wanted!r}; use its id", 409)
    return match[0]


@tool(
    "tico_computer_restart",
    "Restart a computer's runner on the latest code (the Owner, an admin, or the person whose computer it is): it "
    "restarts once no turn is running there, so nothing is cut off. Example: tico computer restart 'Shared cloud machine'",
    {"computer": _s("The computer's label or id (tico computer list)")},
    required=("computer",),
    writes=True,
)
def computer_restart(api, args):
    person = _as_person(api)
    row = _computer(_computers(person), args["computer"])
    done = person.post(f"computers/{_path(row['id'])}/restart", {}, key=_key(args))
    return {
        "computer": row.get("label"),
        "id": row["id"],
        **done,
        "detail": "Its runner restarts on the latest code once no turn is running"
        + (" (a turn is running now)" if done.get("running") else ""),
    }


@tool(
    "tico_computer_versions",
    "Which Tico release this server runs and, for each computer, the release and runner version it runs and whether "
    "it is current, updating, behind or failed (with the error). The server's own update state is the Owner's. "
    "Example: tico computer versions",
    {},
)
def computer_versions(api, args):
    person = _as_person(api)
    rows = []
    for r in _computers(person):
        update = r.get("update") if isinstance(r.get("update"), dict) else {}
        line = {
            "computer": r.get("label"),
            "id": r.get("id"),
            "online": bool(r.get("online")),
            "release": r.get("release"),
            "runner_version": r.get("version"),
            "state": update.get("state") or "unknown",
            "wanted": update.get("wanted_release") or update.get("target") or r.get("wanted"),
        }
        if update.get("error"):
            line["error"] = update["error"]
        rows.append(line)
    try:
        server = person.get("system/update")
    except APIError as exc:
        if exc.status != 403:
            raise
        server = None
    states = {}
    for line in rows:
        states[line["state"]] = states.get(line["state"], 0) + 1
    words = ", ".join(f"{n} {s}" for s, n in sorted(states.items())) or "no computers"
    return {
        "summary": (f"Server {server.get('running')} ({server.get('state')}); " if server else "")
        + f"computers: {words}.",
        "server": {k: server.get(k) for k in ("running", "state", "from", "to", "message", "previous") if k in server}
        if server
        else None,
        "computers": rows,
    }


@tool(
    "tico_upgrade_status",
    "This installation's update (the Owner): the release it runs, an update in progress (state, from, to, message), "
    "the last releases it ran and how its computers stand against it. Example: tico upgrade status",
    {},
)
def upgrade_status(api, args):
    return api.get("system/update")


@tool(
    "tico_upgrade_check",
    "Ask now whether a newer Tico release is out (the Owner): current, latest, available (false with `held` when this "
    "server may not move to it) and `managed` when Tico updates this server from outside (Tico Cloud). "
    "Example: tico upgrade check",
    {},
    writes=True,
)
def upgrade_check(api, args):
    return api.post("system/update/check", {}, key=_key(args))


@tool(
    "tico_upgrade_start",
    "Update this installation to a release (the Owner, self-hosted): the server restarts on it and running turns resume "
    "after. Follow it with tico upgrade status. Example: tico upgrade start 0.6.0",
    {"version": _s("The release to move to, e.g. 0.6.0 (tico upgrade check names the latest)", maxLength=64)},
    required=("version",),
    writes=True,
)
def upgrade_start(api, args):
    return api.post("system/update", {"version": str(args["version"]).strip()}, key=_key(args))


# ----------------------------------------------------------------------------- Tico Cloud machines
def _not_cloud():
    return {
        "offered": False,
        "detail": "Cloud machines are for Tico Cloud companies; this one runs on its own computers",
    }


@tool(
    "tico_cloud_show",
    "The company's cloud machines (Tico Cloud): the shared machine's size, state, Always on, Pause after, Grow "
    "storage, this month's cost and starts, the computer it is and the bots set to run there; the sizes on offer with "
    "their price, and the caps. Example: tico cloud show",
    {},
)
def cloud_show(api, args):
    person = _as_person(api)
    seen = person.get("cloud-machines")
    if not seen.get("offered"):
        return _not_cloud()
    shared = dict(seen.get("shared") or {})
    if shared:
        slugs = {b.get("id"): b.get("slug") for b in _bots(person)}
        shared["bots"] = [slugs.get(b, b) for b in shared.get("bots") or []]
        if shared.get("runner_id"):
            try:
                shared["computer"] = _computer(_computers(person), shared["runner_id"]).get("label")
            except APIError:
                pass
    return {
        "offered": True,
        "shared": shared or None,
        "sizes": [
            {k: s.get(k) for k in ("name", "cpu", "memory_gb", "disk_gb", "usd_per_hour", "start_usd") if k in s}
            for s in seen.get("sizes") or []
        ],
        "caps": seen.get("caps") or {},
    }


@tool(
    "tico_cloud_set",
    "Change the shared cloud machine (the Owner or an admin; it spends the company's credit): its `size` (a running "
    "machine restarts at it once its turns finish), `always_on`, `pause_minutes` (5 to 1440 idle minutes before it "
    "pauses) and `grow_storage`. Left out: unchanged. Example: tico cloud set --size large --pause-minutes 60",
    {
        "size": _s("small, default, large or xlarge (tico cloud show lists them)"),
        "always_on": {"type": "boolean", "description": "Keep it running when idle"},
        "pause_minutes": {
            "type": "integer",
            "minimum": 5,
            "maximum": 1440,
            "description": "Idle minutes before it pauses, 5-1440",
        },
        "grow_storage": {"type": "boolean", "description": "Move to the next size with more disk when nearly full"},
    },
    writes=True,
)
def cloud_set(api, args):
    body = {k: args[k] for k in ("size", "always_on", "pause_minutes", "grow_storage") if args.get(k) is not None}
    if not body:
        raise ValueError(
            "Name something to change: --size, --always-on/--no-always-on, --pause-minutes or --grow-storage"
        )
    return _as_person(api).post("cloud-machines/shared", body, key=_key(args))


@tool(
    "tico_cloud_replace",
    "Delete the shared cloud machine and its disk to replace a broken one (the Owner or an admin): the next work makes "
    "a new one with the same size and settings. `confirm` must be its computer's exact name. "
    "Example: tico cloud replace --confirm 'Shared cloud machine'",
    {"confirm": _s("The shared machine's computer name, exactly (tico cloud show)")},
    required=("confirm",),
    writes=True,
)
def cloud_replace(api, args):
    person = _as_person(api)
    seen = person.get("cloud-machines")
    if not seen.get("offered"):
        return _not_cloud()
    runner = (seen.get("shared") or {}).get("runner_id")
    if not runner:
        return {"replaced": False, "detail": "There is no shared cloud machine now; the next work makes one"}
    try:
        name = _computer(_computers(person), runner).get("label") or runner
    except APIError:
        name = runner
    if args["confirm"] != name:
        raise APIError(
            "confirmation_required", f"To replace it, confirm its exact name: --confirm '{name}'; nothing changed", 409
        )
    return {"replaced": True, **(person.call("DELETE", "cloud-machines/shared", None, key=_key(args)) or {})}


# ----------------------------------------------------------------------------- subscriptions
@tool(
    "tico_subscription_list",
    "The company's AI subscriptions (a person): each one's name, kind, runtime, status, a preview of its value (never "
    "the value), who added it and owns it and the computers given it; each computer given a person's plans for a tool "
    "(`people_plans`: whose, the plan in use and why); and the computers that cannot take one until they update. "
    "Adding one or pasting a value is the person's own, in Settings > Models. Example: tico subscription list",
    {},
)
def subscription_list(api, args):
    person = _as_person(api)
    subs = person.get("model-subscriptions")
    given = person.get("computer-subscriptions")
    labels = {r.get("id"): r.get("label") for r in _computers(person)}
    capable = set(given.get("capable") or [])
    return {
        "subscriptions": [
            {
                **{
                    k: s.get(k)
                    for k in ("id", "name", "kind", "label", "runtime", "status", "status_detail", "preview")
                },
                "computers": [labels.get(r, r) for r in s.get("computers") or []],
                "added_by": s.get("added_by_name") or s.get("added_by"),
                **({"owner": s.get("owner_name")} if s.get("owner_name") else {}),
            }
            for s in subs.get("subscriptions") or []
        ],
        "people_plans": [
            {
                "computer": labels.get(a.get("runner_id"), a.get("runner_id")),
                "tool": a.get("runtime"),
                "plans": f"{(a.get('set') or {}).get('person_name')}'s plans",
                "count": (a.get("set") or {}).get("plans"),
                "on": (a.get("set") or {}).get("on_name"),
                "why": _set_why((a.get("set") or {}).get("why") or {}),
            }
            for a in given.get("assignments") or []
            if a.get("person")
        ],
        "cannot_take_one": [label for rid, label in labels.items() if rid not in capable],
        "detail": "Give one, or a person's plans (`person`), to a computer: tico settings set models.computer_subscription",
    }


def _set_why(why):
    """Why a computer given a person's plans is on the plan it is on, in a line (backend model_subscriptions.set_state)."""
    kind, plan, until = why.get("kind"), why.get("plan"), why.get("until")
    if kind == "held":
        return f"{plan} at its limit until {until}"
    if kind == "reconnect":
        return f"{plan} needs reconnecting"
    if kind == "all_held":
        return f"all at their limit, next reset {until}: the computer's own login, the Gateway or waiting"
    if kind == "none":
        return "the person owns no plan for this tool"
    return "soonest reset with usage left"


# ----------------------------------------------------------------------------- registration
MINE = TOOLS[_FIRST:]
AUDIENCE = {
    **{t["name"]: ADMINS for t in MINE},
    **{
        name: OWNER_ONLY
        for name in ("tico_upgrade_status", "tico_upgrade_check", "tico_upgrade_start", "tico_subscription_list")
    },
}


# ----------------------------------------------------------------------------- the `tico` commands
# `tico computer restart` and `versions` are in COMPUTER (clients/commands/computers.py).
UPKEEP = (
    Group("upgrade", "update this installation to a newer Tico release (the Owner)"),
    Command("upgrade status", "the release this server runs and any update in progress", example="tico upgrade status"),
    Command("upgrade check", "ask now whether a newer release is out", example="tico upgrade check"),
    Command(
        "upgrade start",
        "update to a release, self-hosted; running turns resume after",
        example="tico upgrade start 0.6.0",
    ),
    Group("cloud", "the company's Tico Cloud machines"),
    Command("cloud show", "the shared cloud machine, the sizes on offer and the caps", example="tico cloud show"),
    Command(
        "cloud set",
        "change the shared machine's size, Always on, Pause after or Grow storage (the Owner or an admin)",
        args=(
            Arg("pause_minutes", metavar="N"),
            Arg("always_on", action=argparse.BooleanOptionalAction, default=None),
            Arg("grow_storage", action=argparse.BooleanOptionalAction, default=None),
        ),
        example="tico cloud set --size large --pause-minutes 60",
    ),
    Command(
        "cloud replace",
        "delete the shared machine and its disk; the next work makes a new one (the Owner or an admin)",
        args=(Arg("confirm", "--confirm", metavar="NAME"),),
        example="tico cloud replace --confirm 'Shared cloud machine'",
    ),
    Group("subscription", "the company's AI subscriptions"),
    Command(
        "subscription list",
        "each subscription, its status and the computers given it; values never shown (a person)",
        example="tico subscription list",
    ),
)
