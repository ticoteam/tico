"""The Slack bridge for native threads (`_threads-plan.md` §5, `docs/dev/threads-contract.md`).

A Slack channel is linked to one Tico thread, and the two talk both ways.

- Linking is an audience operation. A public channel links to a `company` thread, a private one to a `members`
  thread, and every channel member must map to a roster person who can read that thread. An admin's request is stored
  as `requested` (the API process holds no Slack token); the gateway checks it (`Bridge.gate`) and activates or refuses.
- Inbound: a channel message is written as the person it maps to, through `threads.post` with `via='slack'` and an
  origin id. A Slack thread reply becomes a sub-thread. An edit or delete is a revision or a tombstone
  (`threads.apply_revision`). A Block holds the message: nothing is written, and the author gets an ephemeral note.
  A held edit or history message is held the same way.
- Outbound: each `slack:<channel>` delivery is posted as the Tico app, attributed ("*Ana* · via Tico"), never as the
  person. A revision is a `chat.update`, a tombstone a `chat.delete`. A sub-thread two levels or deeper is flattened
  into its root's Slack thread with a "↳ <title>" line. Every send carries its delivery id in the message metadata, so
  an `uncertain` send is found in the channel's history instead of being sent twice.
- A linked channel that becomes Slack Connect, gains a member who does not map, or whose thread narrows, is suspended
  and gets one Needs-you item for the admin who asked. Nothing moves out while it is suspended; a channel message that
  arrives meanwhile is queued (`slack_inbound_queue`) and carried in, in order, when the link is active again.
- Every inbound write goes through the org rule check as the person (`org_rule_check.inbound`, held as that person,
  with its provenance: the Slack channel and ts). A Block holds it; Send anyway can release it (`replay_held`).
- History is opt-in: the last 30 days, at most 5,000 messages, written with `refs.import`, so nothing wakes.

Core is `backend/threads.py` (the contract's entry points: `viewer`, `readable`, `post`, `apply_revision`,
`add_outside_delivery`, `outside_due`, `settle_delivery`, `follow`). Tests use the real core and fake only Slack.
"""

import datetime as dt
import json
import logging
import re
import sqlite3

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb as H
from . import org_rule_check as OR
from . import screening
from . import slack_channels as SC
from . import slack_gateway as G
from .auth import Identity
from .store import Problem

LOG = logging.getLogger("tico.slack.threads")
HISTORY_DAYS = 30
HISTORY_CAP = 5000              # messages, top-level and replies together
HISTORY_PAGE = 200              # one history or replies call
HISTORY_PAGES = 50              # the most history pages one import reads
HISTORY_RETRY = 60              # seconds before a pending import is tried again
CHECK_SECONDS = 600             # how often linked channels are checked against their thread
SEND_CHECK_SECONDS = 60         # the Slack side of a link (its channel and members) is asked at most this often before sends
RECONCILE_PAGES = 50            # history or replies pages one uncertain send's lookup reads, to the end of the window
USER_CACHE_SECONDS = 3600       # a Slack user's users.info answer is reused this long (membership checks, every message)
OUTSIDE_BATCH = 500             # core's outside_due cap: one read covers every Slack channel, so it is the largest
SEND_BATCH = 50                 # deliveries read per channel per pass
RECONCILE_AFTER = 60            # an uncertain send waits this long before the history lookup
RECONCILE_WINDOW = 120          # the lookup starts this long before the uncertain send
MAX_SEND_TRIES = 2              # an uncertain post found absent this many times fails
MIRROR_KINDS = ("say", "ask", "answer")
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{6,}$")
# Slack refusals that mean the link cannot work (the channel, the app's access, the token). Any other refusal fails
# one message only.
CHANNEL_ERRORS = frozenset({"channel_not_found", "not_in_channel", "is_archived", "missing_scope", "restricted_action",
                            "invalid_auth", "account_inactive", "token_revoked", "not_authed", "is_ext_shared"})
