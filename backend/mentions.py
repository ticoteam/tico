"""Mentions: what people ask a human in Slack, kept for that human (docs/mentions.md).

A human connects their own Slack through the mentions app (`connectors/slack-mentions-app-manifest.yaml`,
their user token in the vault as `<HUMAN>_SLACK_USER_TOKEN`). The Slack gateway receives every message
in the channels they are in (never their DMs or group DMs: the people writing there expect a DM to
reach only them) and keeps only what names them: a message with `<@them>` becomes a mention, one per
Slack thread, so a second mention in the same thread joins the first (and reopens it if it was done).
A later message in that thread from anyone is kept as context; the human's own reply there is kept as
their reply. The gateway then reads the thread for context (names, permalink, the thread so far) and
marks the mention open.

This is not a task: nothing runs, nobody is notified, the Needs you list is untouched. The human's own
frontend shows their open mentions, drafts an answer with a bot's context, and marks them done. A
reply the human approves is queued here (`POST /api/v2/mentions/{id}/reply`) and posted as them by
the gateway, which is the only process holding Slack tokens.

Only the human a mention belongs to can read or change it, the owner included; bots never can.
"""

import datetime as dt
import json

from fastapi import Request
from pydantic import Field

from .models import Contract
from .store import H, Problem

# The tables are `MENTIONS_SCHEMA` in store.py.
# `mentions.status`: pending (context not read yet; not shown), open, done, ignored (a channel
# shared outside the company). `mention_items.kind`: mention (names the human), message (anyone
# else later in the thread), reply (the human's own: sent from Slack, or queued here and posted
# by the gateway). `state`: new (context to read), ready, and for a queued reply ready, sending,
# sent, failed, uncertain.
VISIBLE = ("open", "done")
TEXT_CHARS = 4_000
CONTEXT_LINES = 30
APP_TOKEN_ENV = "SLACK_MENTIONS_APP_TOKEN"
USER_TOKEN_SUFFIX = "_SLACK_USER_TOKEN"


def user_token_env(person):
    """The vault's bot variable name for this human's Slack user token: their id in capitals."""
    return person.upper().replace("-", "_") + USER_TOKEN_SUFFIX


def slack_time(ts):
    """A Slack `ts` as the hub's time, in `H.now()`'s format so the two sort together."""
    try:
        return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    except (TypeError, ValueError, OverflowError, OSError):
        return H.now()


def record_slack(c, person, channel, ts, thread, author_id, text, kind="mention", now=None):
    """One Slack message into a human's mention of that thread. A `mention` opens the thread's
    mention (pending until its context is read) or joins it; `message` and `reply` only join one
    that exists. Returns the mention id, or None when there is nothing to join or it is a repeat."""
    now = now or H.now()
    row = c.execute("SELECT id,status FROM mentions WHERE person=? AND source='slack' AND place=? AND thread=?",
                    (person, channel, thread)).fetchone()
    if row is None:
        if kind != "mention":
            return None
        mid = H.new_id()
        c.execute("INSERT INTO mentions(id,person,source,place,thread,status,created,updated,last_at) "
                  "VALUES(?,?,'slack',?,?,'pending',?,?,?)", (mid, person, channel, thread, now, now, slack_time(ts)))
    else:
        mid = row["id"]
        if row["status"] == "ignored":
            return None
    added = c.execute("INSERT OR IGNORE INTO mention_items(id,mention_id,kind,source_id,author_id,text,at,state,created,updated) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (H.new_id(), mid, kind, str(ts), author_id, str(text or "")[:TEXT_CHARS], slack_time(ts),
                       "new" if kind != "reply" else "sent", now, now)).rowcount
    if not added:
        return None
    c.execute("UPDATE mentions SET updated=?, last_at=max(last_at, ?) WHERE id=?", (now, slack_time(ts), mid))
    return mid


def view(c, row):
    """What the human's frontend reads for one mention."""
    items = [dict(r) for r in c.execute(
        "SELECT id,kind,author,author_id,text,at,permalink,state,error FROM mention_items WHERE mention_id=? "
        "ORDER BY at, created", (row["id"],))]
    return {"id": row["id"], "source": row["source"], "status": row["status"], "title": row["title"],
            "where": row["place_name"], "permalink": row["permalink"], "bot": row["bot"],
            "created": row["created"], "updated": row["updated"], "last_at": row["last_at"], "done_at": row["done_at"],
            "ready": not any(i["state"] == "new" for i in items),
            "items": items, "context": json.loads(row["context_json"] or "[]")}


