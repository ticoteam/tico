"""Org rules (docs/rules-plan.md §1, §5, §6): one sentence each, internal (between teammates) or external (what someone outside
requires of us). A rule says who follows it and, for an internal rule, with whom. Due dates live in backend/rule_due.py.

- Records: `rule_sources` (who an external rule is from), `org_rules`, and `org_rule_holds` (the checks Decide runs on
  outputs fill it; nothing reads it yet).
- Matching: `matching(c, author, recipients)` is the active rules that apply to one write. Groups are read through
  `people.group_chain`, so a nested group counts; `all_bots` covers any bot, including one made later. With is empty
  (any output) or names a recipient.
- Permissions: a bot suggests and never accepts, edits or retires. The owner and admins manage every rule. A lead of a
  group in Who, and whoever manages a bot in Who (its operator, its owners, or a person above it on the chart, as
  `Auth.bot_manager` says), manage the rules that name them. Everyone rules are the owner's and admins' alone. A party
  named in With reads a rule and cannot edit it.
- Visibility: every teammate reads every sentence and From. A quote shows only where its file is readable
  (backend/files.py `file_visible`), checked on every response, a replayed one included; otherwise the cite is
  {hidden: true}. A write's stored response carries no quote; the route reads the rule again after the write.
- Every write: one permission check, an audit_log row (`org_rule:<id>`, actor, via) and an event.
- A bot's settings (write audience, mail, spend, turns, task caps, the fixed limits) are read-only sentences on its
  Rules tab and in `--mine`. They are read from the modules that hold them and never stored as rules.

Unversioned and idempotent (`ensure`), like backend/strategy.py.
"""
import json
from datetime import timedelta
from typing import Literal

from fastapi import Query, Request
from pydantic import Field

from . import audit_log
from . import bot_access as A
from . import groups as G
from . import hubdb as H
from . import people as P
from . import rule_due as D
from . import task_caps
from .models import Contract
from .store import Problem

KINDS = ("internal", "external")
SOURCE_KINDS = ("vendor", "customer", "government", "other")
IF_BROKEN = ("block", "escalate", "warn")
STATUSES = ("suggested", "active", "retired")
TEXT_MAX = 1000
DESCRIPTION_MAX = 2000
NAME_MAX = 200
LIST_DEFAULT = 50
LIST_MAX = 100
DUE_LIST_MAX = 200
BOT_LIST_MAX = 50           # the rules a bot's tab shows per kind, per page (a page also stops at LIST_BYTES)
LIST_BYTES = 200_000        # the serialized size one page of rules may reach; a page always holds at least one row
SCOPE_MAX = 100             # the names one Who or With may hold, in all
DUE_PER_RULE = 20           # the due dates a rule's view names; the rest are counted (`due_more`)
SETTINGS_TASK_CAPS = 20     # the task limits a bot's settings name; the rest are counted
SCOPE_KEYS = ("everyone", "all_bots", "groups", "bots", "people", "names")

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS rule_sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('vendor','customer','government','other')), created_by TEXT NOT NULL, "
    "created_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS rule_sources_name ON rule_sources(lower(name))",
    "CREATE TABLE IF NOT EXISTS org_rules(id TEXT PRIMARY KEY, "
    "kind TEXT NOT NULL CHECK (kind IN ('internal','external')), text TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
    "source_id TEXT, cite TEXT, who TEXT NOT NULL, with_scope TEXT NOT NULL DEFAULT '{}', "
    "if_broken TEXT NOT NULL DEFAULT 'block' CHECK (if_broken IN ('block','escalate','warn')), reviewer TEXT, "
    "status TEXT NOT NULL CHECK (status IN ('suggested','active','retired')), suggested_by TEXT NOT NULL, "
    "decided_by TEXT, decided_at TEXT, via TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS org_rules_status ON org_rules(status, kind, created_at)",
    "CREATE INDEX IF NOT EXISTS org_rules_source ON org_rules(source_id) WHERE source_id IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS org_rule_holds(id TEXT PRIMARY KEY, operation TEXT NOT NULL, payload TEXT NOT NULL, "
    "author TEXT NOT NULL, destinations TEXT NOT NULL DEFAULT '[]', rule_ids TEXT NOT NULL DEFAULT '[]', "
    "verdict TEXT NOT NULL DEFAULT '{}', why TEXT NOT NULL DEFAULT '', nonce TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK (state IN ('held','sent','kept')), reviewer TEXT, task_id TEXT, created_at TEXT NOT NULL, "
    "decided_at TEXT, decided_by TEXT)",
    "CREATE INDEX IF NOT EXISTS org_rule_holds_author ON org_rule_holds(author, state)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    D.ensure(c)


# ----------------------------------------------------------------------------- small helpers
def _text(value, limit, what):
    text = " ".join(str(value or "").split())
    if not text:
        raise Problem("validation", f"Write the {what} in one sentence", 422)
    if len(text) > limit:
        raise Problem("validation", f"Keep the {what} under {limit} characters", 422)
    return text


def _long(value, limit, what):
    text = str(value or "").strip()
    if len(text) > limit:
        raise Problem("validation", f"Keep the {what} under {limit} characters", 422)
    return text


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _has(scope):
    return bool(scope) and bool(scope.get("everyone") or scope.get("all_bots") or scope.get("groups")
                                or scope.get("bots") or scope.get("people"))


def _row(r):
    d = dict(r)
    d["who"] = json.loads(d.pop("who") or "{}")
    d["with"] = json.loads(d.pop("with_scope") or "{}")
    d["cite"] = json.loads(d.pop("cite") or "null")
    return d


def _get(c, rule_id):
    r = c.execute("SELECT * FROM org_rules WHERE id=?", (rule_id,)).fetchone()
    if not r:
        raise Problem("not_found", f"There is no rule {rule_id}", 404)
    return _row(r)


def _core(row):
    """The stored fields an audit row keeps (the derived views are not stored)."""
    keep = ("id", "kind", "text", "description", "source_id", "cite", "who", "with", "if_broken", "reviewer", "status",
            "version")
    return {k: row.get(k) for k in keep}


def _via(who):
    return who.via or H.VIA.get() or "api"


def redact_audit(c, auth, who, found):
    """The audit rows of org rules (backend/audit_log.py `rows`), with each cite's quote shown only where the reader may
    read its file now, as `view` shows it. Changed in place: `before` and `after` of each row."""
    memo = {}
    for item in found:
        if not str(item.get("object") or "").startswith("org_rule:"):
            continue
        for key in ("before", "after"):
            snap = item.get(key)
            cite = snap.get("cite") if isinstance(snap, dict) else None
            if not cite:
                continue
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (cite.get("file_id"),)).fetchone()
            if not _cite_visible(c, auth, who, row, memo):
                snap["cite"] = {"hidden": True}


def _audit(c, actor, rule_id, before, after, who):
    audit_log.write(c, actor, "org_rule:" + rule_id, before, after, _via(who))


def _event(c, actor, action, rule_id, detail=None):
    H.event(c, actor, "org_rule." + action, "org_rule:" + rule_id, detail or {})


def _save(c, rule_id, status=None, extra=None):
    """One UPDATE that bumps the version: the fields `extra` names, and the status when given."""
    sets, values = ["version=version+1", "updated_at=?"], [H.now()]
    if status:
        sets.append("status=?")
        values.append(status)
    for key, value in (extra or {}).items():
        sets.append(f"{key}=?")
        values.append(value)
    c.execute(f"UPDATE org_rules SET {', '.join(sets)} WHERE id=?", (*values, rule_id))


def _check_version(row, version):
    if version is not None and int(version) != int(row["version"]):
        raise Problem("conflict", f"The rule changed since you opened it (version {row['version']}, not {version}); "
                                  "reload it and make your change again", 409, extra={"version": row["version"]})


