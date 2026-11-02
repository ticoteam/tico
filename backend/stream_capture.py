"""A run's raw provider stream, kept for a week to turn into a replay fixture (`hub run export --fixture`).

A bot whose `capture_stream` is on has its runner record every frame it wrote to the harness and every
frame it read back (runner-rs/src/capture.rs), scrubbed of the turn's secrets and capped at about 5 MB.
A raw stream can hold customer text, so only owners and engineering bots read it, and only for a run
they may already see.
"""

import json
import re

from . import task_privacy as privacy
from .store import H, Problem

KEEP_DAYS = 7
# The runner's 5 MB cap plus its truncation marker and the JSON around it.
MAX_FRAMES = 5_200_000
# Credential shapes masked again here, whatever the runner missed: provider keys, GitHub and Slack tokens,
# AWS access key ids and bearer headers.
TOKEN_RE = re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
                      r"|github_pat_[A-Za-z0-9_]{20,}|xox[abeprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"
                      r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}=*")
MASK = "••••"
# A fixture's expected events: enough for any replay; a run with more says so instead of growing the answer.
MAX_EVENTS = 5000


def wanted(config):
    return (config or {}).get("capture_stream") == "on"


def sweep(c):
    c.execute("DELETE FROM attempt_captures WHERE created_at<?", (H.shift(H.now(), days=-KEEP_DAYS),))
    c.execute("DELETE FROM attempt_capture_drops WHERE created_at<?", (H.shift(H.now(), days=-KEEP_DAYS),))


def forget_bot(c, bot):
    """Capture turned off: what was kept goes with it."""
    c.execute("DELETE FROM attempt_captures WHERE bot=?", (bot,))


def mask(text):
    return TOKEN_RE.sub(MASK, text)


def scrub_value(c, value, label, attempt_id="", conversation_id=""):
    """A secret taken out after the run (backend/credential_cards.py) leaves the raw streams that carried it too:
    the run named, and every run in the conversation named."""
    forms = {value, json.dumps(value)[1:-1]}
    ids = {attempt_id} if attempt_id else set()
    if conversation_id:
        ids.update(r[0] for r in c.execute("SELECT attempt_id FROM attempt_conversations WHERE conversation_id=?",
                                           (conversation_id,)))
    # A run not yet settled hands its stream in later, holding the value: marked (no secret kept), it is not stored.
    # That includes a lapsed lease (`expired`), which the runner's next call restores before it uploads.
    for aid in ids:
        c.execute("INSERT OR IGNORE INTO attempt_capture_drops(attempt_id,created_at) SELECT id,? FROM attempts "
                  "WHERE id=?", (H.now(), aid))
    for aid in ids:
        # A reason can quote the model error that contained the removed value too.
        diagnostic = c.execute("SELECT end_reason,last_event FROM attempt_diagnostics WHERE attempt_id=?", (aid,)).fetchone()
        if diagnostic:
            fields = [diagnostic['end_reason'], diagnostic['last_event']]
            for index, field in enumerate(fields):
                if field:
                    for form in sorted(forms, key=len, reverse=True):
                        field = field.replace(form, label)
                    fields[index] = field
            c.execute("UPDATE attempt_diagnostics SET end_reason=?,last_event=? WHERE attempt_id=?", (*fields, aid))
        # Always-on stderr can contain the same customer material as the opt-in stream.
        c.execute("UPDATE attempt_diagnostics SET stderr_tail=NULL,stderr_at=NULL WHERE attempt_id=?", (aid,))
        row = c.execute("SELECT frames FROM attempt_captures WHERE attempt_id=?", (aid,)).fetchone()
        if row and any(f in row["frames"] for f in forms):
            frames = row["frames"]
            for form in sorted(forms, key=len, reverse=True):
                frames = frames.replace(form, label)
            c.execute("UPDATE attempt_captures SET frames=?,bytes=? WHERE attempt_id=?",
                      (frames, len(frames.encode()), aid))


