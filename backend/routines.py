"""Routines are automations now (backend/automations.py). What is left here:

- `create`, `row`, `update` and `remove` for what Tico still seeds the old way (a template's first routines, the
  Librarian's market routines, the fleet's task sweep, the Strategy Bot's passes): `create` writes the `schedules` row
  and, once the install's cutover ran, moves it to a schedule or event automation in the same write
  (backend/redesign_migration.py `adopt`). `row`, `update` and `remove` of a moved routine read and change its
  automation (on/off; removing deletes it). Nothing fires a `schedules` row any more.
- `no_input`: why a built-in routine's occurrence has nothing to work on; a converted routine's automation skips it.
- The message bots' mailboxes (`message_bot_mailboxes` and friends), which mail and the converter read."""
import json
import re

from clients.manifest import tools_of
from clients.routines import DEFAULT_ZONE, validate_schedules
from . import people as P
from .scheduler import next_due, stamp
from .store import H, Problem
from . import bot_settings as BS

DEFAULT_TEXT = "Run this scheduled routine."
# Every active bot looks at its own open tasks once a day, staggered across
# 08:00-09:59 America/Los_Angeles by slug so the fleet does not wake at once.
OPEN_TASKS_KEY = "open-tasks-daily"      # retired; the key finds the old rows
SWEEP_KEY = "task-sweep"
SWEEP_TEXT = ("Sweep stuck tasks across the fleet (follow playbooks/task-sweep.md if you have it). "
              "`hub task list --stuck` lists every bot's open work that has not moved in a day and waits on "
              "nobody. For each: start it with `hub task run <id>` when the bot can simply do it; "
              "fix the cause when something in the bot or Tico keeps it stuck; or tell the requester "
              "in one line why it cannot move. Then review the open tasks you filed for humans "
              "(`hub task list --requester me --status open`): close each whose condition is already true or superseded "
              "with a one-line note, and ask once about one you cannot tell. "
              "Nothing to do: finish this task with one line.")
OPEN_TASKS_TEXT = ("Daily open-tasks check. List the tasks you own that are still open (`hub task list`). "
                   "Move the top one forward: finish it, take its next step, or say in one line with "
                   "`hub task update <id> --note` what it is waiting on. If a person asked for something that "
                   "is not a task yet, file it now. Nothing open: finish this task with one line.")
FIELDS = ("title", "cron", "on", "timezone", "text")


def _clock(at):
    return at or H.parse_ts(H.now())      # the hub clock: a demo build's routines are due by its own now


def validate(fields):
    """One routine's fields, checked the way a manifest entry is (clients/routines.py)."""
    entry = {"title": fields.get("title"), "cron": fields.get("cron") or "", "on": fields.get("on") or "",
             "timezone": fields.get("timezone") or DEFAULT_ZONE, "instructions": fields.get("text") or ""}
    return validate_schedules([entry])[0]


def require_trigger(c, event):
    """A routine may listen for a webhook only while its trigger exists (backend/triggers.py)."""
    from . import triggers
    if event and event.startswith(triggers.EVENT) and not triggers.exists(c, event):
        raise ValueError(f"No trigger {event[len(triggers.EVENT):]}; add it in Settings > Webhooks first")


def _moved(c, sid):
    """The automation a routine moved to: (True, its row or None), or (False, None) for a routine not moved yet."""
    from . import redesign_migration
    if not redesign_migration.holds(c, "routine", sid):
        return False, None
    found = c.execute("SELECT m.automation, a.* FROM redesign_migrated m LEFT JOIN automations a ON a.id=m.automation "
                      "WHERE m.kind='routine' AND m.id=?", (sid,)).fetchone()
    return True, (found if found and found["automation"] and found["id"] else None)


def row(c, sid):
    """One routine. A moved one reads as its automation: on/off, deleted with it, `automation` its id. One the cutover
    kept back (it could not move) reads as None: nothing runs it."""
    found = c.execute("SELECT s.*,coalesce(sc.timezone,?) AS timezone,coalesce(sc.enabled,1) AS enabled "
                      "FROM schedules s LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.id=?",
                      (DEFAULT_ZONE, sid)).fetchone()
    if not found:
        return None
    moved, auto = _moved(c, sid)
    if not moved:
        return dict(found)
    if not auto:
        return None
    return {**dict(found), "enabled": int(auto["enabled"]), "deleted_at": auto["deleted_at"], "automation": auto["id"],
            "next_due_at": auto["next_due_at"]}


