"""Stop quarantined turns through the normal Stop protocol and preserve their reason.

The first runner call records the quarantine reason on the attempt. Renewal returns
it with Stop. Completion requeues the work of the run (`requeues`) until the bot becomes active again. A quarantine
stops the run the hub itself chose to cut, so it is judged as a computer's reported restart is (retry_facts): only a
consumed approval, a fallback whose work is unknown or a named outside action (merge, publish, send, payment) holds it
for review. Such a run resumes with a note (`requeue`) that says it was stopped by a quarantine and to check what it
already did first.

A restart (`restart`) is the same keeper Stop for another reason: a computer's model subscription changed under the
run (backend/model_subscriptions.py), so its work goes back in the queue to start fresh on the new login. It is judged
the same way.
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
    if row["stop_requested_at"]:
        return row
    said = line(c, row["bot_id"])
    c.execute("UPDATE attempts SET stop_requested_at=? WHERE id=? AND stop_requested_at IS NULL", (H.KEEPER, row["id"]))
    H.event(c, H.KEEPER, "attempt.stop", row["id"], {"bot": row["bot_id"], "quarantine": said})
    return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                     "FROM attempts a JOIN assignments x ON x.bot=a.bot_id WHERE a.id=?", (row["id"],)).fetchone()


def restart(c, row, line):
    """Record a keeper Stop on a live attempt whose computer's sign-in for its runtime changed: the run ends, its work
    goes back in the queue (`requeues`) and starts fresh. A Stop already recorded stands. Returns whether it was new."""
    if row["stop_requested_at"]:
        return False
    c.execute("UPDATE attempts SET stop_requested_at=? WHERE id=? AND stop_requested_at IS NULL", (H.KEEPER, row["id"]))
    H.event(c, H.KEEPER, "attempt.stop", row["id"], {"bot": row["bot_id"], "restart": line})
    return True


def _stop_detail(c, row):
    if not row["stop_requested_at"] or row["stop_requested_at"] != H.KEEPER:
        return {}
    found = c.execute("SELECT detail FROM events WHERE action='attempt.stop' AND target=? "
                      "ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
    return (json.loads(found[0] or "{}") or {}) if found else {}


def stopped_by(c, row):
    """The line a keeper Stop on this attempt was recorded with (a quarantine's, or a restart's), or None for a
    person's Stop or none."""
    detail = _stop_detail(c, row)
    return detail.get("quarantine") or detail.get("restart")


def restarted(c, row):
    """Whether this attempt's Stop is a restart (`restart`), not a quarantine's."""
    return bool(_stop_detail(c, row).get("restart"))


def requeues(c, row):
    """Whether a quarantine-stopped attempt's work goes back in the queue to run when the quarantine lifts. Judged as a
    computer's reported restart is (`retry_facts`): a consumed approval, a fallback whose work is unknown or a named
    outside action (a merge, publish, send or payment, which may already have happened) holds it for review
    (`auto_reconcile`). Any other run, a shell or an edit included, goes back in the queue; one that never started has
    nothing to repeat."""
    from .execution import outside_actions, retry_facts
    job = c.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
    if not job:
        return False
    facts = retry_facts(c, {**dict(job), "attempt_id": row["id"]}, row["started_at"], H.now())
    if facts["consumed"] or facts["fallback_unknown"]:
        return False
    return not row["started_at"] or not outside_actions(c, {**dict(job), "attempt_id": row["id"]},
                                                     row["started_at"], H.now())


def resume_note(at):
    """The note a run stopped by a quarantine resumes with: what stopped it, and to check what it already did first."""
    return (f"You were interrupted at {at} UTC by a quarantine. Check what already happened first, then continue from "
            "there. No consumed approval or named irreversible outside action was recorded.")


def requeue(c, row, at):
    """Record, as the sweep records a resume (`job_recovery`), the note a requeued quarantine-stopped run starts with:
    the claim hands it to the bot with the run."""
    c.execute("INSERT OR REPLACE INTO job_recovery(job_id,attempt_id,decision,note,actor,created_at) "
              "VALUES(?,?,?,?,?,?)", (row["job_id"], row["id"], "resume", resume_note(at), H.KEEPER, H.now()))


def then(c, row, held):
    """What a stopped turn's notice says happens next."""
    if restarted(c, row):
        return RESTART_HELD if held else RESTARTED
    return HELD if held else REQUEUED


REQUEUED = ("This run goes back in the queue and runs again when the quarantine lifts, after checking what it already "
            "did.")
HELD = ("This run may already have done something outside Tico that running it again could repeat (a named merge, "
        "send or payment, or an approval it used), so it is held for review instead of running again.")
RESTARTED = "This run goes back in the queue and starts fresh on the new sign-in."
RESTART_HELD = HELD
