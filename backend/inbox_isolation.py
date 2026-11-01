"""A computer may be kept for inbox bots alone.

Every bot on a computer runs as the same operating-system user, and an inbox bot reads mail with a
Google Workspace key that acts as any mailbox in the company. A team that wants that key walled off
keeps a computer for its inbox bots (`POST runners/<id>/inbox-only {"enabled": true}`); the server then
places no other bot there. It is a choice, not a rule: an inbox bot may share any other computer.

Installs from before this choice existed keep what they had: the first time the setting is needed, every
computer that holds only inbox bots is marked as kept for them.
"""
import json

from .store import Problem, P

KEY = "inbox-only-runners"


def _bots_by_runner(c):
    rows = c.execute("SELECT a.runner_id,a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
                     "WHERE b.state<>'archived' ORDER BY a.bot").fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["runner_id"], []).append(row["bot"])
    return result


def _seed(c, people):
    """Computers holding only inbox bots now: what the old rule kept for them."""
    return {runner_id for runner_id, bots in _bots_by_runner(c).items()
            if bots and all(P.inbox_person(b, people) for b in bots)}


def inbox_only(c, people, save=False):
    """Ids of the computers kept for inbox bots. `save` stores the seed on a write connection."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    if row:
        return set(json.loads(row[0]))
    ids = _seed(c, people)
    if save:
        _store(c, ids)
    return ids


def _store(c, ids):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(sorted(ids))))


def set_inbox_only(c, runner_id, enabled, people):
    ids = inbox_only(c, people)
    ids.add(runner_id) if enabled else ids.discard(runner_id)
    _store(c, ids)


def violations(c, people):
    """Computers kept for inbox bots that hold another bot: [{runner_id, label, inbox: [...], others: [...]}]."""
    ids, found = inbox_only(c, people), []
    labels = {r["id"]: r["label"] for r in c.execute("SELECT id,label FROM runners WHERE revoked_at IS NULL")}
    for runner_id, bots in _bots_by_runner(c).items():
        if runner_id not in labels or runner_id not in ids:
            continue
        inbox = [b for b in bots if P.inbox_person(b, people)]
        others = [b for b in bots if b not in inbox]
        if others:
            found.append({"runner_id": runner_id, "label": labels[runner_id], "inbox": inbox, "others": others})
    return found


def check(c, bot, runner_id, people):
    """Refuse putting a bot that is not an inbox bot on a computer kept for inbox bots."""
    if runner_id not in inbox_only(c, people, save=True) or P.inbox_person(bot, people):
        return
    raise Problem("inbox_isolation", f"This computer is kept for inbox bots, whose mail key can open every mailbox in "
                  f"the team, so {bot} cannot go there. Pick another computer, or stop keeping this one for inbox bots: "
                  f"`hub api POST runners/{runner_id}/inbox-only '{{\"enabled\": false}}'`.", 409)
