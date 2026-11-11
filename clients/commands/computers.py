"""Computers (list, detail, performance, turns at once, the browser exit), health, maintenance and support. Restarts, releases, this installation's update, the cloud machine and subscriptions are in computer_upkeep.

Moved from clients/hubtools.py; clients/commands/__init__.py says how the table is put together. HEALTH and COMPUTER
are the `tico health` and `tico computer` command lines as specs (clients/cli_spec.py), with `tico health ready`
(clients/commands/connections.py) and `tico computer restart` and `versions` (clients/commands/computer_upkeep.py)."""

import hashlib
import json
from urllib.parse import quote

from clients.cli_spec import NULL, Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._schemas import _key, _s
from clients.commands._requester import _as_person


@tool(
    "tico_computer_turns_at_once",
    "Set how many turns a computer you administer runs at once. Null is automatic "
    "(its own capacity); N is 1 to 256. A computer that cannot take a raised number runs at most the capacity it "
    "reports.",
    {
        "computer": _s("Computer id"),
        "limit": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 256,
            "description": "1-256 turns at once, or null (`automatic` on a command line) for its own capacity",
        },
    },
    ("computer", "limit"),
    writes=True,
)
def computer_turns_at_once(api, args):
    who = _as_person(api)
    return who.post(f"computers/{args['computer']}/turns-at-once", {"turns_at_once": args['limit']}, key=_key(args))


@tool(
    "tico_computer_browser_exit",
    "Read or set whether a computer shares its internet with bots, so their browsers "
    "reach the web from it (docs/computers.md). Only the person whose computer it is turns it on or picks the bots "
    "and daily cap; an admin may turn it off. With usage, also what went through it per day, bot and hostname.",
    {
        "computer": _s("Computer id"),
        "sharing": {"type": "boolean", "description": "Whether it shares (on or off); omit to read"},
        "bots": {"type": "array", "items": {"type": "string"}, "description": "Bot slugs it shares with; omit to keep"},
        "daily_cap_gb": {
            "type": "number",
            "minimum": 0.1,
            "maximum": 10000,
            "description": "The daily cap in GB; omit to keep (default 5)",
        },
        "usage": {"type": "boolean", "description": "Also read the last 7 days of what went through it"},
    },
    ("computer",),
    writes=True,
)
def computer_browser_exit(api, args):
    who = _as_person(api)
    path = f"computers/{quote(str(args['computer']), safe='')}/browser-exit"
    if "sharing" in args:
        body = {"sharing": bool(args["sharing"])}
        if args.get("bots") is not None:
            body["bots"] = list(args["bots"])
        if args.get("daily_cap_gb") is not None:
            body["daily_cap_bytes"] = int(float(args["daily_cap_gb"]) * 1_000_000_000)
        who.post(path, body, key=_key(args))
    seen = who.get(path)
    if args.get("usage"):
        seen["usage"] = who.get(path + "/usage?days=7&limit=100")
    return seen


# A computer's full row runs to tens of thousands of characters (every repository, each bot's readiness, runtime
# catalogs, recent log errors), so the list is one short row each and the detail is paged section by section.
COMPUTER_SECTIONS = ("assigned", "repositories", "bots", "runtimes", "harnesses", "recent_errors")


COMPUTER_PAGE = 20


COMPUTER_TEXT = 300


COMPUTER_DETAIL_CHARS = 40_000


def _computer_counts(row):
    """Warnings and errors as numbers: each bot's warnings and problems (a bot not ready that names none counts one),
    and a failed update."""
    readiness = row.get("readiness") if isinstance(row.get("readiness"), dict) else {}
    reports = [r for r in (readiness.get("bots") or {}).values() if isinstance(r, dict)]
    warnings = sum(len(r.get("warnings") or []) for r in reports)
    errors = sum(len(r.get("problems") or []) or (0 if r.get("ready") else 1) for r in reports)
    return warnings, errors + bool((row.get("update") or {}).get("error")), readiness


