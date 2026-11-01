"""Who may see, read, write to and manage each teammate: a bot, a person or a group (docs/permissions.md).

Every teammate has the same four levels:

    see     know they exist: the org chart, lists, the @mention picker, their page
    read    read their Home (for a bot, also its activity, as backend/bot_access.py describes)
    write   DM them, add them to a thread; posting in their Home needs Read too
    manage  edit their settings and their access

See, Read and Write each name an audience, in the shape bots have always had (`bot_access.audience`): Everyone, or
lists of people (roster ids), groups (`teams`) and bots (slugs). Naming a group names every person and bot in it and
in the groups nested under it, resolved live: leaving the group drops what it gave. Read or Write implies See; Write
without Read is allowed (a bot's "Requests only"). Manage is not stored. It is a rule:

    bot     its owners, the admins and the company owner, whoever it reports up to (`Auth.bot_manager`)
    person  themselves, whoever they report up to (`people.manages`), the admins and the company owner
    group   its leads (the lead of the group or of a group above it), the admins and the company owner

and it carries full See, Read and Write. A bot also has full access to itself and to the bots below it in the
`reports_to` chain, as before. A credential that is neither a person nor a bot (a runner) keeps the reach it always
had: everything.

Storage. A bot's audiences stay on the bot (`bot_config.access_json`). A person's or a group's are a row in
`teammate_accesses`; no row means the defaults:

    person  Everyone, Everyone, Everyone
    group   Everyone, Everyone, members of the group

Entry points, each taking a database connection, a viewer (an `auth.Identity`) and a target (`bot:<slug>`,
`human:<id>` or `group:<id>`):

  levels_for(c, viewer, target)         -> {see, read, write, manage}
  levels_many(c, viewer, targets)       -> {target: {see, read, write, manage}}, one pass for a list
  can(c, viewer, level, target)         -> bool
  may_reply(c, viewer, target)          -> bool   the reply-back rule: the target wrote to the viewer, or the
                                                  viewer holds open work the target asked for
  group_member_actors(c, group_id)      -> {"human:<id>", "bot:<slug>"}, the group and the groups under it
  readers(c, target, level="read")      -> "everyone" | {actor, ...}
  hidden(c, viewer, level="see")        -> {target, ...} the viewer may not see (or read, ...)
  sql_visible(c, viewer, column, ...)   -> a SQL condition that drops rows whose actor the viewer may not see

`auth` is optional everywhere: the Auth instance when the caller has one, so a request's admin list is the one it
already read. Without it the admins are read from the database (`access.owner_and_admin_emails`).
"""
import json
import sqlite3

from . import bot_access as A
from . import people as P
from .store import H, Problem