def _page(rows, cursor, limit, render=None):
    """One page: at most `limit` rows and at most LIST_BYTES of JSON, whichever comes first. `render` turns a chunk of
    rows into their views (the chunk is at most `limit` rows, so one lookup serves it). `next_cursor` is the offset of
    the first row not on the page; a page always holds one row, so a list cannot stall on a large one."""
    try:
        start = int(cursor or 0)
        if start < 0:
            raise ValueError
    except ValueError:
        raise Problem("validation", "That cursor is not valid", 422) from None
    chunk = rows[start:start + limit]
    items = render(chunk) if render else chunk
    out, used = [], 0
    for item in items:
        size = len(json.dumps(item, ensure_ascii=False, default=str).encode())
        if out and used + size > LIST_BYTES:
            break
        out.append(item)
        used += size
    end = start + len(out)
    return out, (str(end) if end < len(rows) else None)


def _limit(value, default=LIST_DEFAULT, cap=LIST_MAX):
    try:
        n = int(value or default)
    except (TypeError, ValueError):
        raise Problem("validation", "limit is a whole number", 422) from None
    return max(1, min(n, cap))


# ----------------------------------------------------------------------------- the roster and names
def _ctx(c):
    roster, configs = G._roster(c), G._configs(c)
    return {
        "roster": roster, "configs": configs, "people": roster.get("people") or [],
        "groups": {gid: g.get("name") or gid for gid, g in (roster.get("org_groups") or {}).items()},
        "bots": {r["id"]: r["display_name"] or r["slug"] for r in c.execute("SELECT id, slug, display_name FROM bots")},
        "bot_slugs": {r["slug"]: r["id"] for r in c.execute("SELECT id, slug FROM bots")},
        "sources": {r["id"]: {"id": r["id"], "name": r["name"], "kind": r["kind"]}
                    for r in c.execute("SELECT * FROM rule_sources")},
    }


def _person_name(ctx, pid):
    for p in ctx["people"]:
        if p["id"] == pid:
            return p.get("name") or pid
    return pid


def _scope_view(ctx, scope, *, flags=True):
    out = {"groups": [{"id": g, "name": ctx["groups"].get(g, g)} for g in scope.get("groups", [])],
           "bots": [{"id": b, "name": ctx["bots"].get(b, b)} for b in scope.get("bots", [])],
           "people": [{"id": p, "name": _person_name(ctx, p)} for p in scope.get("people", [])]}
    if flags:
        out = {"everyone": bool(scope.get("everyone")), "all_bots": bool(scope.get("all_bots")), **out}
    return out


def _one_hit(hits, text, what):
    unique = sorted(set(hits))
    if len(unique) > 1:
        raise Problem("ambiguous", f"{text} is more than one {what}: use the {what}'s id", 400)
    return unique[0] if unique else None


def _find_group(ctx, text):
    t = text.casefold()
    return _one_hit([gid for gid, name in ctx["groups"].items() if gid == text or str(name).casefold() == t],
                    text, "group")


def _find_person(ctx, text):
    t = text.casefold()
    return _one_hit([p["id"] for p in ctx["people"]
                     if p["id"] == text or str(p.get("name") or "").casefold() == t
                     or str(p.get("email") or "").casefold() == t], text, "person")


def _find_bot(ctx, text):
    t = text.casefold()
    return _one_hit([ident for ident, name in ctx["bots"].items()
                     if ident == text or ctx["bot_slugs"].get(text) == ident or str(name).casefold() == t],
                    text, "bot")


_FINDERS = {"group": _find_group, "person": _find_person, "bot": _find_bot}
_PREFIXES = (("group:", "group"), ("bot:", "bot"), ("person:", "person"), ("human:", "person"))


def _resolve(ctx, text, kinds=("group", "person", "bot")):
    """One teammate named in a Who, With or filter, as (kind, id). A `group:`, `person:` (or `human:`) or `bot:` prefix says
    which kind it is, and must be one of `kinds`. A bare name must match one teammate, of one kind: a name that is a group
    and a bot is ambiguous (400) until it carries a prefix."""
    text = str(text or "").strip()
    for prefix, kind in _PREFIXES:
        if text.startswith(prefix):
            if kind not in kinds:
                raise Problem("validation", f"{text} names a {kind}, and this takes {' or '.join(kinds)}", 422)
            name = text[len(prefix):].strip()
            found = _FINDERS[kind](ctx, name)
            if not found:
                raise Problem("not_found", f"Unknown {kind}: {name}", 404)
            return kind, found
    hits = [(kind, found) for kind in kinds for found in [_FINDERS[kind](ctx, text)] if found]
    if len(hits) > 1:
        raise Problem("ambiguous", f"{text} is a " + " and a ".join(kind for kind, _ in hits) + "; write "
                      + " or ".join(f"{kind}:{text}" for kind, _ in hits), 400)
    if not hits:
        raise Problem("not_found", f"Unknown {'/'.join(kinds)}: {text}", 404)
    return hits[0]


def _strip(text, prefix):
    return text[len(prefix):] if text.startswith(prefix) else text


def scope_in(c, value, *, flags):
    """A Who or With as the API takes it, stored as ids. Each list names a group, person or bot by id or name (with an
    optional `bot:`, `human:` or `person:` prefix); `names` takes any of them (a group, then a person, then a bot).
    Who may say everyone and all_bots; With may not. Anything unknown is refused, never kept."""
    raw = value if isinstance(value, dict) else {}
    unknown = sorted(set(raw) - set(SCOPE_KEYS))
    if unknown:
        raise Problem("validation", f"Unknown field {unknown[0]} in who or with", 422)
    if not flags and (raw.get("everyone") or raw.get("all_bots")):
        raise Problem("validation", "With names teammates: not everyone and not all bots", 422)
    ctx = _ctx(c)
    out = {"everyone": bool(raw.get("everyone")) if flags else False,
           "all_bots": bool(raw.get("all_bots")) if flags else False,
           "groups": [], "bots": [], "people": []}

    def add(kind, found):
        key = {"group": "groups", "bot": "bots", "person": "people"}[kind]
        if found not in out[key]:
            out[key].append(found)

    for field, kinds in (("groups", ("group",)), ("people", ("person",)), ("bots", ("bot",)), ("names", None)):
        for text in raw.get(field) or []:
            kind, found = _resolve(ctx, text, kinds or ("group", "person", "bot"))
            add(kind, found)
    if out["everyone"]:
        out["groups"], out["bots"], out["people"] = [], [], []
    for key in ("groups", "bots", "people"):
        out[key] = sorted(out[key])
    if len(out["groups"]) + len(out["bots"]) + len(out["people"]) > SCOPE_MAX:
        raise Problem("validation", f"Keep one Who or With to {SCOPE_MAX} names", 422)
    return out


def owner_actor(c, value):
    """A due date's owner as an actor: `bot:<id>` or `human:<id>`, given by id or name."""
    text = str(value or "").strip()
    if not text:
        raise Problem("validation", "Say who owns the due date", 422)
    kind, found = _resolve(_ctx(c), text, ("person", "bot"))
    return ("human:" if kind == "person" else "bot:") + found


def who_key(c, actor, ctx=None):
    """A teammate as the matcher reads it: its bot id or person id, and the groups it is in (nested ones too)."""
    ctx = ctx or _ctx(c)
    roster, configs = ctx["roster"], ctx["configs"]
    kind, _, ident = str(actor or "").partition(":")
    if kind == "bot":
        team = "" if ident in G.SYSTEM_BOTS else P.team_of(ident, configs, roster)
        return {"bot": ident, "person": None, "chain": P.group_chain(team, roster) if team else []}
    if kind in ("human", "person"):
        team = (P.person(ident, roster) or {}).get("team")
        return {"bot": None, "person": ident, "chain": P.group_chain(team, roster) if team else []}
    return {"bot": None, "person": None, "chain": []}


