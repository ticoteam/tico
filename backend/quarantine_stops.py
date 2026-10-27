"""Stop quarantined turns through the normal Stop protocol and preserve their reason.

The first runner call records the quarantine reason on the attempt. Renewal returns
it with Stop. Completion requeues the work of a run that is safe to repeat (`requeues`) until the bot becomes
active again; any other run is held for review like a lost one, so a lift never repeats an outside action.
"""
import json

from .store import H


def line(c, bot, at=None):
    """What a stopped turn's end reason says: "Quarantined: <reason>, resumes about HH:MM UTC", or "until a person
    clears it" for a quarantine only a person lifts. An escape names what the safety check stopped, never the refused
    words: the end reason is read more widely than the review."""
    at = at or H.now()
    resumes = H.quarantine_resumes(c, bot)
    if resumes["auto"]:
        why = H.quarantine_reason(c, bot)
    else:
        review = H.quarantine_review(c, bot) or {}
        why = f"the safety check stopped a {review['what']}" if review.get("what") else "a person must review it"
    until = resumes["resumes_at"]
    when = (f"resumes about {until[11:16]} UTC" + ("" if until[:10] == at[:10] else f" on {until[:10]}")
            if until else "until a person clears it")
    return f"Quarantined: {why}, {when}"


def stop(c, row):
    """Record the quarantine's Stop on `row`, a leased or running attempt of a quarantined bot whose runner still holds
    it, and return the attempt read again. A Stop already recorded (a person's, or this one) stands."""
    if row["stop_requested"]:
        return row
    said = line(c, row["bot"])
    c.execute("UPDATE attempts SET stop_requested=? WHERE id=? AND stop_requested IS NULL", (H.KEEPER, row["id"]))
    H.event(c, H.KEEPER, "attempt.stop", row["id"], {"bot": row["bot"], "quarantine": said})
    return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                     "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (row["id"],)).fetchone()


def stopped_by(c, row):
    """The quarantine line a Stop on this attempt was recorded with, or None for a person's Stop or none."""
    if not row["stop_requested"] or row["stop_requested"] != H.KEEPER:
        return None
    found = c.execute("SELECT detail_json FROM events WHERE action='attempt.stop' AND target=? "
                      "ORDER BY ts DESC LIMIT 1", (row["id"],)).fetchone()
    return (json.loads(found[0] or "{}") or {}).get("quarantine") if found else None


def requeues(c, row):
    """Whether a quarantine-stopped attempt's work goes back in the queue: only when running it again repeats nothing,
    judged by `retry_facts` as the sweep judges any other lost run (read-only calls, a host that reports its calls, no
    approval used), or when it never started and reported nothing. Any other run may already have pushed, sent or
    paid, so its jobs are held for review (`auto_reconcile`) instead of running again when the quarantine lifts."""
    from .execution import retry_facts
    job = c.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
    if not job:
        return False
    facts = retry_facts(c, {**dict(job), "attempt_id": row["id"]}, row["started"], H.now())
    return facts["safe"] or not row["started"] and not facts["calls"] and not facts["consumed"]


REQUEUED = "This run goes back in the queue and runs again when the quarantine lifts."
HELD = "This run may already have changed something outside Tico, so it is held for review instead of running again."
