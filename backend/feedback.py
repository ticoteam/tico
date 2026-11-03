"""Feedback on bot work: a person's thumbs up or down, with an optional reason, on something a bot made.

- Subjects: a message a bot wrote (Assistant chat, threads, DMs, task comments), a task a bot filed (its requester is a
  bot) and an approval a bot asked for. Anything else is refused (422).
- One row per person and subject (`feedbacks`, migration 0041): rating again replaces it. The row names the bot and,
  where the work links to a runner attempt, the attempt, its model and harness, for training. It keeps no copy of the
  rated words: deleting a message erases them, and the reason is the person's own.
- Privacy: every read, write, count and list asks whether the viewer reads the subject, with the checks the subject's
  own pages use (`threads._reactable` for a message, `Auth.task` for a task, the approval route's checks for an
  approval). An unreadable subject answers 404, and its feedback is left out of every answer, so a private Assistant
  chat's feedback is its person's alone. Only people rate; a bot or a computer never does.
- 👎 can tell the bot (a quiet note for its next run, tied to the rated message so a private conversation's feedback
  stays in that conversation's session, threads.note_fits_run) or make a rule (an internal org rule drafted from the
  reason, added as the person through backend/org_rules.py, so it is active or suggested as their rights say).
"""
import json
import sqlite3
from typing import Literal
from urllib.parse import quote

from fastapi import Request
from pydantic import BaseModel, Field

from . import hubdb as H
from . import org_rules as O
from . import threads
from . import task_privacy as privacy
from .models import Contract
from .store import Problem

SUBJECTS = ("message", "task", "approval")
RATINGS = ("up", "down")
REASON_MAX = 1000
IDS_MAX = 200                   # the subjects one markers read names
LIST_DEFAULT = 50
LIST_MAX = 100
LIST_BYTES = 200_000            # one list page's serialized size; a page always holds at least one row
LIST_SCAN = 1000                # rows one list page reads past unreadable ones before it hands back a cursor
RECENT_REASONS = 5
RATED_TEXT_MAX = 1500           # the rated words a rule draft is given, at most
RULE_IF_BROKEN = "warn"         # a rule from one 👎 goes through with a note until a person makes it stricter
# The drafter, for tests: `write(system, user) -> {"rule": "..."}`. None reads the server's writer at the call.
WRITER = None

DRAFT_SYSTEM = (
    "You turn a person's complaint about a bot's work into one org rule for that bot: one plain sentence, an "
    "instruction the bot can follow every time, at most 200 characters, with no names of people and nothing copied "
    "from the work beyond what the rule needs. Reply with one JSON object and nothing else: {\"rule\": \"...\"}.")


class Nothing(BaseModel):
    """The body of a route that takes none (mutate keys its idempotency on the body)."""


class FeedbackGive(Contract):
    subject_type: Literal["message", "task", "approval"]
    subject_id: str = Field(min_length=1, max_length=128)
    rating: Literal["up", "down"]
    reason: str = Field(default="", max_length=REASON_MAX)
    tell_bot: bool = False


class FeedbackRule(Contract):
    text: str = Field(min_length=1, max_length=O.TEXT_MAX)
    all_bots: bool = False


# ----------------------------------------------------------------------------- subjects
def _person(who):
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Feedback is a person's: a bot or a computer gives none", 403)


def _missing(subject_type):
    return Problem("not_found", f"{subject_type.capitalize()} not found", 404)


def _attempt(c, attempt_id):
    """(attempt id, model, harness) of a run, or Nones; a local hub has no attempts table."""
    if not attempt_id:
        return None, None, None
    try:
        row = c.execute("SELECT id, coalesce(handed_model, model) AS model, handed_runtime FROM attempts WHERE id=?",
                        (attempt_id,)).fetchone()
    except sqlite3.OperationalError:       # a hub with no runs has no attempts table
        row = None
    return (row["id"], row["model"], row["handed_runtime"]) if row else (attempt_id, None, None)


def _made_in(c, action, target):
    """The run a task or approval was made in, as hubdb.event stamped it."""
    row = c.execute("SELECT detail FROM events WHERE action=? AND target=? ORDER BY created_at, id LIMIT 1",
                    (action, target)).fetchone()
    return (H._json(row["detail"], {}) or {}).get("attempt_id") if row else None