def _hits(scope, key):
    """Whether a teammate is named by a Who or With scope: everyone, all bots, its bot, its person, or a group it is in."""
    if scope.get("everyone"):
        return True
    if scope.get("all_bots") and key["bot"]:
        return True
    if key["bot"] and key["bot"] in scope.get("bots", ()):
        return True
    if key["person"] and key["person"] in scope.get("people", ()):
        return True
    return bool(set(key["chain"]) & set(scope.get("groups", ())))


def _active(c):
    return [_row(r) for r in c.execute("SELECT * FROM org_rules WHERE status='active' ORDER BY created_at, id")]


def any_active(c):
    """Whether any rule is in force: one indexed row, read before anything else on every bot write (most writes meet no
    rule, and an install with none never builds the roster for them)."""
    return c.execute("SELECT 1 FROM org_rules WHERE status='active' LIMIT 1").fetchone() is not None


def matching(c, author, recipients=()):
    """The active rules that apply to one write: the author is in Who, and With is empty or names a recipient. With no
    rule in force it reads nothing more; the recipients are looked up only once a rule's Who names the author."""
    if not any_active(c):
        return []
    ctx = _ctx(c)
    author_key = who_key(c, author, ctx)
    keys = None
    out = []
    for row in _active(c):
        if not _hits(row["who"], author_key):
            continue
        if _has(row["with"]):
            keys = [who_key(c, r, ctx) for r in recipients] if keys is None else keys
            if not any(_hits(row["with"], k) for k in keys):
                continue
        out.append(row)
    return out


def applies_to(c, bot, rules, ctx=None):
    """The rules a bot reads as its own: those whose Who names it, and those whose With names it."""
    key = who_key(c, "bot:" + bot, ctx)
    return [r for r in rules if _hits(r["who"], key) or (_has(r["with"]) and _hits(r["with"], key))]


# ----------------------------------------------------------------------------- permissions
def _led(ctx, actor):
    roster, configs = ctx["roster"], ctx["configs"]
    led = set()
    for gid in ctx["groups"]:
        lead = G.lead_of(gid, roster, configs)
        if lead and task_caps.actor(lead) == actor:
            led.add(gid)
    return led


def may_manage(c, auth, who, scope, ctx=None):
    """Accept, edit, retire, skip, and add or change due dates: the owner, admins, a lead of a group in Who, a manager of a
    bot in Who (`Auth.bot_manager`: its operator and owners, and a person above it). Never a bot. Everyone rules are the
    owner's and admins' alone. A conversation member of a bot is not its manager."""
    if who.role == "bot":
        return False
    if auth.bot_admin(who):
        return True
    if who.role != "human" or scope.get("everyone"):
        return False
    if set(scope.get("groups") or ()) & _led(ctx or _ctx(c), who.actor):
        return True
    return any(auth.bot_manager(c, who, slug) for slug in scope.get("bots") or ())


def _check_manage(c, auth, who, scope, ctx=None):
    if not may_manage(c, auth, who, scope, ctx):
        raise Problem("forbidden", "Only the owner, an admin, a lead of a group in Who or an owner of a bot in Who "
                                   "changes this rule", 403)


# ----------------------------------------------------------------------------- views
def _cite_visible(c, auth, who, row, memo):
    """Whether a cite of this file shows its quote to `who`: they may read the file now. Counsel reads it as the people
    its run answers would (counsel.cite_readers), so a quote never enters a run whose asker could not read the file."""
    from . import counsel
    from . import files as F
    if not row:
        return False
    if not counsel.is_counsel(who):
        return F.file_visible(c, auth, who, row, memo.setdefault("files", {}))
    if "readers" not in memo:
        memo["readers"] = counsel.cite_readers(c, auth, who)
    return bool(memo["readers"]) and all(F.file_visible(c, auth, reader, row, memo.setdefault(reader.actor, {}))
                                         for reader in memo["readers"])


def _cite(c, auth, who, cite, memo=None):
    if not cite:
        return None
    row = c.execute("SELECT * FROM bot_files WHERE id=?", (cite.get("file_id"),)).fetchone()
    if not _cite_visible(c, auth, who, row, memo if memo is not None else {}):
        return {"hidden": True}
    return {"file_id": cite.get("file_id"), "version": cite.get("version"), "section": cite.get("section"),
            "quote": cite.get("quote")}


def view(c, auth, who, row, ctx, dues=None, quote=True):
    """A rule as the caller reads it. `quote=False` is what a write stores: the cite is re-read after the write, so a
    replayed response never carries a quote its file no longer lets the caller see."""
    dues = dues if dues is not None else D.views(c, D.live_rows(c, [row["id"]]))
    reviewer = None
    if row["reviewer"]:
        reviewer = {"id": row["reviewer"], "name": _person_name(ctx, row["reviewer"])}
    out = {
        "id": row["id"], "kind": row["kind"], "text": row["text"], "description": row["description"],
        "who": _scope_view(ctx, row["who"]),
        "with": _scope_view(ctx, row["with"], flags=False) if row["kind"] == "internal" else None,
        "source": ctx["sources"].get(row["source_id"]) if row["source_id"] else None,
        "if_broken": row["if_broken"], "reviewer": reviewer, "status": row["status"],
        "suggested_by": row["suggested_by"], "decided_by": row["decided_by"], "decided_at": row["decided_at"],
        "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        "due_at": [{"id": d["id"], "due_on": d["due_on"], "status": d["status"], "repeats": d["repeats"],
                 "task_days": d["task_days"]}
                for d in dues[:DUE_PER_RULE]],
        "due_more": max(0, len(dues) - DUE_PER_RULE),
        # Whether this caller may accept, edit, retire or add due dates (the UI hides what it can't use).
        "can_edit": row["status"] != "retired" and may_manage(c, auth, who, row["who"], ctx),
    }
    if row.get("cite"):
        out["cite"] = _cite(c, auth, who, row["cite"], ctx.setdefault("cite_memo", {})) if quote else {"pending": True}
    return out


# ----------------------------------------------------------------------------- settings as sentences
PERIOD_WORDS = {"per_30m": "30 minutes", "hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month"}
WINDOW_WORDS = (("turns_per_hour", "hour"), ("turns_per_day", "day"))   # a turn budget's window, in words