def computer_summary(row):
    """One computer in a few hundred characters: who runs it, whether it is up, its release and update, and how
    ready it is, with counts in place of the text."""
    update = row.get("update") or {}
    warnings, errors, readiness = _computer_counts(row)
    state = (
        "failed"
        if update.get("error")
        else {"needs_update": "update_available"}.get(update.get("state"), update.get("state") or "unknown")
    )
    level = "offline" if not row.get("online") else "not_ready" if errors else "warnings" if warnings else "ready"
    return {
        "id": row.get("id"),
        "label": row.get("label"),
        "online": bool(row.get("online")),
        "last_seen_at": row.get("last_seen_at"),
        "owner_id": row.get("owner_id"),
        "platform": row.get("platform") or "",
        "accepts_member_bots": bool(row.get("accepts_member_bots")),
        "release": row.get("release") or "",
        "version": row.get("version") or "",
        "wanted_release": update.get("wanted_release") or "",
        "update": state,
        "readiness": level,
        "bots": len(row.get("bots") or []),
        "signed_in": sorted(
            name
            for name, r in (readiness.get("runtimes") or {}).items()
            if isinstance(r, dict) and r.get("authenticated") == "ready"
        ),
        "warnings": warnings,
        "errors": errors,
        "recent_errors": len(readiness.get("recent_errors") or []),
        "latest_sample": row.get("latest_sample"),
        "overloaded": bool(row.get("overloaded")),
    }


def _bounded(value, cap, text=COMPUTER_TEXT):
    """Cut long text, long lists and long maps of records inside one section item, and say how much was cut."""
    if isinstance(value, str):
        return value if len(value) <= text else value[:text] + "… (cut)"
    if isinstance(value, list):
        cut = [f"… {len(value) - cap} more"] if len(value) > cap else []
        return [_bounded(v, cap, text) for v in value[:cap]] + cut
    if isinstance(value, dict):
        keys = list(value)
        many = len(keys) > cap and all(isinstance(v, dict) for v in value.values())
        out = {k: _bounded(value[k], cap, text) for k in (keys[:cap] if many else keys)}
        return {**out, "…": f"{len(keys) - cap} more"} if many else out
    return value


def _computer_section(row, name):
    """A section's items in a stable order, or None when the computer has none: maps by name, recent errors
    newest first."""
    readiness = row.get("readiness") if isinstance(row.get("readiness"), dict) else {}
    found = {"assigned": row.get("bots"), "repositories": row.get("repositories")}.get(name, readiness.get(name))
    if isinstance(found, dict):
        return sorted(found.items())
    if isinstance(found, list):
        return list(reversed(found)) if name == "recent_errors" else found
    return None


def _page(items, name, offset, cap, text):
    """One page of a section's items, each cut to size (a map's records by name), and where the next page starts
    (None on the last page)."""
    chosen = items[offset : offset + cap]
    page = (
        [_bounded(v, cap, text) for v in chosen]
        if name in ("assigned", "repositories", "recent_errors")
        else {k: _bounded(v, cap, text) for k, v in chosen}
    )
    return page, offset + cap if offset + cap < len(items) else None


