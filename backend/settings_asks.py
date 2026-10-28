"""Ask someone who can (settings stage 3, docs/permissions.md): a person who may not change a setting says yes to
"Shall I ask an admin?", and BotOps or their Assistant asks for them (`POST /api/v2/settings/catalog/{key}/ask`).

One request goes to one person who may: the setting's Confirm card in that person's BotOps chat, and a task on their
list that the asker requested. The card is theirs to tap or cancel, as any card (backend/assistant.py), and runs with
their rights; its outcome settles the task (done, or declined) and is told to the asker in the chat they asked from.
The asker's words never run anything: only the other person's tap does."""
import json
from dataclasses import replace

from . import hubdb as H
from .store import Problem

BOTOPS = "botops"
MAX_OPEN = 5            # asks one person may have waiting at once
# What the asker is told, by how the card ended.
TOLD = {"done": "{name} confirmed it: {what} is done.",
        "cancelled": "{name} said no to {what}: nothing changed.",
        "failed": "{name} confirmed {what}, but it did not go through: {error}",
        "expired": "{name} didn't answer about {what} within a day, so the card expired: nothing changed.",
        "interrupted": "{name} confirmed {what}, but it was cut off before it finished, so it may or may not have "
                       "changed: check it, and ask again if it didn't."}


def _name(c, actor):
    return (H.human(c, H.actor_id(actor)) or {}).get("name") or H.actor_id(actor)


def who_to_ask(c, auth, settings, asker, e, target, to=""):
    """The identity of the one person to ask: `to` (a person's id) when named and they may; else an admin who may
    (the owner, for a setting only the owner or a task mover changes, or when no admin may; then anyone else who may,
    such as a bot's owner). Never the asker."""
    from . import settings_catalog
    found = []
    for row in c.execute("SELECT id FROM humans ORDER BY lower(coalesce(name, id)), id"):
        actor = "human:" + row["id"]
        if actor == asker.actor or (to and row["id"] != H.actor_id(to)):
            continue
        try:
            who = auth.identity_for_actor(c, actor)
        except Problem:
            continue                                    # left the team
        if settings_catalog.may(c, auth, who, e, target, settings):
            found.append(who)
    if not found:
        raise Problem("no_one", f"{_name(c, to) if to else 'No one else on the team'} may change {e.name}", 409)
    owners = [w for w in found if w.role == "owner"]
    admins = [w for w in found if w.role != "owner" and auth.bot_admin(w)]
    others = [w for w in found if w.role != "owner" and not auth.bot_admin(w)]      # a bot's owners, task movers
    order = (owners, admins, others) if e.who in ("owner", "mover") else (admins, owners, others)
    return next(group[0] for group in order if group)


def check_asker(c, asker, asked, instruction):
    """A person asks for themselves, or BotOps or their Assistant asks for them on their own yes: the Assistant cites
    the person's words (`instruction` refuses when they lend nothing). At most MAX_OPEN asks wait at once."""
    if not H.is_human(asker.actor) or asker.confirmed or asker.via not in ("", BOTOPS, "assistant"):
        raise Problem("forbidden", "A person asks someone who can, or BotOps or their Assistant for them", 403)
    if asker.via == "assistant":
        instruction(c, asker, asked)
    waiting = c.execute("SELECT count(*) FROM assistant_actions WHERE status='pending' AND ask_json IS NOT NULL "
                        "AND json_extract(ask_json, '$.asker')=?", (asker.actor,)).fetchone()[0]
    if waiting >= MAX_OPEN:
        raise Problem("too_many", f"{MAX_OPEN} of your asks are still waiting for an answer; ask again when one is "
                      "settled", 409)


def _what(c, e, target):
    """The setting, and what it is for when its entry names one (a bot, a computer, a person or a rule): two bots'
    limits are two asks."""
    if target.get("person"):
        return f"{e.name} for {_name(c, 'human:' + target['person'])}"
    on = target.get("bot") or target.get("computer") or target.get("id")
    return f"{e.name} for {on}" if on else e.name