def _join(words):
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def settings_for(c, bot, ctx=None):
    """The bot's settings as sentences, read from the modules that hold them. Each is {text, source, edit}; `edit` names
    the page and anchor where the setting is changed, or is null when no one changes it in the product."""
    from . import hubdb as HD
    from . import mail_settings
    from . import turn_budgets
    from . import usage_limits
    ctx = ctx or _ctx(c)
    name = ctx["bots"].get(bot, bot)
    out = []

    row = c.execute("SELECT access FROM bot_config WHERE bot=?", (bot,)).fetchone()
    write = A.document(row["access"] if row else None)["write"]
    if write["everyone"]:
        text = f"Everyone can message {name}"
    else:
        names = ([ctx["groups"].get(g, g) for g in write["teams"]] + [ctx["bots"].get(b, b) for b in write["bots"]]
                 + [_person_name(ctx, p) for p in write["people"]])
        text = f"Only {_join(names)} can message {name}" if names else f"Nobody can message {name}"
    out.append({"text": text, "source": "bot_access", "edit": {"page": "bot", "anchor": "access"}})

    mail = mail_settings.stored(c, bot)
    sends = bool(mail and mail["outbound_send"])
    out.append({"text": f"{name} {'can send mail' if sends else 'cannot send mail outside Tico'}",
                "source": "mail_settings", "edit": {"page": "settings", "anchor": "people"}})

    default = usage_limits.company(c)
    limits, source = usage_limits.effective(c, bot, default)
    for key, value in limits.items():
        period = key[:-4]
        if not value:
            continue
        if source.get(period) == "bot":
            out.append({"text": f"Spends up to ${value:g} a {PERIOD_WORDS[period]}", "source": "usage_limits",
                        "edit": {"page": "bot", "anchor": "spend", "path": f"/api/v2/usage/limits/{bot}"}})
        elif source.get(period) == "company":
            out.append({"text": f"Follows the company limit: up to ${value:g} a {PERIOD_WORDS[period]}",
                        "source": "usage_limits",
                        "edit": {"page": "strategy", "anchor": "budget", "path": "/api/v2/usage/limits"}})
    found = usage_limits.pools(c, default)
    for scope in usage_limits.scopes_of(c, bot, default):
        caps = found[scope]
        label = usage_limits.label(c, scope, default)
        path = "/api/v2/usage/pools/" + ("company" if scope == usage_limits.COMPANY
                                         else "groups/" + scope.split(":", 1)[1])
        for key in usage_limits.KEYS:
            if caps.get(key):
                out.append({"text": f"Spends up to ${caps[key]:g} a {PERIOD_WORDS[key[:-4]]} across {label}",
                            "source": "usage_limits", "edit": {"page": "strategy", "anchor": "budget", "path": path}})

    config = c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()
    declared = turn_budgets._declared(config["config"] if config else None)
    own = turn_budgets.own(declared)
    shared = turn_budgets.limits(c, declared)
    for key, word in WINDOW_WORDS:
        if not shared[key]:
            continue
        if own[key] is not None:
            text = f"Up to {shared[key]} turns per {word}"
        else:
            text = f"Team default: {shared[key]} turns per {word}"
        out.append({"text": text, "source": "turn_budgets", "edit": {"page": "bot", "anchor": "turns"}})

    caps = c.execute("SELECT receiver, per_day FROM task_caps WHERE sender=? ORDER BY receiver LIMIT ?",
                     ("bot:" + bot, SETTINGS_TASK_CAPS)).fetchall()
    for cap in caps:
        kind, _, ident = cap["receiver"].partition(":")
        who_name = _person_name(ctx, ident) if kind == "human" else ctx["bots"].get(ident, ident)
        text = f"Can't send tasks to {who_name}" if cap["per_day"] == 0 else \
            f"Up to {cap['per_day']} tasks a day to {who_name}"
        out.append({"text": text, "source": "task_caps", "edit": {"page": "bot", "anchor": "tasks"}})
    more = c.execute("SELECT count(*) FROM task_caps WHERE sender=?", ("bot:" + bot,)).fetchone()[0] - len(caps)
    if more > 0:
        out.append({"text": f"{more} more task limits", "source": "task_caps",
                    "edit": {"page": "bot", "anchor": "tasks"}})

    out.append({"text": f"At most {HD.UNSOLICITED_PER_DAY} unasked messages a day to each person", "source": "hubdb",
                "edit": None})
    out.append({"text": f"Asks go {HD.MAX_ASK_DEPTH} deep at most", "source": "hubdb", "edit": None})
    out.append({"text": f"{HD.CAP_PER_HOUR} bot-to-bot messages an hour in one chat", "source": "hubdb", "edit": None})
    return out


# ----------------------------------------------------------------------------- writes
def _if_broken(value):
    if value not in IF_BROKEN:
        raise Problem("validation", "If broken is " + ", ".join(IF_BROKEN), 422)
    return value


def _reviewer(c, value):
    if value in (None, ""):
        return None
    _, pid = _resolve(_ctx(c), value, ("person",))
    return pid


def _source_named(c, value):
    """A From by id or by name (case-insensitive), for the list filter."""
    text = str(value or "").strip()
    rows = c.execute("SELECT id FROM rule_sources WHERE id=? OR lower(name)=lower(?)", (text, text)).fetchall()
    if len(rows) > 1:
        raise Problem("validation", f"{text} is more than one source: use its id", 422)
    if not rows:
        raise Problem("not_found", f"There is no source {text}", 404)
    return rows[0]["id"]


def _scope_checked(c, auth, who, value):
    """A Who as written. An Everyone rule is the owner's and admins' alone."""
    scope = scope_in(c, value, flags=True)
    if not _has(scope):
        raise Problem("validation", "Say who follows it: everyone, all bots, a group, a bot or a person", 422)
    if scope["everyone"] and not auth.bot_admin(who):
        raise Problem("forbidden", "Only the owner and admins make a rule Everyone", 403)
    return scope


def add_rule(c, auth, who, body):
    H._writer(c, who.actor)
    kind = body["kind"]
    if kind not in KINDS:
        raise Problem("validation", "Kind is internal or external", 422)
    text = _text(body.get("text"), TEXT_MAX, "rule")
    description = _long(body.get("description"), DESCRIPTION_MAX, "description")
    source_id = None
    if kind == "external":
        if not body.get("source_id"):
            raise Problem("validation", "An external rule says who it is from: source_id", 422)
        if body.get("with"):
            raise Problem("validation", "With is for internal rules", 422)
        source_id = _source_named(c, body["source_id"])
    elif body.get("source_id"):
        raise Problem("validation", "Only an external rule has a From (source_id)", 422)
    cite = body.get("cite")
    if cite and kind != "external":
        raise Problem("validation", "Only an external rule cites a document", 422)
    if cite and cite.get("file_id") is None:
        raise Problem("validation", "A cite names its file", 422)
    ctx = _ctx(c)
    given = scope_in(c, body.get("who") or {}, flags=True)
    if who.role == "bot" and not _has(given):
        # Counsel names no Who when no department is named: the suggestion is stored with none, and the owner sets it
        # when they accept (decide_rule refuses an accept with no Who).
        scope = given
    else:
        scope = _scope_checked(c, auth, who, body.get("who") or {})
    with_scope = scope_in(c, body.get("with") or {}, flags=False) if kind == "internal" else \
        {"groups": [], "bots": [], "people": []}
    # A bot's add is a suggestion, and so is a person's add they may not manage (the rule is theirs to suggest). Whoever may
    # manage the rule, Everyone included, makes it active at once.
    status = "active" if who.role != "bot" and may_manage(c, auth, who, scope, ctx) else "suggested"
    now = H.now()
    row = {"id": H.new_id(), "kind": kind, "text": text, "description": description, "source_id": source_id,
           "cite": _json(cite) if cite else None, "who": _json(scope), "with_scope": _json(with_scope),
           "if_broken": _if_broken(body.get("if_broken") or "block"), "reviewer": _reviewer(c, body.get("reviewer")),
           "status": status, "suggested_by": who.actor, "decided_by": who.actor if status == "active" else None,
           "decided_at": now if status == "active" else None, "via": _via(who), "version": 1,
           "created_at": now, "updated_at": now}
    c.execute("INSERT INTO org_rules (id, kind, text, description, source_id, cite, who, with_scope, if_broken, "
              "reviewer, status, suggested_by, decided_by, decided_at, via, version, created_at, updated_at) VALUES "
              "(:id, :kind, :text, :description, :source_id, :cite, :who, :with_scope, :if_broken, :reviewer, "
              ":status, :suggested_by, :decided_by, :decided_at, :via, :version, :created_at, :updated_at)", row)
    stored = _get(c, row["id"])
    _audit(c, who.actor, row["id"], None, _core(stored), who)
    _event(c, who.actor, "added", row["id"], {"kind": kind, "status": status})
    return view(c, auth, who, stored, ctx, quote=False)