def computer_detail(row, section=None, offset=0):
    """One computer in full, every large section capped at COMPUTER_PAGE items and long text cut, with a note on each
    cut saying how to read the rest; with `section`, one page of that section from `offset`. A row still over
    COMPUTER_DETAIL_CHARS is paged smaller and cut shorter; one that cannot fit at all names the sections and how to
    read them instead."""
    more = lambda name, at: f"tico_computer_list computer={row.get('id')} section={name} offset={at}"
    fits = lambda result: len(json.dumps(result, default=str)) <= COMPUTER_DETAIL_CHARS
    offset = max(0, int(offset or 0))
    for cap, text in ((COMPUTER_PAGE, COMPUTER_TEXT), (10, COMPUTER_TEXT), (5, 200), (2, 100), (1, 50)):
        if section:
            items = _computer_section(row, section) or []
            page, after = _page(items, section, offset, cap, text)
            result = {
                "id": row.get("id"),
                "label": row.get("label"),
                "section": section,
                "offset": offset,
                "total": len(items),
                "items": page,
                **({"more": more(section, after)} if after else {}),
            }
        else:
            readiness = row.get("readiness") if isinstance(row.get("readiness"), dict) else {}
            result = {k: _bounded(v, cap, text) for k, v in row.items() if k not in ("readiness", "bots")}
            result["readiness"] = {
                k: _bounded(v, cap, text) for k, v in readiness.items() if k not in COMPUTER_SECTIONS
            }
            result["truncated"] = {}
            for name in COMPUTER_SECTIONS:
                items = _computer_section(row, name)
                if items is None:
                    continue
                page, after = _page(items, name, 0, cap, text)
                if name in ("assigned", "repositories"):
                    result["bots" if name == "assigned" else name] = page
                else:
                    result["readiness"][name] = page
                if after:
                    result["truncated"][name] = {"shown": len(page), "total": len(items), "more": more(name, after)}
        if fits(result):
            return result
    keep = {k: _bounded(row.get(k), 1, 50) for k in ("id", "label", "online", "last_seen_at", "release", "version")}
    if section:
        items = _computer_section(row, section) or []
        return {
            **keep,
            "section": section,
            "offset": offset,
            "total": len(items),
            "items": [],
            "too_large": f"Item {offset + 1} is too large to show here; Settings > Computers shows it",
            **({"more": more(section, offset + 1)} if offset + 1 < len(items) else {}),
        }
    sections = {name: _computer_section(row, name) for name in COMPUTER_SECTIONS}
    return {
        **keep,
        "too_large": "This computer's detail is too large to show at once; read it a section at a time",
        "truncated": {
            name: {"shown": 0, "total": len(items), "more": more(name, 0)} for name, items in sections.items() if items
        },
    }


@tool(
    "tico_computer_list",
    "Company computers and their latest CPU/memory/swap, running turns and overloaded flag, one short row each: id, label, "
    "online and last seen, operator, release (Tico), version (runner software), wanted release, update (current, "
    "update_available, updating, failed, incompatible or unknown), readiness (ready, warnings, not_ready or offline), "
    "how many bots run there, the AI tools signed in, and counts of warnings, errors and recent log errors; and "
    "whether each team service is ok. "
    "For one computer in full (the bots on it, repositories, each bot's readiness, runtimes, update error, recent "
    "errors) pass "
    "`computer`: large sections come capped, and `truncated` says how to read the rest with `section` and `offset`.",
    {
        "computer": _s("One computer's label or id: its full detail instead of the list"),
        "section": {
            "type": "string",
            "enum": list(COMPUTER_SECTIONS),
            "description": "With `computer`: one page of this section only",
        },
        "offset": {
            "type": "integer",
            "minimum": 0,
            "description": "With `section`: the first item to return (default 0)",
        },
    },
)
def computers(api, args):
    listing = _as_person(api).get("computers") or {}
    rows = listing.get("computers") or []
    wanted = str(args.get("computer") or "").strip()
    if not wanted:
        if args.get("section"):
            raise ValueError("Name the computer whose section you want: `computer`")
        return {
            "computers": [computer_summary(r) for r in rows],
            "services": [
                {"name": s.get("name"), "ok": not s.get("last_error"), "last_succeeded_at": s.get("last_succeeded_at")}
                for s in listing.get("services") or []
            ],
            "detail": "One computer in full: tico_computer_list computer=<label or id>",
        }
    match = [r for r in rows if wanted == r.get("id")] or [
        r for r in rows if wanted.lower() == str(r.get("label") or "").strip().lower()
    ]
    if len(match) != 1:
        raise ValueError(
            f"No computer named {wanted!r}; tico computer list names them"
            if not match
            else f"More than one computer is called {wanted!r}; use its id"
        )
    if args.get("section") and args["section"] not in COMPUTER_SECTIONS:
        raise ValueError("section is one of " + ", ".join(COMPUTER_SECTIONS))
    return computer_detail(match[0], args.get("section"), args.get("offset") or 0)