MISSING_CHANNEL = ("channel_not_found", "not_in_channel")
MAX_DRAIN = 500                 # queued inbound events carried in one pass of one link
STALLED = frozenset({"denied", "retry"})   # outcomes that keep the queue where it is

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS slack_thread_links (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  channel_ref TEXT NOT NULL,
  channel_id TEXT NOT NULL DEFAULT '',
  channel_name TEXT NOT NULL DEFAULT '',
  channel_private INTEGER NOT NULL DEFAULT 0,
  audience TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL CHECK (state IN ('requested','active','suspended','refused','unlinked')),
  reason TEXT NOT NULL DEFAULT '',
  history_state TEXT NOT NULL DEFAULT 'off' CHECK (history_state IN ('off','pending','running','done','failed')),
  history_count INTEGER NOT NULL DEFAULT 0,
  linked_by TEXT NOT NULL,
  needs_task_id TEXT,
  failure_task_id TEXT,
  checked_at TEXT,
  activated_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS slack_thread_links_thread ON slack_thread_links(thread_id, state)",
    "CREATE INDEX IF NOT EXISTS slack_thread_links_channel ON slack_thread_links(channel_id, state)",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_thread_links_open_thread ON slack_thread_links(thread_id) "
    "WHERE state IN ('requested','active','suspended')",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_thread_links_open_channel ON slack_thread_links(channel_id) "
    "WHERE state IN ('active','suspended') AND channel_id <> ''",
    """CREATE TABLE IF NOT EXISTS slack_link_members (
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  slack_user_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (link_id, slack_user_id))""",
    """CREATE TABLE IF NOT EXISTS slack_message_links (
  id TEXT PRIMARY KEY,
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  channel_id TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  ts TEXT NOT NULL DEFAULT '',
  thread_ts TEXT NOT NULL DEFAULT '',
  message_id TEXT,
  delivery_id TEXT,
  origin_id TEXT,
  actor TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('sent','held','deleted','sending','uncertain','ready','failed')),
  edit_ts TEXT NOT NULL DEFAULT '',
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_message_links_ts ON slack_message_links(channel_id, ts) WHERE ts <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS slack_message_links_out ON slack_message_links(link_id, message_id) "
    "WHERE direction = 'out'",
    "CREATE INDEX IF NOT EXISTS slack_message_links_message ON slack_message_links(message_id)",
    "CREATE INDEX IF NOT EXISTS slack_message_links_state ON slack_message_links(state, updated_at)",
    """CREATE TABLE IF NOT EXISTS slack_inbound_queue (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  link_id TEXT NOT NULL REFERENCES slack_thread_links(id),
  event_key TEXT NOT NULL UNIQUE,
  event_json TEXT NOT NULL,
  created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS slack_inbound_queue_link ON slack_inbound_queue(link_id, seq)",
]


def ensure(c):
    """The bridge's tables, idempotent like the other modules' (`strategy.ensure`). A column added after a table was first
    made is added here, once."""
    for statement in SCHEMA:
        c.execute(statement)
    if not any(r[1] == "failure_task_id" for r in c.execute("PRAGMA table_info(slack_thread_links)")):
        c.execute("ALTER TABLE slack_thread_links ADD COLUMN failure_task_id TEXT")


def outside_recipient(channel_id):
    return f"slack:{channel_id}"


def _core():
    from . import threads
    return threads


def _epoch(iso):
    try:
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _refs(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _one(c, sql, args=()):
    row = c.execute(sql, args).fetchone()
    return dict(row) if row else None


def _link(c, link_id):
    return _one(c, "SELECT * FROM slack_thread_links WHERE id=?", (link_id,))


def _thread(c, thread_id):
    return _one(c, "SELECT * FROM conversations WHERE id=?", (thread_id,))


def _linked(c, channel_id):
    return _one(c, "SELECT * FROM slack_thread_links WHERE channel_id=? AND state IN ('active','suspended')",
                (channel_id,))


def _seen(c, channel_id, ts):
    return c.execute("SELECT 1 FROM slack_message_links WHERE channel_id=? AND ts=?", (channel_id, ts)).fetchone() is not None


def _record(c, link, event, direction, state, *, message_id=None, origin_id=None, actor="", error=None):
    now = H.now()
    c.execute("INSERT INTO slack_message_links (id,link_id,channel_id,direction,ts,thread_ts,message_id,origin_id,actor,"
              "state,error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (H.new_id(), link["id"], event["channel"], direction, event["ts"], event.get("thread_ts") or "",
               message_id, origin_id, actor, state, error and str(error)[:300], now, now))


def _due(c, channel_id, limit=SEND_BATCH):
    """The pending deliveries to one channel, oldest first. The limit applies after the filter, to this channel alone:
    core's outside_due reads every Slack destination at once, so a busy channel would hold the others back."""
    return [dict(r) for r in c.execute(
        "SELECT d.*, m.body, m.from_actor, m.origin_id, m.deleted_at, m.conversation_id FROM message_deliveries d "
        "JOIN messages m ON m.id=d.message_id WHERE d.recipient=? AND d.state='pending' "
        "AND (d.next_attempt_at IS NULL OR d.next_attempt_at<=?) ORDER BY d.created_at LIMIT ?",
        (outside_recipient(channel_id), H.now(), max(1, min(int(limit), 500))))]


def _sweep(core, c, channel_id, limit=OUTSIDE_BATCH):
    """Every pending delivery to the channel is settled `moot`: nothing is sent after an unlink."""
    for _ in range(50):
        due = _due(c, channel_id, limit)
        if not due:
            return
        for d in due:
            core.settle_delivery(c, d["id"], "moot", error="unlinked")


def _ts_key(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _newer_edit(row, edited):
    """Whether an edit's Slack ts is newer than the last edit accepted for its message: Slack's edit ts is the order."""
    return not edited or not row["edit_ts"] or _ts_key(edited) > _ts_key(row["edit_ts"])


def failure_item(c, link_id, code):
    """One Needs-you item for a link's terminal send failure, for the admin who linked it. It is reused while it is open,
    so a run of failed messages on one channel files one item, and a new one is filed once the last is resolved."""
    row = _link(c, link_id)
    if not row:
        return
    task = H.task(c, row["failure_task_id"]) if row["failure_task_id"] else None
    if task and task["status"] in H.ACTIVE_STATUSES:
        return
    name = (row["channel_name"] or row["channel_id"]).lstrip("#")
    title = f"A message to Slack #{name} did not post"
    body = (f"Slack refused it ({str(code)[:100]}), and Tico stopped trying that message. It is still in the thread. "
            "Check the channel and the link in the thread's settings.")
    try:
        created = H.task_create(c, H.KEEPER, title, body, row["linked_by"], lint=False)
        c.execute("UPDATE slack_thread_links SET failure_task_id=? WHERE id=?", (created["id"], link_id))
    except H.Refused:
        LOG.warning("Could not file the Needs-you item for a failed Slack send")


def _members(c, link_id):
    """The person ids mapped to a link's channel members."""
    return [r["person_id"] for r in c.execute("SELECT person_id FROM slack_link_members WHERE link_id=? "
                                              "ORDER BY person_id", (link_id,))]


def _subtree(c, root_id):
    """The root and every sub-thread under it (each sub-thread's root_id is the root's id): what a link covers."""
    return [r[0] for r in c.execute("SELECT id FROM conversations WHERE root_id=? OR id=?", (root_id, root_id))]


def _auth(store):
    from .auth import Auth
    return Auth(store)


def _can_see(c, core, auth, who, thread_id):
    """Whether `who` reads the thread. A hidden thread and a missing one answer the same, so the admin routes give one 404."""
    return bool(thread_id) and bool(core.readable(c, core.viewer(auth, who), thread_id))


def _identity_viewer(c, core, actor):
    """A viewer for an actor without an Auth (the replay check runs under the write lock): a person's identity with the
    address the roster holds, which is what readability needs here."""
    if str(actor).startswith("bot:"):
        return core.viewer(None, Identity(actor, "bot"))
    human = H.human(c, H.actor_id(actor)) or {}
    return core.viewer(None, Identity(actor, "human", email=str(human.get("email") or "").lower()))


def audience(c, core, viewer_for, link_id, thread_id, author, text):
    """The recipients a Slack message is checked against (an org rule's With is matched on them): the channel's mapped
    members who read the thread, and the people and bots the text @mentions who read it too. Sorted, never the author."""
    found = {H.human_actor(pid) for pid in _members(c, link_id)}
    found.update(actor for actor, _kind in core.mentions(c, thread_id, text, author=author))
    out = []
    for actor in sorted(found):
        if actor == author:
            continue
        try:
            viewer = viewer_for(actor)
        except Problem:
            continue                            # not a person who is in good standing: not in the audience
        if core.readable(c, viewer, thread_id):
            out.append(actor)
    return out


def _audit(c, who, link_id, before, after):
    """The link's audit row (`audit_log`), beside the event the link writes."""
    from . import audit_log
    audit_log.write(c, who.actor, "slack_link:" + str(link_id), before, after)


def view(row):
    """What the API and the CLI show for one link."""
    return {"id": row["id"], "thread_id": row["thread_id"],
            "channel": {"id": row["channel_id"], "name": row["channel_name"] or row["channel_ref"],
                        "private": bool(row["channel_private"])},
            "audience": row["audience"], "state": row["state"], "reason": row["reason"],
            "history": {"state": row["history_state"], "count": row["history_count"]},
            "linked_by": row["linked_by"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


# ----------------------------------------------------------------------------- requests (the API process, no Slack)
def request_link(store, who, thread_id, channel_ref, history=False, auth=None):
    """Ask for a link. The gateway checks it and activates it. Writes one audit row. A thread the admin cannot read is
    the same 404 as a missing one."""
    ref = str(channel_ref or "").strip()[:200]
    if not ref:
        raise Problem("invalid", "Give a channel name like #customer-success, or its id", 422)
    core = _core()
    with store.transaction() as c:
        thread = _thread(c, thread_id) if _can_see(c, core, auth or _auth(store), who, thread_id) else None
        if not thread:
            raise Problem("not_found", "That thread was not found", 404)
        if (thread.get("root_id") or thread["id"]) != thread["id"]:
            raise Problem("not_root", "Link the root thread: a link covers the whole thread and its sub-threads", 422)
        if thread.get("audience") not in ("company", "members"):
            raise Problem("audience", "Only a company thread (for a public channel) or a members thread (for a private "
                          "one) can be linked to Slack", 422)
        if core.shared_root(c, thread_id):
            raise Problem("shared_thread", "This thread is shared with another company, so it can't be linked to Slack: "
                          "their words would reach your workspace's channel", 409)
        if c.execute("SELECT 1 FROM slack_thread_links WHERE thread_id=? AND state IN ('requested','active','suspended')",
                     (thread_id,)).fetchone():
            raise Problem("conflict", "This thread is already linked to a Slack channel", 409)
        now, link_id = H.now(), H.new_id()
        c.execute("INSERT INTO slack_thread_links (id,thread_id,channel_ref,audience,state,history_state,linked_by,"
                  "created_at,updated_at) VALUES (?,?,?,?, 'requested', ?,?,?,?)",
                  (link_id, thread_id, ref, thread["audience"], "pending" if history else "off", who.actor, now, now))
        H.event(c, who.actor, "slack.link.request", link_id, {"thread": thread_id, "history": bool(history)})
        _audit(c, who, link_id, None, {"thread": thread_id, "channel": ref, "history": bool(history)})
        return view(_link(c, link_id))


def unlink_link(store, who, link_id, core=None, auth=None):
    """Stop a link. Its unsent deliveries are settled `moot`, and the channel leaves every node of the thread's subtree in
    the same transaction, so nothing is sent after the unlink. A link the admin cannot see is the same 404 as a missing one."""
    core = core or _core()
    with store.transaction() as c:
        row = _link(c, link_id)
        if not row or row["state"] == "unlinked" or not _can_see(c, core, auth or _auth(store), who, row["thread_id"]):
            raise Problem("not_found", "That Slack link was not found", 404)
        c.execute("UPDATE slack_thread_links SET state='unlinked', updated_at=? WHERE id=?", (H.now(), link_id))
        c.execute("DELETE FROM slack_inbound_queue WHERE link_id=?", (link_id,))
        if row["channel_id"]:
            _sweep(core, c, row["channel_id"])
            for node in _subtree(c, row["thread_id"]):
                core.remove_outside_member(c, H.KEEPER, node, outside_recipient(row["channel_id"]))
        H.event(c, who.actor, "slack.unlink", link_id, {"channel": row["channel_id"]})
        _audit(c, who, link_id, {"state": row["state"]}, {"state": "unlinked"})
        return view(_link(c, link_id))


def resume_link(store, who, link_id, auth=None):
    """Check a suspended or refused link again: the gateway runs it through the same gate."""
    core = _core()
    with store.transaction() as c:
        row = _link(c, link_id)
        if not row or not _can_see(c, core, auth or _auth(store), who, row["thread_id"]):
            raise Problem("not_found", "That Slack link was not found", 404)
        if row["state"] not in ("suspended", "refused"):
            raise Problem("conflict", "Only a suspended or refused link can be checked again", 409)
        c.execute("UPDATE slack_thread_links SET state='requested', reason='', updated_at=? WHERE id=?", (H.now(), link_id))
        H.event(c, who.actor, "slack.link.request", link_id, {"resume": True})
        _audit(c, who, link_id, {"state": row["state"]}, {"state": "requested"})
        return view(_link(c, link_id))


_LINKS_SCAN = 500


def list_links(store, who, thread_id="", limit=50, cursor="", auth=None):
    """The links the admin can see, newest first, a page at a time: a listed thread must be readable (a hidden or missing
    one is a 404), and an unfiltered listing leaves out the links to threads the admin cannot read. `next_cursor` (the
    last row read's `created_at|id`) reads on; one page reads at most `_LINKS_SCAN` rows, so a run of hidden links ends
    a short page with a cursor rather than an unbounded scan."""
    core, limit = _core(), max(1, min(int(limit or 50), 100))
    with store.read() as c:
        auth = auth or _auth(store)
        viewer = core.viewer(auth, who)
        sql, args = "SELECT * FROM slack_thread_links WHERE state<>'unlinked'", []
        if thread_id:
            if not _can_see(c, core, auth, who, thread_id):
                raise Problem("not_found", "Thread not found", 404)
            sql += " AND thread_id=?"
            args.append(thread_id)
        at, _, after = str(cursor or "").partition("|")
        if at and after:
            sql += " AND (created_at<? OR (created_at=? AND id<?))"
            args.extend([at, at, after])
        out, last, more = [], None, False
        for n, row in enumerate(c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, _LINKS_SCAN + 1))):
            if len(out) >= limit or n >= _LINKS_SCAN:
                more = True
                break
            last = dict(row)
            if core.readable(c, viewer, last["thread_id"]):
                out.append(view(last))
    return {"links": out, "next_cursor": f"{last['created_at']}|{last['id']}" if more and last else None}


def _event_key(event):
    """The queue's key for an event. A message and its @mention are the same message (channel, ts), so both forms share
    a key and the second is dropped in the queue; an edit is keyed by Slack's edit ts, a delete by its message."""
    channel, ts, op = str(event.get("channel") or ""), str(event.get("ts") or ""), event.get("op")
    if op in ("store", "route"):
        return f"{channel}:{ts}:message"
    if op == "edit":
        return f"{channel}:{ts}:edit:{event.get('edited_ts') or ''}"
    if op == "delete":
        return f"{channel}:{ts}:delete"
    return str(event.get("event_id") or "")


def _place(thread_id):
    """A write's place as the rule check reads it: the thread it goes to."""
    return lambda c, author, recipients: OR.place_for(c, author, recipients, thread_id)


def _apply_edit(c, core, row, body, *, deleted, edited, team):
    """One revision of a carried message, or its tombstone, through core. `row` is the message's link row. A repeated
    text changes nothing; the revision is one past the newest of the link's and the message's."""
    msg = _one(c, "SELECT revision, body, deleted_at FROM messages WHERE id=?", (row["message_id"],))
    if not msg or msg["deleted_at"]:
        return "unknown"
    if not deleted and not _newer_edit(row, edited):
        return "stale"                          # an older edit arriving late, or a repeat: the newest edit wins
    if not deleted and body == msg["body"]:
        c.execute("UPDATE slack_message_links SET edit_ts=? WHERE id=?", (edited, row["id"]))
        return "unchanged"
    revision = max(int(row["revision"] or 0), int(msg["revision"] or 0)) + 1
    origin = {"origin_id": row["origin_id"], "revision": revision, "install": f"slack:{team}", "actor": row["actor"]}
    if core.apply_revision(c, origin, body=body, deleted=deleted) is None:
        return "ignored"
    c.execute("UPDATE slack_message_links SET revision=?, state=?, edit_ts=?, updated_at=? WHERE id=?",
              (revision, "deleted" if deleted else "sent", edited or row["edit_ts"], H.now(), row["id"]))
    return "deleted" if deleted else "edited"


# ----------------------------------------------------------------------------- the gateway's side
class Bridge:
    """One per gateway process. The tests drive `tick()` and the gate directly, with a fake core and a fake Slack."""

    def __init__(self, gateway, core=None):
        self.gw = gateway
        self.store, self.slack, self.settings, self.clock = gateway.store, gateway.slack, gateway.settings, gateway.clock
        self._core = core
        self._auth = None
        self.next_check = ""            # ISO time; linked channels are checked when it has passed ("" = now)
        self.backoff = {}               # channel id -> ISO time before which it is not sent to (rate limit)
        self._recovered = False
        self._users = {}                # Slack user id -> (expires, users.info answer)
        self._verified = {}             # link id -> epoch seconds until its Slack side is trusted (SEND_CHECK_SECONDS)

    # --- seams
    def core(self):
        return self._core or _core()

    @property
    def auth(self):
        if self._auth is None:
            from .auth import Auth
            self._auth = Auth(self.store)
        return self._auth

    def who(self, c, actor):
        return self.auth.identity_for_actor(c, actor)

    def viewer(self, c, actor):
        return self.core().viewer(self.auth, self.who(c, actor))

    def bot_viewer(self, slug):
        """A bot's viewer for the digest follow. `identity_for_actor` only rebuilds people, so the bot's identity is
        made here; core still decides what it may read."""
        return self.core().viewer(self.auth, Identity(f"bot:{slug}", "bot"))

    def person_for(self, c, slack_user_id):
        """(person, reason): the roster person a Slack user is, or None and why. The gateway's `check_user` decides, so
        linking and every message apply the same rules."""
        try:
            user = self.slack_user(slack_user_id)
        except (G.SlackError, G.SlackUnreachable) as exc:
            return None, f"users.info: {getattr(exc, 'code', type(exc).__name__)}"
        person, _user, reason = self.gw.check_user(c, user)
        return person, reason

    def slack_user(self, slack_user_id):
        """A Slack user's users.info answer, reused for USER_CACHE_SECONDS: membership is checked on every link check
        and on every message, and Slack's rate limit is per workspace."""
        now = _epoch(self.clock())
        hit = self._users.get(slack_user_id)
        if hit and hit[0] > now:
            return hit[1]
        user = self.slack.users_info(slack_user_id)
        self._users[slack_user_id] = (now + USER_CACHE_SECONDS, user)
        return user

    def resolve_members(self, channel_id):
        """(persons, problems): each channel member who maps to a roster person (person id -> Slack user id), and why
        any other member does not. The bot's own user is not a member for this purpose."""
        persons, problems = {}, []
        for uid in self.slack.conversations_members(channel_id):
            if uid == self.gw.bot_user_id:
                continue
            with self.store.read() as c:
                person, reason = self.person_for(c, uid)
            if person:
                persons[person["id"]] = uid
            else:
                problems.append(reason or "not a roster person")
        return persons, problems

    def can_read(self, c, thread, person_id):
        try:
            return bool(self.core().readable(c, self.viewer(c, H.human_actor(person_id)), thread))
        except Problem:
            return False

    # --- linking (the gateway checks a requested link)
    def channel_info(self, ref):
        """The Slack channel a link asks for, by id or by name, or (None, why)."""
        if CHANNEL_ID_RE.match(ref):
            try:
                return self.slack.conversations_info(ref), None
            except G.SlackError as exc:
                if exc.code in MISSING_CHANNEL:
                    return None, f"Tico can't see the channel {ref}. Invite Tico to it first."
                raise
        name = ref.lstrip("#").lower()
        for channel in self.slack.conversations_list():
            if str(channel.get("name") or "").lower() == name:
                return channel, None
        return None, f"No channel named #{name} that Tico can see. Invite Tico to it first."

    def gate(self, link):
        """Check a requested link and activate it, or refuse it with the reason. Returns the state after the check."""
        try:
            info, why = self.channel_info(link["channel_ref"])
            if info is None:
                return self._refuse(link, why)
            if info.get("is_im") or info.get("is_mpim"):
                return self._refuse(link, "That is a direct message, not a channel")
            if G.is_external(info):
                return self._refuse(link, "The channel is shared with another workspace (Slack Connect)")
            if not info.get("is_member") and not info.get("is_private"):
                return self._refuse(link, "Invite Tico to the channel first")
            private = bool(info.get("is_private"))
            need = "members" if private else "company"
            with self.store.read() as c:
                thread = _thread(c, link["thread_id"])
            if not thread:
                return self._refuse(link, "The thread no longer exists")
            if thread.get("audience") != need:
                want = "a company thread" if need == "company" else "a members thread"
                return self._refuse(link, f"This channel is {'private' if private else 'public'}, so it links to {want}; "
                                    f"this thread is {thread.get('audience') or 'unset'}")
            persons, problems = self.resolve_members(info["id"])
            if problems:
                return self._refuse(link, f"{len(problems)} channel member(s) don't map to a roster person who can read "
                                    f"the thread ({problems[0]})")
            with self.store.read() as c:
                unreadable = [pid for pid in persons if not self.can_read(c, thread, pid)]
            if unreadable:
                return self._refuse(link, f"{len(unreadable)} channel member(s) can't read the thread")
            return self._activate(link, info, persons, need)
        except (G.SlackError, G.SlackUnreachable) as exc:
            LOG.warning("slack link %s: Slack did not answer (%s); it stays requested", link["id"],
                        getattr(exc, "code", type(exc).__name__))
            return "requested"

    def _refuse(self, link, why):
        with self.store.transaction() as c:
            c.execute("UPDATE slack_thread_links SET state='refused', reason=?, updated_at=? "
                      "WHERE id=? AND state='requested'", (str(why)[:300], H.now(), link["id"]))
            H.event(c, H.KEEPER, "slack.link.refused", link["id"], {"reason": str(why)[:300]})
        return "refused"

    def _activate(self, link, info, persons, need):
        now, private = H.now(), bool(info.get("is_private"))
        try:
            with self.store.transaction() as c:
                if c.execute("SELECT 1 FROM slack_thread_links WHERE channel_id=? AND state IN ('active','suspended')",
                             (info["id"],)).fetchone():
                    raise Problem("conflict", "That channel is already linked to another thread", 409)
                # `activated_at` is set once, by the first activation of this link: it is the fence. A suspension that
                # ends keeps it, so what waited while suspended still goes; a relink is a new link, so what was posted
                # while unlinked never does.
                moved = c.execute("UPDATE slack_thread_links SET state='active', channel_id=?, channel_name=?, "
                                  "channel_private=?, audience=?, reason='', checked_at=?, updated_at=?, "
                                  "activated_at=CASE WHEN activated_at='' THEN ? ELSE activated_at END "
                                  "WHERE id=? AND state='requested'",
                                  (info["id"], str(info.get("name") or ""), int(private), need, now, now, now,
                                   link["id"])).rowcount
                if not moved:
                    return "unlinked"           # it was unlinked while it was being checked
                c.execute("DELETE FROM slack_link_members WHERE link_id=?", (link["id"],))
                for pid, uid in persons.items():
                    c.execute("INSERT INTO slack_link_members (link_id,slack_user_id,person_id,created_at) VALUES (?,?,?,?)",
                              (link["id"], uid, pid, now))
                core = self.core()
                for node in _subtree(c, link["thread_id"]):   # the sub-threads made before the link too
                    core.add_outside_delivery(c, node, outside_recipient(info["id"]))
                listed, _by_name = SC.lookup(c, self.settings, info["id"], str(info.get("name") or ""))
                for slug in (listed or {}).get("readers") or []:
                    try:
                        viewer = self.bot_viewer(slug)
                        if private and not core.readable(c, viewer, link["thread_id"]):
                            # A members thread opens to its members only: a listed reader joins it, as the bridge's
                            # access, before its digest follow (the follow needs the thread to be readable).
                            core.add_member(c, H.KEEPER, link["thread_id"], f"bot:{slug}")
                        core.follow(c, viewer, link["thread_id"], notify="digest")
                    except Problem:
                        continue                # a reader that cannot be made a member is not followed
                H.event(c, H.KEEPER, "slack.link.active", link["id"], {"channel": info["id"], "members": len(persons)})
            return "active"
        except Problem as exc:
            return self._refuse(link, exc.detail)
        except sqlite3.IntegrityError:
            return self._refuse(link, "That channel is already linked to another thread")

    # --- suspension
    def suspend(self, c, link_id, reason):
        """Stop a link and file one Needs-you item for the admin who asked (reused while it is open)."""
        row = _link(c, link_id)
        if not row or row["state"] != "active":
            return
        reason = str(reason)[:300]
        c.execute("UPDATE slack_thread_links SET state='suspended', reason=?, updated_at=? WHERE id=?",
                  (reason, H.now(), link_id))
        H.event(c, H.KEEPER, "slack.suspend", link_id, {"reason": reason})
        task = H.task(c, row["needs_task_id"]) if row["needs_task_id"] else None
        if task and task["status"] in H.ACTIVE_STATUSES:
            return
        name = (row["channel_name"] or row["channel_id"]).lstrip("#")
        title = f"Slack channel #{name} stopped linking to a Tico thread"
        body = (f"The link stopped: {reason}. Nothing moves between the channel and the thread until an owner or admin "
                "checks the link again in the thread's settings.")
        try:
            created = H.task_create(c, H.KEEPER, title, body, row["linked_by"], lint=False)
            c.execute("UPDATE slack_thread_links SET needs_task_id=? WHERE id=?", (created["id"], link_id))
        except H.Refused:
            LOG.warning("Could not file the Needs-you item for a suspended Slack link")

    def check(self, link):
        """Why a linked channel must be suspended, or None."""
        return self._check(link)[0]

    def _check(self, link):
        """(reason, persons): why the channel must be suspended, or None, and the channel's current mapped members (person
        id -> Slack user id), read from Slack now."""
        info = self.slack.conversations_info(link["channel_id"])
        if info.get("is_member") is False:
            return "Tico is no longer in the channel", {}
        if G.is_external(info):
            return "the channel became Slack Connect (shared with another workspace)", {}
        if bool(info.get("is_private")) != bool(link["channel_private"]):
            return "the channel's privacy changed", {}
        persons, problems = self.resolve_members(link["channel_id"])
        if problems:
            return f"a channel member doesn't map to a roster person who can read the thread ({problems[0]})", {}
        with self.store.read() as c:
            thread = _thread(c, link["thread_id"])
            if not thread or thread.get("audience") != link["audience"]:
                return "the thread's audience changed", {}
            for pid in persons:
                if not self.can_read(c, thread, pid):
                    return "a linked member can no longer read the thread", {}
        return None, persons

    def _snapshot(self, link_id, persons):
        """Replaces the stored members of a link with the ones a passing check just read from Slack. Restricted messages
        are decided against this table (`blocked_for`), so it is kept to what Slack says now."""
        now = H.now()
        with self.store.transaction() as c:
            c.execute("DELETE FROM slack_link_members WHERE link_id=?", (link_id,))
            for pid, uid in persons.items():
                c.execute("INSERT INTO slack_link_members (link_id,slack_user_id,person_id,created_at) VALUES (?,?,?,?)",
                          (link_id, uid, pid, now))

    def check_links(self):
        """Every few minutes each active link is checked; a broken one is suspended. A Slack error skips the link."""
        now = self.clock()
        if self.next_check and now < self.next_check:
            return
        self.next_check = H.shift(now, seconds=CHECK_SECONDS)
        with self.store.read() as c:
            links = [dict(r) for r in c.execute("SELECT * FROM slack_thread_links WHERE state='active' AND channel_id<>''")]
        for link in links:
            try:
                reason = self.check(link)
            except (G.SlackError, G.SlackUnreachable):
                continue
            with self.store.transaction() as c:
                if reason:
                    self.suspend(c, link["id"], reason)
                else:
                    c.execute("UPDATE slack_thread_links SET checked_at=? WHERE id=?", (H.now(), link["id"]))

    # --- inbound
    def linked(self, channel_id):
        with self.store.read() as c:
            return _linked(c, channel_id) is not None

    def receive(self, event):
        """A channel message, edit or delete from a linked channel. It joins the link's queue, so the order holds, and
        the queue is carried in now when the link is active. A suspended link keeps it until it is checked again.
        Returns the outcome of the newest event (`queued` while the link is not active)."""
        with self.store.read() as c:
            link = _linked(c, event["channel"])
        if not link:
            return "unlinked"
        if event["op"] == "store" and event.get("author") != "human":
            return "bot"                        # Tico's own posts and other bots' are never carried in
        with self.store.transaction() as c:
            c.execute("INSERT OR IGNORE INTO slack_inbound_queue (link_id,event_key,event_json,created_at) "
                      "VALUES (?,?,?,?)", (link["id"], _event_key(event), json.dumps(event, sort_keys=True), H.now()))
        try:
            return self.drain(link["id"])
        except Exception as exc:                # noqa: BLE001 the queue is kept; the next pass carries it in
            LOG.error("Slack inbound carry failed: %s", type(exc).__name__)
            return "queued"

    def drain(self, link_id):
        """Carry in one link's queued events, oldest first, while the link is active. It stops at an event that cannot
        carry yet (`STALLED`: the sender does not map, which suspends the link, or the write was moved); that event and
        the ones after it wait for the next pass."""
        outcome = "queued"
        for _ in range(MAX_DRAIN):
            with self.store.read() as c:
                link = _link(c, link_id)
                head = _one(c, "SELECT * FROM slack_inbound_queue WHERE link_id=? ORDER BY seq LIMIT 1", (link_id,))
            if not head:
                return outcome
            if not link or link["state"] != "active":
                return "queued"
            outcome = self._process(json.loads(head["event_json"]), link)
            if outcome in STALLED:
                return outcome
            with self.store.transaction() as c:
                c.execute("DELETE FROM slack_inbound_queue WHERE seq=?", (head["seq"],))
        return outcome

    def drain_all(self):
        """The pass for the gateway loop: every active link's queue, carried in."""
        with self.store.read() as c:
            ids = [r[0] for r in c.execute("SELECT DISTINCT q.link_id FROM slack_inbound_queue q "
                                           "JOIN slack_thread_links l ON l.id=q.link_id WHERE l.state='active' LIMIT 50")]
        out = []
        for link_id in ids:
            try:
                out.append(self.drain(link_id))
            except Exception as exc:            # noqa: BLE001 one link's queue never stops the others
                LOG.error("Slack queue for a link failed: %s", type(exc).__name__)
                out.append("error")
        return out

    def _process(self, event, link):
        if event["op"] == "edit":
            return self._revise(event, link, deleted=False)
        if event["op"] == "delete":
            return self._revise(event, link, deleted=True)
        return self.post_in(event, link)

    def body_for(self, text):
        """The Slack text as Tico reads it: markup decoded, and the Tico app's mention made `@<assistant>` the way the
        gateway's persist makes it, so the wake rule sees it."""
        return G.humanize(text, {self.gw.bot_user_id: self.settings.assistant_name})[:G.TEXT_CHARS]

    def _note(self, channel, user, text):
        """A private note to the author, sent after the commit that decided it. Nothing here stops ingestion."""
        if not user:
            return
        try:
            self.slack.post_ephemeral(channel, user, text)
        except Exception as exc:                # noqa: BLE001 a note that fails is logged, never raised
            LOG.warning("Could not send the Block note (%s)", getattr(exc, "code", type(exc).__name__))

    def _author_user(self, link_id, row):
        """The Slack user a carried message came from, recovered from its mapping: an edit event carries no user."""
        actor = str(row.get("actor") or "")
        if not actor.startswith("human:"):
            return ""
        with self.store.read() as c:
            found = c.execute("SELECT slack_user_id FROM slack_link_members WHERE link_id=? AND person_id=?",
                              (link_id, actor[len("human:"):])).fetchone()
        return str(found[0]) if found else ""

    def _held(self, event, link, actor, reason):
        with self.store.transaction() as c:
            if not _seen(c, event["channel"], event["ts"]):
                _record(c, link, event, "in", "held", actor=actor, error=reason)
        self._note(event["channel"], event.get("user_id"), "Tico held this message, so it did not reach the thread.")
        return "held"

    def audience_of(self, c, link, thread_id, author, text):
        """The audience a message is checked against (see `audience`), read through this bridge's viewers."""
        return audience(c, self.core(), lambda actor: self._viewer_for(c, actor), link["id"], thread_id, author, text)

    def _viewer_for(self, c, actor):
        if str(actor).startswith("bot:"):
            return self.bot_viewer(str(actor)[len("bot:"):])
        return self.viewer(c, actor)

    def inbound(self, actor, text, channel, ts, thread_id, link):
        """The org-rule verdict for a Slack message, held as its person (`org_rule_check.inbound`) and checked against the
        audience (a With rule is matched on it): its provenance is the Slack channel and ts, and the bridge is the sender.
        Checked outside the write; `_write` revalidates the audience inside it."""
        with self.store.read() as c:
            recipients = self.audience_of(c, link, thread_id, actor, text)
        return OR.inbound(self.store, self.gw.judge, actor, "slack_message", text, recipients, source="slack",
                          authenticated=f"slack:{self.gw.bot_user_id or self.gw.team_id}",
                          origin=f"{channel}:{ts}", place=_place(thread_id))

    def _audience_moved(self, c, gate, link, actor, thread_id, text):
        """Revalidation inside the write: the audience the write sees must be the one the check decided on. Moved, the
        write waits for its next try, which checks again outside the write lock (no engine runs under it)."""
        now = self.audience_of(c, link, thread_id, actor, text)
        if gate is not None:
            return now != sorted(gate.get("recipients") or [])
        if not OR.in_force(c):
            return False
        return bool(OR.R.matching(c, actor, now, OR.place_for(c, actor, now, thread_id)))

    def post_in(self, event, link):
        """One channel message, carried into the thread as its person. A Slack thread reply becomes a sub-thread."""
        channel, ts = event["channel"], event["ts"]
        with self.store.read() as c:
            if _seen(c, channel, ts):
                return "duplicate"
            person, reason = self.person_for(c, event["user_id"])
            parent = self.parent_for(c, event) if person else None
        if not person:
            with self.store.transaction() as c:
                self.suspend(c, link["id"], f"a message came from someone who doesn't map to a roster person ({reason})")
            return "denied"                     # the event stays queued until the link is checked again
        actor = H.human_actor(person["id"])
        text = self.body_for(event["text"])
        if screening.screen(self.store, "slack", text, actor=actor, role="human", key=ts).get("action") == "hold":
            return self._held(event, link, actor, "screening hold")
        thread_id = parent["conversation_id"] if parent else link["thread_id"]
        gate = self.inbound(actor, text, channel, ts, thread_id, link)
        return self._write(event, link, actor, thread_id, parent, text, gate)

    def _write(self, event, link, actor, thread_id, parent, text, gate, *, carry_import=False):
        """The write for one carried message: held by the org rule check (nothing is written, and the author gets a
        note), or posted as the person with its origin and `refs.slack`. Returns the outcome."""
        channel, ts = event["channel"], event["ts"]
        origin_id = f"{H.new_id()}@slack:{self.gw.team_id}"
        origin = {"origin_id": origin_id, "revision": 0, "install": f"slack:{self.gw.team_id}", "actor": actor}
        refs = {"slack": {"channel": channel, "ts": ts, "team": self.gw.team_id}}
        if carry_import:
            refs["import"] = True
        held = False
        try:
            with self.store.transaction() as c:
                if _seen(c, channel, ts):
                    return "duplicate"
                if self._audience_moved(c, gate, link, actor, thread_id, text):
                    return "retry"                  # the audience moved since the check: the next try checks again
                verdict = OR.apply(c, gate, text, {"link_id": link["id"], "event": event, "thread_id": thread_id,
                                                   "text": text, "team": self.gw.team_id,
                                                   "parent_message_id": (parent or {}).get("message_id"),
                                                   "import": carry_import})
                if verdict and "moved" in verdict:
                    return "retry"
                if verdict and "held" in verdict:
                    _record(c, link, event, "in", "held", origin_id=origin_id, actor=actor, error="org rule hold")
                    held = True
                else:
                    posted = self.core().post(c, self.viewer(c, actor), thread_id, OR.warned_text(verdict, text),
                                              kind="say", refs=refs,
                                              parent_message_id=(parent or {}).get("message_id"), via="slack",
                                              origin=origin)
                    _record(c, link, event, "in", "sent", message_id=posted.get("id"), origin_id=origin_id,
                            actor=actor)
                    return "posted"
        except Problem as exc:
            with self.store.transaction() as c:
                _record(c, link, event, "in", "failed", origin_id=origin_id, actor=actor, error=exc.code)
            return "refused"
        if held and not carry_import:
            self._note(channel, event.get("user_id"), "Tico held this message, so it did not reach the thread.")
        return "held"

    def parent_for(self, c, event):
        """The Tico message a Slack thread reply answers: the message its thread root was carried as, or None."""
        thread_ts = event.get("thread_ts") or ""
        if not thread_ts or thread_ts == event["ts"]:
            return None
        return _one(c, "SELECT m.id AS message_id, m.conversation_id AS conversation_id FROM slack_message_links l "
                       "JOIN messages m ON m.id = l.message_id WHERE l.channel_id=? AND l.ts=? AND l.state='sent'",
                    (event["channel"], thread_ts))

    def _revise(self, event, link, *, deleted):
        """An edit or a delete in a linked channel: a revision or a tombstone of the message carried in. A repeated edit
        (the same Slack edit ts, or the same text) changes nothing. A Block holds the edit: the message keeps its text."""
        with self.store.read() as c:
            row = _one(c, "SELECT * FROM slack_message_links WHERE channel_id=? AND ts=? AND direction='in'",
                       (event["channel"], event["ts"]))
            msg = _one(c, "SELECT conversation_id, body, deleted_at FROM messages WHERE id=?",
                       (row["message_id"],)) if row and row["message_id"] else None
        if not row:
            return "unknown"
        if row["state"] == "held":
            if not deleted:
                self._note(event["channel"], self._author_user(link["id"], row),
                           "Tico held this message, so the edit did not reach the thread.")
            return "held"
        if row["state"] != "sent" or not row["origin_id"] or not msg or msg["deleted_at"]:
            return "unknown"
        edited = str(event.get("edited_ts") or "")
        if not deleted and not _newer_edit(row, edited):
            return "duplicate" if edited == row["edit_ts"] else "stale"     # a repeat, or an older edit arriving late
        body = None if deleted else self.body_for(event["text"])
        if not deleted and body == msg["body"]:
            with self.store.transaction() as c:
                c.execute("UPDATE slack_message_links SET edit_ts=? WHERE id=?", (edited, row["id"]))
            return "unchanged"
        if not deleted:
            verdict = screening.screen(self.store, "slack", body, actor=row["actor"], role="human",
                                       key=f"{event['ts']}:{edited}")
            if verdict.get("action") == "hold":
                self._note(event["channel"], self._author_user(link["id"], row),
                           "Tico held this edit, so the message in the thread was not changed.")
                return "held"
        gate = None if deleted else self.inbound(row["actor"], body, event["channel"], event["ts"],
                                                 msg["conversation_id"], link)
        try:
            outcome, note = self._apply_revision(event, link, row, msg, body, deleted, edited, gate)
        except Problem as exc:
            return f"refused: {exc.code}"
        if note:
            self._note(event["channel"], self._author_user(link["id"], row), note)
        return outcome

    def _apply_revision(self, event, link, row, msg, body, deleted, edited, gate):
        """The write for one edit or tombstone, in one transaction. Returns (outcome, Block note or None): the note is sent
        after the commit, by the caller, so a failed note never holds the write."""
        with self.store.transaction() as c:
            if not deleted and self._audience_moved(c, gate, link, row["actor"], msg["conversation_id"], body):
                return "retry", None
            if gate is not None:
                verdict = OR.apply(c, gate, body, {"link_id": link["id"], "event": event, "edit": True,
                                                   "text": body, "team": self.gw.team_id,
                                                   "thread_id": msg["conversation_id"]})
                if verdict and "moved" in verdict:
                    return "retry", None
                if verdict and "held" in verdict:
                    return "held", "Tico held this edit, so the message in the thread was not changed."
            return _apply_edit(c, self.core(), row, body, deleted=deleted, edited=edited, team=self.gw.team_id), None

    # --- outbound
    def outbound(self):
        """Send what is due, each channel from its own queue, so a busy channel never holds another back. A channel's
        Slack side is verified before its sends (`verify_send`): one that cannot be verified pauses, and a rate limit
        pauses it until its window passes."""
        out = []
        with self.store.read() as c:
            links = [dict(r) for r in c.execute("SELECT * FROM slack_thread_links WHERE state='active' AND channel_id<>''")]
        for link in links:
            if self.backoff.get(link["channel_id"], "") > self.clock():
                continue
            try:
                reason = self.verify_send(link)
            except (G.SlackError, G.SlackUnreachable) as exc:
                out.append({"channel": link["channel_id"], "state": "paused",
                            "error": getattr(exc, "code", type(exc).__name__)})
                continue
            if reason:
                with self.store.transaction() as c:
                    self.suspend(c, link["id"], reason)
                out.append({"channel": link["channel_id"], "state": "suspended", "reason": reason})
                continue
            with self.store.read() as c:
                due = _due(c, link["channel_id"], SEND_BATCH)
            for d in due:
                if self.backoff.get(link["channel_id"], "") > self.clock():
                    break
                try:
                    out.append(self.send_one(link, d))
                except Exception as exc:            # noqa: BLE001 one delivery never stops the others
                    LOG.error("Slack delivery %s failed: %s", d.get("id"), type(exc).__name__)
                    out.append({"delivery_id": d.get("id"), "state": "error"})
        return out

    def verify_send(self, link):
        """The Slack side of a send check: the channel and its members, asked at most every SEND_CHECK_SECONDS per link.
        A passing check refreshes the stored member snapshot. Returns the reason to suspend, or None. A Slack error raises,
        and the caller pauses the channel."""
        now = _epoch(self.clock())
        if self._verified.get(link["id"], 0.0) > now:
            return None
        reason, persons = self._check(link)
        if not reason:
            self._snapshot(link["id"], persons)
            self._verified[link["id"]] = now + SEND_CHECK_SECONDS
        return reason

    def verified_now(self, link_id):
        """Whether the link's member snapshot was checked against Slack within SEND_CHECK_SECONDS."""
        return self._verified.get(link_id, 0.0) > _epoch(self.clock())

    def local_check(self, link_id):
        """The local half of a send check, against the store as it is now: the link is active, the thread keeps its
        audience, and every mapped member still reads it. Runs before every send, so a narrowed audience or a removed
        member stops the next send at once. Returns (reason or None, the link row)."""
        with self.store.read() as c:
            row = _link(c, link_id)
            if not row or row["state"] != "active":
                return None, row
            thread = _thread(c, row["thread_id"])
            if not thread or thread.get("audience") != row["audience"]:
                return "the thread's audience changed", row
            for pid in _members(c, link_id):
                if not self.can_read(c, thread, pid):
                    return "a linked member can no longer read the thread", row
        return None, row

    def blocked_for(self, c, link, msg):
        """Why this message must not reach the channel, or None. A restricted message is checked per message with core's
        predicate: every mapped member of the verified snapshot must read it. Nobody mapped means nobody can read it, so it
        is not sent."""
        if not msg.get("restricted"):
            return None
        members = _members(c, link["id"])
        if not members:
            return "no channel member can read this message"
        for pid in members:
            try:
                readable = self.core().message_readable(c, self.viewer(c, H.human_actor(pid)), msg)
            except Problem:
                readable = False
            if not readable:
                return "a channel member can't read this message"
        return None

    def plan(self, c, link, d):
        """What to do with one delivery, from the store alone: settle it, wait, or send it (post, update or delete)."""
        msg = _one(c, "SELECT * FROM messages WHERE id=?", (d["message_id"],))
        if not msg:
            return {"do": "settle", "state": "moot", "error": "message gone"}
        if self.core().remote_guest(c, msg.get("from_actor")):      # another company's words never reach a channel
            return {"do": "settle", "state": "moot", "error": "from another company"}
        refs = _refs(msg.get("refs_json"))
        if (refs.get("slack") or {}).get("channel") == link["channel_id"]:
            return {"do": "settle", "state": "delivered"}           # it came from this channel: no echo
        row = _one(c, "SELECT * FROM slack_message_links WHERE link_id=? AND message_id=? AND direction='out'",
                   (link["id"], msg["id"]))
        if row and row["state"] == "deleted":
            return {"do": "settle", "state": "delivered"}
        if row and row["state"] in ("uncertain", "sending"):
            return {"do": "wait"}
        if msg.get("deleted_at"):
            if row and row["ts"]:
                return {"do": "delete", "row": row}
            return {"do": "settle", "state": "moot", "error": "deleted before it was sent"}
        if msg.get("kind") not in MIRROR_KINDS:
            return {"do": "settle", "state": "moot", "error": "not mirrored"}
        if msg.get("restricted") and not self.verified_now(link["id"]):
            return {"do": "wait"}                    # fail closed: the member snapshot is stale, the next pass checks it
        blocked = self.blocked_for(c, link, msg)
        if blocked:
            if row and row["ts"]:
                return {"do": "delete", "row": row}      # a posted message the channel may no longer see comes down
            return {"do": "settle", "state": "moot", "error": blocked}
        if row and row["ts"]:
            # Already in Slack: it is brought to the newest revision, whichever delivery this is.
            if int(msg.get("revision") or 0) > int(row["revision"] or 0):
                place = self.place(c, link, msg)
                return {"do": "update", "row": row, "msg": msg, "prefix": place.get("prefix", "")}
            return {"do": "settle", "state": "delivered"}
        # Not in Slack yet. A message written before this link row was first active never goes out (the fence): a
        # relink does not export what was posted while the channel was unlinked.
        if link.get("activated_at") and (msg.get("created") or "") < link["activated_at"]:
            return {"do": "settle", "state": "moot", "error": "written before the link was active"}
        if int(d.get("revision") or 0) < int(msg.get("revision") or 0):
            return {"do": "settle", "state": "moot", "error": "a newer revision follows"}
        place = self.place(c, link, msg)
        if place.get("wait"):
            return {"do": "wait"}
        if place.get("moot"):
            return {"do": "settle", "state": "moot", "error": "outside this thread"}
        return {"do": "post", "row": row, "msg": msg, "thread_ts": place["thread_ts"], "prefix": place["prefix"]}

    def place(self, c, link, msg):
        """Where a message goes in Slack: the channel itself (thread_ts ''), the Slack thread of its root message (a
        reply one level down), or the root's Slack thread with a "↳ title" line (deeper)."""
        if msg["conversation_id"] == link["thread_id"]:
            return {"thread_ts": "", "prefix": ""}
        conv = _thread(c, msg["conversation_id"])
        if not conv:
            return {"moot": True}
        top, hops = conv, 0
        while top.get("parent_id") != link["thread_id"]:
            if not top.get("parent_id") or hops > 64:
                return {"moot": True}
            top = _thread(c, top["parent_id"])
            hops += 1
            if not top:
                return {"moot": True}
        title = conv.get("subject") or "Sub-thread"
        prefix = f"↳ {title}" if conv["id"] != top["id"] else ""
        anchor = top.get("parent_message_id")
        mapped = _one(c, "SELECT ts FROM slack_message_links WHERE link_id=? AND message_id=? AND ts<>'' AND state='sent'",
                      (link["id"], anchor)) if anchor else None
        if mapped:
            return {"thread_ts": mapped["ts"], "prefix": prefix}
        if anchor and c.execute("SELECT 1 FROM message_deliveries WHERE message_id=? AND recipient=? AND state='pending'",
                                (anchor, outside_recipient(link["channel_id"]))).fetchone():
            return {"wait": True}                   # the message it answers is still on its way to Slack
        return {"thread_ts": "", "prefix": f"↳ {title}"}

    def author_name(self, c, actor):
        kind, _, ident = str(actor or "").partition(":")
        if kind == "human":
            return H.person_name(c, ident)
        if kind == "bot":
            row = _one(c, "SELECT display_name FROM bots WHERE slug=?", (ident,))
            return (row or {}).get("display_name") or ident
        return "Tico"

    def text_for(self, c, msg, prefix):
        lines = [f"*{G.slack_escape(self.author_name(c, msg.get('from_actor')))}* · via Tico"]
        if prefix:
            lines.append(G.slack_escape(prefix))
        lines.append(G.slack_escape(msg.get("body") or ""))
        return "\n".join(lines)[:G.POST_CHARS]

    def send_one(self, link, d):
        """One delivery, after the local check: a narrowed audience, a removed member or an inactive link stops it here,
        before anything is sent. A channel that is no longer eligible is suspended, not sent to."""
        reason, fresh = self.local_check(link["id"])
        if not fresh or fresh["state"] != "active":
            return {"delivery_id": d["id"], "state": "paused"}
        if reason:
            with self.store.transaction() as c:
                self.suspend(c, link["id"], reason)
            return {"delivery_id": d["id"], "state": "suspended", "reason": reason}
        link = fresh
        with self.store.read() as c:
            step = self.plan(c, link, d)
            text = self.text_for(c, step["msg"], step.get("prefix", "")) if step["do"] in ("post", "update") else ""
        do = step["do"]
        if do == "wait":
            return {"delivery_id": d["id"], "state": "waiting"}
        if do == "settle":
            return self.settle(d, step["state"], step.get("error"))
        if do == "delete":
            return self.send_delete(link, d, step)
        if do == "update":
            return self.send_update(link, d, step, text)
        return self.send_post(link, d, step, text)

    def settle(self, d, state, error=None):
        with self.store.transaction() as c:
            self.core().settle_delivery(c, d["id"], state, error=error)
        return {"delivery_id": d["id"], "state": state}

    def _set_row(self, row_id, state, error=None):
        with self.store.transaction() as c:
            c.execute("UPDATE slack_message_links SET state=?, error=?, updated_at=? WHERE id=?",
                      (state, error and str(error)[:200], H.now(), row_id))

    def _slack_failed(self, link, d, row_id, exc):
        """A Slack refusal. A rate limit pauses the channel and retries; any other is terminal for the message, and it
        surfaces in Needs you (`failure_item`); a channel-level one also suspends the link."""
        if getattr(exc, "code", "") == "ratelimited":
            wait = int(getattr(exc, "retry_after", None) or G.RATE_LIMIT_DEFAULT)
            self.backoff[link["channel_id"]] = H.shift(self.clock(), seconds=wait)
            if row_id:
                self._set_row(row_id, "ready")
            return {"delivery_id": d["id"], "state": "rate_limited"}
        code = getattr(exc, "code", "") or type(exc).__name__
        with self.store.transaction() as c:
            if row_id:
                c.execute("UPDATE slack_message_links SET state='failed', error=?, updated_at=? WHERE id=?",
                          (str(code)[:200], H.now(), row_id))
            self.core().settle_delivery(c, d["id"], "failed", error=str(code)[:200])
            failure_item(c, link["id"], code)
            if code in CHANNEL_ERRORS:
                self.suspend(c, link["id"], f"Slack refused the post ({code})")
        return {"delivery_id": d["id"], "state": "failed", "error": code}

    def send_post(self, link, d, step, text):
        msg, row = step["msg"], step.get("row")
        thread_ts, now, revision = step.get("thread_ts", ""), H.now(), int(msg.get("revision") or 0)   # the text sent
        with self.store.transaction() as c:
            if row:
                row_id = row["id"]
                c.execute("UPDATE slack_message_links SET state='sending', delivery_id=?, revision=?, thread_ts=?, "
                          "attempts=attempts+1, updated_at=? WHERE id=?", (d["id"], revision, thread_ts, now, row_id))
            else:
                row_id = H.new_id()
                c.execute("INSERT INTO slack_message_links (id,link_id,channel_id,direction,ts,thread_ts,message_id,"
                          "delivery_id,revision,attempts,actor,state,created_at,updated_at) "
                          "VALUES (?,?,?,'out','',?,?,?,?,1,?,'sending',?,?)",
                          (row_id, link["id"], link["channel_id"], thread_ts, msg["id"], d["id"], revision,
                           msg.get("from_actor") or "", now, now))
        try:
            reply = self.slack.post_thread_message(link["channel_id"], thread_ts, text, d["id"])
        except G.SlackUnreachable:
            self._set_row(row_id, "uncertain")
            return {"delivery_id": d["id"], "state": "uncertain"}
        except G.SlackError as exc:
            return self._slack_failed(link, d, row_id, exc)
        with self.store.transaction() as c:
            c.execute("UPDATE slack_message_links SET state='sent', ts=?, updated_at=? WHERE id=?",
                      (str(reply.get("ts") or ""), H.now(), row_id))
            state = self._settle_sent(c, d, msg, revision)
        return {"delivery_id": d["id"], "state": state, "ts": reply.get("ts")}

    def _settle_sent(self, c, d, msg, sent_rev):
        """The delivery is delivered while the revision sent is still the newest. A newer revision written meanwhile keeps
        it pending: the next pass brings Slack to that revision as an update."""
        current = _one(c, "SELECT revision FROM messages WHERE id=?", (msg["id"],))
        if current and int(current["revision"] or 0) > int(sent_rev):
            return "pending"
        self.core().settle_delivery(c, d["id"], "delivered")
        return "delivered"

    def send_update(self, link, d, step, text):
        row, msg = step["row"], step["msg"]
        try:
            self.slack.update_message(link["channel_id"], row["ts"], text)
        except G.SlackUnreachable:
            self._set_row(row["id"], "uncertain")
            return {"delivery_id": d["id"], "state": "uncertain"}
        except G.SlackError as exc:
            return self._slack_failed(link, d, row["id"], exc)
        sent_rev = int(msg.get("revision") or 0)
        with self.store.transaction() as c:
            c.execute("UPDATE slack_message_links SET state='sent', revision=?, delivery_id=?, updated_at=? WHERE id=?",
                      (sent_rev, d["id"], H.now(), row["id"]))
            state = self._settle_sent(c, d, msg, sent_rev)
        return {"delivery_id": d["id"], "state": state, "edit": True}

    def send_delete(self, link, d, step):
        row = step["row"]
        try:
            try:
                self.slack.delete_message(link["channel_id"], row["ts"])
            except G.SlackError as exc:
                if exc.code == "ratelimited":
                    raise
                # A message the app cannot delete keeps its place, as a tombstone line.
                self.slack.update_message(link["channel_id"], row["ts"], "(message deleted)")
        except G.SlackUnreachable:
            self._set_row(row["id"], "uncertain")
            return {"delivery_id": d["id"], "state": "uncertain"}
        except G.SlackError as exc:
            return self._slack_failed(link, d, row["id"], exc)
        with self.store.transaction() as c:
            c.execute("UPDATE slack_message_links SET state='deleted', updated_at=? WHERE id=?", (H.now(), row["id"]))
            self.core().settle_delivery(c, d["id"], "delivered")
        return {"delivery_id": d["id"], "state": "delivered", "delete": True}

    def _find_delivery(self, row):
        """Where an uncertain post landed, by its delivery id: (the Slack message or None, whether the whole window was
        read). A reply is looked for in its Slack thread (conversations.replies), a top-level message in the channel's
        history. Every page of the window is read before a message is called missing."""
        oldest, cursor = f"{_epoch(row['updated_at']) - RECONCILE_WINDOW:.6f}", ""
        for _ in range(RECONCILE_PAGES):
            try:
                if row["thread_ts"]:
                    page, cursor = self.slack.replies_metadata_page(row["channel_id"], row["thread_ts"], oldest, cursor)
                else:
                    page, cursor = self.slack.history_metadata_page(row["channel_id"], oldest, cursor)
            except (G.SlackError, G.SlackUnreachable):
                return None, False
            hit = next((m for m in page if ((m.get("metadata") or {}).get("event_payload") or {}).get(
                "delivery_id") == row["delivery_id"]), None)
            if hit:
                return hit, True
            if not cursor:
                return None, True
        return None, False

    def reconcile(self):
        """An uncertain post is looked for in the channel's history by its delivery id: found, it is sent; absent after
        the window, it is posted again, once more at most. An uncertain update or delete is idempotent: sent again."""
        now = self.clock()
        with self.store.read() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM slack_message_links WHERE state='uncertain' AND direction='out'")]
        for row in rows:
            if row["updated_at"] > H.shift(now, seconds=-RECONCILE_AFTER):
                continue
            if row["ts"]:
                self._set_row(row["id"], "ready")
                continue
            hit, complete = self._find_delivery(row)
            if not hit and not complete:
                continue                        # the lookup failed or ran out of pages: still uncertain, tried again
            with self.store.transaction() as c:
                if hit:
                    c.execute("UPDATE slack_message_links SET state='sent', ts=?, updated_at=? WHERE id=?",
                              (str(hit.get("ts") or ""), H.now(), row["id"]))
                    current = _one(c, "SELECT revision FROM messages WHERE id=?", (row["message_id"],))
                    if not current or int(current["revision"] or 0) <= int(row["revision"] or 0):
                        self.core().settle_delivery(c, row["delivery_id"], "delivered")
                elif row["attempts"] >= MAX_SEND_TRIES:
                    c.execute("UPDATE slack_message_links SET state='failed', error='absent after retry', updated_at=? "
                              "WHERE id=?", (H.now(), row["id"]))
                    self.core().settle_delivery(c, row["delivery_id"], "failed", error="absent after retry")
                    failure_item(c, row["link_id"], "absent after retry")
                else:
                    c.execute("UPDATE slack_message_links SET state='ready', updated_at=? WHERE id=?", (H.now(), row["id"]))

    def recover(self):
        """A send in flight when the process stopped is uncertain, not sent again; a history import starts again."""
        with self.store.transaction() as c:
            c.execute("UPDATE slack_message_links SET state='uncertain', updated_at=? WHERE state='sending'", (H.now(),))
            c.execute("UPDATE slack_thread_links SET history_state='pending', updated_at=? WHERE history_state='running'",
                      (H.now(),))

    def sweep_unlinked(self):
        """Pending deliveries to a channel that is no longer linked are settled `moot`, as the unlink did for the ones due."""
        with self.store.read() as c:
            channels = [dict(r)["channel_id"] for r in c.execute(
                "SELECT DISTINCT channel_id FROM slack_thread_links WHERE state='unlinked' AND channel_id<>'' "
                "AND channel_id NOT IN (SELECT channel_id FROM slack_thread_links WHERE state IN ('active','suspended'))"
                " LIMIT 20")]
        for channel_id in channels:
            with self.store.transaction() as c:
                _sweep(self.core(), c, channel_id, limit=100)

    # --- history
    def _history_state(self, link_id, state, count=None):
        with self.store.transaction() as c:
            if count is None:
                c.execute("UPDATE slack_thread_links SET history_state=?, updated_at=? WHERE id=?", (state, H.now(), link_id))
            else:
                c.execute("UPDATE slack_thread_links SET history_state=?, history_count=?, updated_at=? WHERE id=?",
                          (state, count, H.now(), link_id))

    def import_history(self, link):
        """The last `HISTORY_DAYS` of the channel, at most `HISTORY_CAP` messages, oldest first, each written as its
        mapped person with `refs.import`, so it wakes no bot. A message from someone who is not a linked member, a bot's,
        or one the Block holds is skipped. Idempotent: a message already carried is not carried twice."""
        oldest = f"{_epoch(self.clock()) - HISTORY_DAYS * 86400:.6f}"
        found, cursor = [], ""
        try:
            for _ in range(HISTORY_PAGES):
                page, cursor = self.slack.history_page(link["channel_id"], oldest, cursor, HISTORY_PAGE)
                found.extend(page)
                if not cursor or len(found) >= HISTORY_CAP:
                    break
            for top in [m for m in found if int(m.get("reply_count") or 0) > 0]:
                if len(found) >= HISTORY_CAP:
                    break
                found.extend(self.slack.replies(link["channel_id"], top["ts"], oldest, HISTORY_PAGE))
        except (G.SlackError, G.SlackUnreachable):
            self._history_state(link["id"], "pending")
            return {"state": "pending"}
        seen, messages = set(), []
        for m in sorted(found, key=lambda m: float(m.get("ts") or 0)):
            ts = str(m.get("ts") or "")
            if not ts or ts in seen or m.get("bot_id") or m.get("subtype") not in (None, "", "thread_broadcast", "file_share"):
                continue
            seen.add(ts)
            messages.append(m)
        messages = messages[:HISTORY_CAP]
        with self.store.read() as c:
            members = {r["slack_user_id"]: r["person_id"] for r in c.execute(
                "SELECT slack_user_id, person_id FROM slack_link_members WHERE link_id=?", (link["id"],))}
        count = 0
        for m in messages:
            uid = str(m.get("user") or "")
            if uid not in members:
                continue
            text = self.body_for(m.get("text") or "")
            if not text.strip():
                continue
            try:
                count += self._import_one(link, m, members[uid], text)
            except Problem:
                continue
        self._history_state(link["id"], "done", count)
        return {"state": "done", "count": count}

    def _import_one(self, link, m, person_id, text):
        channel, ts = link["channel_id"], str(m.get("ts"))
        actor = H.human_actor(person_id)
        with self.store.read() as c:
            if _seen(c, channel, ts):
                return 0
        if screening.screen(self.store, "slack", text, actor=actor, role="human", key=ts).get("action") == "hold":
            return 0
        event = {"channel": channel, "ts": ts, "thread_ts": str(m.get("thread_ts") or ts), "op": "store",
                 "user_id": str(m.get("user") or ""), "author": "human", "text": text}
        with self.store.read() as c:
            parent = self.parent_for(c, event)
        thread_id = parent["conversation_id"] if parent else link["thread_id"]
        gate = self.inbound(actor, text, channel, ts, thread_id, link)
        return 1 if self._write(event, link, actor, thread_id, parent, text, gate, carry_import=True) == "posted" else 0

    # --- the gateway's pass
    def tick(self):
        """One pass for the gateway loop: recover, gate requests, check links, send, reconcile, sweep, import."""
        if not self._recovered:
            self.recover()
            self._recovered = True
        out = {"gated": [], "sent": [], "history": []}
        with self.store.read() as c:
            requested = [dict(r) for r in c.execute("SELECT * FROM slack_thread_links WHERE state='requested' "
                                                    "ORDER BY created_at LIMIT 5")]
        for link in requested:
            try:
                out["gated"].append(self.gate(link))
            except Exception as exc:            # noqa: BLE001 a check that breaks is tried again, never lost
                LOG.error("Slack link %s check failed: %s", link["id"], type(exc).__name__)
                out["gated"].append("error")
        self.check_links()
        out["carried"] = self.drain_all()
        out["sent"] = self.outbound()
        self.reconcile()
        self.sweep_unlinked()
        with self.store.read() as c:
            pending = [dict(r) for r in c.execute(
                "SELECT * FROM slack_thread_links WHERE state='active' AND history_state='pending' AND updated_at<? LIMIT 1",
                (H.shift(self.clock(), seconds=-HISTORY_RETRY),))]
        for link in pending:
            self._history_state(link["id"], "running")
            try:
                out["history"].append(self.import_history(link))
            except Exception as exc:                # noqa: BLE001 the import is tried again, not lost
                LOG.error("Slack history import failed: %s", type(exc).__name__)
                self._history_state(link["id"], "pending")
        return out


def slack_audience(c, bot, replay):
    """Who a held Slack write would reach now, as its check counted them (`org_rule_check.current_audience`): the link's
    mapped members who read the thread, and the mentions. `_same_audience` refuses Send anyway when it differs."""
    link = _link(c, str(replay.get("link_id") or ""))
    if not link or link["state"] != "active":
        raise Problem("conflict", "The Slack link is not active, so this stays held", 409)
    core = _core()
    return audience(c, core, lambda actor: _identity_viewer(c, core, actor), link["id"],
                    str(replay.get("thread_id") or ""), "", replay.get("text") or "")


@OR.replayer("slack_message", audience=slack_audience)
def replay_held(c, bot, hold, replay, ingest, gate=None):
    """Send anyway for a held Slack message or edit: the write the bridge makes, as the person it is held for. The
    audience was checked by the caller (`slack_audience`), and the decided check (`gate`) is not asked again. The link
    must still be active."""
    link = _link(c, str(replay.get("link_id") or ""))
    if not link or link["state"] != "active":
        raise Problem("conflict", "The Slack link is not active, so this stays held", 409)
    event, team = replay.get("event") or {}, str(replay.get("team") or "")
    text = OR.warned_text(gate, replay.get("text") or "")
    row = _one(c, "SELECT * FROM slack_message_links WHERE channel_id=? AND ts=? AND direction='in'",
               (event.get("channel"), event.get("ts")))
    core = _core()
    if replay.get("edit"):
        if not row:
            raise Problem("not_found", "That Slack message was not found", 404)
        return {"outcome": _apply_edit(c, core, row, text, deleted=False, edited=str(event.get("edited_ts") or ""),
                                       team=team)}
    if not row or row["state"] != "held":
        raise Problem("conflict", "That Slack message is not held", 409)
    refs = {"slack": {"channel": event["channel"], "ts": event["ts"], "team": team}}
    if replay.get("import"):
        refs["import"] = True
    posted = core.post(c, core.viewer(None, bot), replay["thread_id"], text, kind="say", refs=refs,
                       parent_message_id=replay.get("parent_message_id"), via="slack",
                       origin={"origin_id": row["origin_id"], "revision": 0, "install": f"slack:{team}",
                               "actor": row["actor"]})
    c.execute("UPDATE slack_message_links SET state='sent', message_id=?, updated_at=? WHERE id=?",
              (posted.get("id"), H.now(), row["id"]))
    return {"id": posted.get("id"), "state": "sent"}


# ----------------------------------------------------------------------------- the REST routes (the API process)
class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=200)
    channel: str = Field(min_length=1, max_length=200)
    history: bool = False


def install(app, settings, store, auth):
    """`/api/v2/slack/links`: request, list, unlink and check again. Admins only: a link is an audience operation."""
    def admin(request: Request):
        who = request.state.identity
        auth.domain(who)
        if not auth.bot_admin(who):
            raise Problem("forbidden", "Only an owner or an admin links a Slack channel to a thread", 403)
        return who

    @app.get("/api/v2/slack/links")
    def links(request: Request, thread: str = "", limit: int = 50, cursor: str = ""):
        return list_links(store, admin(request), thread, limit, cursor, auth=auth)

    @app.post("/api/v2/slack/links")
    def link_add(request: Request, body: LinkRequest):
        return request_link(store, admin(request), body.thread_id, body.channel, history=body.history, auth=auth)

    @app.delete("/api/v2/slack/links/{link_id}")
    def link_remove(request: Request, link_id: str):
        return unlink_link(store, admin(request), link_id, auth=auth)

    @app.post("/api/v2/slack/links/{link_id}/resume")
    def link_resume(request: Request, link_id: str):
        return resume_link(store, admin(request), link_id, auth=auth)