def _readable_message(c, auth, who, message_id):
    _, message = threads._reactable(c, threads.viewer(auth, who), message_id)
    return message


def _readable_task(c, auth, who, task_id):
    try:
        return auth.task(c, who, task_id)
    except Problem:
        raise _missing("task") from None


def _readable_approval(c, auth, who, approval_id):
    """The approval, when the viewer reads it as GET /api/v2/approvals/{id} lets them."""
    try:
        row = auth.approval(c, who, approval_id)
        privacy.require_payload(c, who, row)
        privacy.require_message(c, who, H.message(c, row["message_id"]))
    except Problem:
        raise _missing("approval") from None
    return row


def subject(c, auth, who, subject_type, subject_id):
    """What feedback on one subject records, when the viewer reads it (404 otherwise) and a bot made it (422 otherwise):
    {bot_id, conversation_id, attempt_id, model, harness, revision, note_message_id, link, text}. `note_message_id` is
    the message a note to the bot is tied to; `text` the rated words, for a rule draft only."""
    if subject_type == "message":
        message = _readable_message(c, auth, who, subject_id)
        bot = message.get("sender_id")
        if not H.is_bot(bot):
            raise Problem("not_bot", "Only a bot's message takes feedback", 422)
        refs = message.get("refs") if isinstance(message.get("refs"), dict) else H._json(message.get("refs"), {}) or {}
        run = (refs.get("run") or {}) if isinstance(refs.get("run"), dict) else {}
        revision = c.execute("SELECT revision FROM messages WHERE id=?", (message["id"],)).fetchone()
        return {"bot_id": bot, "conversation_id": message.get("conversation_id"),
                "attempt": _attempt(c, run.get("attempt_id")), "revision": revision[0] if revision else None,
                "note_message_id": message["id"],
                "link": "/#/thread/%s?m=%s" % (quote(message["conversation_id"] or "", safe=""), quote(message["id"], safe="")),
                "text": message.get("body") or ""}
    if subject_type == "task":
        task = _readable_task(c, auth, who, subject_id)
        bot = task.get("requester_id")
        if not H.is_bot(bot):
            raise Problem("not_bot", "Only a task a bot filed takes feedback", 422)
        latest = c.execute("SELECT id FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                           "ORDER BY created_at DESC, rowid DESC LIMIT 1", (task.get("conversation_id"),)).fetchone()
        return {"bot_id": bot, "conversation_id": task.get("conversation_id"),
                "attempt": _attempt(c, _made_in(c, "task.create", task["id"])), "revision": task.get("version"),
                "note_message_id": latest[0] if latest else None, "link": "/#/task/" + quote(task["id"], safe=""),
                "text": f"{task.get('title') or ''}\n\n{task.get('body') or ''}".strip()}
    if subject_type == "approval":
        row = _readable_approval(c, auth, who, subject_id)
        bot = row.get("requested_by_id")
        if not H.is_bot(bot):
            raise Problem("not_bot", "Only an approval a bot asked for takes feedback", 422)
        message = H.message(c, row["message_id"]) or {}
        link = ("/#/task/" + quote(row["task_id"], safe="")) if row.get("task_id") else \
            "/#/thread/%s?m=%s" % (quote(message.get("conversation_id") or "", safe=""), quote(row["message_id"], safe=""))
        return {"bot_id": bot, "conversation_id": message.get("conversation_id"),
                "attempt": _attempt(c, _made_in(c, "approval.request", row["id"])), "revision": None,
                "note_message_id": row["message_id"], "link": link,
                "text": f"{row.get('kind')}: " + json.dumps(row.get("payload") or {}, sort_keys=True, default=str)}
    raise Problem("validation", "subject_type is message, task or approval", 422)


def readable(c, auth, who, subject_type, subject_id, memo=None):
    """Whether the viewer reads this subject now (the per-subject check behind every list and count)."""
    key = (subject_type, subject_id)
    if memo is not None and key in memo:
        return memo[key]
    check = {"message": _readable_message, "task": _readable_task, "approval": _readable_approval}.get(subject_type)
    try:
        found = bool(check) and check(c, auth, who, subject_id) is not None
    except Problem:
        found = False
    if memo is not None:
        memo[key] = found
    return found


# ----------------------------------------------------------------------------- views
def note_status(c, note_id):
    """waiting, carried, settled or canceled, read live from the note; None without one."""
    note = H.note(c, note_id) if note_id else None
    if not note:
        return None
    if note.get("canceled_at"):
        return "canceled"
    if note.get("settled_at"):
        return "settled"
    return "waiting" if H.note_waiting(c, note) else "carried"


def view(c, row):
    row = dict(row)
    rule = c.execute("SELECT status FROM org_rules WHERE id=?", (row["org_rule_id"],)).fetchone() \
        if row.get("org_rule_id") else None
    return {"id": row["id"], "person_id": row["person_id"], "subject_type": row["subject_type"],
            "subject_id": row["subject_id"], "bot_id": row["bot_id"], "rating": row["rating"], "reason": row["reason"],
            "outcome": row["outcome"], "note_id": row["note_id"], "note_status": note_status(c, row["note_id"]),
            "org_rule_id": row["org_rule_id"], "rule_status": rule["status"] if rule else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _row(c, feedback_id):
    found = c.execute("SELECT * FROM feedbacks WHERE id=?", (feedback_id,)).fetchone()
    return dict(found) if found else None


def own_row(c, auth, who, feedback_id):
    """The caller's own feedback row on a subject they still read; 404 otherwise (another's row is never confirmed)."""
    _person(who)
    row = _row(c, feedback_id)
    if not row or row["person_id"] != who.actor or not readable(c, auth, who, row["subject_type"], row["subject_id"]):
        raise Problem("not_found", "Feedback not found", 404)
    return row


# ----------------------------------------------------------------------------- writes
def _cancel_waiting(c, who, note_id):
    """Take back a note no run has carried yet: the feedback it said is gone or replaced."""
    if note_status(c, note_id) == "waiting":
        H.note_cancel(c, who.actor, note_id)
        return True
    return False


def _tell(c, who, row, subj):
    """A quiet note to the bot for its next run, tied to the rated message so that a private conversation's feedback
    reaches only that conversation's session (threads.note_fits_run). It bypasses the /notes route's write-level and
    bot-contact gates on purpose: reading the subject is what lets a person rate the bot's work, and the note is about
    that work alone. hubdb's own checks still apply (an active or paused bot, a person sender)."""
    name = H.actor_name(c, who.actor)
    reason = row["reason"]
    said = f': "{reason}"' if reason else ""
    body = (f"👎 from {name} on {subj['link']}{said}. Fix this going forward. If it is a lasting lesson, record it in "
            "your memory; a personal preference belongs with that person.")
    note = H.note_create(c, who.actor, row["bot_id"], body)
    c.execute("UPDATE notes SET message_id=? WHERE id=?", (subj["note_message_id"], note["id"]))
    return note["id"]


def give(c, auth, who, body):
    """Rate a subject: the caller's row for it is made or replaced. A replaced row's note that no run carried yet is
    taken back; `tell_bot` (a 👎 only) leaves a new one with this reason."""
    _person(who)
    H._writer(c, who.actor)
    if body["tell_bot"] and body["rating"] != "down":
        raise Problem("validation", "tell_bot goes with a 👎", 422)
    subj = subject(c, auth, who, body["subject_type"], body["subject_id"])
    attempt_id, model, harness = subj["attempt"]
    now = H.now()
    reason = str(body.get("reason") or "").strip()[:REASON_MAX]
    old = c.execute("SELECT * FROM feedbacks WHERE person_id=? AND subject_type=? AND subject_id=?",
                    (who.actor, body["subject_type"], body["subject_id"])).fetchone()
    old = dict(old) if old else None
    row = {"person_id": who.actor, "subject_type": body["subject_type"], "subject_id": body["subject_id"],
           "bot_id": subj["bot_id"], "conversation_id": subj["conversation_id"], "attempt_id": attempt_id,
           "model": model, "harness": harness, "subject_revision": subj["revision"], "rating": body["rating"],
           "reason": reason, "updated_at": now}
    if old is None:
        row.update({"id": H.new_id(), "outcome": "none", "note_id": None, "org_rule_id": None, "created_at": now})
    else:
        row.update({k: old[k] for k in ("id", "outcome", "note_id", "org_rule_id", "created_at")})
        if _cancel_waiting(c, who, old["note_id"]):
            row["note_id"] = None
            row["outcome"] = "rule" if old["org_rule_id"] else "none"
    if body["tell_bot"]:
        row["note_id"] = _tell(c, who, row, subj)
        row["outcome"] = "told_bot"
    c.execute("INSERT INTO feedbacks(id,person_id,subject_type,subject_id,bot_id,conversation_id,attempt_id,model,harness,"
              "subject_revision,rating,reason,outcome,note_id,org_rule_id,created_at,updated_at) VALUES(:id,:person_id,"
              ":subject_type,:subject_id,:bot_id,:conversation_id,:attempt_id,:model,:harness,:subject_revision,:rating,"
              ":reason,:outcome,:note_id,:org_rule_id,:created_at,:updated_at) ON CONFLICT(person_id,subject_type,"
              "subject_id) DO UPDATE SET bot_id=excluded.bot_id,conversation_id=excluded.conversation_id,"
              "attempt_id=excluded.attempt_id,model=excluded.model,harness=excluded.harness,"
              "subject_revision=excluded.subject_revision,rating=excluded.rating,reason=excluded.reason,"
              "outcome=excluded.outcome,note_id=excluded.note_id,updated_at=excluded.updated_at", row)
    # Counts and kinds only: the reason is the person's words and stays in its row.
    H.event(c, who.actor, "feedback.given", row["id"], {"subject_type": row["subject_type"], "rating": row["rating"],
                                                        "bot": row["bot_id"], "told_bot": bool(body["tell_bot"])})
    return view(c, _row(c, row["id"]))


def remove(c, auth, who, feedback_id):
    row = own_row(c, auth, who, feedback_id)
    _cancel_waiting(c, who, row["note_id"])
    c.execute("DELETE FROM feedbacks WHERE id=?", (row["id"],))
    H.event(c, who.actor, "feedback.removed", row["id"], {"subject_type": row["subject_type"], "bot": row["bot_id"]})
    return {"ok": True, "id": row["id"]}


def make_rule(c, auth, who, feedback_id, body):
    """An internal org rule from a 👎, added as the person (org_rules.add_rule): active when they manage its Who, else a
    suggestion. Who is the rated bot, or every bot with `all_bots`, which add_rule checks again as the wider scope."""
    row = own_row(c, auth, who, feedback_id)
    if row["rating"] != "down":
        raise Problem("validation", "A rule comes from a 👎", 422)
    scope = {"all_bots": True} if body.get("all_bots") else {"bots": [row["bot_id"]]}
    rule = O.add_rule(c, auth, who, {"kind": "internal", "text": body["text"], "who": scope, "if_broken": RULE_IF_BROKEN})
    c.execute("UPDATE feedbacks SET outcome='rule', org_rule_id=?, updated_at=? WHERE id=?", (rule["id"], H.now(), row["id"]))
    H.event(c, who.actor, "feedback.rule", row["id"], {"rule": rule["id"], "status": rule.get("status")})
    return {"feedback": view(c, _row(c, row["id"])), "rule": rule}


# ----------------------------------------------------------------------------- reads
def markers(c, auth, who, subject_type, subject_ids):
    """Every person's feedback on these subjects, for those the viewer reads."""
    auth.domain(who)
    if subject_type not in SUBJECTS:
        raise Problem("validation", "subject_type is message, task or approval", 422)
    ids = [i.strip() for i in str(subject_ids or "").split(",") if i.strip()]
    ids = list(dict.fromkeys(ids))
    if len(ids) > IDS_MAX:
        raise Problem("validation", f"Ask for at most {IDS_MAX} subjects at once", 422)
    seen = [i for i in ids if readable(c, auth, who, subject_type, i)]
    if not seen:
        return {"items": []}
    rows = c.execute("SELECT * FROM feedbacks WHERE subject_type=? AND subject_id IN (%s) ORDER BY created_at, id"
                     % ",".join("?" * len(seen)), [subject_type, *seen]).fetchall()
    return {"items": [view(c, r) for r in rows]}


def _cursor(value):
    created, sep, ident = str(value or "").partition("~")
    if not sep:
        raise Problem("validation", "Use the next_cursor of the page before", 422)
    return created, ident


def listing(c, auth, who, args):
    """Feedback newest first, a page at a time, of the subjects the viewer reads: `bot_id`, `rating` and `since` (an ISO
    time or a duration such as 7d) narrow it. A page stops at `limit` rows, LIST_BYTES, or LIST_SCAN rows read."""
    from .views import since_time
    auth.domain(who)
    limit = args.get("limit") or LIST_DEFAULT
    if not 1 <= limit <= LIST_MAX:
        raise Problem("limit", f"limit is between 1 and {LIST_MAX}", 422)
    where, params = [], []
    if args.get("bot_id"):
        where.append("bot_id=?")
        params.append(H.resolve_actor(c, args["bot_id"]) or args["bot_id"])
    if args.get("rating"):
        if args["rating"] not in RATINGS:
            raise Problem("validation", "rating is up or down", 422)
        where.append("rating=?")
        params.append(args["rating"])
    if args.get("since"):
        where.append("created_at>=?")
        params.append(since_time(args["since"]))
    if args.get("cursor"):
        created, ident = _cursor(args["cursor"])
        where.append("(created_at<? OR (created_at=? AND id<?))")
        params += [created, created, ident]
    sql = "SELECT * FROM feedbacks" + (" WHERE " + " AND ".join(where) if where else "") + \
        " ORDER BY created_at DESC, id DESC LIMIT ?"
    rows = c.execute(sql, [*params, LIST_SCAN + 1]).fetchall()
    items, size, memo, consumed = [], 0, {}, -1
    for index, r in enumerate(rows):
        if index == LIST_SCAN or len(items) == limit:
            break
        if readable(c, auth, who, r["subject_type"], r["subject_id"], memo):
            item = view(c, r)
            grown = size + len(json.dumps(item, default=str))
            if items and grown > LIST_BYTES:
                break
            items.append(item)
            size = grown
        consumed = index
    more = consumed < len(rows) - 1
    last = rows[consumed] if consumed >= 0 else None
    return {"items": items, "next_cursor": f"{last['created_at']}~{last['id']}" if more and last else None}


def recent_reasons(c, who):
    """The caller's own five latest distinct reasons (compared trimmed and in lower case), with how often each was used."""
    _person(who)
    rows = c.execute(
        "SELECT lower(trim(reason)) AS norm, MAX(updated_at) AS last, COUNT(*) AS n FROM feedbacks "
        "WHERE person_id=? AND trim(reason)<>'' GROUP BY norm ORDER BY last DESC, norm LIMIT ?",
        (who.actor, RECENT_REASONS)).fetchall()
    out = []
    for r in rows:
        # The words as the person last wrote them, not the lowered key.
        shown = c.execute("SELECT trim(reason) FROM feedbacks WHERE person_id=? AND lower(trim(reason))=? "
                          "ORDER BY updated_at DESC, id DESC LIMIT 1", (who.actor, r["norm"])).fetchone()[0]
        out.append({"text": shown, "count": r["n"]})
    return {"reasons": out}


# ----------------------------------------------------------------------------- rule drafts
def _writer(c, store):
    if WRITER is not None:
        return WRITER
    from .judge import over_spend_cap
    from .updates import writer_for
    # The decision engine (backend/judge.py) answers fixed questions and writes no sentence, so a draft is written by
    # the server's writer (the daily-update drafts' model), held to Decide's monthly spend cap all the same.
    if over_spend_cap(c, store.settings) is not None:
        return None
    return writer_for(c, store.settings, store)


def draft_input(c, auth, who, feedback_id):
    """What a rule draft is written from, read in one snapshot: the reason, the bot and up to RATED_TEXT_MAX of the
    rated words. Nothing is stored."""
    row = own_row(c, auth, who, feedback_id)
    if row["rating"] != "down":
        raise Problem("validation", "A rule comes from a 👎", 422)
    subj = subject(c, auth, who, row["subject_type"], row["subject_id"])
    return {"reason": row["reason"], "bot": H.actor_name(c, row["bot_id"]), "subject_type": row["subject_type"],
            "rated": (subj["text"] or "")[:RATED_TEXT_MAX]}


def draft(write, given):
    """One sentence, or None when the model did not give one."""
    user = (f"Bot: {given['bot']}\nWhat was rated: a {given['subject_type']}\nThe person's reason: "
            f"{given['reason'] or '(none given)'}\nThe rated work:\n{given['rated']}")
    try:
        answer = write(DRAFT_SYSTEM, user)
    except Exception:  # noqa: BLE001 - a draft that fails leaves the person's own reason as the rule text
        return None
    text = " ".join(str((answer or {}).get("rule") or "").split())
    return text[:O.TEXT_MAX] or None


# ----------------------------------------------------------------------------- routes
def install(app, store, auth, mutate):
    @app.post("/api/v2/feedback")
    def feedback_give(request: Request, body: FeedbackGive):
        """Rate a message, task or approval a bot made: 👍 or 👎 with an optional reason. Rating again replaces it.
        `tell_bot` (a 👎 only) leaves the bot a note for its next run in that conversation."""
        who = request.state.identity
        return mutate(request, body, lambda c: give(c, auth, who, body.model_dump()))

    # Declared before /api/v2/feedback/{feedback_id} routes so these words are never read as an id.
    @app.get("/api/v2/feedback/list")
    def feedback_list(request: Request, bot_id: str | None = None, rating: str | None = None, since: str | None = None,
                      cursor: str | None = None, limit: int = LIST_DEFAULT):
        """Feedback newest first, paged, on the subjects the caller reads."""
        args = {"bot_id": bot_id, "rating": rating, "since": since, "cursor": cursor, "limit": limit}
        with store.read() as c:
            return listing(c, auth, request.state.identity, args)

    @app.get("/api/v2/feedback/recent-reasons")
    def feedback_recent_reasons(request: Request):
        """The caller's five latest distinct reasons, each with how often they used it."""
        with store.read() as c:
            return recent_reasons(c, request.state.identity)

    @app.get("/api/v2/feedback")
    def feedback_markers(request: Request, subject_type: str, subject_ids: str = ""):
        """Everyone's feedback on these subjects (comma-separated ids, at most 200) that the caller reads."""
        with store.read() as c:
            return markers(c, auth, request.state.identity, subject_type, subject_ids)

    @app.delete("/api/v2/feedback/{feedback_id}")
    def feedback_remove(request: Request, feedback_id: str):
        """Remove my feedback; a note it left that no run carried yet is taken back."""
        who = request.state.identity
        return mutate(request, Nothing(), lambda c: remove(c, auth, who, feedback_id))

    @app.post("/api/v2/feedback/{feedback_id}/rule-draft")
    def feedback_rule_draft(request: Request, feedback_id: str):
        """A one-sentence rule drafted from my 👎's reason and the rated work. Stores nothing; with no model (or Decide
        at its spend cap) the reason itself comes back, `drafted` false."""
        who = request.state.identity
        with store.read() as c:
            given = draft_input(c, auth, who, feedback_id)
            write = _writer(c, store)
        # The model call holds no lock and no transaction.
        text = draft(write, given) if write is not None else None
        return {"text": text or given["reason"], "drafted": text is not None}

    @app.post("/api/v2/feedback/{feedback_id}/rule")
    def feedback_rule(request: Request, feedback_id: str, body: FeedbackRule):
        """Make my 👎 an internal org rule for the bot (or all bots): active when I manage its Who, else suggested."""
        who = request.state.identity
        return mutate(request, body, lambda c: make_rule(c, auth, who, feedback_id, body.model_dump()))


__all__ = ["install", "give", "remove", "make_rule", "markers", "listing", "recent_reasons", "subject", "readable"]
