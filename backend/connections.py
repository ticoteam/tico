"""Connections: every way the outside world reaches this company, in one list (Library › Connections).

`GET /api/v2/connections` gathers rows from where each connection already lives: the Slack app, the GitHub App, the
mail bots' mailboxes, the Email & Phone inboxes, the meeting importers, incoming webhooks (triggers), outgoing
webhooks, the physical-mail intake and Listening. A row says what it is, whether it works, whose it is, who reads what
it brings in, the bots on it and where to change it:

    {id, kind, name, detail, status, scope, readers, bots: [{slug, name}], more_bots, configure, can_configure}

`scope` is `company`, `team` or `person`; `readers` is who reads what comes in, in words. `status` is `on`, `off`,
`waiting`, `error` or `paused`. `configure` is the app address of the existing card that changes it.

Who sees which rows: the owner and admins see every row; anyone else sees their own (their mailbox, the webhooks they
made) and the company's rows they may read: Slack and GitHub once connected, an Email & Phone inbox in their audience
(backend/inbound.py), meeting importers for whoever reads meetings, incoming webhooks except Finance's, the
physical-mail intake, and Listening when they receive or read a destination. Bots listed are only ones the reader may
see. The list is paged (`limit`, `cursor`) and byte-capped like every list. Each source reads its rows in a stable
order with the reader's visibility already applied, from where the cursor left off (`<source>` or `<source>:<key>`), so
no row is lost past a page and no page is empty while more follow.
"""

from fastapi import Query, Request

from .store import H, Problem, encode

LIMIT_MAX = 100
BYTES_MAX = 96 * 1024
BOTS_MAX = 20            # bots named on one row; `more_bots` counts the rest
SCAN_MAX = 500           # bots read for one row (GitHub repository access)
KINDS = ("slack", "github", "mailbox", "inbox", "meetings", "trigger", "webhook", "postal", "listening", "slack_link",
         "peer")


def table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def columns(c, name):
    return {r[1] for r in c.execute(f"PRAGMA table_info({name})")}


class View:
    """One reader: who they are and what they may see, worked out once."""

    def __init__(self, c, auth, who, settings):
        self.c, self.auth, self.who, self.settings = c, auth, who, settings
        self.person = who.role in ("owner", "human")
        self.admin = who.role == "owner" or (who.role == "human" and bool(auth.bot_admin(who)))
        self._seen = None
        self._names = {}
        self._hidden = None

    @property
    def hidden(self):
        """The Email & Phone inboxes narrowed to a group the reader is not in (backend/inbound.py)."""
        if self._hidden is None:
            from . import inbound
            self._hidden = inbound.hidden_inboxes(self.c, self.who, self.auth) if table(self.c, "inbound_inboxes") \
                else set()
        return self._hidden

    def bots(self, slugs):
        """The bots among `slugs` the reader may see, named, at most BOTS_MAX; and how many more there are."""
        slugs = [s for s in dict.fromkeys(slugs) if s]
        if not slugs:
            return [], 0
        seen = self.auth.bot_accesses(self.c, self.who, slugs)
        shown = [s for s in slugs if (seen.get(s) or {}).get("see")]
        names = self.names("bot:" + s for s in shown[:BOTS_MAX])
        return [{"slug": s, "name": names.get("bot:" + s) or s} for s in shown[:BOTS_MAX]], max(0, len(shown) - BOTS_MAX)

    def names(self, actors):
        from . import names
        wanted = {a for a in actors if a and a not in self._names}
        if wanted:
            self._names.update(names.lookup(self.c, wanted))
        return self._names

    def name(self, actor):
        return self.names([actor]).get(actor) or H.actor_id(actor)


def row(kind, ident, name, status, scope, readers, configure, *, detail="", bots=(), more=0, can=False):
    return {"id": kind + ":" + ident if ident else kind, "kind": kind, "name": name, "detail": detail, "status": status,
            "scope": scope, "readers": readers, "bots": list(bots), "more_bots": more, "configure": configure,
            "can_configure": bool(can)}


def keyed(rows, after, n):
    """At most `n` of a small source's rows past `after`, as (key, row) by key: its key is the row id's ident."""
    pairs = sorted((r["id"].partition(":")[2], r) for r in rows)
    return [(k, r) for k, r in pairs if after is None or k > after][:n]


# ---- each source: (v, after, n) -> up to n (key, row) pairs past `after`, by key, only rows the reader may see

def slack(v, after, n):
    return keyed(slack_rows(v), after, n)


