"""Bots left in setup: shown on Overview, and their person told once.

A starter bot is parked (`needs_setup`) until someone finishes its setup, and a parked bot claims no work, so
its routines' jobs and its tasks pile up where nobody looks. After a day it is listed with the stuck bots
(backend/health.py `stuck`, which Overview and Health show) with how long, and a person who may manage it gets
"Finish with defaults": the same setup-done call the bot itself makes (`POST /api/v2/bots/{bot}/onboarded`).
Nothing finishes setup on its own; setup answers are a person's to give or to skip.

The person who added the bot (else the owner and the bot administrators) gets one message saying so, once per bot.
"""

import time
from datetime import datetime, timezone

from . import hubdb as H
from .statuses import PARKED_SQL

STUCK_HOURS = 24
NUDGED = "bot.setup_nudged"
SWEEP_EVERY = 3600          # seconds; a day-long threshold needs no finer clock
_last = {"at": 0.0}


def stuck(c, at=None):
    """Active bots parked in setup for more than STUCK_HOURS, as health `stuck` rows (reason `setup`, `since` when
    the bot was added). Few bots are ever parked; each costs one indexed read of its queue."""
    cutoff = H.shift(at or H.now(), hours=-STUCK_HOURS)
    rows = c.execute("SELECT b.slug,b.display_name,b.created FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                     f"WHERE bc.onboarding_state IN {PARKED_SQL} AND b.state='active' AND coalesce(b.created,'')<>'' "
                     "AND b.created<? ORDER BY b.created", (cutoff,)).fetchall()
    out = []
    for row in rows:
        queued = c.execute("SELECT count(*) n, min(created) oldest FROM jobs WHERE bot=? AND state='queued'",
                           (row["slug"],)).fetchone()
        where = c.execute("SELECT r.label FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?",
                          (row["slug"],)).fetchone()
        out.append({"bot": row["slug"], "name": row["display_name"] or row["slug"], "queued": queued["n"],
                    "oldest": queued["oldest"], "computer": where["label"] if where else "", "reason": "setup",
                    "since": row["created"], "runner_id": "", "runtime": ""})
    return out


def held_for(since, at=None):
    """"3 days" or "30 hours": how long a bot has been in setup."""
    end = H.parse_ts(at) if at else datetime.now(timezone.utc)
    hours = max(0, int((end - H.parse_ts(since)).total_seconds() // 3600))
    return f"{hours // 24} days" if hours >= 48 else f"{hours} hours"


def recipients(c, bot, owner_id, admin_emails):
    """The person who added the bot, when that is a person still on the roster; else the owner and the bot
    administrators."""
    from .auth import standing
    row = c.execute("SELECT created_by FROM bot_config WHERE bot=?", (bot,)).fetchone()
    added = row["created_by"] if row else ""
    if H.is_human(added) and H.human(c, H.actor_id(added)) and not standing(c, H.actor_id(added)):
        return [added]
    admins = {r["id"] for r in c.execute("SELECT id,email FROM humans") if (r["email"] or "").lower() in admin_emails}
    return ["human:" + pid for pid in sorted(({owner_id} if owner_id else set()) | admins)
            if not standing(c, pid)]


def nudge(c, owner_id, admin_emails, at=None):
    """Tell each stuck bot's person once (a keeper event marks it). Returns {bot: [actors told]}."""
    told = {}
    for row in stuck(c, at):
        if c.execute("SELECT 1 FROM events WHERE actor=? AND action=? AND target=? LIMIT 1",
                     (H.KEEPER, NUDGED, row["bot"])).fetchone():
            continue
        people = recipients(c, row["bot"], owner_id, admin_emails)
        waiting = f", with {row['queued']} job{'s' if row['queued'] != 1 else ''} waiting" if row["queued"] else ""
        text = (f"{row['name']} has been waiting in setup for {held_for(row['since'], at)}{waiting}, so it runs nothing. "
                "Finish its setup on its page, or choose Finish with defaults on Overview.")
        for actor in people:
            H.say(c, H.KEEPER, actor, text, kind="notice")
        # Marked even with nobody to tell, so a roster change later does not bring an old bot back as news.
        H.event(c, H.KEEPER, NUDGED, row["bot"], {"told": people, "since": row["since"]})
        told[row["bot"]] = people
    return told


def tick(store, auth, force=False):
    """The server loop's hourly sweep: nothing to write unless a bot has been parked for a day."""
    if not force and time.time() - _last["at"] < SWEEP_EVERY:
        return {}
    _last["at"] = time.time()
    with store.read() as c:
        if not stuck(c):
            return {}
    with store.transaction() as c:
        return nudge(c, auth.owner_id(c), {e.lower() for e in auth.bot_admins})