def _set_moved(c, actor, auto, enabled):
    from . import automations
    before = automations.flow_of(auto)
    automations._save(c, actor, auto["id"], {**before, "enabled": bool(enabled)}, before, via="routine")


def create(c, actor, bot, fields, *, key=None, at=None):
    """A new routine for `bot`. With `key`, the same key names the same routine: creating it
    again updates it in place (a bot that sets up its own routines every turn makes one)."""
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)        # a routine names its bot by id or slug
    at = _clock(at)
    bot = (H.resolve_bot(c, bot) or {}).get("id", H.actor_id(bot))     # a routine names its bot by id
    entry = validate(fields)
    from . import strategy_acts
    # more runs cost more: the owner's
    strategy_acts.gate(c, actor, "cost", f"add a routine for {bot}: {entry['title']}")
    require_trigger(c, entry["on"])
    H._takes_tasks(c, actor, "bot:" + bot, routine=True)        # every run of a routine is a task
    if key is not None:
        existing = c.execute("SELECT id FROM schedules WHERE bot=? AND routine_key=?", (bot, key)).fetchone()
        if existing:
            if _moved(c, existing["id"])[0]:
                return row(c, existing["id"])      # moved to an automation: a re-seed must not bring it back
            return update(c, actor, existing["id"], {**fields, "enabled": fields.get("enabled", True)},
                          at=at, restore=True)
    sid = bot + ":" + (key or H.new_id()[:12])
    now = stamp(at)
    event = entry["on"] or None
    due = None if event else stamp(next_due(entry["cron"], at, entry["timezone"]))
    c.execute("INSERT INTO schedules(id,bot,routine_key,cron,event_name,title,playbook,next_due_at,source,updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,'hub',?)",
              (sid, bot, key, entry["cron"], event, entry["title"], entry["instructions"] or DEFAULT_TEXT, due, now))
    c.execute("INSERT INTO schedule_config(schedule_id,timezone,enabled) VALUES(?,?,?)",
              (sid, entry["timezone"], 1 if fields.get("enabled", True) else 0))
    H.event(c, actor, "routine.created", sid, {"bot": bot, "cron": entry["cron"], "on": entry["on"]})
    from . import redesign_migration
    redesign_migration.adopt(c, "routine", sid)        # after the cutover: an automation at once
    return row(c, sid)


def update(c, actor, sid, changes, *, at=None, restore=False):
    """Change what a routine says or when it runs. A cadence change moves the next fire from
    now; a text change reaches the next occurrence and never a task already opened."""
    at = _clock(at)
    moved, auto = _moved(c, sid)
    if moved:
        # Moved: only on/off reaches its automation (its words and time are edited there).
        if not auto or (auto["deleted_at"] and not restore):
            raise Problem("not_found", "Routine not found", 404)
        if changes.get("enabled") is not None and bool(changes["enabled"]) != bool(auto["enabled"]):
            if changes["enabled"]:
                from . import strategy_acts
                strategy_acts.gate(c, actor, "cost", f"start the automation \"{auto['name']}\"")
            _set_moved(c, actor, auto, changes["enabled"])        # its `routine.updated` event comes with it
        return row(c, sid)
    current = row(c, sid)
    if not current or (current["deleted_at"] and not restore):
        raise Problem("not_found", "Routine not found", 404)
    merged = {"title": current["title"], "cron": current["cron"] or "", "on": current["event_name"] or "",
              "timezone": current["timezone"], "text": current["playbook"] or ""}
    for name in FIELDS:
        if changes.get(name) is not None:
            merged[name] = changes[name]
    if changes.get("cron"):
        merged["on"] = ""
    elif changes.get("on"):
        merged["cron"] = ""
    entry = validate(merged)
    # A new cadence or a routine started again can cost more: the owner's. Pausing it or rewording it costs nothing more.
    if any(changes.get(k) is not None for k in ("cron", "on", "timezone")) or restore and current["deleted_at"] \
            or changes.get("enabled") and not current["enabled"]:
        from . import strategy_acts
        strategy_acts.gate(c, actor, "cost", f"change when {current['bot']}'s routine \"{current['title']}\" runs")
    if (entry["on"] or None) != current["event_name"]:
        require_trigger(c, entry["on"])
    now = stamp(at)
    event = entry["on"] or None
    cadence_changed = (entry["cron"] != (current["cron"] or "") or event != current["event_name"]
                       or entry["timezone"] != current["timezone"] or bool(current["deleted_at"]))
    if event:
        due = None
    else:
        due = stamp(next_due(entry["cron"], at, entry["timezone"])) if cadence_changed else current["next_due_at"]
    c.execute("UPDATE schedules SET cron=?,event_name=?,title=?,playbook=?,next_due_at=?,deleted_at=NULL,updated_at=? WHERE id=?",
              (entry["cron"], event, entry["title"], entry["instructions"] or DEFAULT_TEXT, due, now, sid))
    enabled = current["enabled"] if changes.get("enabled") is None else (1 if changes["enabled"] else 0)
    c.execute("INSERT INTO schedule_config(schedule_id,timezone,enabled) VALUES(?,?,?) ON CONFLICT(schedule_id) "
              "DO UPDATE SET timezone=excluded.timezone,enabled=excluded.enabled", (sid, entry["timezone"], enabled))
    H.event(c, actor, "routine.updated", sid, {k: v for k, v in changes.items() if v is not None and k != "text"})
    return row(c, sid)


