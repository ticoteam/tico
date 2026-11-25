# pyright: strict
"""The Tools registry (docs/connect-tools.md, "Tools registry"): every tool the company's agents may reach for, with
words on how to use it, and each agent's own list.

The registry is the shipped guides (templates/tool-guides/<slug>.md) with the company's rows over them (`tool_guides`),
and every vendor in the Tools index the reader may see (api_tools.index_rows): a vendor with no words reads "No guide
yet". A vendor tool's slug is its vendor id (tool_services.vendor_of), so a guide and the vendor's tools meet on it; a
built tool is a script or program the company made, with its repository in `source` and the tools it needs in `needs`.
A company row's field, when set, wins over the shipped one; `guidance` is the company's alone. Archiving hides a guide
(a shipped one too); the vendor's Tools rows still list it, with no guide.

An agent's list (`bot_tool_guides`) is what it says it uses, each with a note; its page shows that list with what it is
granted in Tools and what it was seen using in the last 30 days (proxy calls and key reads).

Anyone signed in writes a guide (every write is in the audit log with who made it); archiving is the guide's owner's,
the Tools Owner's, the Tools page owner's or a credential admin's. A bot's list is kept by whoever keeps its tool notes
(tool_access NOTE): the bot itself, anyone who may manage it, Agent Ops, and the Assistant for them.

Never a credential value: access is a level, how a run reaches the tool and its variables' names.
"""

import json
import re
from functools import lru_cache
from typing import Any, cast

import yaml
from fastapi import Query, Request

from . import config as _config
from . import tool_access as _TA
from . import tool_services as _TS
from .api_answers.tool_guides import (
    BotToolGuideList,
    BotToolGuideListed,
    ToolGuide,
    ToolGuideArchived,
    ToolGuideDetail,
    ToolGuideList,
)
from .api_contract import api_v2
from .models import BotToolGuideNote, Empty, ToolGuideWrite
from .store import H as _H
from .store import Problem as _Problem
from .store import encode as _encode

# The store and the neighbours used below are not strictly typed: they are used through Any here (as
# backend/access_requests.py does).
config: Any = _config
TA: Any = _TA
TS: Any = _TS
H: Any = _H
Problem: Any = _Problem
encode: Any = _encode
Row = dict[str, Any]
Entry = tuple[Row, list[Row], Row | None]


def _mod(name: str) -> Any:
    """A neighbour module, imported when used and typed as Any at the door. Every name is a literal import (see
    backend/access_requests.py _mod)."""
    if name == "api_tools":
        from . import api_tools as found
    elif name == "bot_tools":
        from . import bot_tools as found
    elif name == "integrations":
        from . import integrations as found
    elif name == "names":
        from . import names as found
    elif name == "page_owners":
        from . import page_owners as found
    elif name == "secret_drop":
        from . import secret_drop as found
    elif name == "tool_proxy":
        from . import tool_proxy as found
    else:
        raise ValueError(name)
    return found


SHIPPED_DIR = config.ROOT / "templates" / "tool-guides"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
USED_DAYS = 30
LIST_MAX = 500
LIST_BYTES_MAX = 192 * 1024
LEVELS = {"read": "read", "read_write": "write"}
TEXT_FIELDS = ("name", "category", "summary", "instructions", "guidance", "source")