def connected(c, person):
    """Whether this human's Slack is set up for mentions: the app's token and theirs are in the
    vault. Names only; nothing is decrypted. A frontend shows its Mentions section only then (or
    while they still have mentions), so nobody else sees an empty one."""
    want = {APP_TOKEN_ENV, user_token_env(person)}
    have = {r[0] for r in c.execute("SELECT env FROM credentials WHERE env IN (?,?) AND ciphertext IS NOT NULL",
                                    tuple(sorted(want)))}
    return have == want


def owned(c, who, mid):
    row = c.execute("SELECT * FROM mentions WHERE id=?", (mid,)).fetchone()
    if not row or row["person"] != H.actor_id(who.actor) or row["status"] not in VISIBLE:
        raise Problem("not_found", "Mention not found", 404)
    return row


class MentionUpdate(Contract):
    status: str | None = Field(default=None, pattern=r"^(open|done)$")
    bot: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")


class MentionReply(Contract):
    text: str = Field(min_length=1, max_length=TEXT_CHARS)


def install_mentions(app, store, mutate):
    def person(request, posting=False):
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "Mentions are a human's own", 403)
        if posting and who.via:
            # A reply goes out in Slack under the human's own name: only their own click sends it.
            raise Problem("forbidden", "Only the human can send a reply as themselves", 403)
        return who

    @app.get("/api/v2/mentions")
    def mentions(request: Request, since: str = "", limit: int = 100):
        """The caller's open mentions and those done in the last two weeks; with `since`, only
        what changed after it (a frontend polls with the newest `updated` it holds). `connected`
        says whether their Slack is set up for mentions at all."""
        who = person(request)
        with store.read() as c:
            rows = c.execute(
                "SELECT * FROM mentions WHERE person=? AND status IN ('open','done') AND updated>? "
                "AND (status='open' OR done_at>=?) ORDER BY updated DESC LIMIT ?",
                (H.actor_id(who.actor), since or "", H.shift(H.now(), days=-14), max(1, min(limit, 200)))).fetchall()
            return {"mentions": [view(c, r) for r in rows], "now": H.now(),
                    "connected": connected(c, H.actor_id(who.actor))}

    @app.get("/api/v2/mentions/{mid}")
    def mention(mid: str, request: Request):
        who = person(request)
        with store.read() as c:
            return view(c, owned(c, who, mid))

    @app.post("/api/v2/mentions/{mid}")
    def update(mid: str, request: Request, body: MentionUpdate):
        """Mark it done or open again, or record which bot's context drafts it (set once)."""
        who = person(request)

        def change(c):
            row = owned(c, who, mid)
            now = H.now()
            if body.status and body.status != row["status"]:
                c.execute("UPDATE mentions SET status=?,done_at=?,updated=? WHERE id=?",
                          (body.status, now if body.status == "done" else None, now, mid))
            if body.bot and not row["bot"]:
                c.execute("UPDATE mentions SET bot=?,updated=? WHERE id=?", (body.bot, now, mid))
            return view(c, c.execute("SELECT * FROM mentions WHERE id=?", (mid,)).fetchone())
        return mutate(request, body, change)

    @app.post("/api/v2/mentions/{mid}/reply")
    def reply(mid: str, request: Request, body: MentionReply):
        """Queue the human's approved reply; the gateway posts it as them in the thread."""
        who = person(request, posting=True)

        def queue(c):
            row = owned(c, who, mid)
            if row["source"] != "slack":
                raise Problem("unsupported", "Only a Slack mention can be answered from here", 422)
            now, iid = H.now(), H.new_id()
            c.execute("INSERT INTO mention_items(id,mention_id,kind,source_id,author_id,text,at,state,created,updated) "
                      "VALUES(?,?,'reply',?,?,?,?,'ready',?,?)", (iid, mid, "queued:" + iid, who.actor, body.text, now, now, now))
            c.execute("UPDATE mentions SET updated=? WHERE id=?", (now, mid))
            H.event(c, who.actor, "mention.reply", mid, {"item": iid})
            return {"item": iid, "state": "ready"}
        return mutate(request, body, queue)