def keep(c, row, body):
    """A runner hands in its attempt's stream (`row` is the attempt, already checked as that runner's)."""
    sweep(c)
    found = c.execute("SELECT config FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
    if not wanted(json.loads(found[0] or "{}") if found else {}):
        # Turned off since the turn began, or never on: nothing is kept.
        raise Problem("capture_off", "This bot does not capture its provider stream", 409)
    if c.execute("SELECT 1 FROM attempt_capture_drops WHERE attempt_id=?", (row["id"],)).fetchone():
        # A secret was taken out while this run was going; the stream may still carry it.
        c.execute("DELETE FROM attempt_captures WHERE attempt_id=?", (row["id"],))
        return {"kept": False, "reason": "a secret was removed during this run"}
    if json.loads(row["result"] or "{}").get("held_reply"):
        # The org rule check held this run's reply (backend/org_rule_check.py): its stream would carry it again.
        c.execute("DELETE FROM attempt_captures WHERE attempt_id=?", (row["id"],))
        return {"kept": False, "reason": "this run's reply was held by an org rule"}
    frames = mask(body.frames)
    c.execute("INSERT INTO attempt_captures(attempt_id,bot,provider,frames,truncated,dropped_bytes,bytes,created_at) "
              "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET provider=excluded.provider,"
              "frames=excluded.frames,truncated=excluded.truncated,dropped_bytes=excluded.dropped_bytes,"
              "bytes=excluded.bytes,created_at=excluded.created_at",
              (row["id"], row["bot"], body.provider, frames, int(body.truncated), body.dropped_bytes,
               len(frames.encode()), H.now()))
    return {"kept": True, "bytes": len(frames.encode()), "truncated": body.truncated}


def may_read(c, who):
    if who.role == "owner":
        return True
    if who.role != "bot" or not str(who.actor).startswith("bot:"):
        return False
    team = c.execute("SELECT team FROM bot_config WHERE bot=?", (who.actor[4:],)).fetchone()
    return bool(team and team["team"] == "engineering")


def require_run(c, who, auth, aid):
    """The checks the run list makes (app.py turns): the bot, the conversation, the message and the run itself."""
    attempt = c.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if not attempt:
        raise Problem("not_found", "Turn not found", 404)
    auth.require_read(c, who, attempt["bot"])
    msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0])
    if msg:
        auth.conversation(c, who, msg["conversation_id"])
        privacy.require_message(c, who, msg)
    else:
        # A deleted message still has its room: the run is read only by those who may read that room.
        rooms = [r[0] for r in c.execute("SELECT conversation_id FROM attempt_conversations WHERE attempt_id=?", (aid,))]
        if not rooms:
            raise Problem("not_found", "Turn not found", 404)
        for room in rooms:
            auth.conversation(c, who, room)
    privacy.require_attempt(c, who, aid)
    return attempt, msg


def export(c, who, auth, aid, full=True):
    """The run as a three-part replay fixture: what it was given, the raw stream, and what Tico made of it.
    Without `full`, only what was captured and how big it is (for a bot's context)."""
    if not may_read(c, who):
        raise Problem("forbidden", "Only owners and engineering bots read a run's raw stream", 403)
    attempt, msg = require_run(c, who, auth, aid)
    from .org_rule_check import hides_run
    if hides_run(c, attempt):
        # Its reply is not checked yet, or was held (backend/org_rule_check.py): the stream and events would show it.
        raise Problem("not_found", "This run's stream is held back until its reply is checked", 404)
    cap = c.execute("SELECT * FROM attempt_captures WHERE attempt_id=? AND created_at>=?",
                    (aid, H.shift(H.now(), days=-KEEP_DAYS))).fetchone()
    from . import run_diagnostics
    diagnostic = c.execute("SELECT stderr_tail,stderr_at FROM attempt_diagnostics WHERE attempt_id=? AND stderr_at>=?",
                           (aid, H.shift(H.now(), days=-KEEP_DAYS))).fetchone()
    stderr = diagnostic['stderr_tail'] if diagnostic else ''
    if not cap and not stderr:
        raise Problem("not_found", "No stream or stderr was captured for this run, or it is over a week old", 404)
    stream_captured = bool(cap)
    cap = dict(cap) if cap else {"provider": "runner", "frames": "", "bytes": 0, "truncated": False,
                                "dropped_bytes": 0, "created_at": diagnostic['stderr_at']}
    diagnostics = run_diagnostics.metadata(c, aid)
    if not full:
        return {"attempt": {"id": aid, "bot": attempt["bot"], "state": attempt["state"]}, "provider": cap["provider"],
                "bytes": cap["bytes"], "stderr_bytes": len((stderr or "").encode()), "stream_captured": stream_captured,
                **diagnostics, "truncated": bool(cap["truncated"]), "captured": cap["created_at"],
                "fixture": f"hub run export {aid} --fixture"}
    # A deleted message's text is not served again; the stream may still hold it until the week is out.
    message = ({"kind": msg["kind"], "from": msg["from_actor"], "body": mask(msg["body"] or "")} if msg
               else {"deleted": True})
    config = c.execute("SELECT config FROM bot_config WHERE bot=?", (attempt["bot"],)).fetchone()
    declared = json.loads(config[0] or "{}") if config else {}
    events = [{"seq": e["seq"], "kind": e["kind"], "payload": json.loads(e["payload"])}
              for e in c.execute("SELECT seq,kind,payload FROM attempt_events WHERE attempt_id=? ORDER BY seq "
                                 "LIMIT ?", (aid, MAX_EVENTS + 1))]
    result = json.loads(attempt["result"]) if attempt["result"] else {}
    return {
        "attempt": {"id": aid, "bot": attempt["bot"], "state": attempt["state"], "created_at": attempt["created_at"],
                    "started_at": attempt["started_at"], "finished_at": attempt["finished_at"], **diagnostics},
        "provider": cap["provider"],
        "stream_captured": stream_captured,
        "stderr_tail": stderr or "",
        "truncated": bool(cap["truncated"]),
        "dropped_bytes": cap["dropped_bytes"],
        "captured": cap["created_at"],
        "input": {"message": message,
                  "config": {k: declared.get(k) for k in ("runtime", "harness", "model", "reasoning_effort")
                             if declared.get(k) is not None}},
        "transcript": cap["frames"],
        "output": {"events": json.loads(mask(json.dumps(events[:MAX_EVENTS]))),
                   "events_truncated": len(events) > MAX_EVENTS,
                   "result": json.loads(mask(json.dumps(result))),
                   "final_text": mask(attempt["final_text"] or "")},
    }