@lru_cache(maxsize=1)
def shipped() -> dict[str, Row]:
    """{slug: guide} from templates/tool-guides/: the frontmatter's name, category, summary and commands (`origin`
    vendor unless it says), and the body as the instructions."""
    out: dict[str, Row] = {}
    for path in sorted(SHIPPED_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, head, body = text.split("---", 2) if text.startswith("---") else ("", "", text)
        meta = yaml.safe_load(head) or {}
        out[path.stem] = {
            "name": str(meta.get("name") or path.stem),
            "origin": "built" if meta.get("origin") == "built" else "vendor",
            "category": str(meta.get("category") or ""),
            "summary": str(meta.get("summary") or ""),
            "instructions": body.strip(),
            "commands": [str(c) for c in meta.get("commands") or []],
        }
    return out


def _json_list(value: Any) -> list[Any]:
    try:
        found = json.loads(value or "[]")
    except ValueError:
        return []
    return list(cast(list[Any], found)) if isinstance(found, list) else []


def _guide(slug: str, row: Any, ship: Any, vendor_name: str = "") -> Row:
    """One guide as the reader sees it: the company's row over the shipped guide."""
    row, ship = row or {}, ship or {}

    def pick(field: str) -> Any:
        return row.get(field) or ship.get(field) or ""

    instructions = pick("instructions")
    return {
        "slug": slug,
        "name": pick("name") or vendor_name or slug,
        "origin": row.get("origin") if row.get("origin") == "built" else ship.get("origin") or "vendor",
        "category": pick("category"),
        "summary": pick("summary"),
        "instructions": instructions,
        "guidance": row.get("guidance") or "",
        "commands": _json_list(row.get("commands")) or list(ship.get("commands") or []),
        "source": row.get("source") or "",
        "needs": _json_list(row.get("needs")),
        "owner": row.get("owner"),
        "has_guide": bool(instructions),
        "shipped": bool(ship),
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


def _tool_id(index_row: Any) -> str | None:
    """The tools row id of an API or website Tools row (`api:<id>`, `website:<id>`), else None."""
    kind, _, tid = str(index_row["id"]).partition(":")
    return tid if kind in ("api", "website") and tid else None


def registry(c: Any, who: Any, auth: Any, settings: Any, admins: Any) -> dict[str, Entry]:
    """{slug: (guide, the vendor's Tools rows the reader may see, the company's row or None)}, by slug."""
    api_tools = _mod("api_tools")
    vendors: dict[str, list[Row]] = {}
    for row in api_tools.index_rows(c, who, auth, settings, admins):
        if row.get("vendor") and not row.get("requestable"):
            vendors.setdefault(row["vendor"], []).append(row)
    rows = {r["slug"]: dict(r) for r in c.execute("SELECT * FROM tool_guides")}
    ship = shipped()
    out: dict[str, Entry] = {}
    for slug in sorted(set(ship) | set(rows) | set(vendors)):
        row = rows.get(slug)
        archived = bool(row and row["archived_at"])
        if archived and slug not in vendors:
            continue
        vrows = vendors.get(slug, [])
        name = vrows[0].get("vendor_name", "") if vrows else ""
        guide = _guide(slug, None, None, name) if archived else _guide(slug, row, ship.get(slug), name)
        out[slug] = (guide, vrows, row)
    return out


def resolve(reg: dict[str, Entry], word: Any) -> str | None:
    """The registry slug a word names: the slug itself, a vendor alias (gh, postgresql), or a guide's command
    (psql, sqlite3). None when nothing does."""
    word = str(word or "").strip().lower()
    if word in reg:
        return word
    for found in (TS.VENDOR_ALIASES.get(word), TS.vendor_of(word)):
        if found and found in reg:
            return found
    return next((slug for slug, (g, _, _) in reg.items() if word in [x.lower() for x in g["commands"]]), None)


def _agent(bot: Any, actor: Any) -> str | None:
    """The agent a use counts for: the run's bot, else a bot acting as itself; a person's own use counts for none."""
    if bot:
        return str(bot)
    actor = str(actor or "")
    return actor[len("bot:") :] if actor.startswith("bot:") else None


def usage(c: Any, tool_ids: Any) -> dict[str, dict[str, list[Any]]]:
    """{tool id: {agent: [uses, last used]}} over the last USED_DAYS: calls through Tico's proxy, and one for each
    agent's latest key read (credential_uses keeps one row a caller). Read by tool id, which both tables index."""
    tool_ids = sorted(set(tool_ids))
    if not tool_ids:
        return {}
    since = H.shift(H.now(), days=-USED_DAYS)
    marks = ",".join("?" * len(tool_ids))
    out: dict[str, dict[str, list[Any]]] = {}

    def add(tid: str, agent: str | None, uses: int, at: Any) -> None:
        if agent:
            seen = out.setdefault(tid, {}).setdefault(agent, [0, None])
            seen[0] += uses
            seen[1] = max(seen[1] or "", at or "") or None

    for tid, bot, actor, uses, at in c.execute(
        f"SELECT tool_id, bot, actor, count(*), max(created_at) FROM tool_calls WHERE tool_id IN ({marks}) "
        "AND created_at>=? GROUP BY tool_id, bot, actor",
        [*tool_ids, since],
    ):
        add(tid, _agent(bot, actor), uses, at)
    for tid, bot, actor, at in c.execute(
        "SELECT k.tool_id, u.bot, u.actor, u.used_at FROM credential_uses u "
        f"JOIN credentials k ON k.id=u.credential_id WHERE k.tool_id IN ({marks}) AND u.used_at>=?",
        [*tool_ids, since],
    ):
        add(tid, _agent(bot, actor), 1, at)
    return out


def _listings(c: Any) -> dict[str, dict[str, str]]:
    """{slug: {bot: note}} of every agent's list."""
    out: dict[str, dict[str, str]] = {}
    for slug, bot, note in c.execute(
        "SELECT g.slug, b.bot, b.note FROM bot_tool_guides b JOIN tool_guides g ON g.id=b.tool_guide_id"
    ):
        out.setdefault(slug, {})[bot] = note
    return out


def _users(reg_entry: Entry, listed: dict[str, str], used: dict[str, dict[str, list[Any]]]) -> dict[str, list[Any]]:
    """{agent: [listed, note, uses, last used]} for one registry entry."""
    _, vrows, _ = reg_entry
    out: dict[str, list[Any]] = {bot: [True, note, 0, None] for bot, note in listed.items()}
    for tid in filter(None, map(_tool_id, vrows)):
        for bot, (uses, at) in used.get(tid, {}).items():
            item = out.setdefault(bot, [False, "", 0, None])
            item[2] += uses
            item[3] = max(item[3] or "", at or "") or None
    return out


def _page(rows: list[Row], limit: int, cursor: Any) -> tuple[list[Row], str | None]:
    try:
        start = int(cursor or 0)
    except ValueError:
        start = -1
    if start < 0:
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422)
    shown: list[Row] = []
    used, end = 0, start
    for item in rows[start : start + max(1, min(limit, LIST_MAX))]:
        size = len(encode(item).encode())
        if shown and used + size > LIST_BYTES_MAX:
            break
        shown.append(item)
        used += size
        end += 1
    return shown, str(end) if end < len(rows) else None


def _matches(guide: Row, words: list[str]) -> bool:
    text = " ".join([guide["slug"], guide["name"], guide["category"], guide["summary"], *guide["commands"]]).lower()
    return all(w in text for w in words)


def list_page(
    c: Any,
    who: Any,
    auth: Any,
    settings: Any,
    admins: Any,
    q: str = "",
    category: str = "",
    origin: str = "",
    bot: str = "",
    limit: int = LIST_MAX,
    cursor: str = "",
) -> Row:
    """`GET /api/v2/tool-guides`: the registry, most used first. `bot` keeps the tools on that agent's page."""
    reg = registry(c, who, auth, settings, admins)
    keep: set[str] | None = None
    if bot:
        keep = {r["slug"] for r in bot_rows(c, who, auth, admins, _bot_id(c, auth, who, bot), reg)}
    used = usage(c, [tid for _, vrows, _ in reg.values() for tid in map(_tool_id, vrows) if tid])
    listings = _listings(c)
    words = str(q or "").lower().split()
    rows: list[Row] = []
    for slug, entry in reg.items():
        guide, vrows, _ = entry
        if keep is not None and slug not in keep:
            continue
        if category and guide["category"].lower() != category.strip().lower():
            continue
        if origin and guide["origin"] != origin or words and not _matches(guide, words):
            continue
        users = _users(entry, listings.get(slug, {}), used)
        last = max((u[3] for u in users.values() if u[3]), default=None)
        rows.append(
            {
                "slug": slug,
                "name": guide["name"],
                "origin": guide["origin"],
                "category": guide["category"],
                "summary": guide["summary"],
                "connections": len(vrows),
                "used_by": len(users),
                "last_used_at": last,
                "has_guide": guide["has_guide"],
            }
        )
    rows.sort(key=lambda r: (-r["used_by"], not r["has_guide"], r["name"].lower()))
    shown, after = _page(rows, limit, cursor)
    return {"tool_guides": shown, "next": after}


def _access(c: Any, who: Any, vrows: list[Row]) -> list[Row]:
    """The caller's way into each of the vendor's Tools rows: its level, how a run reaches it and the variables' names,
    and for an agent with none the command that asks for Read. Never a value."""
    api_tools = _mod("api_tools")
    url_env = _mod("tool_proxy").url_env
    agent = who.role == "bot"
    out: list[Row] = []
    for row in vrows:
        tid = _tool_id(row)
        item: Row = {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "level": None,
            "arrives": None,
            "env": [],
            "url_env": None,
            "request": None,
        }
        if row["kind"] == "api" and tid:
            item["level"] = LEVELS.get(api_tools.subject_level(c, tid, who.actor) or "", "none")
            item["env"] = sorted({k["env"] for k in api_tools._keys(c, tid) if k["env"]})
            proxied = row.get("delivery") == "proxy"
            item["arrives"] = "proxy" if proxied else "env"
            item["url_env"] = url_env(tid) if proxied else None
            if agent and item["level"] == "none":
                item["request"] = f"tico access request tool:{tid} --level read"
        elif row["kind"] == "website" and tid:
            item["level"] = "write" if api_tools._can_use(c, tid, who.actor) else "none"
            item["arrives"] = "login"
        out.append(item)
    return out


def _requestable(c: Any, who: Any, slug: str, shown: set[str]) -> list[Row]:
    """The vendor's API tools for Everyone the caller holds nothing on, by name and id alone (as the Tools list shows
    them to a person), so an agent learns the tool is there and how to ask for it. Nothing of how they are set up."""
    out: list[Row] = []
    for tool in c.execute("SELECT * FROM tools WHERE kind='api' AND audience IS NULL ORDER BY lower(name), id"):
        if "api:" + tool["id"] in shown:
            continue
        if TS.vendor_of(tool["service"], tool["name"], (), tool["base_url"] or tool["mcp_url"] or "") != slug:
            continue
        out.append(
            {
                "id": "api:" + tool["id"],
                "name": tool["name"],
                "kind": "api",
                "level": "none",
                "arrives": None,
                "env": [],
                "url_env": None,
                "request": f"tico access request tool:{tool['id']} --level read" if who.role == "bot" else None,
            }
        )
    return out


def _seen_bots(c: Any, who: Any, auth: Any, admins: Any, bots: set[str]) -> set[str]:
    """The agents among `bots` the reader may see (the rule every list of bots follows)."""
    if not bots or who.role == "owner" or TA.admin(c, who, admins):
        return set(bots)
    levels = auth.bot_accesses(c, who, sorted(bots))
    return {b for b in bots if (levels.get(b) or {}).get("see")}


def detail(c: Any, who: Any, auth: Any, settings: Any, admins: Any, slug: str) -> Row:
    """`GET /api/v2/tool-guides/{slug}`: one guide, the caller's way into each of its Tools rows, the agents that list
    or use it, and the vendor's learnings."""
    integrations, names = _mod("integrations"), _mod("names")
    reg = registry(c, who, auth, settings, admins)
    found = resolve(reg, slug)
    if found is None:
        raise Problem("not_found", "No such tool in the registry", 404)
    entry = reg[found]
    guide, vrows, _ = entry
    used = usage(c, [tid for tid in map(_tool_id, vrows) if tid])
    users = _users(entry, _listings(c).get(found, {}), used)
    seen = _seen_bots(c, who, auth, admins, set(users))
    labels = names.lookup(c, {"bot:" + b for b in seen})
    used_by = [
        {
            "id": bot,
            "name": labels.get("bot:" + bot) or bot,
            "listed": listed,
            "note": note,
            "uses": uses,
            "last_used_at": at,
        }
        for bot, (listed, note, uses, at) in users.items()
        if bot in seen
    ]
    used_by.sort(key=lambda u: (not u["listed"], -u["uses"], u["name"].lower()))
    learnings = [
        {k: r[k] for k in ("id", "actor", "text", "created_at")} for r in integrations.learnings(c, found)[:50]
    ]
    access = _access(c, who, vrows) + _requestable(c, who, found, {r["id"] for r in vrows})
    return {**guide, "access": access, "used_by": used_by, "learnings": learnings}


def _signed_in(who: Any) -> None:
    if getattr(who, "support", None) or who.role not in ("owner", "human", "bot"):
        raise Problem("forbidden", "Sign in as a person or an agent of this company to change the Tools registry", 403)


def _no_key(fields: dict[str, Any]) -> None:
    """Refuses words that hold a key: a guide names a variable, never its value."""
    scan = _mod("secret_drop").scan
    for name, value in fields.items():
        if isinstance(value, str) and any(i["tier"] == "certain" for i in scan(value)):
            raise Problem(
                "looks_like_key",
                f"{name} looks like it holds a key: name its variable ($STRIPE_API_KEY) and store the key in Tools",
                422,
            )


def write(c: Any, who: Any, auth: Any, settings: Any, admins: Any, slug: str, body: ToolGuideWrite) -> Row:
    """`PUT /api/v2/tool-guides/{slug}`: create or change a guide, only the fields sent. A known vendor, a shipped
    guide or an alias (gh) is that guide; any other new slug is a tool built here, which needs a summary and
    instructions. Writing an archived guide brings it back."""
    _signed_in(who)
    word = str(slug or "").strip().lower()
    reg = registry(c, who, auth, settings, admins)
    slug = resolve(reg, word) or word
    if not SLUG.match(slug):
        raise Problem("invalid_request", "A tool's slug is lowercase letters, digits and dashes", 422)
    sent = body.model_dump(exclude_unset=True)
    _no_key(sent)
    if "commands" in sent:
        sent["commands"] = json.dumps(sent["commands"] or [])
    if "needs" in sent:
        sent["needs"] = json.dumps(sent["needs"] or [])
    for field in TEXT_FIELDS:
        if field in sent and sent[field] is None:
            sent[field] = ""
    if "category" in sent:
        sent["category"] = sent["category"].strip().lower()
    if sent.get("origin") is None:
        sent.pop("origin", None)
    row = c.execute("SELECT * FROM tool_guides WHERE slug=?", (slug,)).fetchone()
    row = dict(row) if row else None
    # A vendor Tools knows (linear) is a vendor's guide even before its tool is added; any other new slug is built here.
    known = slug in reg or slug in TS._known()
    origin = sent.get("origin") or (row["origin"] if row else "vendor" if known else "built")
    after = _guide(slug, {**(row or {}), **sent, "origin": origin, "archived_at": None}, shipped().get(slug))
    if origin == "built" and not (after["summary"] and after["instructions"]):
        raise Problem("invalid_request", "A built tool needs a summary and instructions", 422)
    now = H.now()
    sent["origin"] = origin
    if row is None:
        fields = {"origin": origin, **sent}
        fields.setdefault("owner", who.actor if origin == "built" else None)
        cols = ["id", "slug", *fields, "created_by", "updated_by", "created_at", "updated_at"]
        values = [H.new_id(), slug, *fields.values(), who.actor, who.actor, now, now]
        c.execute(
            f"INSERT INTO tool_guides({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
            values,
        )
        H.event(c, who.actor, "tool_guide.created", slug, {"origin": origin, "fields": sorted(sent)})
    else:
        sets = ", ".join(f"{k}=?" for k in sent)
        c.execute(
            f"UPDATE tool_guides SET {sets}, updated_by=?, updated_at=?, archived_at=NULL WHERE id=?",
            [*sent.values(), who.actor, now, row["id"]],
        )
        H.event(c, who.actor, "tool_guide.updated", slug, {"fields": sorted(sent)})
    saved = dict(c.execute("SELECT * FROM tool_guides WHERE slug=?", (slug,)).fetchone())
    vrows = reg[slug][1] if slug in reg else []
    return _guide(slug, saved, shipped().get(slug), vrows[0].get("vendor_name", "") if vrows else "")


def may_archive(c: Any, who: Any, admins: Any, row: Row | None) -> bool:
    """The guide's owner, the Tools Owner and the Tools page's owner (as themselves), a credential admin, the company's
    owner."""
    page_owners = _mod("page_owners")
    if who.role == "owner" or TA.admin(c, who, admins):
        return True
    if row is not None and row.get("owner") and row["owner"] == who.actor:
        return True
    own = who.role == "bot" and not getattr(who, "via", None)
    keepers = {"bot:" + H.TOOLS_OWNER, page_owners.owner(c, "tools", fallback=False)}
    return own and who.actor in keepers


def archive(c: Any, who: Any, auth: Any, settings: Any, admins: Any, slug: str) -> Row:
    """`DELETE /api/v2/tool-guides/{slug}`: hide a guide, the shipped one too; the vendor's Tools rows stay."""
    _signed_in(who)
    reg = registry(c, who, auth, settings, admins)
    found = resolve(reg, slug)
    row = c.execute("SELECT * FROM tool_guides WHERE slug=?", (found or "",)).fetchone()
    row = dict(row) if row else None
    if found is None or not (row or found in shipped()):
        raise Problem("not_found", "No such guide in the registry", 404)
    if not may_archive(c, who, admins, row):
        raise Problem(
            "forbidden",
            "Only the tool's owner, the Tools Owner or a credential admin archives a guide: ask the Tools Owner",
            403,
        )
    now = H.now()
    if row is None:
        c.execute(
            "INSERT INTO tool_guides(id,slug,created_by,updated_by,created_at,updated_at,archived_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (H.new_id(), found, who.actor, who.actor, now, now, now),
        )
    elif not row["archived_at"]:
        c.execute(
            "UPDATE tool_guides SET archived_at=?, updated_by=?, updated_at=? WHERE id=?",
            (now, who.actor, now, row["id"]),
        )
    H.event(c, who.actor, "tool_guide.archived", found, {})
    return {"slug": found, "archived": True}


def _bot_id(c: Any, auth: Any, who: Any, bot: str) -> str:
    found = (H.resolve_bot(c, bot) or {}).get("id", bot)
    auth.require_read(c, who, found)  # a 404 for someone who cannot see the bot
    if not H.bot(c, found):
        raise Problem("not_found", "Bot not found", 404)
    return found


def bot_rows(c: Any, who: Any, auth: Any, admins: Any, bot: str, reg: dict[str, Entry]) -> list[Row]:
    """One agent's tools: the ones it lists, the ones it holds in Tools (as its page shows them to this reader) and the
    ones it used in the last USED_DAYS, by slug."""
    bot_tools = _mod("bot_tools")
    by_tool = {tid: slug for slug, (_, vrows, _) in reg.items() for tid in map(_tool_id, vrows) if tid}
    found: dict[str, Row] = {}

    def item(slug: str) -> Row:
        return found.setdefault(slug, {"note": "", "listed": False, "access": "none", "uses": 0, "last_used_at": None})

    for slug, note in c.execute(
        "SELECT g.slug, b.note FROM bot_tool_guides b JOIN tool_guides g ON g.id=b.tool_guide_id WHERE b.bot=?", (bot,)
    ):
        if slug in reg:
            item(slug).update(note=note, listed=True)
    near = bot_tools.close(c, who, bot, admins, auth)
    for grant in bot_tools.shown(c, who, bot, near, admins, auth):
        tid = grant["granted"].get("tool_id")
        envs = [e.strip() for e in str(grant.get("env") or "").split(",") if e.strip()]
        slug = by_tool.get(tid) if tid else None
        slug = slug or TS.vendor_of(grant.get("service") or "", grant.get("name") or "", envs)
        if not slug:
            continue
        delivery = c.execute("SELECT delivery FROM tools WHERE id=?", (tid,)).fetchone() if tid else None
        row = item(slug)
        row["access"] = "proxy" if row["access"] == "proxy" or (delivery and delivery[0] == "proxy") else "key"
    for tid, agents in usage(c, by_tool).items():
        if bot in agents:
            row = item(by_tool[tid])
            row["uses"] += agents[bot][0]
            row["last_used_at"] = max(row["last_used_at"] or "", agents[bot][1] or "") or None
    out: list[Row] = []
    for slug, row in found.items():
        guide = reg[slug][0] if slug in reg else _guide(slug, None, None, TS.vendor_name(slug))
        company = reg[slug][2] if slug in reg else None
        built = guide["origin"] == "built" and bool(company) and company["created_by"] == "bot:" + bot
        out.append(
            {
                "slug": slug,
                "name": guide["name"],
                "origin": guide["origin"],
                "category": guide["category"],
                **row,
                "built": built,
            }
        )
    out.sort(key=lambda r: (not r["listed"], -r["uses"], r["name"].lower()))
    return out


def list_for_bot(
    c: Any, who: Any, auth: Any, admins: Any, settings: Any, bot: str, limit: int = LIST_MAX, cursor: str = ""
) -> Row:
    """`GET /api/v2/bots/{bot}/tool-guides`."""
    bot = _bot_id(c, auth, who, bot)
    rows = bot_rows(c, who, auth, admins, bot, registry(c, who, auth, settings, admins))
    shown, after = _page(rows, limit, cursor)
    return {"bot": bot, "tool_guides": shown, "next": after}


def _may_keep_list(c: Any, who: Any, auth: Any, admins: Any, bot: str) -> str:
    found = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if not H.bot(c, found):
        raise Problem("not_found", "Bot not found", 404)
    TA.require(c, who, TA.bot(found), action=TA.NOTE, admins=admins, auth=auth)
    return found


def list_tool(
    c: Any, who: Any, auth: Any, settings: Any, admins: Any, bot: str, slug: str, body: BotToolGuideNote
) -> Row:
    """`PUT /api/v2/bots/{bot}/tool-guides/{slug}`: the agent lists a tool it uses, with what for."""
    auth.domain(who)
    bot = _may_keep_list(c, who, auth, admins, bot)
    reg = registry(c, who, auth, settings, admins)
    found = resolve(reg, slug)
    if found is None:
        raise Problem("not_found", "No such tool in the registry: write its guide first (tico tool guide-write)", 404)
    _no_key({"note": body.note})
    now = H.now()
    row = c.execute("SELECT id, archived_at FROM tool_guides WHERE slug=?", (found,)).fetchone()
    if row is None:
        gid = H.new_id()
        c.execute(
            "INSERT INTO tool_guides(id,slug,origin,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (gid, found, reg[found][0]["origin"], who.actor, who.actor, now, now),
        )
    else:
        gid = row["id"]
    c.execute(
        "INSERT INTO bot_tool_guides(id,bot,tool_guide_id,note,added_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(bot, tool_guide_id) DO UPDATE SET note=excluded.note, updated_at=excluded.updated_at",
        (H.new_id(), bot, gid, body.note, who.actor, now, now),
    )
    H.event(c, who.actor, "bot.tool_listed", bot, {"tool": found})
    return {"bot": bot, "slug": found, "note": body.note, "listed": True}


def unlist_tool(c: Any, who: Any, auth: Any, settings: Any, admins: Any, bot: str, slug: str) -> Row:
    """`DELETE /api/v2/bots/{bot}/tool-guides/{slug}`: off the agent's list. Its grants in Tools are not touched."""
    auth.domain(who)
    bot = _may_keep_list(c, who, auth, admins, bot)
    listed = {
        r["slug"]: (r["id"], _guide(r["slug"], dict(r), shipped().get(r["slug"])))
        for r in c.execute(
            "SELECT g.* FROM bot_tool_guides b JOIN tool_guides g ON g.id=b.tool_guide_id WHERE b.bot=?", (bot,)
        )
    }
    word = resolve({k: (g, [], None) for k, (_, g) in listed.items()}, slug) or str(slug or "").strip().lower()
    gone = 0
    if word in listed:
        gone = c.execute("DELETE FROM bot_tool_guides WHERE bot=? AND tool_guide_id=?", (bot, listed[word][0])).rowcount
    if gone:
        H.event(c, who.actor, "bot.tool_unlisted", bot, {"tool": word})
    return {"bot": bot, "slug": word, "note": "", "listed": False}


def install(app: Any, store: Any, auth: Any, mutate: Any, requester: Any = None) -> None:
    """The registry's routes. `requester` maps Agent Ops with no on-behalf-of header to the person it works for (as
    backend/bot_tools.py does)."""

    def acting(c: Any, who: Any) -> Any:
        return requester(c, who) if requester else who

    def admins() -> Any:
        vault = getattr(app.state, "vault", None)
        return vault.admins if vault is not None else ()

    @app.get(
        "/api/v2/tool-guides",
        **api_v2(
            "Tools",
            "listToolGuides",
            "The Tools registry: shipped guides, the company's guides and built tools, and every vendor in Tools you "
            "may see, most used first; filter by words, category, origin or an agent's tools",
            ToolGuideList,
        ),
    )
    def tool_guides(
        request: Request,
        q: str = Query("", max_length=200),
        category: str = Query("", max_length=40),
        origin: str = Query("", pattern=r"^(vendor|built)?$"),
        bot: str = Query("", max_length=80),
        limit: int = Query(LIST_MAX, ge=1, le=LIST_MAX),
        cursor: str = Query("", max_length=20),
    ) -> Any:
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return list_page(c, who, auth, store.settings, admins(), q, category, origin, bot, limit, cursor)

    @app.get(
        "/api/v2/tool-guides/{slug}",
        **api_v2(
            "Tools",
            "getToolGuide",
            "One tool's guide, how you reach each of its Tools rows (a level, the variables' names, never a value, and "
            "the command that asks for access when you have none), the agents that list or use it and its learnings",
            ToolGuideDetail,
        ),
    )
    def tool_guide(request: Request, slug: str) -> Any:
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return detail(c, who, auth, store.settings, admins(), slug)

    @app.put(
        "/api/v2/tool-guides/{slug}",
        **api_v2(
            "Tools",
            "writeToolGuide",
            "Write or change a tool's guide, only the fields sent (anyone signed in; audited). A new slug that is no "
            "vendor is a tool built here, which needs a summary and instructions. Never a key: name its variable",
            ToolGuide,
        ),
    )
    def write_tool_guide(request: Request, slug: str, body: ToolGuideWrite) -> Any:
        who = request.state.identity

        def work(c: Any) -> Any:
            return write(c, acting(c, who), auth, store.settings, admins(), slug, body)

        return mutate(request, body, work)

    @app.delete(
        "/api/v2/tool-guides/{slug}",
        **api_v2(
            "Tools",
            "archiveToolGuide",
            "Archive a guide (its owner, the Tools Owner, the Tools page's owner or a credential admin); the vendor's "
            "Tools rows stay, with no guide",
            ToolGuideArchived,
        ),
    )
    def archive_tool_guide(request: Request, slug: str) -> Any:
        who = request.state.identity

        def work(c: Any) -> Any:
            return archive(c, acting(c, who), auth, store.settings, admins(), slug)

        return mutate(request, Empty(), work)

    @app.get(
        "/api/v2/bots/{bot}/tool-guides",
        **api_v2(
            "Bots",
            "listBotToolGuides",
            "The tools an agent lists with its notes, the ones it holds in Tools and the ones it used in the last 30 "
            "days, each with how it reaches it and how often",
            BotToolGuideList,
        ),
    )
    def bot_tool_guides(
        request: Request,
        bot: str,
        limit: int = Query(LIST_MAX, ge=1, le=LIST_MAX),
        cursor: str = Query("", max_length=20),
    ) -> Any:
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return list_for_bot(c, who, auth, admins(), store.settings, bot, limit, cursor)

    @app.put(
        "/api/v2/bots/{bot}/tool-guides/{slug}",
        **api_v2(
            "Bots",
            "listBotToolGuide",
            "Put a tool on an agent's list with what it uses it for (the agent, anyone who manages it, Agent Ops, the "
            "Assistant for them)",
            BotToolGuideListed,
        ),
    )
    def list_bot_tool(request: Request, bot: str, slug: str, body: BotToolGuideNote) -> Any:
        who = request.state.identity

        def work(c: Any) -> Any:
            return list_tool(c, acting(c, who), auth, store.settings, admins(), bot, slug, body)

        return mutate(request, body, work)

    @app.delete(
        "/api/v2/bots/{bot}/tool-guides/{slug}",
        **api_v2(
            "Bots",
            "unlistBotToolGuide",
            "Take a tool off an agent's list; what it holds in Tools is not touched",
            BotToolGuideListed,
        ),
    )
    def unlist_bot_tool(request: Request, bot: str, slug: str) -> Any:
        who = request.state.identity

        def work(c: Any) -> Any:
            return unlist_tool(c, acting(c, who), auth, store.settings, admins(), bot, slug)

        return mutate(request, Empty(), work)
