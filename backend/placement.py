"""Every active bot has a computer.

A bot that becomes active (added, built by BotOps, activated, resumed) with no computer is placed here, so it never
sits active and silent: on the company's only computer, or the least busy one that takes it. A member's bot goes
on that member's own computer or one that takes members' bots, the same rule a person placing it meets
(`Execution.assign`). With no computer that takes it the bot stays as it is and Health says so; the sweep
in the scheduler tries again as computers arrive.
"""
from types import SimpleNamespace

from .getting_started import _online_runners
from .store import H, Problem

SWEEP_LIMIT = 20


def candidates(c, auth, bot):
    """The computers that may host `bot`, best first: online before offline, then the fewest bots, then the oldest."""
    config = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not config:
        return []
    operator, owner, member = config["owner_id"], auth.owner_id(c), auth.member_bot(c, bot)
    online = {r["id"] for r in _online_runners(c)}
    load = {r["runner_id"]: r["n"] for r in c.execute(
        "SELECT a.runner_id, count(*) n FROM assignments a JOIN bots b ON b.id=a.bot WHERE b.state='active' "
        "GROUP BY a.runner_id")}
    def takes(r):
        # A member's bot: its member's computer, or one opened to members' bots. Any other: its operator's or the owner's.
        return (r["owner_id"] == operator or bool(r["accepts_member_bots"])) if member else r["owner_id"] in (operator, owner)
    from .cloud_machines import cloud_runners
    others = cloud_runners(c, but=bot)          # a cloud machine (another bot's own, or the shared one) never is
    rows = [r for r in c.execute("SELECT * FROM runners WHERE revoked_at IS NULL") if takes(r) and r["id"] not in others]
    # A bot BotOps builds has its repository on BotOps's computer, so it goes there when that computer takes it; a starter
    # the runner sets up itself goes anywhere.
    declared = H._json((c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone() or {"config": "{}"})["config"], {}) or {}
    home = c.execute("SELECT runner_id FROM assignments WHERE bot='botops'").fetchone()
    home = home["runner_id"] if home and not declared.get("materialize") and bot != "botops" else None
    return sorted(rows, key=lambda r: (r["id"] not in online, r["id"] != home, load.get(r["id"], 0), r["created_at"], r["id"]))


def auto_place(c, execution, bot, by=""):
    """Put an active bot with no computer on one. `{runner_id, label}`, or None when nothing was needed or nothing takes it."""
    row = H.bot(c, bot)
    if not row or row["state"] != "active" or c.execute("SELECT 1 FROM assignments WHERE bot=?", (bot,)).fetchone():
        return None
    from .agents import external_harness
    if external_harness(c, bot):
        return None                    # an external agent has a credential, not a computer
    from . import cloud_machines
    if cloud_machines.offered(c, execution.store.settings) and cloud_machines.adopt(c, bot, by or H.KEEPER):
        return None                    # its cloud machine is made when it has work (backend/cloud_machines.py)
    try:
        who = execution.auth.owner_identity(c)
    except Problem:
        return None
    for runner in candidates(c, execution.auth, bot):
        c.execute("SAVEPOINT auto_place")
        try:
            execution.assign(c, who, bot, SimpleNamespace(runner_id=runner["id"], expected_generation=0))
        except Problem:
            c.execute("ROLLBACK TO auto_place")
            c.execute("RELEASE auto_place")
            continue                   # this one refuses (inbox isolation, closed, busy): the next may not
        c.execute("RELEASE auto_place")
        H.event(c, by or who.actor, "bot.auto_placed", bot, {"runner": runner["id"], "label": runner["label"]})
        return {"runner_id": runner["id"], "label": runner["label"]}
    return None


def sweep(c, execution):
    """Place the active bots that still have no computer (one arrived, a runner came back). Cheap when there are none."""
    rows = c.execute("SELECT b.id, b.slug FROM bots b JOIN bot_config bc ON bc.bot=b.id LEFT JOIN assignments a ON a.bot=b.id "
                     "WHERE b.state='active' AND a.bot IS NULL ORDER BY b.slug LIMIT ?", (SWEEP_LIMIT,)).fetchall()
    from .batch_work import isolated
    result = []
    for row in rows:
        with isolated(c, "placement", row["slug"]):
            placed = auto_place(c, execution, row["id"])
            if placed:
                result.append((row["slug"], placed))
    return result