def file(c, auth, settings, asker, chosen, e, card, room, bot, note="", target=None):
    """The task that carries the card to `chosen`, the asker its requester, and the card marked as this ask (so its
    outcome settles the task and tells the asker). The same card asked for again is the same request; another value
    for the same setting on the same target waits for the first answer."""
    action = card["action"]
    if card.get("repeated"):
        # The card was already waiting (assistant.create_proposal reuses an identical pending one). Only the same
        # person's ask is the same request; anyone else's, or the person's own card, is never taken over silently.
        held = c.execute("SELECT ask_json FROM assistant_actions WHERE id=?", (action["id"],)).fetchone()
        ask = json.loads(held["ask_json"]) if held and held["ask_json"] else None
        if ask and ask["asker"] == asker.actor:
            return ask["task"]
        by = f"{_name(c, ask['asker'])} already asked them for" if ask else "they already have their own card for"
        raise Problem("already_waiting", f"{_name(c, chosen.actor)} has exactly this change waiting: {by} it. Nothing "
                      "was asked; ask again once it is settled", 409)
    on = {k: str(v) for k, v in (target or {}).items() if v}
    what = _what(c, e, on)
    for row in c.execute("SELECT ask_json FROM assistant_actions WHERE status='pending' AND owner=? AND id<>? AND "
                         "json_extract(ask_json, '$.asker')=? AND json_extract(ask_json, '$.key')=?",
                         (chosen.actor, action["id"], asker.actor, e.key)):
        if (json.loads(row["ask_json"]).get("target") or {}) == on:
            # Refused before anything is kept: the card just made goes with the transaction.
            raise Problem("already_asked", f"You already asked {_name(c, chosen.actor)} about {what}; their answer "
                          "to that comes first (they confirm or cancel it, or it expires after a day)", 409)
    asker_name = _name(c, asker.actor)
    body = (f"{asker_name} asked to change {what} ({e.where}) and may not change it themselves. "
            f"The Confirm card in your chat with BotOps does it: {action.get('description') or action['summary']}.\n\n"
            + (f"{asker_name} said: {note}\n\n" if note else "")
            + f"Confirm or cancel that card. Either settles this request, and {asker_name} is told.")
    # Filed as the asker, who is then its requester: the request is theirs, BotOps or the Assistant only carried it.
    task = H.task_create(c, asker.actor, f"{asker_name} asks you to change {what}"[:200], body, chosen.actor,
                         lint=False)
    ask = {"task": task["id"], "asker": asker.actor, "room": room["id"], "bot": bot, "key": e.key, "target": on,
           "what": what}
    c.execute("UPDATE assistant_actions SET ask_json=? WHERE id=?", (json.dumps(ask), action["id"]))
    H.event(c, asker.actor, "settings.asked", e.key, {"to": chosen.actor, "task": task["id"], "action": action["id"]})
    return task["id"]


def settled(c, row, status, result=None):
    """A card's outcome, for the ask it carries (if any): its task done (the change ran) or declined (cancelled,
    refused at the tap, or expired), and the asker told in the chat they asked from."""
    ask = json.loads(row.get("ask_json") or "null") if isinstance(row, dict) else None
    if not ask:
        return
    name, what = _name(c, row["owner"]), ask.get("what") or "the change"
    error = str((result or {}).get("error") or "")[:300]
    told = TOLD.get(status, TOLD["failed"]).format(name=name, what=what, error=error or "it was refused")
    task = H.task(c, ask["task"])
    if task and task.get("status") not in ("done", "declined", "closed"):
        H.task_update(c, row["owner"], ask["task"], status="done" if status == "done" else "declined", note=told)
    room = H.conversation(c, ask["room"]) if ask.get("room") else None
    if room and not room.get("closed_at"):
        H._write_message(c, "bot:" + ask.get("bot", BOTOPS), ask["asker"], told, room, "say",
                         {"settings_ask": row["id"], "task": ask["task"]}, None, None, delivered_at=H.now())


def sweep(c, at=None):
    """Hourly (backend/approval_expiry.py): an asked card nobody tapped or cancelled within a day expires, as a late tap
    would find it, so its request is declined, the asker is told and their ask no longer counts against MAX_OPEN."""
    from .assistant import CONFIRM_TTL_H
    from .batch_work import isolated
    at = at or H.now()
    gone = []
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_actions'").fetchone():
        return gone                                     # a database made without the Assistant's tables
    for row in H._rows(c.execute("SELECT * FROM assistant_actions WHERE status='pending' AND ask_json IS NOT NULL "
                                 "AND created<? ORDER BY created", (H.shift(at, hours=-CONFIRM_TTL_H),))):
        with isolated(c, "settings_asks", row["id"]):
            c.execute("UPDATE assistant_actions SET status='expired', decided_at=? WHERE id=? AND status='pending'",
                      (at, row["id"]))
            settled(c, row, "expired")
            gone.append(row["id"])
    return gone + recover(c, at, orphans=True)


def interrupted(c, at, ttl_s, orphans=False):
    """The asked cards whose confirm never settled (a restart mid-run): those still `running` past `ttl_s`, and with
    `orphans` (the hourly sweep) those already marked failed without settling their ask (their task still open)."""
    rows = H._rows(c.execute("SELECT * FROM assistant_actions WHERE ask_json IS NOT NULL AND ((status='running' AND "
                             "running_since<?) OR (? AND status='failed')) ORDER BY created",
                             (H.shift(at, seconds=-ttl_s), int(orphans))))
    ended = ("done", "declined", "closed")
    return [r for r in rows if r["status"] == "running"
            or (H.task(c, json.loads(r["ask_json"])["task"]) or {}).get("status") not in (None, *ended)]


def recover(c, at=None, orphans=False):
    """A confirm cut off before it finished settles its ask once: the task declined, and the asker told it may or may
    not have changed. Nothing is run again. The Assistant's expiry calls it before its own (backend/assistant.py)."""
    from .assistant import RUNNING_TTL_S
    from .batch_work import isolated
    at, done = at or H.now(), []
    for row in interrupted(c, at, RUNNING_TTL_S, orphans):
        with isolated(c, "settings_asks", row["id"]):
            c.execute("UPDATE assistant_actions SET status='failed', decided_at=?, confirm_hash=NULL, result_json=? "
                      "WHERE id=? AND status='running'",
                      (at, json.dumps({"status_code": 0, "error": "It did not finish; ask again"}), row["id"]))
            settled(c, row, "interrupted")
            done.append(row["id"])
    return done


def acting(chosen):
    """The chosen person as BotOps acting for them, so the plan makes their Confirm card (never a direct change)."""
    return replace(chosen, via=BOTOPS, confirmed=False)