def edit_rule(c, auth, who, rule_id, body):
    row = _get(c, rule_id)
    if row["status"] == "retired":
        raise Problem("conflict", "A retired rule is not edited", 409)
    if who.role == "bot":
        raise Problem("forbidden", "A bot suggests a change to a rule; a person edits it", 403)
    ctx = _ctx(c)
    _check_manage(c, auth, who, row["who"], ctx)
    changes = {}
    if body.get("who") is not None:
        scope = _scope_checked(c, auth, who, body["who"])
        _check_manage(c, auth, who, scope, ctx)
        changes["who"] = _json(scope)
    _check_version(row, body.get("version"))
    if body.get("text") is not None:
        changes["text"] = _text(body["text"], TEXT_MAX, "rule")
    if body.get("description") is not None:
        changes["description"] = _long(body["description"], DESCRIPTION_MAX, "description")
    if body.get("with") is not None:
        if row["kind"] != "internal":
            raise Problem("validation", "With is for internal rules", 422)
        changes["with_scope"] = _json(scope_in(c, body["with"], flags=False))
    if body.get("if_broken") is not None:
        changes["if_broken"] = _if_broken(body["if_broken"])
    if body.get("reviewer") is not None:
        changes["reviewer"] = _reviewer(c, body["reviewer"])
    if body.get("source_id") is not None:
        if row["kind"] != "external":
            raise Problem("validation", "Only an external rule has a From", 422)
        changes["source_id"] = _source_named(c, body["source_id"])
    if not changes:
        return view(c, auth, who, row, ctx, quote=False)
    before = _core(row)
    _save(c, rule_id, extra=changes)
    stored = _get(c, rule_id)
    _audit(c, who.actor, rule_id, before, _core(stored), who)
    _event(c, who.actor, "edited", rule_id, {"fields": sorted(changes)})
    return view(c, auth, who, stored, ctx, quote=False)


def decide_rule(c, auth, who, rule_id, body, action):
    """accept | skip | retire. Accept and skip act on a suggestion; retire on a rule in force."""
    row = _get(c, rule_id)
    if who.role == "bot":
        raise Problem("forbidden", "A bot suggests a rule; a person accepts, skips or retires it", 403)
    if action == "accept" and body.get("who") is None and not _has(row["who"]):
        raise Problem("validation", "Say who follows it: a suggestion with no Who is accepted with one", 422)
    ctx = _ctx(c)
    _check_manage(c, auth, who, row["who"], ctx)
    if action == "accept":
        if row["status"] != "suggested":
            raise Problem("conflict", "Only a suggested rule is accepted", 409)
        scope = _scope_checked(c, auth, who, body["who"]) if body.get("who") is not None else row["who"]
        _check_manage(c, auth, who, scope, ctx)
        _check_version(row, body.get("version"))
        before = _core(row)
        extra = {"decided_by": who.actor, "decided_at": H.now()}
        if scope != row["who"]:
            extra["who"] = _json(scope)
        _save(c, rule_id, status="active", extra=extra)
        event = "accepted"
    elif action == "skip":
        if row["status"] != "suggested":
            raise Problem("conflict", "Only a suggested rule is skipped", 409)
        _check_version(row, body.get("version"))
        before = _core(row)
        _save(c, rule_id, status="retired", extra={"decided_by": who.actor, "decided_at": H.now()})
        event = "skipped"
    else:
        if row["status"] == "retired":
            raise Problem("conflict", "The rule is already retired", 409)
        _check_version(row, body.get("version"))
        before = _core(row)
        _save(c, rule_id, status="retired", extra={"decided_by": who.actor, "decided_at": H.now()})
        event = "retired"
    stored = _get(c, rule_id)
    _audit(c, who.actor, rule_id, before, _core(stored), who)
    _event(c, who.actor, event, rule_id, {})
    if action == "retire":
        H.hook(c, "org_rule.retired", rule_id, lambda: {"id": rule_id, "kind": stored["kind"], "text": stored["text"],
                                                        "source_id": stored["source_id"], "retired_by": who.actor},
               who.actor)
    return {"rule": view(c, auth, who, stored, ctx, quote=False), "due": D.views(c, D.live_rows(c, [rule_id]))}


def due_row(c, text):
    """A due date from its id, or from a rule that has exactly one due date in force."""
    found = D.one(c, text)
    if found:
        return found
    rows = D.live_rows(c, [text])
    if not rows:
        raise Problem("not_found", f"There is no due date {text}", 404)
    if len(rows) > 1:
        raise Problem("validation", "That rule has more than one due date: give the due date's id", 422)
    return rows[0]


def _check_due_owner(c, auth, who, rule, owner):
    """Who may own a due date: its routine makes the due date's tasks as that owner, outside `task_caps`, so the caller
    names only themselves, a teammate the rule's Who names, or someone they manage (a person below them on the chart,
    a bot they manage). The owner and Admins name anyone."""
    if owner == who.actor or auth.bot_admin(who):
        return
    if _hits(rule["who"], who_key(c, owner)):
        return
    kind, _, ident = owner.partition(":")
    if kind == "human" and auth.manages(c, who, "person", ident):
        return
    if kind == "bot" and auth.bot_manager(c, who, ident):
        return
    raise Problem("forbidden", "A due date's owner is you, a teammate the rule's Who names, or someone you manage", 403)


def add_due(c, auth, who, rule_id, body):
    row = _get(c, rule_id)
    if row["status"] == "retired":
        raise Problem("conflict", "A retired rule takes no due date", 409)
    if who.role == "bot":
        raise Problem("forbidden", "A bot suggests a due date; a person adds it", 403)
    _check_manage(c, auth, who, row["who"])
    first = D.parse_date(body.get("first_due"), "first_due")
    repeats = D.parse_repeats(body.get("repeats"))
    zone = D.parse_zone(body.get("timezone"))
    owner = owner_actor(c, body.get("owner"))
    _check_due_owner(c, auth, who, row, owner)
    from . import rule_due_routine as RR
    days = RR.task_days_of(body)
    due_id = D.add(c, who.actor, rule_id, first.isoformat(), repeats, zone, owner)
    if days is not RR.UNCHANGED and days is not None:
        RR.sync(c, who.actor, D.one(c, due_id), row["text"], days, _via(who))
    after = D.views(c, [D.one(c, due_id)])[0]
    _audit(c, who.actor, rule_id, None, {"due_at": after}, who)
    _event(c, who.actor, "due_added", rule_id, {"due_at": due_id})
    return after


def edit_due(c, auth, who, due_text, body):
    row = due_row(c, due_text)
    if row["retired_at"] or row["ended_on"]:
        raise Problem("not_found", f"There is no due date {due_text}", 404)
    rule = _get(c, row["rule_id"])
    if who.role == "bot":
        raise Problem("forbidden", "A bot suggests a due date change; a person makes it", 403)
    _check_manage(c, auth, who, rule["who"])
    changes = {}
    if body.get("first_due") is not None:
        changes["first_due"] = D.parse_date(body["first_due"], "first_due").isoformat()
    if body.get("repeats") is not None:
        changes["repeats"] = D.parse_repeats(body["repeats"])
    if body.get("timezone") is not None:
        changes["timezone"] = D.parse_zone(body["timezone"])
    if body.get("owner") is not None:
        changes["owner"] = owner_actor(c, body["owner"])
        if changes["owner"] != row["owner_id"]:
            # A changed owner passes the same test as a new one; an owner kept as it is is never asked again.
            _check_due_owner(c, auth, who, rule, changes["owner"])
    from . import rule_due_routine as RR
    days = RR.task_days_of(body)
    if days is RR.UNCHANGED:
        days = RR.current_days(c, row)
    before = D.views(c, [row])[0]
    replaced = changes.get("first_due", row["first_due"]) != row["first_due"] or \
        changes.get("repeats", row["repeats"]) != row["repeats"]
    if replaced:
        new_id = D.restart(c, who.actor, row, changes)
    else:
        D.update_in_place(c, row["id"], changes)
        new_id = row["id"]
    # The routine follows the row it now belongs to: the same automation, with this row's days, owner, zone and id.
    if days is not None or row["routine_id"]:
        RR.sync(c, who.actor, D.one(c, new_id), rule["text"], days, _via(who))
    after = D.views(c, [D.one(c, new_id)])[0]
    replaces = row["id"] if replaced else None
    _audit(c, who.actor, rule["id"], {"due_at": before}, {"due_at": after}, who)
    _event(c, who.actor, "due_edited", rule["id"], {"due": new_id, "replaces": replaces})
    return {**after, "replaces": replaces}