@tool(
    "tico_computer_performance",
    "A computer's CPU, memory, load, swap and free disk over the last hours (up to 336): "
    "a summary of when it was overloaded and what ran then, its overloads, the bots with the most turns there and "
    "the busiest processes, and up to 96 points (columns named in `columns`). Numbers and process names only.",
    {
        "computer": _s("The computer's label or id (tico computer list)"),
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": 336,
            "default": 24,
            "description": "How many hours back, 1-336",
        },
    },
    required=("computer",),
)
def computer_performance(api, args):
    # The route accepts either a computer label or id.
    wanted = str(args["computer"]).strip()
    seen = api.get(f"computers/{quote(wanted, safe='')}/performance?hours={int(args.get('hours') or 24)}")
    points = seen.get("points") or []
    step = max(1, -(-len(points) // 96))
    return {
        **{
            k: seen.get(k)
            for k in (
                "computer",
                "performance",
                "hours",
                "summary",
                "overloads",
                "busiest",
                "columns",
                "mem_total",
                "disk_total",
                "scope",
            )
        },
        "runs": len(seen.get("runs") or []),
        "points": points[::step],
    }


@tool(
    "tico_health_check",
    "What is wrong, most urgent first: bots with no computer, computers offline, failing runs, a "
    "credential a bot needs, setup that never finished, paused or stopped bots, failing watchers or queued work, "
    "and computers low on disk space. Includes the same checks as Health and their fixes. Fix what you may, then report. "
    "The Assistant also gets the live snapshot of the team's bots.",
    {},
)
def health_check(api, args):
    return _as_person(api).get("health/issues")


@tool(
    "tico_health_metrics",
    "The server's flight recorder, for owners, admins and bots: per route request counts, errors, p50/p95 and "
    "bytes by caller kind; slow requests; CPU, memory, event-loop lag and stalls with thread stacks; top SQL by total "
    "time; write-lock waits; database size and growth by table; and each start's release and image check.",
    {
        "minutes": {"type": "integer", "description": "Look back this many minutes (default 60, at most 20160)"},
        "section": _s("all (default) or a comma list of requests, slow, process, sql, db, events"),
    },
)
def health_metrics(api, args):
    query = {k: args[k] for k in ("minutes", "section") if args.get(k)}
    return _as_person(api).get("system/metrics", **query)


@tool(
    "tico_support_file",
    "Tell the Tico team about something the product cannot do, or a fault you cannot fix. BotOps files it as the "
    "person who asked, with their rights; with its own rights it is refused. Say what they asked for, what you tried, what the "
    "product answered, the bot and the version. No secrets, no other people's details.",
    {"message": _s("What happened and what is missing, in plain words")},
    required=("message",),
    writes=True,
)
def support_file(api, args):
    return _as_person(api).post(
        "support/tickets",
        {"message": "[BotOps] " + args["message"], "include_ids": True, "source": "botops"},
        key=_key(args),
    )


@tool(
    "tico_support_report",
    "Report a Tico bug to the Tico team (the maintainers). When the team lets bots send reports without "
    "asking, it is sent at once and this returns the ticket. Otherwise a person is asked for an OK on a task: they see "
    "the whole message in Needs you. Nothing leaves this install until they OK it; then send it with tico_support_send. "
    "Write it self-contained: what failed, since when, the exact error, what you tried. No secrets, tokens or customer "
    "data. Don't hand the person a copy-and-paste task instead.",
    {
        "message": _s("The whole report, plain text, up to 4000 characters"),
        "include_ids": {
            "type": "boolean",
            "description": "Send the Tico version and this install's ID with it (default true)",
        },
        "task": _s("The task the report belongs to; the OK is asked on it. Without one, a task is filed"),
    },
    required=("message",),
    writes=True,
)
def support_report(api, args):
    message = support_message(args.get("message"))
    payload = {"message": message, "content_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest()}
    if args.get("include_ids") is False:
        payload["include_ids"] = False
    body = {"payload": payload}
    if args.get("task"):
        body["task_id"] = args["task"]
    row = api.post("support/reports", body, key=_key(args))
    if row.get("sent"):
        # The team rule let it go at once (backend/support.py Support.report): filed as the install's default person.
        return {
            "ticket": row.get("ticket"),
            "sent": True,
            "filed_as": row.get("filed_as"),
            "next": "Sent to the Tico team without a person's OK. Replies reach the person it was filed as.",
        }
    ok = row.get("requirement_id") or row.get("id")
    return {
        "ok": ok,
        "task_id": row.get("task_id"),
        "status": row.get("status") or "pending",
        "next": f"A person must OK it in Needs you. Then send it: tico support send --ok {ok} (tico_support_send). "
        "Until then nothing is sent.",
    }


def support_message(value):
    """The report as backend/support.py `clean_message` keeps it (line ends as \\n, no blank space round it), so the hash
    a person OKs is the hash of what is sent."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


@tool(
    "tico_support_send",
    "Send a Tico bug report a person OK'd (tico_support_report gave its OK id). It is filed as the person who "
    "OK'd it and its replies reach their Support page. Before their OK, after they send it back, or for an OK you did "
    "not ask for, it is refused; a second call answers with the same ticket. When Tico support cannot be reached, "
    "try again later (Maintenance also sends it): it still goes once.",
    {"ok": _s("The OK id tico_support_report returned")},
    required=("ok",),
    writes=True,
)
def support_send(api, args):
    return api.post("support/tickets/from-approval", {"approval": args["ok"]}, key=_key(args))


@tool(
    "tico_health_findings",
    "What Check health found on this install: each open problem with its count, last seen time and "
    "Tico-bug report, whether BotOps is there to take findings, and whether bug reports go without asking.",
    {},
)
def health_findings(api, args):
    return api.get("health/findings")


@tool(
    "tico_health_report",
    "Report a health finding as a Tico bug. The server writes the report from the finding and attaches the "
    "redacted diagnostics bundle: one ticket per problem, and a message on it when it happens again. With the team rule "
    "off it asks a person for an OK first.",
    {"key": _s("The finding's key or signature, from its task")},
    writes=True,
)
def health_report(api, args):
    key = args.get("key") or ""
    if not key:
        raise ValueError("name a finding's key")
    from urllib.parse import quote

    return api.post("health/findings/" + quote(key, safe="") + "/report", {}, key=_key(args))


MAINTENANCE_STEPS = ["collect", "clean_up", "check_health", "learn", "suggest", "update", "dream"]


@tool(
    "tico_maintenance",
    "Maintenance: the schedule, the pause switch and the monthly judge spend cap, then each step's switch, "
    "how often it runs, its bot limit and its last run. A live run comes with them.",
    {},
)
def maintenance_status(api, args):
    return _as_person(api).get("maintenance")


@tool(
    "tico_maintenance_set",
    "Maintenance: set the schedule (hourly, 2h, 3h, 6h, 12h or daily), pause or resume it, or set the "
    "monthly judge spend cap in dollars. With `step`, change that step: `enabled` on or off, `frequency` (run = every run, "
    "day, week, or custom with `custom_every` and `custom_unit`) and `max_bots` for dream. "
    "The owner or an admin.",
    {
        "schedule": {"type": "string", "enum": ["hourly", "2h", "3h", "6h", "12h", "daily"]},
        "paused": {"type": "boolean", "description": "true pauses the schedule, false resumes it"},
        "monthly_cap_usd": {"type": "number", "minimum": 0, "maximum": 100000},
        "step": {"type": "string", "enum": MAINTENANCE_STEPS},
        "enabled": {"type": "boolean", "description": "The step on or off"},
        "frequency": {"type": "string", "enum": ["run", "day", "week", "custom"]},
        "custom_every": {"type": "integer", "minimum": 1, "maximum": 999},
        "custom_unit": {"type": "string", "enum": ["hours", "days"]},
        "max_bots": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    writes=True,
)
def maintenance_set(api, args):
    body = {k: args[k] for k in ("schedule", "paused", "monthly_cap_usd") if args.get(k) is not None}
    change = {
        k: args[k]
        for k in ("enabled", "frequency", "custom_every", "custom_unit", "max_bots")
        if args.get(k) is not None
    }
    if change:
        if not args.get("step"):
            raise ValueError("name the step: --step KEY with --on/--off, --frequency, --every, --unit or --max-bots")
        body["steps"] = [{"key": args["step"], **change}]
    if not body:
        raise ValueError("name something to change: --schedule, --pause/--resume, --cap or a step's switch")
    return _as_person(api).call("PATCH", "maintenance", body, _key(args))


@tool(
    "tico_maintenance_runs",
    "Maintenance: recent runs, newest first: when each started, what started it (the schedule or Run now), "
    "whether it finished, and each step's status, reason, counts and cost. Page with `before` from `next`.",
    {"limit": {"type": "integer", "minimum": 1, "maximum": 200}, "before": _s("`next` from the page before")},
)
def maintenance_runs(api, args):
    query = {k: args[k] for k in ("limit", "before") if args.get(k) not in (None, "")}
    return _as_person(api).call("GET", "maintenance/runs", query=query)


@tool(
    "tico_maintenance_run",
    "Maintenance: run now, all steps or only `step`. A plain run is claimed and due by the same rules as the "
    "schedule, so a step runs only when it is due and its switch is on; `step` runs that step even when it is not due. "
    "A run already going is returned, not a second one. "
    "The owner or an admin.",
    {"step": {"type": "string", "enum": MAINTENANCE_STEPS}},
    writes=True,
)
def maintenance_run(api, args):
    body = {"step": args["step"]} if args.get("step") else {}
    return _as_person(api).call("POST", "maintenance/run", body, _key(args))


# ----------------------------------------------------------------------------- the `tico` commands
def _slugs(text):
    return [b.strip() for b in text.split(",") if b.strip()]


HEALTH = (
    Group("health", "what is wrong, and where"),
    Command(
        "health check", "what is wrong with the bots, most urgent first, each with its fix", example="tico health check"
    ),
    Command(
        "health metrics",
        "the server's flight recorder: requests, CPU, loop lag, SQL, database size",
        example="tico health metrics --minutes 30 --section slow,sql",
    ),
    Command(
        "health findings",
        "what Check health found on this install, each problem with its report",
        example="tico health findings",
    ),
    Command(
        "health report",
        "report a finding as a Tico bug: one ticket per problem, with the redacted diagnostics",
        args=(Arg("key", positional=True, nargs="?"),),
        example="tico health report runner-crash-loop",
    ),
    Command(
        "health ready",
        "is this company able to work now: blockers with their fixes, then warnings; exits 1 when not ready",
        args=(Arg("bots", "--bot", metavar="SLUG"), Arg("github", "--no-github", action="store_const", const=False)),
        example="tico health ready --bot builder",
    ),
)

COMPUTER = (
    Group("computer", "the computers a bot may go on"),
    Command(
        "computer list",
        "the computers a bot may go on, one short row each; --computer for one in full",
        example="tico computer list --computer mac-studio --section bots",
    ),
    Command(
        "computer performance",
        "a computer's CPU, memory and disk over time, overloads and what ran then",
        args=(Arg("hours", default=24),),
        example="tico computer performance mac-studio --hours 6",
    ),
    Command(
        "computer turns-at-once",
        "set how many turns a computer runs at once: N, or automatic for its own capacity",
        args=(Arg("limit", type=lambda v: NULL if v == "automatic" else int(v)),),
        example="tico computer turns-at-once mac-studio 4",
    ),
    Command(
        "computer browser-exit",
        "read or set whether a computer shares its internet with bots: on or off",
        args=(
            Arg("sharing", positional=True, nargs="?", action="store", choices=("on", "off")),
            Arg("bots", action="store", type=_slugs, metavar="A,B"),
            Arg("daily_cap_gb", "--cap-gb", metavar="GB"),
            Arg("usage", default=None),
        ),
        convert=lambda f: f | {"sharing": f["sharing"] == "on"} if "sharing" in f else f,
        example="tico computer browser-exit mac-studio on --bots researcher --cap-gb 2",
    ),
    Command(
        "computer restart",
        "restart a computer's runner on the latest code once no turn runs (the Owner, an admin or its person)",
        example="tico computer restart 'Shared cloud machine'",
    ),
    Command(
        "computer versions",
        "the server's release and each computer's release, runner version and update state",
        example="tico computer versions",
    ),
)