def slack_rows(v):
    from . import slack_app
    state = slack_app.status(v.c)
    if not state["configured"] and not v.admin:
        return []
    readers = []
    if table(v.c, "slack_channels"):
        for (raw,) in v.c.execute("SELECT readers_json FROM slack_channels"):
            readers += [str(r).removeprefix("bot:") for r in (H._json(raw, []) or []) if isinstance(r, str)]
    bots, more = v.bots(readers)
    status = {"off": "off", "waiting": "waiting", "connected": "on"}.get(state["state"], "error")
    return [row("slack", "", "Slack", status, "company", "Everyone", "#/library/tools/slack",
                detail=state["message"] if status == "error" else "", bots=bots, more=more, can=v.who.role == "owner")]


def github(v, after, n):
    return keyed(github_rows(v), after, n)


def github_rows(v):
    app_row = v.c.execute("SELECT org, installation_id FROM github_app WHERE id='app'").fetchone() \
        if table(v.c, "github_app") else None
    if not app_row and not v.admin:
        return []
    slugs = [r[0] for r in v.c.execute("SELECT DISTINCT bot FROM bot_repo_access ORDER BY bot LIMIT ?", (SCAN_MAX,))] \
        if app_row and table(v.c, "bot_repo_access") else []
    bots, more = v.bots(slugs)
    status = "off" if not app_row else "on" if app_row["installation_id"] else "waiting"
    return [row("github", "", "GitHub", status, "company", "Bots with repository access", "#/library/tools/github",
                detail=app_row["org"] if app_row else "", bots=bots, more=more, can=v.who.role == "owner")]


def mailboxes(v, after, n):
    return keyed(mailbox_rows(v), after, n)


def mailbox_rows(v):
    """The mail bots' mailboxes: each person's inbox bot and the mailbox it keeps (one per person at most)."""
    from . import mail, routines
    boxes = routines.message_bot_mailboxes(v.c)
    if not boxes:
        return []
    mine = set() if v.admin else set(mail.visible_addresses(v.c, v.who)) if v.person else set()
    synced = {r["address"]: r for r in v.c.execute("SELECT address, synced_at, error FROM mail_mailboxes")}
    out = []
    for box in boxes:
        address = box["address"]
        if not (v.admin or address in mine or v.who.actor == "bot:" + box["bot"]):
            continue
        state = synced.get(address)
        status = "error" if state and state["error"] else "on" if state and state["synced_at"] else "waiting"
        bots, more = v.bots([box["bot"]])
        out.append(row("mailbox", address, address, status, "person", v.name("human:" + box["person_id"]),
                       "#/settings/inbox", detail=state["error"] or "" if state else "", bots=bots, more=more,
                       can=v.admin or v.who.actor == "human:" + box["person_id"]))
    return out


def inboxes(v, after, n):
    """Email & Phone inboxes the reader is in the audience of."""
    from . import groups as G
    from . import inbound
    if not table(v.c, "inbound_inboxes"):
        return []
    clause, values = inbound.without("inbox_id", v.hidden)
    groups = G._roster(v.c).get("org_groups") or {}
    out = []
    for r in v.c.execute("SELECT * FROM inbound_inboxes WHERE state<>'released' AND inbox_id>? AND " + clause +
                         " ORDER BY inbox_id LIMIT ?", (after or "", *values, n)):
        team = r["audience"] == "team" and r["group_id"]
        readers = (groups.get(team) or {}).get("name") or team if team else "Everyone"
        status = "on" if r["state"] == "active" else r["state"] if r["state"] in ("off", "error", "paused") else "waiting"
        out.append((r["inbox_id"], row("inbox", r["inbox_id"], r["name"], status, "team" if team else "company",
                                       readers, "#/settings/inbound", detail=r["address"], can=v.admin)))
    return out


def meetings(v, after, n):
    return keyed(meeting_rows(v), after, n)


def meeting_rows(v):
    from .meeting_importers import IMPORTERS, status_of
    from .updates import meeting_reader
    if not (v.admin or meeting_reader(v.who)):
        return []
    saved = {r["source"]: dict(r) for r in v.c.execute("SELECT * FROM meeting_importers")}
    now, out = H.now(), []
    for source, spec in IMPORTERS.items():
        cfg = saved.get(source)
        if not cfg:
            continue
        health = v.c.execute("SELECT last_success,last_error,detail_json FROM service_health WHERE service=?",
                             ("recording:" + source,)).fetchone()
        state = status_of(bool(cfg.get("enabled")), health, spec["interval"], now)
        status = {"syncing": "on", "delayed": "error"}.get(state, state)
        out.append(row("meetings", source, spec["name"], status, "company", "Meeting attendees", "#/meetings",
                       can=v.who.role == "owner"))
    return out