def remove_due(c, auth, who, due_text):
    row = due_row(c, due_text)
    if row["retired_at"]:
        raise Problem("not_found", f"There is no due date {due_text}", 404)
    rule = _get(c, row["rule_id"])
    if who.role == "bot":
        raise Problem("forbidden", "A bot suggests a removal; a person removes a due date", 403)
    _check_manage(c, auth, who, rule["who"])
    if row["ended_on"]:
        # An edit ended this row and started another (replaced_by), which now holds the routine: remove that one.
        later = c.execute("SELECT id FROM rule_due_dates WHERE replaces=?", (row["id"],)).fetchone()
        raise Problem("ended", f"Due date {row['id']} was replaced by an edit; remove the one in force", 409,
                      extra={"replaced_by_id": later["id"] if later else None})
    before = D.views(c, [row])[0]
    D.remove(c, row["id"])
    from . import rule_due_routine as RR
    RR.off(c, who.actor, row, _via(who))
    stored = D.one(c, row["id"])
    _audit(c, who.actor, rule["id"], {"due_at": before}, {"due_at": D.views(c, [stored])[0]}, who)
    _event(c, who.actor, "due_removed", rule["id"], {"due_at": row["id"]})
    return {"id": row["id"], "retired_at": stored["retired_at"]}


def satisfy_due(c, auth, who, due_text, due_on=None):
    row = due_row(c, due_text)
    if row["retired_at"] or row["ended_on"]:
        raise Problem("not_found", f"There is no due date {due_text}", 404)
    rule = _get(c, row["rule_id"])
    if who.actor != row["owner_id"] and (who.role == "bot" or not may_manage(c, auth, who, rule["who"])):
        raise Problem("forbidden", "Only the due date's owner, or whoever can edit the rule, marks it satisfied", 403)
    day = D.satisfy(c, who.actor, row, due_on, via="person")
    _audit(c, who.actor, rule["id"], None, {"due": row["id"], "satisfied": day}, who)
    _event(c, who.actor, "due_satisfied", rule["id"], {"due": row["id"], "due_on": day})
    return {"due": D.views(c, [D.one(c, row["id"])])[0], "satisfied": day}


def add_source(c, who, body):
    H._writer(c, who.actor)
    name = _text(body.get("name"), NAME_MAX, "name")
    kind = body.get("kind")
    if kind not in SOURCE_KINDS:
        raise Problem("validation", "Kind is " + ", ".join(SOURCE_KINDS), 422)
    found = c.execute("SELECT * FROM rule_sources WHERE lower(name)=lower(?)", (name,)).fetchone()
    if found:
        if found["kind"] != kind:
            raise Problem("conflict", f"{found['name']} is already a source of kind {found['kind']}", 409)
        return dict(found)
    row = {"id": H.new_id(), "name": name, "kind": kind, "created_by": who.actor, "created_at": H.now()}
    c.execute("INSERT INTO rule_sources (id, name, kind, created_by, created_at) VALUES (:id, :name, :kind, :created_by, "
              ":created_at)", row)
    audit_log.write(c, who.actor, "rule_source:" + row["id"], None, row, _via(who))
    H.event(c, who.actor, "rule_source.added", "rule_source:" + row["id"], {"kind": kind})
    return row


def seed_external(c, who, text, source_name, source_kind):
    """An active external rule the system registers once: an outside obligation Finance tracks, such as a tax filing.
    Everyone sees it and its due dates carry their owners, so no one has to accept it. Found again by its text and its
    source, so a second seed adds nothing. Returns the rule's id."""
    source = add_source(c, who, {"name": source_name, "kind": source_kind})
    found = c.execute("SELECT id FROM org_rules WHERE kind='external' AND text=? AND source_id=?",
                      (text, source["id"])).fetchone()
    if found:
        return str(found["id"])
    scope = {"everyone": True, "all_bots": False, "groups": [], "bots": [], "people": []}
    now = H.now()
    row = {"id": H.new_id(), "kind": "external", "text": text, "description": "", "source_id": source["id"],
           "cite": None, "who": _json(scope), "with_scope": _json({"groups": [], "bots": [], "people": []}),
           "if_broken": "warn", "reviewer": None, "status": "active", "suggested_by": who.actor,
           "decided_by": who.actor, "decided_at": now, "via": _via(who), "version": 1, "created_at": now,
           "updated_at": now}
    c.execute("INSERT INTO org_rules (id, kind, text, description, source_id, cite, who, with_scope, if_broken, "
              "reviewer, status, suggested_by, decided_by, decided_at, via, version, created_at, updated_at) VALUES "
              "(:id, :kind, :text, :description, :source_id, :cite, :who, :with_scope, :if_broken, :reviewer, "
              ":status, :suggested_by, :decided_by, :decided_at, :via, :version, :created_at, :updated_at)", row)
    _audit(c, who.actor, row["id"], None, _core(_get(c, row["id"])), who)
    _event(c, who.actor, "added", row["id"], {"kind": "external", "status": "active"})
    return row["id"]


def _party(ctx, text):
    """A teammate named in a filter, as the key its scope keeps it under."""
    kind, found = _resolve(ctx, text)
    return {"group": "groups", "person": "people", "bot": "bots"}[kind], found


def _own_bot(who, bot):
    """The bot `--mine` reads: the caller's own bot (a bot's own actor), or the one named (for a person)."""
    if bot:
        return _strip(bot, "bot:")
    if who.role == "bot" and str(who.actor).startswith("bot:"):
        return who.actor[4:]
    raise Problem("validation", "Say which bot: bot=<slug>", 422)


def _list_rules(c, auth, who, args):
    auth.domain(who)
    kind = args.get("kind") or None
    if kind and kind not in KINDS:
        raise Problem("validation", "Kind is internal or external", 422)
    status = args.get("status") or "active"
    if status not in STATUSES + ("any",):
        raise Problem("validation", "Status is suggested, active, retired or any", 422)
    ctx = _ctx(c)
    sql, params = "SELECT * FROM org_rules WHERE 1=1", []
    if status != "any":
        sql += " AND status=?"
        params.append(status)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if args.get("source"):
        sql += " AND source_id=?"
        params.append(_source_named(c, args["source"]))
    rows = [_row(r) for r in c.execute(sql + " ORDER BY created_at DESC, id DESC", params)]
    if args.get("who"):
        key, ident = _party(ctx, args["who"])
        rows = [r for r in rows if ident in r["who"].get(key, ())]
    if args.get("with"):
        key, ident = _party(ctx, args["with"])
        rows = [r for r in rows if ident in r["with"].get(key, ())]
    bot = None
    if args.get("mine"):
        bot = _own_bot(who, args.get("bot"))
    elif args.get("bot"):
        bot = _strip(args["bot"], "bot:")
    if bot:
        bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        if bot not in ctx["bots"]:
            raise Problem("not_found", f"Unknown bot: {bot}", 404)
        auth.require_see(c, who, bot)
        rows = applies_to(c, bot, rows, ctx)
    page, nxt = _page(rows, args.get("cursor"), _limit(args.get("limit")), _renderer(c, auth, who, ctx))
    out = {"rules": page, "next_cursor": nxt}
    if args.get("mine"):
        out["bot"] = bot
        out["settings"] = settings_for(c, bot, ctx)
    return out


def _renderer(c, auth, who, ctx):
    """Rows to views, with their due dates read once for the chunk."""
    def render(chunk):
        dues = D.due_rows(c, [r["id"] for r in chunk])
        return [view(c, auth, who, r, ctx, dues.get(r["id"], [])) for r in chunk]
    return render