LEVELS = ("see", "read", "write", "manage")
AUDIENCE_LEVELS = A.LEVELS          # see, read, write: the stored ones
KINDS = ("bot", "human", "group")
FULL = {"see": True, "read": True, "write": True, "manage": True}
NONE = {"see": False, "read": False, "write": False, "manage": False}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS teammate_accesses (id TEXT PRIMARY KEY, target_type TEXT NOT NULL "
    "CHECK (target_type IN ('human','group')), target_id TEXT NOT NULL, access TEXT NOT NULL, "
    "revision INTEGER NOT NULL DEFAULT 1, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS teammate_accesses_target ON teammate_accesses(target_type, target_id)",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    migrate_team_pages(c)


def migrate_team_pages(c):
    """A team page's "Seen by" (pin_places.audience_json) is now its group's Read: the page is the group's content,
    while See (being on the chart) stays as it was. Each narrowed page moves into its group's Read once, only when
    Read is still Everyone; when both are narrowed, Read becomes whoever both let in. Nothing widens. Then it is
    cleared."""
    try:
        rows = c.execute("SELECT place, audience_json FROM pin_places "
                         "WHERE place LIKE 'team:%' AND audience_json IS NOT NULL").fetchall()
    except sqlite3.OperationalError:     # a database from before team pages
        return
    from . import audit_log
    for place, raw in ((r[0], r[1]) for r in rows):
        gid = place[5:]
        try:
            level = A.audience(json.loads(raw))
        except (ValueError, TypeError):
            level = {"everyone": True}
        if not level.get("everyone"):
            row = c.execute("SELECT id, access FROM teammate_accesses WHERE target_type='group' AND target_id=?",
                            (gid,)).fetchone()
            before = _complete(row[1] if row else None, "group", gid)
            doc = dict(before, read=_narrowest(c, before["read"], level))
            if doc != before:
                text, now = json.dumps(doc, sort_keys=True, separators=(",", ":")), H.now()
                if row:
                    c.execute("UPDATE teammate_accesses SET access=?,revision=revision+1,updated_by='system',updated_at=? "
                              "WHERE id=?", (text, now, row[0]))
                else:
                    c.execute("INSERT INTO teammate_accesses (id,target_type,target_id,access,revision,updated_by,"
                              "created_at,updated_at) VALUES (?,?,?,?,1,'system',?,?)", (H.new_id(), "group", gid, text, now, now))
                audit_log.write(c, "system", f"group:{gid}:access", before, doc, "team-page-audience")
        c.execute("UPDATE pin_places SET audience_json=NULL WHERE place=?", (place,))


def _narrowest(c, stored, legacy):
    """Whoever both audiences let in. Everyone gives way to the other; one that lets in no one the other does not is
    kept as it is (its groups still resolve live); otherwise the people and bots in both, named one by one."""
    if stored.get("everyone"):
        return dict(legacy)
    if legacy.get("everyone") or stored == legacy:
        return dict(stored)
    from . import views
    roster, entries = views.roster(c, shared=True), views.entries(c, shared=True)

    def actors(level):
        out = {"human:" + p for p in level.get("people") or []} | {"bot:" + b for b in level.get("bots") or []}
        for gid in level.get("teams") or []:
            out |= group_member_actors(c, gid, roster, entries)
        return out
    mine, theirs = actors(stored), actors(legacy)
    if mine <= theirs:
        return dict(stored)
    if theirs <= mine:
        return dict(legacy)
    both = mine & theirs
    return {"everyone": False, "people": sorted(a[6:] for a in both if a.startswith("human:")), "teams": [],
            "bots": sorted(a[4:] for a in both if a.startswith("bot:"))}


def forget_group(c, actor, gid):
    """A deleted group's access goes with it, and no other teammate's audience names it any more: a group made later
    with the same id starts from the defaults. What it was stays in the audit log."""
    from . import audit_log
    for row in list(stored_rows(c).values()):
        kind, ident = row["target_type"], row["target_id"]
        before = _complete(row["access"], kind, ident)
        if kind == "group" and ident == gid:
            c.execute("DELETE FROM teammate_accesses WHERE id=?", (row["id"],))
            audit_log.write(c, actor, f"group:{gid}:access", before, None, "group-deleted")
            continue
        after = {level: (dict(value, teams=[t for t in value["teams"] if t != gid]) if gid in value["teams"] else value)
                 for level, value in before.items()}
        if after != before:
            c.execute("UPDATE teammate_accesses SET access=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?",
                      (json.dumps(after, sort_keys=True, separators=(",", ":")), actor, H.now(), row["id"]))
            audit_log.write(c, actor, f"{kind}:{ident}:access", before, after, "group-deleted")


# ----------------------------------------------------------------------------- targets and stored shape
def parse_target(target):
    """(kind, id) for `bot:<slug>`, `human:<id>` (or `person:<id>`) and `group:<id>`; a Problem for anything else."""
    kind, _, ident = str(target or "").strip().partition(":")
    kind = "human" if kind == "person" else kind
    if kind not in KINDS or not ident:
        raise Problem("validation", "A teammate is bot:<slug>, human:<id> or group:<id>", 422)
    return kind, ident


def default_document(kind, ident):
    """The audiences a person or group has before anyone narrows them."""
    everyone = dict(A.OPEN_LEVEL, people=[], teams=[], bots=[])
    if kind == "group":
        return {"see": dict(everyone), "read": dict(everyone),
                "write": {"everyone": False, "people": [], "teams": [ident], "bots": []}}
    return {level: dict(everyone) for level in AUDIENCE_LEVELS}


def _complete(raw, kind, ident):
    """A stored document with every level present; a level missing or unreadable takes its default."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = None
    value = value if isinstance(value, dict) else {}
    base = default_document(kind, ident)
    return {level: A.audience(value[level]) if isinstance(value.get(level), dict) else base[level]
            for level in AUDIENCE_LEVELS}


def stored_rows(c, kind=None):
    """{(target_type, target_id): row} for every stored person or group document."""
    try:
        if kind:
            rows = c.execute("SELECT * FROM teammate_accesses WHERE target_type=?", (kind,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM teammate_accesses").fetchall()
    except sqlite3.OperationalError:     # a database from before the table: everyone has the defaults
        return {}
    return {(r["target_type"], r["target_id"]): r for r in rows}


def document(c, target):
    """The three audiences of a teammate as stored (or defaulted), for the editor. A bot's come from its config."""
    kind, ident = parse_target(target)
    if kind == "bot":
        row = c.execute("SELECT access_json FROM bot_config WHERE bot=?", (ident,)).fetchone()
        return A.document(row["access_json"] if row else None)
    row = stored_rows(c, kind).get((kind, ident))
    return _complete(row["access"] if row else None, kind, ident)


def revision(c, target):
    kind, ident = parse_target(target)
    row = stored_rows(c, kind).get((kind, ident))
    return int(row["revision"]) if row else 0


# ----------------------------------------------------------------------------- shared context
class _Context:
    """What deciding many targets for one viewer reads once: the roster, the bots' entries, the viewer's groups."""

    def __init__(self, c, viewer, auth=None):
        from . import views
        self.c, self.viewer, self.auth = c, viewer, auth
        self.roster = views.roster(c, shared=True)
        self.entries = views.entries(c, shared=True)
        self.archived = {r["slug"] for r in H.bots(c) if r.get("state") == "archived"}
        self.parents = {slug: str(e.get("reports_to") or "") for slug, e in self.entries.items()}
        self.actor = str(getattr(viewer, "actor", "") or "")
        self.me = H.actor_id(self.actor)
        self.role = getattr(viewer, "role", "")
        self._stored = None
        self._admin = None
        self._chain = None
        self._leads = None

    @property
    def stored(self):
        if self._stored is None:
            self._stored = stored_rows(self.c)
        return self._stored

    @property
    def admin(self):
        if self._admin is None:
            self._admin = is_admin(self.c, self.viewer, self.auth)
        return self._admin

    @property
    def chain(self):
        """The groups the viewer is in: its own and every group that one is nested in."""
        if self._chain is None:
            if self.role == "bot" and self.actor.startswith("bot:"):
                own = P.team_of(self.me, self.entries, self.roster) or ""
            elif self.role in ("human", "owner"):
                own = (P.person(self.me, self.roster) or {}).get("team") or ""
            else:
                own = ""
            self._chain = P.group_chain(own, self.roster) or ([own] if own else [])
        return self._chain

    def names(self, level):
        if self.role == "bot":
            return A.names(level, bot=self.me, team=self.chain)
        return A.names(level, person=self.me, team=self.chain)

    def led(self):
        """The groups the viewer leads, and every group below them."""
        if self._leads is None:
            from . import groups as G
            out = set()
            for gid in self.roster.get("org_groups") or {}:
                lead = G.lead_of(gid, self.roster, self.entries)
                if lead and _lead_actor(lead) == self.actor:
                    out |= P.group_and_below(gid, self.roster)
            self._leads = out
        return self._leads


def _lead_actor(ref):
    text = str(ref or "").strip()
    return "human:" + text[len("person:"):] if text.startswith("person:") else text


def is_admin(c, viewer, auth=None):
    """The company owner or an Admin (Tico support with app access reads as one), as `Auth.bot_admin` says."""
    role = getattr(viewer, "role", "")
    if role == "owner":
        return True
    if role != "human":
        return False
    if auth is not None:
        return auth.bot_admin(viewer)
    if getattr(viewer, "support", "") == "app":
        return True
    from . import access
    return str(getattr(viewer, "email", "") or "").lower() in set(access.owner_and_admin_emails(c))


def _above(parents, me, slug):
    seen, parent = {slug}, parents.get(slug, "")
    while parent and parent not in seen:
        if parent == me:
            return True
        seen.add(parent)
        parent = parents.get(parent, "")
    return False


# ----------------------------------------------------------------------------- bots
def bot_levels(c, viewer, slugs=None, admin=None, auth=None, ctx=None):
    """{slug: {see, read, write}} for this viewer, for `slugs` (default every bot with a configuration). The rules
    `Auth.bot_accesses` has always applied, with one change: a bot viewer in a group matches that group in an audience
    (it used to match only people)."""
    from . import read_cache
    wanted = list(dict.fromkeys(slugs)) if slugs is not None else None
    rows = {r["bot"]: r for r in read_cache.configs(c, wanted)}
    full = {"see": True, "read": True, "write": True}
    role = getattr(viewer, "role", "")
    if role not in ("human", "owner", "bot") or role == "owner":
        return {slug: dict(full) for slug in (wanted if wanted is not None else rows)}
    out = {}
    for slug in (wanted if wanted is not None else rows):
        row = rows.get(slug)
        doc = A.document(row["access_json"]) if row else None
        if doc is None or A.is_open(doc):
            out[slug] = dict(full)
            continue
        ctx = ctx or _Context(c, viewer, auth)
        if role == "bot":
            me = ctx.me
            if me == slug or _above(ctx.parents, me, slug):
                out[slug] = dict(full)
                continue
            levels = {level: ctx.names(doc[level]) for level in AUDIENCE_LEVELS}
            # A bot may answer one that wrote to it, or that holds work it asked for, so a
            # private bot can still be replied to: nothing else about it opens up.
            if not levels["write"] and may_reply(c, viewer, "bot:" + slug):
                levels["write"] = True
        else:
            pid = ctx.me
            admin_now = ctx.admin if admin is None else admin
            managed = (admin_now or row["operator"] == pid or pid in A.owner_ids(row["bot_owners_json"])
                       or P.manages(pid, "bot", slug, ctx.roster, ctx.entries, ctx.archived, True))
            if managed:
                out[slug] = dict(full)
                continue
            levels = {level: ctx.names(doc[level]) for level in AUDIENCE_LEVELS}
        levels["see"] = levels["see"] or levels["read"] or levels["write"]
        out[slug] = levels
    return out


def _bot_manage(c, viewer, slug, ctx):
    """`Auth.bot_manager` (from the database when there is no Auth): who may edit the bot's settings and access."""
    if ctx.auth is not None:
        return ctx.auth.bot_manager(c, viewer, slug)
    role = ctx.role
    if role == "owner":
        return True
    if role != "human" or slug in _system_bots(c):
        return False
    if ctx.admin:
        return True
    row = c.execute("SELECT operator,bot_owners_json FROM bot_config WHERE bot=?", (slug,)).fetchone()
    if row and (row["operator"] == ctx.me or ctx.me in A.owner_ids(row["bot_owners_json"])):
        return True
    return P.manages(ctx.me, "bot", slug, ctx.roster, ctx.entries, ctx.archived, True)


def _system_bots(c):
    """The built-in bots, which only the company owner manages (`Auth.system_bot`)."""
    from . import groups as G
    return set(G.SYSTEM_BOTS)


# ----------------------------------------------------------------------------- people and groups
def _human_levels(ctx, pid):
    if ctx.role == "owner" or ctx.admin:
        return dict(FULL)
    if ctx.role == "human" and P.manages(ctx.me, "person", pid, ctx.roster, ctx.entries, ctx.archived, True):
        return dict(FULL)          # themselves, or someone they report up to
    row = ctx.stored.get(("human", pid))
    doc = _complete(row["access"] if row else None, "human", pid)
    levels = {level: ctx.names(doc[level]) for level in AUDIENCE_LEVELS}
    if ctx.role == "bot" and not levels["write"] and may_reply(ctx.c, ctx.viewer, "human:" + pid):
        levels["write"] = True     # the reply-back rule, as for bots
    levels["see"] = levels["see"] or levels["read"] or levels["write"]
    return {**levels, "manage": False}


def _group_levels(ctx, gid):
    if ctx.role == "owner" or ctx.admin or gid in ctx.led():
        return dict(FULL)
    row = ctx.stored.get(("group", gid))
    doc = _complete(row["access"] if row else None, "group", gid)
    levels = {level: ctx.names(doc[level]) for level in AUDIENCE_LEVELS}
    levels["see"] = levels["see"] or levels["read"] or levels["write"]
    return {**levels, "manage": False}


# ----------------------------------------------------------------------------- the entry points
def levels_many(c, viewer, targets, auth=None):
    """{target: {see, read, write, manage}} for every target, read in one pass."""
    targets = list(dict.fromkeys(targets))
    parsed = {t: parse_target(t) for t in targets}
    role = getattr(viewer, "role", "")
    if role not in ("human", "owner", "bot"):
        # A runner or another system's key: the reach it always had, but it manages nothing.
        return {t: {**FULL, "manage": False} for t in targets}
    ctx = _Context(c, viewer, auth)
    bots = [ident for kind, ident in parsed.values() if kind == "bot"]
    bot_out = bot_levels(c, viewer, bots, auth=auth, ctx=ctx) if bots else {}
    out = {}
    for target, (kind, ident) in parsed.items():
        if kind == "bot":
            manage = _bot_manage(c, viewer, ident, ctx)
            levels = dict(FULL) if manage else {**bot_out[ident], "manage": False}
        elif kind == "human":
            levels = _human_levels(ctx, ident)
        else:
            levels = _group_levels(ctx, ident)
        out[target] = levels
    return out


def levels_for(c, viewer, target, auth=None):
    """{see, read, write, manage} for this viewer on one teammate."""
    target = _normal(target)
    return levels_many(c, viewer, [target], auth)[target]


def can(c, viewer, level, target, auth=None):
    if level not in LEVELS:
        raise ValueError(level)
    return levels_for(c, viewer, target, auth)[level]


def require(c, viewer, level, target, auth=None, missing=None):
    """Refuse unless the viewer holds `level`: 404 when they cannot even see the teammate, 403 naming what is missing
    when they can."""
    levels = levels_for(c, viewer, target, auth)
    kind, ident = parse_target(target)
    noun = {"bot": "Bot", "human": "Person", "group": "Group"}[kind]
    if not levels["see"]:
        raise Problem("not_found", missing or f"{noun} not found", 404)
    if not levels[level]:
        raise Problem("forbidden", {
            "read": f"You can see {ident} but not read their Home. Ask whoever manages them for Read access.",
            "write": f"You can see {ident} but not message them. Ask whoever manages them for Write access.",
            "manage": f"Only whoever manages {ident} changes this.",
        }[level], 403)
    return levels


def _normal(target):
    kind, ident = parse_target(target)
    return f"{kind}:{ident}"


def may_reply(c, viewer, target):
    """The reply-back rule: the viewer may answer a teammate who wrote to it, or who asked it for work it still holds.
    It is scoped to the two of them; nothing else about the target opens up."""
    kind, ident = parse_target(target)
    actor = str(getattr(viewer, "actor", "") or "")
    if kind == "group" or not actor:
        return False
    other = f"{kind}:{ident}"
    if c.execute("SELECT 1 FROM messages WHERE to_actor=? AND from_actor=? LIMIT 1", (actor, other)).fetchone():
        return True
    return bool(c.execute("SELECT 1 FROM tasks WHERE requester=? AND owner=? AND status NOT IN "
                          "('done','closed','declined') LIMIT 1", (other, actor)).fetchone())


def group_member_actors(c, group_id, roster=None, entries=None):
    """Every person and bot in the group and in the groups nested under it, as actors."""
    from . import views
    roster = roster or views.roster(c, shared=True)
    entries = entries if entries is not None else views.entries(c, shared=True)
    below = P.group_and_below(group_id, roster)
    if not below:
        return set()
    archived = {r["slug"] for r in H.bots(c) if r.get("state") == "archived"}
    out = {"human:" + p["id"] for p in P.visible_people(roster) if p.get("team") in below}
    out |= {"bot:" + slug for slug in entries
            if slug not in archived and P.team_of(slug, entries, roster) in below}
    return out


def all_actors(c, roster=None, entries=None):
    """Every person and non-archived bot: who a set-based answer can name."""
    from . import views
    roster = roster or views.roster(c, shared=True)
    entries = entries if entries is not None else views.entries(c, shared=True)
    archived = {r["slug"] for r in H.bots(c) if r.get("state") == "archived"}
    return ({"human:" + p["id"] for p in roster.get("people") or []}
            | {"bot:" + slug for slug in entries if slug not in archived})


def readers(c, target, level="read", auth=None):
    """Everyone who holds `level` on the teammate: "everyone" when its audience is Everyone, else a set of actors
    (people, bots and its managers). For bulk filters (the SSE view, a fan-out) that ask about many readers at once."""
    if level not in LEVELS:
        raise ValueError(level)
    kind, ident = parse_target(target)
    if level != "manage":
        doc = document(c, target)
        if doc[level]["everyone"]:
            return "everyone"
        if level == "see" and (doc["read"]["everyone"] or doc["write"]["everyone"]):
            return "everyone"
    out = set()
    for actor in sorted(all_actors(c)):
        viewer = _viewer_for(c, actor)
        if viewer is not None and levels_for(c, viewer, target, auth)[level]:
            out.add(actor)
    return out


def _viewer_for(c, actor):
    """An identity for an actor, without elevating it: the owner when it is the owner's address."""
    from .auth import Identity
    if actor.startswith("bot:"):
        return Identity(actor, "bot")
    human = H.human(c, H.actor_id(actor))
    if not human:
        return None
    from . import access
    email = str(human.get("email") or "").lower()
    owner = str((access._load_json(c, access.OWNER) or {}).get("email") or "").lower()
    return Identity(actor, "owner" if email and email == owner else "human", email=email)


def hidden(c, viewer, level="see", kinds=KINDS, auth=None):
    """Every teammate (as `kind:id`) the viewer lacks `level` on, of these kinds. Empty for the owner, the admins and
    a credential that is neither a person nor a bot."""
    role = getattr(viewer, "role", "")
    if role not in ("human", "bot") or is_admin(c, viewer, auth):
        return set()
    if level in ("see", "read") and "bot" not in kinds and not stored_rows(c):
        return set()            # nobody narrowed a person or a group: the defaults let everyone see them
    from . import views
    targets = []
    roster = views.roster(c, shared=True)
    if "human" in kinds:
        targets += ["human:" + p["id"] for p in roster.get("people") or []]
    if "group" in kinds:
        targets += ["group:" + gid for gid in roster.get("org_groups") or {}]
    if "bot" in kinds:
        targets += ["bot:" + r["bot"] for r in c.execute("SELECT bot FROM bot_config")]
    if not targets:
        return set()
    return {t for t, levels in levels_many(c, viewer, targets, auth).items() if not levels[level]}


def sql_visible(c, viewer, column, level="see", kinds=KINDS, bare=None, auth=None, unseen=None):
    """A SQL condition, true when the teammate in `column` is one the viewer holds `level` on. `column` holds actors
    (`human:<id>`, `bot:<slug>`); `bare` ("human", "bot" or "group") says it holds bare ids of that kind instead.
    `unseen` is `hidden(...)` when the caller has it already. Always "1" when nothing is hidden."""
    unseen = hidden(c, viewer, level, kinds, auth) if unseen is None else unseen
    if bare:
        values = sorted(t.split(":", 1)[1] for t in unseen if t.startswith(bare + ":"))
    else:
        values = sorted(t for t in unseen if not t.startswith("group:"))
    if not values:
        return "1"
    return f"coalesce({column},'') NOT IN {A.qlist(values)}"


# ----------------------------------------------------------------------------- writing a person's or group's access
def save(c, who, target, body, auth=None, via=None):
    """Set a person's or a group's three audiences: Manage on them, the revision the editor read, an audit row and an
    event. `body` has `see`, `read` and `write` (each Everyone or lists) and `revision`. A bot's access is written by
    `SettingsAdmin.set_access`, as before."""
    kind, ident = parse_target(target)
    if kind == "bot":
        raise Problem("validation", "A bot's access is set on the bot", 422)
    _require_existing(c, who, kind, ident, target, auth)
    row = stored_rows(c, kind).get((kind, ident))
    current = int(row["revision"]) if row else 0
    if int(body.get("revision") or 0) != current:
        raise Problem("version_conflict", "Access changed; refresh before saving", 409)
    from . import views
    roster = views.roster(c, shared=True)
    people = {p["id"] for p in roster.get("people") or []} | {h["id"] for h in H.humans(c)}
    groups = set(roster.get("org_groups") or {})
    bots = {r["slug"] for r in H.bots(c)}
    doc = A.parse({level: body.get(level) for level in AUDIENCE_LEVELS}, people, groups, bots)
    before = _complete(row["access"] if row else None, kind, ident)
    if before == doc:
        raise Problem("unchanged", "That is already who has access", 409)
    now = H.now()
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    if row:
        c.execute("UPDATE teammate_accesses SET access=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?",
                  (text, who.actor, now, row["id"]))
    else:
        c.execute("INSERT INTO teammate_accesses (id,target_type,target_id,access,revision,updated_by,created_at,"
                  "updated_at) VALUES (?,?,?,?,1,?,?,?)", (H.new_id(), kind, ident, text, who.actor, now, now))
    from . import audit_log
    audit_log.write(c, who.actor, f"{kind}:{ident}:access", before, doc, via)
    H.event(c, who.actor, f"{'person' if kind == 'human' else 'group'}.access_changed", ident,
            {"before": A.summary(before), "after": A.summary(doc)})
    return view(c, who, target, auth)


def view(c, who, target, auth=None):
    """What the Access editor reads: the three audiences, the revision, what the viewer holds, and the groups an
    audience can name. For whoever manages the teammate."""
    kind, ident = parse_target(target)
    _require_existing(c, who, kind, ident, target, auth)
    from . import views
    roster = views.roster(c, shared=True)
    names = {gid: g.get("name") or gid for gid, g in (roster.get("org_groups") or {}).items()}
    return {"target": f"{kind}:{ident}", **document(c, target), "default": default_document(kind, ident),
            "revision": revision(c, target), "you": levels_for(c, who, target, auth),
            "teams": group_choices(c, who, names, auth)}


def _require_existing(c, who, kind, ident, target, auth):
    """Manage on a teammate that is there. One that is not and one the viewer may not see read the same 404."""
    if not _exists(c, kind, ident):
        raise Problem("not_found", ("Person" if kind == "human" else "Group") + " not found", 404)
    require(c, who, "manage", target, auth)


def group_choices(c, viewer, names, auth=None):
    """`[{id, name}]` for {group id: name}, less the groups the viewer may not see: what an Access editor offers."""
    groups_hidden = unseen(c, viewer, auth)[1]
    return [{"id": gid, "name": name} for gid, name in sorted(names.items()) if gid not in groups_hidden]


def group_rows(c, viewer, groups, auth=None):
    """`[{id, name, parent}]` for a roster's `org_groups`, less the groups the viewer may not see; a parent they may
    not see reads as the nearest one above it they may (or null)."""
    from . import views
    roster = views.roster(c, shared=True)
    groups_hidden = unseen(c, viewer, auth)[1]
    return [{"id": gid, "name": g.get("name") or gid,
             "parent": visible_group(g.get("parent") or "", roster, groups_hidden) or None}
            for gid, g in groups.items() if gid not in groups_hidden]


def _exists(c, kind, ident):
    from . import views
    roster = views.roster(c, shared=True)
    if kind == "human":
        return bool(P.person(ident, roster) or H.human(c, ident))
    if kind == "group":
        return ident in (roster.get("org_groups") or {})
    return bool(H.bot(c, ident))


# ----------------------------------------------------------------------------- hiding unseen people and groups
def unseen(c, viewer, auth=None):
    """(person ids, group ids) the viewer may not see: what lists, the chart and the @mention picker leave out."""
    found = hidden(c, viewer, "see", ("human", "group"), auth)
    return ({t.split(":", 1)[1] for t in found if t.startswith("human:")},
            {t.split(":", 1)[1] for t in found if t.startswith("group:")})


def visible_group(gid, roster, groups_hidden):
    """The nearest group at or above `gid` the viewer may see, or "": an unseen group's members show in the group
    it is in."""
    for at in P.group_chain(gid, roster):
        if at not in groups_hidden:
            return at
    return ""


def _rehang(parent, parents, gone):
    """Walk a chart key (`p:<id>`, `g:<id>`, `b:<slug>`) up past the nodes the viewer may not see."""
    seen = set()
    while parent in gone and parent not in seen:
        seen.add(parent)
        parent = parents.get(parent, "")
    return parent


def redact_chart(view, roster, people_hidden, groups_hidden):
    """An org view (`people`, `bots`, `org_groups`, each row with `org_parent`) without the people and groups the
    viewer may not see. Whatever hung under one hangs from the nearest thing above it they may see; a `team` naming
    an unseen group names the group it is in; a `reports_to` naming an unseen person names the next one up."""
    if not people_hidden and not groups_hidden:
        return view
    gone = {"p:" + pid for pid in people_hidden} | {"g:" + gid for gid in groups_hidden}
    parents = {}
    for key, prefix in (("people", "p:"), ("bots", "b:"), ("org_groups", "g:")):
        for row in view.get(key) or []:
            parents[prefix + str(row.get("id"))] = row.get("org_parent") or ""
    out = dict(view)
    out["people"] = [_redact_row(row, roster, parents, gone, groups_hidden)
                     for row in view.get("people") or [] if row.get("id") not in people_hidden]
    if "bots" in view:
        # Rehung first (a hidden manager gives way to the next one up), then its own fields.
        out["bots"] = [redact_bot(_redact_row(row, roster, parents, gone, groups_hidden), roster, people_hidden,
                                  groups_hidden, team_was=row.get("team")) for row in view.get("bots") or []]
    if "seats" in view:
        out["seats"] = [_redact_row(row, roster, parents, gone, groups_hidden) for row in view.get("seats") or []]
    groups = []
    for row in view.get("org_groups") or []:
        if row.get("id") in groups_hidden:
            continue
        row = dict(row, org_parent=_rehang(row.get("org_parent") or "", parents, gone))
        if row.get("parent"):
            row["parent"] = visible_group(row["parent"], roster, groups_hidden)
        if row.get("reports_to") in people_hidden:
            row["reports_to"] = ""
        groups.append(row)
    out["org_groups"] = groups
    if "teams" in out:
        out["teams"] = {gid: v for gid, v in (out["teams"] or {}).items() if gid not in groups_hidden}
    return out


def redact_bots(c, viewer, rows, auth=None):
    """Bot rows (`/api/employees`) without the people and groups the viewer may not see: their place on the chart
    rehung as on `/api/v2/org`, and an unseen person left out of `operator`, `users` and `bot_owners`."""
    people_hidden, groups_hidden = unseen(c, viewer, auth)
    if not people_hidden and not groups_hidden:
        return rows
    from . import views
    roster, entries = views.roster(c, shared=True), views.entries(c, shared=True)
    archived = {row["slug"] for row in H.bots(c) if row.get("state") == "archived"}
    chart = P.org_view(roster, entries, archived, bare_people=True)
    view = redact_chart({"people": chart.get("people") or [], "org_groups": chart.get("org_groups") or [],
                         "bots": [{**row, "id": row.get("name")} for row in rows]}, roster, people_hidden, groups_hidden)
    for row in view["bots"]:
        row.pop("id", None)
    return view["bots"]


def bot_redactor(c, viewer, auth=None):
    """`redact_bot` for this viewer, as a function of one bot row; the row as it is when nothing is hidden from them.
    For the bot list and a bot's detail (`/api/v2/bots`), as `/api/v2/org` and `/api/employees` do through the chart."""
    people_hidden, groups_hidden = unseen(c, viewer, auth)
    if not people_hidden and not groups_hidden:
        return lambda row: row
    from . import views
    roster = views.roster(c, shared=True)
    return lambda row: redact_bot(row, roster, people_hidden, groups_hidden)


def redact_bot(row, roster, people_hidden, groups_hidden, team_was=None):
    """One bot's metadata less the people and groups the viewer may not see: its operator (and `operator_name`), its
    owners, users and co-owners, a `reports_to` naming such a person (and `reports_to_name`), and a group (`team`, and
    the `department` that names it) read as the nearest group above it they may see. `team_was`: its group before
    the chart rehung it."""
    if not people_hidden and not groups_hidden:
        return row
    row = dict(row)

    def gone(ref):
        ref = str(ref or "")
        return (ref[6:] if ref.startswith("human:") else ref) in people_hidden
    if row.get("operator") and gone(row["operator"]):
        row["operator"] = None
        if "operator_name" in row:
            row["operator_name"] = None
    for key in ("owners", "users", "bot_owners"):
        if isinstance(row.get(key), list):
            row[key] = [p for p in row[key] if not gone(p.get("id") if isinstance(p, dict) else p)]
    if row.get("reports_to") and gone(row["reports_to"]):
        row["reports_to"] = ""
        if "reports_to_name" in row:
            row["reports_to_name"] = None
    was = row.get("team") if team_was is None else team_was
    if was in groups_hidden:
        row["team"] = visible_group(was, roster, groups_hidden) or None
        if "department" in row:
            group = (roster.get("org_groups") or {}).get(row["team"]) or {}
            row["department"] = group.get("name") or row["team"] or ""
    return row


# ----------------------------------------------------------------------------- what a payload says about people
class Known:
    """Which of the people a payload names one reader may be told about, for REST answers and the stream alike: a
    person they may not see is named only to someone in the same conversation, or a party to the same task (its
    owner, its requester, or in its conversation). Elsewhere the field reads "" (null for `from_person`). The owner,
    the admins and whoever manages that person see them anyway, as everywhere."""

    TASK_FIELDS = ("owner", "requester", "closed_by", "origin_actor")

    def __init__(self, c, viewer, auth=None):
        self.c, self.viewer, self.auth = c, viewer, auth
        self.me = str(getattr(viewer, "actor", "") or "")
        self._people = self._strangers = None
        self._in = {}

    @property
    def people(self):
        """The viewer's unseen people, as actors."""
        if self._people is None:
            self._people = {"human:" + pid for pid in unseen(self.c, self.viewer, self.auth)[0]}
        return self._people

    def member(self, conversation_id):
        if not conversation_id:
            return False
        if conversation_id not in self._in:
            self._in[conversation_id] = self._in_conversation(conversation_id)
        return self._in[conversation_id]

    def _in_conversation(self, conversation_id):
        """On its participant list or its root's, or a member of its root, directly or through a group: a native DM lists
        only its starter as a participant, and thread_members holds the rest."""
        row = self.c.execute("SELECT participants_json, COALESCE(root_id,id) FROM conversations WHERE id=?",
                             (conversation_id,)).fetchone()
        if not row:
            return False
        if self.me in (H._json(row[0], []) or []):
            return True
        root = row[1]
        if root != conversation_id:
            above = self.c.execute("SELECT participants_json FROM conversations WHERE id=?", (root,)).fetchone()
            if above and self.me in (H._json(above[0], []) or []):
                return True
        from . import threads
        names = threads.Viewer(self.auth, self.viewer).member_names(self.c)
        return self.c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor IN (%s) LIMIT 1"
                              % ",".join("?" * len(names)), (root, *names)).fetchone() is not None

    def name(self, actor, conversation_id):
        """`actor`, or "" when they are unseen and the reader is not in this conversation."""
        return "" if actor in self.people and not self.member(conversation_id) else actor

    def reactions(self, marks, conversation_id):
        """A message's reactions: the count stays, the actors leave out whoever this reader may not be told about."""
        if not self.people or self.member(conversation_id):
            return marks
        return [{**mark, "actors": [a for a in mark["actors"] if a not in self.people]} for mark in marks]

    def members(self, rows, conversation_id):
        """A thread's member rows: someone not in the thread is told only of the teammates they may see, bots and groups
        too, and never who added whom they may not see."""
        if self.member(conversation_id):
            return rows
        if self._strangers is None:
            self._strangers = hidden(self.c, self.viewer, "see", KINDS, self.auth)
        return [{**row, "added_by": "" if row.get("added_by") in self._strangers else row.get("added_by")}
                for row in rows if row["actor"] not in self._strangers]

    def message(self, message):
        if not self.people or not isinstance(message, dict):
            return message
        named = [key for key in ("from_actor", "to_actor") if message.get(key) in self.people]
        if not named or self.member(message.get("conversation_id")):
            return message
        return {**message, **{key: "" for key in named}}

    def messages(self, messages):
        return [self.message(m) for m in messages] if self.people else messages

    def conversation(self, conversation):
        if not self.people or not isinstance(conversation, dict) or self.me in (conversation.get("participants") or []):
            return conversation
        return {**conversation, "participants": [a for a in conversation.get("participants") or []
                                                 if a not in self.people]}

    def task(self, task):
        if not self.people or not isinstance(task, dict):
            return task
        named = [key for key in self.TASK_FIELDS if task.get(key) in self.people]
        origin = task.get("from_person") in self.people
        if not named and not origin:
            return task
        if self.me in (task.get("owner"), task.get("requester")) or self.member(task.get("conversation_id")):
            return task
        return {**task, **{key: "" for key in named}, **({"from_person": None} if origin else {})}

    def tasks(self, tasks):
        return [self.task(t) for t in tasks] if self.people else tasks


def _redact_row(row, roster, parents, gone, groups_hidden):
    row = dict(row)
    if "org_parent" in row:
        row["org_parent"] = _rehang(row.get("org_parent") or "", parents, gone)
    if row.get("team") in groups_hidden:
        row["team"] = visible_group(row["team"], roster, groups_hidden)
    reports = str(row.get("reports_to") or "")
    bare = reports[6:] if reports.startswith("human:") else reports
    if bare and "p:" + bare in gone:
        up = _rehang("p:" + bare, parents, gone)
        row["reports_to"] = ((("human:" if reports.startswith("human:") else "") + up[2:])
                             if up.startswith("p:") else "")
    if row.get("primary_for"):
        row["primary_for"] = [g for g in row["primary_for"] if g not in groups_hidden]
    return row
