"""Budgets: what Strategy › Budget shows, set the way an owner sets them, after every run is in (so it is the last
section). A monthly limit on the whole team, limits on a few groups and bots, one group and one bot near a limit, the
run budgets (a team default and two bots' own), and the task budget Settings › Tasks keeps (the team default, and
Ana's own, filter On).

A limit is sized from what its bots spent before the build, through the same spend the hub counts (backend/usage_limits.py):
a comfortable one sits well under it in every window, a near one at about 86% of the window it is set on. The near
one's window is the longest with real spend in it on the build's date: the month, else the week (Monday on), else the
day, so it is near whatever day the demo is built. Nothing is paused: no limit is met. Every write goes through the API
as Ana."""

import math
from datetime import timedelta

from .. import usage_limits as U

CONTENT = {
    # Limits on spend: each window's factor over the larger of its share of the last 30 days' spend or its own so far.
    "company": {"monthly": 1.4},
    "groups": {"marketing": {"monthly": 1.6}, "engineering": {"monthly": 1.7}},
    "near_group": "support",
    "bots": {"sales": {"daily": 3.0}, "firmware": {"weekly": 2.5}, "video-producer": {"monthly": 2.0}},
    # The first of these with spend in the near window is the bot near its limit (none in the near group).
    "near_bots": ("content", "botops", "pricing", "release-notes", "community"),
    "near": 0.86,
    # The longest window with this much spend in it holds a near limit (else the day): for a group, for a bot.
    "floors": {"group": (("monthly", "month", 40.0), ("weekly", "week", 12.0)),
               "bot": (("monthly", "month", 15.0), ("weekly", "week", 5.0))},
    # Runs a bot may start (backend/turn_budgets.py): the team default and two bots' own.
    "runs_team": {"turns_per_hour": 60, "turns_per_day": 500},
    "runs_bots": {"support": {"turns_per_hour": 120, "turns_per_day": 1200},
                  "sdr": {"turns_per_hour": 20, "turns_per_day": 150}},
    # What bots may put on a person (backend/ask_ingest.py): the team default, and Ana's own with the filter On.
    "tasks_team": {"mode": "rehearsal", "budget": 12, "chores_budget": 4},
    "tasks_ana": {"mode": "on", "budget": 8, "chores_budget": 3},
}


def _up(amount, step):
    return round(math.ceil(round(amount / step, 6)) * step, 2)


def _nice(amount):
    """A limit a person would type: whole dollars, tens over $100, fifties over $500."""
    step = 50 if amount > 500 else 10 if amount > 100 else 1 if amount >= 5 else 0.5
    return _up(amount, step)


def _near(spent, floors):
    """{period_usd: cap} at about CONTENT["near"] of the longest window with real spend, or None when even the day has
    none (a build just after midnight UTC)."""
    name, label = next(((name, label) for name, label, floor in floors if spent[label] >= floor), ("daily", "day"))
    if spent[label] <= 0:
        return None
    cap = spent[label] / CONTENT["near"]
    return {name + "_usd": _up(cap, 0.01 if cap < 2 else 0.1 if cap < 20 else 1)}


def seed(b):
    b.at(hours=3)
    groups = {g["id"] for g in b.call("GET", "groups")}
    since = b.now - timedelta(days=30)

    def read(c):
        default = U.company(c)
        month = U.by_bot(c, since.strftime("%Y-%m-%dT%H:%M:%S"), b.now.strftime("%Y-%m-%dT%H:%M:%S.999999"),
                         default["count_subscription"])
        return {"month": month, "company": U.spent_now(c, scope=U.COMPANY, at=b.now),
                "groups": {g: U.spent_now(c, scope="group:" + g, at=b.now) for g in groups},
                "members": {g: U.members(c, "group:" + g) for g in groups},
                "bots": {bot: U.spent_now(c, bot=bot, at=b.now) for bot in month}}
    spent = b.write(read)

    def room(now, last30, caps):
        """A cap per window, `factor` over the larger of the last 30 days' spend (a month) or the window's own."""
        days = {"daily": ("day", 1 / 30), "weekly": ("week", 7 / 30), "monthly": ("month", 1)}
        out = {}
        for name, factor in caps.items():
            label, share = days[name]
            out[name + "_usd"] = _nice(max(last30 * share, now[label]) * factor)
        return out

    last30 = sum(spent["month"].values())
    b.call("PUT", "usage/pools/company", room(spent["company"], last30, CONTENT["company"]))
    for group, caps in CONTENT["groups"].items():
        if group in groups:
            used = sum(spent["month"].get(bot, 0.0) for bot in spent["members"][group])
            b.call("PUT", f"usage/pools/groups/{group}", room(spent["groups"][group], used, caps))
    near = _near(spent["groups"][CONTENT["near_group"]], CONTENT["floors"]["group"]) if CONTENT["near_group"] in groups else None
    if near:
        b.call("PUT", f"usage/pools/groups/{CONTENT['near_group']}", near)
    for bot, caps in CONTENT["bots"].items():
        if bot in spent["bots"]:
            b.call("PUT", f"usage/limits/{bot}", room(spent["bots"][bot], spent["month"][bot], caps))
    for bot in CONTENT["near_bots"]:
        near = _near(spent["bots"][bot], CONTENT["floors"]["bot"]) if bot in spent["bots"] else None
        if near:
            b.call("PUT", f"usage/limits/{bot}", near)
            break

    b.call("POST", "settings/turn-budgets", CONTENT["runs_team"])
    for bot, body in CONTENT["runs_bots"].items():
        revision = b.call("GET", f"bots/{bot}/budget")["revision"]
        b.call("POST", f"bots/{bot}/budget", {**body, "expected_revision": revision})

    criteria = b.call("GET", "ask-ingest/settings")["team"]["criteria"]
    b.call("PUT", "ask-ingest/settings", {**CONTENT["tasks_team"], "criteria": criteria, "team": True})
    b.call("PUT", "ask-ingest/settings", {**CONTENT["tasks_ana"], "criteria": criteria, "team": False})