def remove(c, actor, sid, *, at=None):
    """Delete a routine (a moved one: its automation). Its history stays."""
    at = _clock(at)
    now = stamp(at)
    moved, auto = _moved(c, sid)
    if moved:
        if auto and not auto["deleted_at"]:
            c.execute("UPDATE automations SET deleted_at=?, enabled=0 WHERE id=?", (now, auto["id"]))
            H.event(c, actor, "routine.deleted", sid, {"automation": auto["id"]})
        return row(c, sid)
    current = row(c, sid)
    if not current:
        return None
    c.execute("UPDATE schedules SET deleted_at=?,updated_at=?,next_due_at=NULL WHERE id=?", (now, now, sid))
    H.event(c, actor, "routine.deleted", sid, {"bot": current["bot"]})
    return row(c, sid)


def no_input(c, settings, schedule, local):
    """Why a built-in routine's run would have nothing to work on, or None: such a run is skipped rather than
    opening a task its bot can only close."""
    from . import strategy_bot, market
    if held := strategy_bot.held_routine(c, schedule):
        return held                 # the Strategy Bot's KPI pass, while the owner has its regular checks off
    # Only the routine as shipped: a team that changed its text may have given it other work.
    if (schedule["bot"] == market.CURATOR.split(":", 1)[1] and schedule["routine_key"] == "curate-the-market"
            and (schedule["playbook"] or "").strip() == market.CURATE_TEXT.strip()):
        return market.nothing_to_curate(c, settings, local)
    return None


def _roster(c):
    found = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    if found:
        return P.load(json.loads(found[0]))
    return P.load({"people": H.humans(c)})


MAILBOX = re.compile(r"^[^\s@{}<>]+@[^\s@{}<>]+\.[^\s@{}<>]+$")


def declared_mailbox(config):
    """The address a bot declares: the identity of its `gmail` tool in bot.yaml (BotOps fills it from the
    `Mailbox:` line in the instructions). Empty when there is none, or when `{{mailbox}}` was never filled."""
    for entry in tools_of(config) or []:
        if isinstance(entry, dict) and str(entry.get("service", "")).lower() == "gmail":
            addr = str(entry.get("identity") or "").strip().lower()
            if MAILBOX.fullmatch(addr):
                return addr
    return ""


def set_declared_mailbox(c, bot, address):
    """Record the address `bot` reads, as the identity of its `gmail` tool in the bot's stored config (the owner's
    word, not the bot's repository: a bot cannot widen what its runs may open). Other tools are kept."""
    address = str(address or "").strip().lower()
    if not MAILBOX.fullmatch(address):
        raise Problem("mailbox", "That is not an email address", 422)
    found = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    if not found:
        raise Problem("not_found", "Bot not found", 404)
    config = json.loads(found["config"] or "{}")
    tools = [dict(t) if isinstance(t, dict) else t for t in (config.get("tools") or config.get("access") or [])]
    gmail = next((t for t in tools if isinstance(t, dict) and str(t.get("service", "")).lower() == "gmail"), None)
    if gmail is None:
        tools.append({"service": "gmail", "identity": address, "can": ["read", "draft"]})
    else:
        gmail["identity"] = address
    config.pop("access", None)
    config["tools"] = tools
    BS.write(c, bot, config)
    c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (bot,))


