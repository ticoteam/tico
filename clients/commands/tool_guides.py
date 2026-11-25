"""The Tools registry (docs/connect-tools.md, "Tools registry"): find a tool, read and write its guide, keep an
agent's own list.

`tico tool guide <slug>` reads one guide, so writing and archiving are `tico tool guide-write` and
`tico tool guide-archive`: a group cannot also take a slug. Descriptions stay short: the Assistant's MCP listing is near
its byte cap, and these are not in it.
"""

from pathlib import Path
from urllib.parse import quote

from clients.cli_spec import Arg, Command, Group
from clients.commands._registry import tool
from clients.commands._requester import _as_person
from clients.commands._schemas import _key, _s

SLUG = _s("The tool: a vendor (stripe, github), a command (gh, psql) or a built tool's slug")
BOT = _s("The agent (id or slug); yours when you are one")
TEXT = ("name", "category", "summary", "instructions", "guidance", "source", "origin", "commands", "needs")


def _q(value):
    return quote(str(value or "").strip(), safe="")


def _bot(api, args):
    """The agent named, or the caller when it is one."""
    if args.get("bot"):
        return str(args["bot"]).removeprefix("bot:")
    actor = str(api.get("me").get("actor") or "")
    if not actor.startswith("bot:"):
        raise ValueError("Say which agent: --bot <slug>")
    return actor.removeprefix("bot:")


@tool(
    "tico_tool_find",
    "Find a tool before working by hand: shipped guides, the company's guides and built tools, every vendor in Tools.",
    {
        "q": _s("Words to look for in a tool's name, summary, category or commands"),
        "category": _s("Only this category (data, finance, engineering, ...)"),
        "built": {"type": "boolean", "description": "Only tools built here"},
        "mine": {"type": "boolean", "description": "Only your own tools (an agent's list, grants and recent use)"},
        "bot": BOT,
    },
)
def tool_find(api, args):
    query = {"q": args.get("q") or None, "category": args.get("category") or None}
    if args.get("built"):
        query["origin"] = "built"
    if args.get("mine") or args.get("bot"):
        query["bot"] = _bot(api, args)
    return api.get("tool-guides", **query)


@tool(
    "tico_tool_guide",
    "One tool's guide, your access to it (variable names, never a value) and who uses it.",
    {"slug": SLUG},
    required=("slug",),
)
def tool_guide(api, args):
    return api.get(f"tool-guides/{_q(args['slug'])}")


@tool(
    "tico_tool_guide_write",
    "Write or change a tool's guide; only what is sent changes. A built tool needs summary and instructions. No keys.",
    {
        "slug": SLUG,
        "name": _s("Its name"),
        "origin": _s("built: a script or program made here", enum=["vendor", "built"]),
        "category": _s("data, finance, engineering, marketing, operations, ..."),
        "summary": _s("One line: what it is for"),
        "instructions": _s("How to use it (Markdown)"),
        "guidance": _s("The company's own notes: when to use it, which account, what to ask first (Markdown)"),
        "commands": {"type": "array", "items": {"type": "string"}, "description": "Programs that are this tool (gh)"},
        "source": _s("A built tool's repository and path"),
        "needs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "level": {"type": "string", "enum": ["read", "write"]}},
                "required": ["tool"],
                "additionalProperties": False,
            },
            "description": "A built tool's own tools, each at a level",
        },
    },
    required=("slug",),
    writes=True,
)
def tool_guide_write(api, args):
    body = {k: args[k] for k in TEXT if args.get(k) is not None}
    return _as_person(api).call("PUT", f"tool-guides/{_q(args['slug'])}", body, key=_key(args))


@tool(
    "tico_tool_guide_archive",
    "Archive a tool's guide (its owner, the Tools Owner or a credential admin).",
    {"slug": SLUG},
    required=("slug",),
    writes=True,
)
def tool_guide_archive(api, args):
    return _as_person(api).call("DELETE", f"tool-guides/{_q(args['slug'])}", {}, key=_key(args))


@tool("tico_tool_mine", "An agent's tools: its list with notes, its grants and what it used in 30 days.", {"bot": BOT})
def tool_mine(api, args):
    return api.get(f"bots/{_q(_bot(api, args))}/tool-guides")


@tool(
    "tico_tool_mine_add",
    "Put a tool on an agent's list, with what it uses it for.",
    {"slug": SLUG, "note": _s("What you use it for"), "bot": BOT},
    required=("slug",),
    writes=True,
)
def tool_mine_add(api, args):
    path = f"bots/{_q(_bot(api, args))}/tool-guides/{_q(args['slug'])}"
    return _as_person(api).call("PUT", path, {"note": args.get("note") or ""}, key=_key(args))


@tool(
    "tico_tool_mine_remove",
    "Take a tool off an agent's list.",
    {"slug": SLUG, "bot": BOT},
    required=("slug",),
    writes=True,
)
def tool_mine_remove(api, args):
    path = f"bots/{_q(_bot(api, args))}/tool-guides/{_q(args['slug'])}"
    return _as_person(api).call("DELETE", path, {}, key=_key(args))


def _file(path):
    return Path(path).expanduser().read_text(encoding="utf-8")


def _words(text):
    return [w.strip() for w in str(text).split(",") if w.strip()]


def _need(text):
    """`stripe:read` as {tool, level}."""
    name, _, level = str(text).partition(":")
    return {"tool": name.strip().lower(), "level": (level.strip().lower() or "read")}


def _joined(fields):
    if isinstance(fields.get("q"), list):
        fields["q"] = " ".join(fields["q"]) or None
        if fields["q"] is None:
            fields.pop("q")
    return fields


CLI = (
    Command(
        "tool find",
        "find a tool before working by hand",
        args=(Arg("q", positional=True, nargs="*", metavar="WORDS"),),
        convert=_joined,
        example="tico tool find csv sql",
    ),
    Command("tool guide", "one tool's guide, your access and who uses it", example="tico tool guide stripe"),
    Command(
        "tool guide-write",
        "write or change a tool's guide",
        tool="tico_tool_guide_write",
        args=(
            Arg("instructions", "--instructions-file", type=_file, metavar="FILE"),
            Arg("guidance", "--guidance-file", type=_file, metavar="FILE"),
            Arg("origin", "--built", action="store_const", const="built"),
            Arg("commands", type=_words, action="store", metavar="A,B"),
            Arg("needs", type=_need, action="append", metavar="TOOL:LEVEL"),
        ),
        example="tico tool guide-write refund-report --built --summary 'Weekly refunds by reason' "
        "--instructions-file README.md --source acme/scripts:refunds.py --needs stripe:read",
    ),
    Command(
        "tool guide-archive",
        "archive a tool's guide",
        tool="tico_tool_guide_archive",
        example="tico tool guide-archive refund-report",
    ),
    Group("tool mine", "an agent's own tools", default="tool mine list"),
    Command("tool mine list", "its list, grants and recent use", tool="tico_tool_mine", example="tico tool mine"),
    Command(
        "tool mine add",
        "list a tool you use, with what for",
        example="tico tool mine add duckdb --note 'weekly revenue from the Stripe export'",
    ),
    Command("tool mine remove", "take a tool off your list", example="tico tool mine remove duckdb"),
)