def triggers(v, after, n):
    """Incoming webhooks (backend/triggers.py): everyone may read them, Finance's only a finance admin."""
    from . import triggers as T
    if not table(v.c, "triggers") or not (v.person or v.who.role == "bot"):
        return []
    sql = "SELECT * FROM triggers WHERE deleted_at IS NULL AND id>?"
    if "consumer" in columns(v.c, "triggers") and not T._finance_admin(v.c, v.who):
        sql += " AND coalesce(consumer,'')<>'finance'"
    out = []
    for r in v.c.execute(sql + " ORDER BY id LIMIT ?", (after or "", n)):
        out.append((r["id"], row("trigger", r["id"], r["name"], "on", "company", "Everyone", "#/settings/webhooks",
                                 detail="Incoming webhook",
                                 can=v.person and (v.admin or r["created_by"] == v.who.actor))))
    return out


def webhooks(v, after, n):
    """Outgoing webhooks (backend/webhooks.py): an admin sees every endpoint, anyone else their own."""
    if not table(v.c, "webhooks"):
        return []
    sql, args = "SELECT * FROM webhooks WHERE deleted_at IS NULL AND id>?", [after or ""]
    if not v.admin:
        sql += " AND (created_by=? OR requested_by=?)"
        args += [v.who.actor, v.who.actor]
    out = []
    for r in v.c.execute(sql + " ORDER BY id LIMIT ?", (*args, n)):
        status = "paused" if r["paused_reason"] else "on" if r["enabled"] else "off"
        out.append((r["id"], row("webhook", r["id"], r["name"], status, "person", v.name(r["created_by"]),
                                 "#/settings/webhooks/outgoing", detail=r["host"],
                                 can=v.person and (v.admin or r["created_by"] == v.who.actor))))
    return out


def postal(v, after, n):
    return keyed(postal_rows(v), after, n)


def postal_rows(v):
    """Physical mail, when this company takes it: letters go to whoever may read the task each is filed on."""
    if not table(v.c, "mail_intake") or not v.person:
        return []
    intake = v.c.execute("SELECT m.inbox_id, i.address FROM mail_intake m LEFT JOIN inbound_inboxes i "
                         "ON i.inbox_id=m.inbox_id WHERE m.singleton=1").fetchone()
    if intake and intake["inbox_id"] in v.hidden:          # its inbox is narrowed to a group the reader is not in
        intake = {"inbox_id": intake["inbox_id"], "address": ""}
    letters = table(v.c, "mail_items") and v.c.execute("SELECT 1 FROM mail_items LIMIT 1").fetchone()
    if not intake and not letters:
        return []
    return [row("postal", "", "Physical mail", "on", "company", "Each letter's task readers", "#/settings/mail",
                detail=(intake["address"] or "") if intake else "", can=v.admin)]


def listening(v, after, n):
    return keyed(listening_rows(v), after, n)


def listening_rows(v):
    """Listening: one row while any destination is set, for the owner, admins and whoever receives or reads one."""
    from . import listening as L
    try:
        dests = L.destination_list(v.c, v.settings)
    except Exception:  # noqa: BLE001 - a broken question set leaves the row out, never the page
        return []
    dests = dests["destinations"]
    if not dests:
        return []
    actors = set()
    for d in dests:
        actors.add(d.get("receiver") or "")
        actors.update(d.get("readers") or [])
    if not (v.admin or v.who.actor in actors):
        return []
    last = v.c.execute("SELECT status FROM listen_runs ORDER BY started_at DESC LIMIT 1").fetchone() \
        if table(v.c, "listen_runs") else None
    status = "waiting" if not last else "error" if last["status"] in ("error", "failed") else "on"
    bots, more = v.bots(H.actor_id(a) for a in sorted(actors) if a.startswith("bot:"))
    people = [a for a in sorted(actors) if a.startswith("human:")]
    readers = ", ".join(v.name(a) for a in people[:5]) or "Its bots"
    return [row("listening", "", "Listening", status, "company", readers, "#/strategy/market/listening",
                detail=f"{len(dests)} destination{'s' if len(dests) != 1 else ''}", bots=bots, more=more,
                can=v.who.role == "owner")]


def thread_links(v, after, n):
    return keyed(thread_link_rows(v), after, n)