def message_bot_mailboxes(c, roster=None):
    """The mailboxes the message bots manage, one per person who has an `inbox_bot`:
    [{"address", "person_id", "bot", "declared"}]. The address is the bot's declared mailbox, and the
    person's own email only when the bot declares none. People who left (hidden) and archived bots are
    skipped, and so is a mailbox that two bots share (listed once)."""
    roster = roster if roster is not None else _roster(c)
    people = [p for p in (roster or {}).get("people") or [] if p.get("inbox_bot") and not p.get("hidden")]
    # The bots and their declarations are read together, a fixed number of statements however long the roster is.
    named = sorted({p["inbox_bot"] for p in people})
    states, configs = {}, {}
    for start in range(0, len(named), 500):
        part = named[start:start + 500]
        ids = sorted({H.actor_id(bot) for bot in part})
        marks = ",".join("?" * len(ids))
        states.update((r["id"], r["state"]) for r in c.execute(f"SELECT id,state FROM bots WHERE id IN ({marks})", ids))
        marks = ",".join("?" * len(part))
        configs.update((r["bot"], r["config"]) for r in c.execute(
            f"SELECT bc.bot AS bot, {BS.CONFIG} AS config FROM bot_config bc {BS.JOIN} WHERE bc.bot IN ({marks})", part))
    out, seen = [], set()
    for person in people:
        bot = person["inbox_bot"]
        state = states.get(H.actor_id(bot))
        if state is None or state == "archived":
            continue
        try:
            config = json.loads(configs.get(bot) or "{}")
        except ValueError:
            config = {}
        declared = declared_mailbox(config)
        address = declared or str(person.get("email") or "").strip().lower()
        if not MAILBOX.fullmatch(address) or address in seen:
            continue
        seen.add(address)
        out.append({"address": address, "person_id": person["id"], "bot": bot, "declared": bool(declared)})
    return out


def mailbox_of(person, boxes):
    """The address that stands for this person: their message bot's mailbox, else their own email."""
    found = next((b["address"] for b in boxes if b["person_id"] == person.get("id")), "")
    return found or str(person.get("email") or "").strip().lower()


def person_for_mailbox(address, boxes, roster):
    """The roster person a mailbox belongs to: by a bot's declared mailbox first, then by email."""
    wanted = str(address or "").strip().lower()
    found = next((b for b in boxes if b["address"] == wanted), None)
    return (P.person(found["person_id"], roster) if found else None) or P.person_by_email(wanted, roster)


def token_mailboxes(c, bot, roster=None):
    """The mailboxes a bot's turn may ask its runner for a token for: every mailbox it holds a level on
    (backend/mailbox_access.py), a person's message bot included only through its grant. `roster` is unused (kept
    for callers that pass it)."""
    from . import mailbox_access
    return [box["address"] for box in mailbox_access.mailboxes_for(c, "bot:" + str(bot))]


def inbox_of(c, bot):
    """The mailboxes a bot works on, as {"mailboxes": [address]}: the ones it holds a level on."""
    return {"mailboxes": token_mailboxes(c, bot)}


def retire_open_tasks_checks(c, at=None):
    """Delete every bot's daily open-tasks routine (fewer random daily runs; one
    BotOps sweep finds the stuck work instead). Returns the bots whose routine went."""
    gone = []
    for r in c.execute("SELECT id, bot FROM schedules WHERE routine_key=? AND deleted_at IS NULL",
                       (OPEN_TASKS_KEY,)).fetchall():
        remove(c, H.KEEPER, r["id"], at=at)
        gone.append(r["bot"])
    return gone


def ensure_task_sweep(c, at=None):
    """The fleet maintainer's one daily sweep of stuck tasks. Set once; a person's delete stays."""
    slug = H.FLEET_MAINTAINER
    if not c.execute("SELECT 1 FROM bots WHERE id=? AND state='active'", (slug,)).fetchone():
        return None
    if c.execute("SELECT 1 FROM schedules WHERE bot=? AND routine_key=?", (slug, SWEEP_KEY)).fetchone():
        return None
    return create(c, H.KEEPER, slug, {"title": "Sweep stuck tasks", "text": SWEEP_TEXT,
                                      "cron": "0 9 * * *", "timezone": "America/Los_Angeles"},
                  key=SWEEP_KEY, at=at)