def bot_rules(c, auth, who, bot, cursors=None, limit=BOT_LIST_MAX):
    """A bot's Rules tab: its settings as sentences, then its internal rules and its external rules, each a page of its
    own (`cursors` by kind, `next_cursor` for the next page), so every rule that applies is reachable. Every teammate
    reads it; a bot the caller cannot see is not found."""
    auth.domain(who)
    auth.require_see(c, who, bot)
    ctx = _ctx(c)
    rows = applies_to(c, bot, _active(c), ctx)
    cursors = cursors or {}
    out = {"bot": bot, "settings": settings_for(c, bot, ctx)}
    for kind in KINDS:
        part = [r for r in rows if r["kind"] == kind]
        page, nxt = _page(part, cursors.get(kind), limit, _renderer(c, auth, who, ctx))
        out[kind] = {"rules": page, "next_cursor": nxt}
    return out


def due_list(c, auth, who, args):
    """Due dates of rules in force: overdue always; the rest that fall due within `next` days."""
    auth.domain(who)
    text = str(args.get("next") or "90d").rstrip("d")
    try:
        horizon = int(text)
    except ValueError:
        raise Problem("validation", "next is a number of days, such as 90d", 422) from None
    want = args.get("status") or None
    if want and want not in D.STATUSES:
        raise Problem("validation", "Status is " + ", ".join(D.STATUSES), 422)
    ctx = _ctx(c)
    active = {r["id"]: r for r in _active(c)}
    rows = []
    for found in D.views(c, [r for r in D.live_rows(c) if r["rule_id"] in active]):
        if want and found["status"] != want:
            continue
        today = D.local_today(found["timezone"])
        if found["status"] != "overdue" and D.parse_date(found["due_on"]) > today + timedelta(days=horizon):
            continue
        rule = active[found["rule_id"]]
        manage = may_manage(c, auth, who, rule["who"], ctx)
        rows.append({"id": found["id"], "rule": {"id": rule["id"], "text": rule["text"], "kind": rule["kind"],
                                                 "source": ctx["sources"].get(rule["source_id"])},
                     "due_on": found["due_on"], "repeats": found["repeats"], "owner_id": found["owner_id"],
                     "first_due": found["first_due"], "timezone": found["timezone"],
                     "routine_id": found["routine_id"], "task_days": found["task_days"],
                     "status": found["status"], "open": found["open"],
                     "history": found["history"],
                     "can_edit": manage, "can_satisfy": manage or who.actor == found["owner_id"]})
    rows.sort(key=lambda r: (r["due_on"], r["id"]))
    page, nxt = _page(rows, args.get("cursor"), _limit(args.get("limit"), 100, DUE_LIST_MAX))
    return {"due": page, "next_cursor": nxt}


def due_show(c, auth, who, due_id):
    """One due date in full, with its rule's sentence, its chain of edits and the occurrences still open."""
    auth.domain(who)
    row = D.one(c, due_id)
    if not row:
        raise Problem("not_found", f"There is no due date {due_id}", 404)
    rule = _get(c, row["rule_id"])
    # The same check as the Due list's rows; a row that ended (edited or removed) or a retired rule is read only.
    live = rule["status"] == "active" and not row["retired_at"] and not row["ended_on"]
    manage = live and may_manage(c, auth, who, rule["who"])
    return {**D.detail(c, row), "rule": {"id": rule["id"], "text": rule["text"], "kind": rule["kind"],
                                         "status": rule["status"]},
            "can_edit": manage, "can_satisfy": manage or (live and who.actor == row["owner_id"])}


def counts(c, auth, who):
    auth.domain(who)
    rows = _active(c)
    active = {r["id"] for r in rows}
    dues = [r for r in D.live_rows(c) if r["rule_id"] in active]
    return {"internal": sum(1 for r in rows if r["kind"] == "internal"),
            "external": sum(1 for r in rows if r["kind"] == "external"),
            "due": len(dues),
            "suggested": c.execute("SELECT count(*) FROM org_rules WHERE status='suggested'").fetchone()[0]}


# ----------------------------------------------------------------------------- HTTP
class RuleScope(Contract):
    everyone: bool = False
    all_bots: bool = False
    groups: list[str] = Field(default_factory=list, max_length=100)
    bots: list[str] = Field(default_factory=list, max_length=100)
    people: list[str] = Field(default_factory=list, max_length=100)
    names: list[str] = Field(default_factory=list, max_length=100)


class RuleCite(Contract):
    """The part of a document a rule rests on: a Files version (`file_id`, `version`; the current one by default), the
    section it is in, and the quote, which must appear in that version's text (backend/counsel.py `check_cite`)."""
    file_id: str = Field(min_length=1, max_length=100)
    version: int | None = Field(default=None, ge=1)
    section: str = Field(default="", max_length=300)
    quote: str = Field(min_length=1, max_length=2000)


class RuleCreate(Contract):
    kind: Literal["internal", "external"]
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    who: RuleScope | None = None
    with_: RuleScope | None = Field(default=None, alias="with")
    source_id: str | None = Field(default=None, max_length=100)
    cite: RuleCite | None = None
    if_broken: Literal["block", "escalate", "warn"] | None = None
    reviewer: str | None = Field(default=None, max_length=200)


class RuleEdit(Contract):
    version: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=TEXT_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    who: RuleScope | None = None
    with_: RuleScope | None = Field(default=None, alias="with")
    source_id: str | None = Field(default=None, max_length=100)
    if_broken: Literal["block", "escalate", "warn"] | None = None
    reviewer: str | None = Field(default=None, max_length=200)


class RuleDecide(Contract):
    version: int | None = Field(default=None, ge=1)
    who: RuleScope | None = None


class DueAdd(Contract):
    first_due: str = Field(min_length=10, max_length=10)
    repeats: Literal["once", "weekly", "monthly", "quarterly", "yearly"]
    timezone: str | None = Field(default=None, max_length=64)
    owner: str = Field(min_length=1, max_length=200)
    task_days: int | None = Field(default=None, ge=0, le=366,
                                  description="Open the owner's task this many days before each due day (a routine); "
                                              "absent or null for none")