def thread_link_rows(v):
    """Slack channels linked to threads (backend/slack_threads.py): a read-only row for each link whose thread the reader
    can read. Only admins may change a link, so only they get Configure. Nothing here asks Slack."""
    from . import threads as TH
    if not table(v.c, "slack_thread_links"):
        return []
    reader = TH.viewer(v.auth, v.who)
    out = []
    for r in v.c.execute("SELECT * FROM slack_thread_links WHERE state IN ('active','suspended') ORDER BY id LIMIT ?",
                         (SCAN_MAX,)):
        if not TH.readable(v.c, reader, r["thread_id"]):
            continue
        company = r["audience"] == "company"
        out.append(row("slack_link", r["id"], "#" + str(r["channel_name"] or r["channel_ref"]).lstrip("#"),
                       "on" if r["state"] == "active" else "paused", "company" if company else "team",
                       "Everyone" if company else "Thread members", f"#/thread/{r['thread_id']}",
                       detail="Slack channel", can=v.admin))
    return out


def peers(v, after, n):
    return keyed(peer_rows(v), after, n)


def peer_rows(v):
    """Other companies' Tico installs this one peers with. The owner sees every peering; anyone else sees the peers
    that carry a thread they can read, and no Configure."""
    from . import threads as TH
    if not table(v.c, "federation_peerings"):
        return []
    found = {r["peer_install"]: dict(r) for r in v.c.execute(
        "SELECT * FROM federation_peerings WHERE status<>'revoked' ORDER BY peer_install LIMIT ?", (SCAN_MAX,))}
    if v.who.role != "owner":
        reader = TH.viewer(v.auth, v.who)
        carried = set()
        for t in v.c.execute("SELECT thread_id, home_install FROM federation_threads ORDER BY thread_id LIMIT ?",
                             (SCAN_MAX,)):
            if not TH.readable(v.c, reader, t["thread_id"]):
                continue
            carried.add(t["home_install"])
            carried.update(m["install"] for m in v.c.execute(
                "SELECT install FROM federation_members WHERE thread_id=? AND status='active'", (t["thread_id"],)))
        found = {install: p for install, p in found.items() if install in carried}
    out = []
    for install, p in sorted(found.items()):
        status = ("waiting" if p["status"] == "pending" else "paused" if p["paused_at"] else
                  "on" if p["status"] == "active" else "error")
        out.append(row("peer", install, p["peer_company"] or install, status, "company", "Shared threads",
                       "#/library/connections", detail=install, can=v.who.role == "owner"))
    return out


SOURCES = (slack, github, mailboxes, inboxes, meetings, triggers, webhooks, postal, listening, thread_links, peers)


def view(c, auth, who, settings):
    if who.support or who.role not in ("owner", "human", "bot"):
        raise Problem("forbidden", "Company members and agents only", 403)
    return View(c, auth, who, settings)


def gather(c, auth, who, settings):
    """Every row the reader may see, in list order (tests and tools; the endpoint pages)."""
    out, cursor = [], ""
    while True:
        got = page(view(c, auth, who, settings), LIMIT_MAX, cursor)
        out += got["connections"]
        if not got["next"]:
            return out
        cursor = got["next"]


def mark(index, key):
    return str(index) if key is None else f"{index}:{key}"


def page(v, limit, cursor):
    """One page from `cursor`: the sources in order, each from where the last page stopped in it, until `limit` rows
    or BYTES_MAX. `next` names the source and the last key shown in it."""
    head, colon, after = str(cursor or "").partition(":")
    try:
        start = int(head or 0)
    except ValueError:
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422) from None
    if not 0 <= start < len(SOURCES):
        raise Problem("invalid_request", "cursor is the `next` of the page before", 422)
    limit = max(1, min(limit, LIMIT_MAX))
    shown, used = [], 0
    for index in range(start, len(SOURCES)):
        last = (after if colon else None) if index == start else None
        for key, item in SOURCES[index](v, last, limit - len(shown) + 1):
            size = len(encode(item).encode())
            if len(shown) == limit or (shown and used + size > BYTES_MAX):
                return {"connections": shown, "next": mark(index, last)}
            shown.append(item)
            used += size
            last = key
    return {"connections": shown, "next": None}


def install(app, store, auth, settings):
    @app.get("/api/v2/connections")
    def connections_list(request: Request, limit: int = Query(default=50, ge=1, le=LIMIT_MAX),
                         cursor: str = Query(default="", max_length=400)):
        """Every connection the caller may see: Slack, GitHub, mail bots' mailboxes, Email & Phone inboxes, meeting
        importers, incoming and outgoing webhooks, physical mail and Listening. Each row: name, status, scope, who reads
        it, its bots and where to configure it. The owner and admins see every row; anyone else their own and the
        company's they may read. Follow `next` with `cursor`."""
        who = request.state.identity
        with store.read_transaction() as c:
            return page(view(c, auth, who, settings), limit, cursor)

