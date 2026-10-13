"""Go-lives waiting for a computer to get a bot's repository (backend/app.py go_live).

Each is one `registry_metadata` row, `go-live-pending:<bot>`: who asked, the options, the computer, and when it
was asked. The computer's readiness report finishes it (app.py `finish_pending_go_live`). One that has not finished
within EXPIRY_DAYS is marked failed and the person who asked is told once; so is one that fails to finish. A failed
row stays until the next go-live for that bot replaces it, so nothing is retried and nobody is told twice.
"""

from datetime import timedelta

from .store import H, encode

PREFIX = "go-live-pending:"
EXPIRY_DAYS = 7


def rows(c):
    """(key, bot, record) for every waiting or failed go-live: one range read on the primary key."""
    found = c.execute("SELECT key,value_json FROM registry_metadata WHERE key>=? AND key<?",
                      (PREFIX, PREFIX[:-1] + ";")).fetchall()
    return [(key, key[len(PREFIX):], H._json(value, {}) or {}) for key, value in found]


def deadline(record):
    asked = H.parse_ts(record.get("requested_at"))
    return asked + timedelta(days=EXPIRY_DAYS) if asked else None


def expired(record, now):
    """Whether a waiting go-live is past its deadline at `now` (an aware datetime). A record with no readable
    request time has no deadline to keep, so it counts as expired."""
    end = deadline(record)
    return end is None or now >= end


def fail(c, key, bot, record, code, notice):
    """Mark the go-live failed with `code`, record it, and tell the person who asked: once, since a failed record
    is skipped from then on."""
    c.execute("UPDATE registry_metadata SET value_json=? WHERE key=?",
              (encode({**record, "failed": {"code": code, "at": H.now()}}), key))
    H.event(c, H.KEEPER, "bot.go_live_failed", bot, {"code": code})
    actor = str(record.get("actor") or "")
    if H.is_human(actor):
        try:
            H.say(c, H.KEEPER, actor, notice + f" Run `hub bot go-live {bot}` again once that is fixed.", kind="notice")
        except H.Refused as exc:
            H.event(c, H.KEEPER, "bot.go_live_notice_refused", bot, {"reason": str(exc)[:300]})


def expire_notice(bot, record):
    computer = record.get("computer") or "its computer"
    return (f"Going live for {bot} stopped: {computer} did not get its repository within {EXPIRY_DAYS} days. "
            "Check that the repository exists on GitHub and that the computer is online and can reach it.")


def expire(c, now):
    """Mark every waiting go-live past its deadline failed (`expired`), so one whose computer never reports still
    ends with a notice. The scheduler calls this; the heartbeat scan does the same for the records it reads."""
    for key, bot, record in rows(c):
        if not record.get("failed") and expired(record, now):
            fail(c, key, bot, record, "expired", expire_notice(bot, record))


def waiting(c, who, auth, full):
    """(bot, computer, since) for the go-lives still waiting that `who` may see: every one for the owner and admins,
    a person who manages a bot for that bot only."""
    out = []
    for _, bot, record in rows(c):
        if record.get("failed") or not (full or (who.role == "human" and auth.bot_manager(c, who, bot))):
            continue
        out.append((bot, record.get("computer") or "its computer", str(record.get("requested_at") or "")[:10]))
    return out
