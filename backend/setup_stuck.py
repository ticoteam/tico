"""Bots left in setup: shown on Overview, and their person told once.

A starter bot is parked (`needs_setup`) until someone finishes its setup, and a parked bot claims no work, so
its routines' jobs and its tasks pile up where nobody looks. After a day it is listed with the stuck bots
(backend/health.py `stuck`, which Overview and Health show) with how long, and a person who may manage it gets
"Finish with defaults": the same setup-done call the bot itself makes (`POST /api/v2/bots/{bot}/onboarded`),
which also turns its template's first routine on, as starting setup would have. Nothing finishes setup on its own;
setup answers are a person's to give or to skip.

The person the "Finish onboarding" reminder goes to (who added the bot, else its operator, else the default person)
gets one message saying so, once per bot, unless that reminder is still open.
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
    rows = c.execute("SELECT b.id,b.slug,b.display_name,b.created_at FROM bot_config bc JOIN bots b ON b.id=bc.bot "
                     f"WHERE bc.onboarding_state IN {PARKED_SQL} AND b.state='active' AND coalesce(b.created_at,'')<>'' "
                     "AND b.created_at<? ORDER BY b.created_at", (cutoff,)).fetchall()
    out = []
    for row in rows:
        queued = c.execute("SELECT count(*) n, min(created_at) oldest FROM jobs WHERE bot_id=? AND state='queued'",
                           (row["id"],)).fetchone()
        where = c.execute("SELECT r.label FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=?",
                          (row["id"],)).fetchone()
        out.append({"bot": row["id"], "name": row["display_name"] or row["slug"], "queued": queued["n"],
                    "oldest": queued["oldest"], "computer": where["label"] if where else "", "reason": "setup",
                    "since": row["created_at"], "runner_id": "", "runtime": ""})
    return out


def held_for(since, at=None):
    """"3 days" or "30 hours": how long a bot has been in setup."""
    end = H.parse_ts(at) if at else datetime.now(timezone.utc)
    hours = max(0, int((end - H.parse_ts(since)).total_seconds() // 3600))
    return f"{hours // 24} days" if hours >= 48 else f"{hours} hours"


def nudge(c, at=None):
    """Tell each stuck bot's person once (a keeper event marks it): the same person the "Finish onboarding"
    reminder goes to (hubdb `_setup_person`), and nobody while that reminder is still open, so one bot never makes
    two asks. Returns {bot: [actors told]}."""
    told = {}
    for row in stuck(c, at):
        if H._open_setup_reminder(c, row["bot"]) or c.execute(
                "SELECT 1 FROM events WHERE actor=? AND action=? AND target=? LIMIT 1",
                (H.KEEPER, NUDGED, row["bot"])).fetchone():
            continue
        person = H._setup_person(c, row["bot"])
        people = [person] if person else []
        waiting = f", with {row['queued']} job{'s' if row['queued'] != 1 else ''} waiting" if row["queued"] else ""
        text = (f"{row['name']} has been waiting in setup for {held_for(row['since'], at)}{waiting}, so it runs nothing. "
                "Finish its setup on its page, or choose Finish with defaults on Overview.")
        for actor in people:
            H.say(c, H.KEEPER, actor, text, kind="notice")
        # Marked even with nobody to tell, so a roster change later does not bring an old bot back as news.
        H.event(c, H.KEEPER, NUDGED, row["bot"], {"told": people, "since": row["since"]})
        told[row["bot"]] = people
    return told


def tick(store, force=False):
    """The server loop's hourly sweep: nothing to write unless a bot has been parked for a day."""
    if not force and time.time() - _last["at"] < SWEEP_EVERY:
        return {}
    _last["at"] = time.time()
    with store.read() as c:
        if not stuck(c):
            return {}
    with store.transaction() as c:
        return nudge(c)