class DueEdit(Contract):
    first_due: str | None = Field(default=None, max_length=10)
    repeats: Literal["once", "weekly", "monthly", "quarterly", "yearly"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    owner: str | None = Field(default=None, max_length=200)
    task_days: int | None = Field(default=None, ge=0, le=366,
                                  description="The routine's lead time in days; null turns the routine off, absent "
                                              "leaves it as it is")


class DueSatisfied(Contract):
    due_on: str | None = Field(default=None, max_length=10)


class RuleSourceCreate(Contract):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    kind: Literal["vendor", "customer", "government", "other"]


def install(app, store, auth, mutate):
    def fresh(who, rule_id):
        """The rule as the caller reads it now: its cite is checked against the file's access on this request, even when the
        write's response was replayed from the idempotency cache."""
        with store.read() as c:
            return view(c, auth, who, _get(c, rule_id), _ctx(c))

    def with_scopes(body, *keys):
        data = body.model_dump(by_alias=True)
        if "who" in keys:
            data["who"] = None if body.who is None else body.who.model_dump()
        if "with" in keys:
            data["with"] = None if body.with_ is None else body.with_.model_dump()
        return data

    @app.get("/api/v2/rules/counts")
    def rules_counts(request: Request):
        """The tab counts: internal and external rules in force, the due dates kept, the suggestions waiting."""
        with store.read() as c:
            return counts(c, auth, request.state.identity)

    @app.get("/api/v2/rules/due")
    def rules_due(request: Request, next: str = "90d", status: str | None = None, cursor: str | None = None,
                  limit: int = 100):
        """Due dates of rules in force. `next=90d` lists those falling due within 90 days (overdue ones always come);
        `status` narrows to overdue, due_soon, upcoming or satisfied."""
        args = {"next": next, "status": status, "cursor": cursor, "limit": limit}
        with store.read() as c:
            return due_list(c, auth, request.state.identity, args)

    @app.get("/api/v2/rules")
    def rules_list(request: Request, kind: str | None = None, status: str | None = "active", who: str | None = None,
                   with_: str | None = Query(default=None, alias="with"), source: str | None = None,
                   bot: str | None = None, mine: bool = False, cursor: str | None = None, limit: int = LIST_DEFAULT):
        """The rules, newest first, paged. `kind` internal|external; `status` suggested|active|retired|any (active by
        default); `who` and `with` name a group, person or bot; `source` a From; `bot` the rules that apply to a bot;
        `mine=true` the same for the caller's bot (or `bot`), plus its settings as sentences. Every teammate reads every
        sentence and From; a quote shows only where its file is readable."""
        args = {"kind": kind, "status": status, "who": who, "with": with_, "source": source, "bot": bot,
                "mine": mine, "cursor": cursor, "limit": limit}
        with store.read() as c:
            return _list_rules(c, auth, request.state.identity, args)

    @app.post("/api/v2/rules")
    def rules_add(request: Request, body: RuleCreate):
        """Add a rule: internal (Who, optionally With) or external (Who and From). A bot's add lands as suggested; a
        person's lands active when they may manage the rule, and as a suggestion when they may not. Only the owner and
        admins add an Everyone rule. A cite is checked first: a quote that is not in its file is not stored, and the
        answer says `dropped` instead of a rule (the drop is counted in the events)."""
        from . import counsel
        who = request.state.identity
        data = with_scopes(body, "who", "with")
        blobs = request.app.state.blobs

        def work(c):
            counsel.private_suggestion(c, who, data)
            if data.get("cite"):
                cite = counsel.check_cite(c, auth, who, blobs, data["cite"])
                if cite is None:
                    return {"rule": None, "dropped": {"reason": "quote_not_in_source", "file_id": data["cite"]["file_id"]}}
                data["cite"] = cite
            return {"rule": add_rule(c, auth, who, data), "dropped": None}
        result = mutate(request, body, work)
        if result["dropped"] is not None:
            return result
        return fresh(who, result["rule"]["id"])

    @app.get("/api/v2/rule-due/{due_id}")
    def rule_due_show(request: Request, due_id: str):
        """One due date in full: its first date, repeat, zone, owner and routine, its status today, the occurrences still
        open, and its chain of edits. An edit ends a row and starts the next, which points back at it (`replaces`); ended
        and removed rows stay in the chain, so their satisfied occurrences stay in reach."""
        with store.read() as c:
            return due_show(c, auth, request.state.identity, due_id)

    @app.get("/api/v2/rules/{rule_id}")
    def rules_show(request: Request, rule_id: str):
        """One rule, with its due dates and, where its file is readable, the quote it cites."""
        who = request.state.identity
        with store.read() as c:
            auth.domain(who)
            return view(c, auth, who, _get(c, rule_id), _ctx(c))

    @app.post("/api/v2/rules/{rule_id}")
    def rules_edit(request: Request, rule_id: str, body: RuleEdit):
        """Edit a rule: only the fields sent. `version` is the rule's as read; another answers 409. A bot cannot edit;
        a party named in With cannot edit."""
        who = request.state.identity
        data = with_scopes(body, "who", "with")

        def work(c):
            return edit_rule(c, auth, who, rule_id, data)
        mutate(request, body, work)
        return fresh(who, rule_id)

    @app.post("/api/v2/rules/{rule_id}/accept")
    def rules_accept(request: Request, rule_id: str, body: RuleDecide):
        """Accept a suggestion, optionally changing its Who first. Bots never accept."""
        who = request.state.identity
        data = {"version": body.version, "who": None if body.who is None else body.who.model_dump()}

        def work(c):
            return decide_rule(c, auth, who, rule_id, data, "accept")
        result = mutate(request, body, work)
        return {"rule": fresh(who, rule_id), "due": result["due_at"]}

    @app.post("/api/v2/rules/{rule_id}/skip")
    def rules_skip(request: Request, rule_id: str, body: RuleDecide):
        """Skip a suggestion: it is set aside, not in force."""
        who = request.state.identity

        def work(c):
            return decide_rule(c, auth, who, rule_id, {"version": body.version}, "skip")
        result = mutate(request, body, work)
        return {"rule": fresh(who, rule_id), "due": result["due_at"]}

    @app.post("/api/v2/rules/{rule_id}/retire")
    def rules_retire(request: Request, rule_id: str, body: RuleDecide):
        """Retire a rule. Its due dates come back, so the caller can ask whether to remove them too."""
        who = request.state.identity

        def work(c):
            return decide_rule(c, auth, who, rule_id, {"version": body.version}, "retire")
        result = mutate(request, body, work)
        return {"rule": fresh(who, rule_id), "due": result["due_at"]}

    @app.post("/api/v2/rules/{rule_id}/due")
    def rules_due_add(request: Request, rule_id: str, body: DueAdd):
        """Add a due date to a rule: a first day, a repeat, an owner (a bot or a person), and the routine's lead time."""
        who = request.state.identity

        def work(c):
            return add_due(c, auth, who, rule_id, body.model_dump())
        return mutate(request, body, work)

    @app.post("/api/v2/rule-due/{due_id}")
    def rules_due_edit(request: Request, due_id: str, body: DueEdit):
        """Edit a due date. A change to first_due or repeats ends this row and starts a new one, so its history stays."""
        who = request.state.identity

        def work(c):
            return edit_due(c, auth, who, due_id, body.model_dump(exclude_unset=True))
        return mutate(request, body, work)

    @app.post("/api/v2/rule-due/{due_id}/remove")
    def rules_due_remove(request: Request, due_id: str, body: DueSatisfied):
        """Remove a due date. Its history is kept."""
        who = request.state.identity

        def work(c):
            return remove_due(c, auth, who, due_id)
        return mutate(request, body, work)

    @app.post("/api/v2/rule-due/{due_id}/satisfied")
    def rules_due_satisfied(request: Request, due_id: str, body: DueSatisfied):
        """Mark an occurrence Satisfied. With more than one open occurrence, `due_on` says which one. `due_id` may be the
        rule's id when it has exactly one due date."""
        who = request.state.identity

        def work(c):
            return satisfy_due(c, auth, who, due_id, body.due_on)
        return mutate(request, body, work)

    @app.get("/api/v2/rule-sources")
    def rule_sources_list(request: Request, cursor: str | None = None, limit: int = LIST_DEFAULT):
        """Where external rules come from (From), by name."""
        auth.domain(request.state.identity)
        with store.read() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM rule_sources ORDER BY lower(name), id")]
        page, nxt = _page(rows, cursor, _limit(limit))
        return {"sources": page, "next_cursor": nxt}

    @app.post("/api/v2/rule-sources")
    def rule_sources_add(request: Request, body: RuleSourceCreate):
        """Add a From (a vendor, customer, government or other). Adding one that exists by name returns it."""
        who = request.state.identity

        def work(c):
            return {"source": add_source(c, who, body.model_dump())}
        return mutate(request, body, work)

    @app.get("/api/v2/bots/{bot}/rules")
    def bot_rules_show(request: Request, bot: str, internal_cursor: str | None = None, external_cursor: str | None = None,
                       limit: int = BOT_LIST_MAX):
        """A bot's Rules tab: its settings as sentences (each with the place it is changed), then its internal and its
        external rules, each paged: pass that kind's `next_cursor` as its cursor for the rest."""
        who = request.state.identity
        cursors = {"internal": internal_cursor, "external": external_cursor}
        with store.read() as c:
            bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            return bot_rules(c, auth, who, bot, cursors, _limit(limit, BOT_LIST_MAX, BOT_LIST_MAX))


__all__ = ["ensure", "matching", "applies_to", "may_manage", "scope_in", "settings_for", "view", "install", "counts", "redact_audit",
           "due_show"]
