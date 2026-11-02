"""A bot's work on a task stops because an account or a credential needs a person: one card in the task's thread.

The card says what broke in one line ("Sales Bot can't reach Google (ana@acme.example).") and carries the fix: the
person's own Connect Google, the computer's sign-in pop-up, or a credential card. The task waits on the card, a
recorded wait like a milestone's (hubdb.wait_detail, wake_rules.parked): it is in the fixer's Needs you under
connect (hubdb.needs_you), and no stall wake, stranded sweep or news spends turns on it meanwhile. It does not set
`waiting_on`: a bot naming a person there asks in a subtask instead, and this card is that ask. When the account
works again the card says "Reconnected" and the task's owner gets ONE wake on that task, through the one wake path
(H._wake, refs `reconnected`, an AWAITED kind in backend/wake_rules.py).

One row per task and connection key (`task_reconnects`), so a second failure on the same task reuses its card and
several tasks can wait on one key; Needs you folds them into one item (views.fold_reconnects). A card is live while
its task is unfinished, still has the owner it was opened for, and the card's bot is not archived; a card that stops
being live is retired (its task ended or moved on, `moved`, `bot_gone`) and asks nothing. The wake waits until nothing
else holds the task: a fix while another card or hold stands is owed, and the last hold to clear delivers every owed
line in one wake (`deliver`, from here, from task_holds.refresh, or folded into the next wake H._wake sends it). The
keys:

- `google:<account>`: the connector's Google sign-in for that mailbox was refused (backend/connectors.py health).
  Each refusing service (mail, calendar) is kept per account in `google_signin_refusals`, apart from the shared
  service_health row a snapshot or sync overwrites; the card is fixed once every service that refused reports that
  account good again, or the person connects it again (backend/google_connect.py). The person is the mailbox's
  own: only they connect it.
- `login:<computer>:<runtime>:<profile>`: the provider refused that profile's sign-in or key on the computer
  (`auth_rejected` on a run, backend/execution.py; `_default` is the computer's own login, named apart from every
  profile); fixed when that computer reports that profile's login ready, or a run on that profile completes. A
  runtime-wide ready says nothing of a named profile, nor one profile's of another's.
- `secret:<bot>:<ENV>`: a credential the task's bot asked for (BotOps may ask for another bot's: the card's `bot` is the
  one it is for, the task's `owner` is BotOps) (backend/credential_cards.py); fixed when that variable is stored for
  or granted to that bot (backend/credentials.py). Cancelling its credential card ends the wait with a wake that
  says so (`dropped`).

Anything else (GitHub, an MCP server's OAuth) is a new key with its own fix kind, opened and fixed through the same
`open` and `fixed`. The person is the bot's operator, else the company owner (usage_limits.recipients), except a
Google mailbox's own person.

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import json

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_reconnects(
  task_id TEXT NOT NULL, key TEXT NOT NULL, bot TEXT NOT NULL, owner TEXT NOT NULL, service TEXT NOT NULL,
  label TEXT NOT NULL, person TEXT NOT NULL, fix TEXT NOT NULL, message_id TEXT, created_at TEXT NOT NULL,
  fixed_at TEXT, ended TEXT, woke_at TEXT, PRIMARY KEY(task_id, key))""",
    "CREATE INDEX IF NOT EXISTS task_reconnects_open ON task_reconnects(key) WHERE fixed_at IS NULL",
    "CREATE TABLE IF NOT EXISTS google_signin_refusals(account TEXT NOT NULL, service TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY(account, service))",
)
WAKE_REF = "reconnected"
CARD_REF = "reconnect"
RECENT_S = 900          # a bot's task worked this recently is the one a connector failure stops
OWED = "fixed_at IS NOT NULL AND woke_at IS NULL AND ended IN ('fixed','cancelled')"


def secret_key(bot, env):
    """A credential's card key: per bot, since the grant is the bot's (BotOps may ask for two bots' on one task)."""
    return f"secret:{bot}:{env}"


def _H():
    from . import hubdb
    return hubdb


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def _present(c):
    return _H()._has_table(c, "task_reconnects")


def _live(alias="r"):
    """SQL for an open card that still stands, and its arguments: its task unfinished and still the owner's it was
    opened for, its bot not archived. Its person must also still read the task (`_reads`, in Python: privacy and
    org-chart reach are not SQL)."""
    H = _H()
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    return (f"{alias}.fixed_at IS NULL AND EXISTS(SELECT 1 FROM tasks t WHERE t.id={alias}.task_id "
            f"AND t.owner_id={alias}.owner AND t.status IN ({marks})) AND NOT EXISTS(SELECT 1 FROM bots b "
            f"WHERE b.id={alias}.bot AND b.state='archived')"), tuple(H.ACTIVE_STATUSES)


def person_for(c, settings, bot, task=None):
    """Who fixes the bot's accounts: its operator, else the company owner, as an actor, or None. For a card on `task`,
    the first of them who can read it: the card is in its thread."""
    from . import usage_limits
    H, email = _H(), getattr(settings, "owner_email", "") or ""
    if not email and settings is None:
        # A hook from hubdb has no settings: the owner record the company keeps.
        from .access import load_owner
        email = load_owner(c, type("NoSettings", (), {"owner_email": ""})())["email"]
    people = [p for p in (usage_limits.recipients(c, bot, email), usage_limits.recipients(c, None, email)) if p]
    for actor in dict.fromkeys(H.human_actor(p["id"]) for p in people):
        if task is None or H.task_private_readable(c, actor, task):
            return actor
    return None


def _bot_name(c, bot):
    return (_H().bot(c, bot) or {}).get("display_name") or bot


def runtime_name(runtime):
    from .bot_tools import HARNESS_NAMES
    return HARNESS_NAMES.get(runtime) or str(runtime or "").title() or "The AI model"


def line(c, bot, service, label, fix=None):
    """The card's one line: what stopped the bot."""
    name = _bot_name(c, bot)
    if service == "secret":
        return f"{name} needs {label} to go on."
    if service == "login":
        return f"{name} can't sign in to {label}."
    return f"{name} can't reach {label}."


def view(row):
    """The card as the thread draws it (message refs `reconnect`): status open, fixed, cancelled or retired."""
    return {"key": row["key"], "service": row["service"], "label": row["label"], "bot": row["bot"],
            "person": row["person"], "fix": json.loads(row["fix"] or "{}"),
            "status": (row.get("ended") or "fixed") if row.get("fixed_at") else "open", "fixed_at": row.get("fixed_at")}


# ------------------------------------------------------------------------------------------------ writes
def open(c, task_id, bot, key, service, label, person, fix, message_id=None, owner=None):
    """Card the task and set it waiting for `person` to fix it, once per task and key: a second failure finds the
    live card and changes nothing. `bot` is the bot the account is for; `owner` the task's owner (that bot, unless
    BotOps asks for another bot's credential). `message_id` is a card already posted (a credential card); otherwise
    one is written to `person` in the task's thread. Only a live task with that owner, and a person who can read it.
    Returns the row, or None."""
    H = _H()
    ensure(c)
    owner = owner or H.bot_actor(bot)
    row = H.task(c, task_id) if task_id else None
    if (not row or row["owner_id"] != owner or not H.is_bot(owner) or row["status"] not in H.ACTIVE_STATUSES
            or not person or not H.task_private_readable(c, person, row)):
        return None
    found = c.execute("SELECT * FROM task_reconnects WHERE task_id=? AND key=? AND fixed_at IS NULL",
                      (task_id, key)).fetchone()
    if found and found["owner"] == owner and not (message_id and found["message_id"] != message_id):
        return dict(found)
    # A new credential card for the same key (another person asked to save it) replaces the old card.
    if found:
        _retire(c, dict(found), refresh=False)
    # A closed card on the same key is history: this failure is a new card (an owed line about the old fix is moot).
    c.execute("DELETE FROM task_reconnects WHERE task_id=? AND key=?", (task_id, key))
    record = {"task_id": task_id, "key": key, "bot": bot, "owner": owner, "service": service, "label": label,
              "person": person, "fix": json.dumps(fix or {}), "message_id": message_id, "created_at": H.now(),
              "fixed_at": None, "ended": None}
    card = {CARD_REF: view(record)}
    if message_id:
        c.execute("UPDATE messages SET refs=json_set(coalesce(refs,'{}'),'$.reconnect',json(?)) WHERE id=?",
                  (json.dumps(card[CARD_REF]), message_id))
    else:
        sent = H._write_message(c, owner, person, line(c, bot, service, label, fix),
                                H.conversation(c, row["conversation_id"]), "say", {"task": task_id, **card}, None, None,
                                delivered_at=H.now())
        record["message_id"] = sent["id"] if sent else None
    c.execute("INSERT INTO task_reconnects(task_id,key,bot,owner,service,label,person,fix,message_id,created_at) "
              "VALUES(:task_id,:key,:bot,:owner,:service,:label,:person,:fix,:message_id,:created_at)", record)
    _wait(c, row, f"Reconnect {label} so {_bot_name(c, bot)} can go on.")
    if key.startswith("login:"):
        _fold_hold(c, bot)
    H.event(c, H.KEEPER, "task.reconnect_wait", task_id, {"key": key, "bot": bot, "person": person})
    return record


def _fold_hold(c, bot):
    """A bot already held for failed sign-ins has a Needs-you item of its own; this login card asks for the same fix,
    so that item closes and the card stands for both. The hold stays until the fix (`_release_sign_in_holds`), and
    the item comes back if the card is retired first (`_unfold_hold`)."""
    if not _H()._has_table(c, "run_backoff"):
        return
    from . import bot_stops, run_backoff
    state = run_backoff.get(c, bot)
    if state.get("held_at") and state.get("error_class") == run_backoff.SIGN_IN and state.get("task_id"):
        c.execute("UPDATE run_backoff SET task_id=NULL WHERE bot=?", (bot,))
        bot_stops.close_item(c, state["task_id"], "A reconnect card on its task asks for the same fix.")


def _unfold_hold(c, bot):
    """The last live login card of a bot still held for failed sign-ins went away unfixed: its own item is back."""
    if not _H()._has_table(c, "run_backoff") or bot_open(c, bot, "login:"):
        return
    from . import run_backoff
    state = run_backoff.get(c, bot)
    if state.get("held_at") and state.get("error_class") == run_backoff.SIGN_IN and not state.get("task_id"):
        run_backoff.raise_hold_item(c, None, bot)


def _wait(c, row, note):
    """The task waits on its card: in the fixer's Needs you, out of the stall sweeps. The row is written first, so
    the hold's reason names it (task_holds.reason)."""
    H = _H()
    if row["status"] != "waiting":
        H._set_status(c, H.KEEPER, row, "waiting", note=note)
    else:
        from . import task_holds
        task_holds.refresh(c, row["id"])
    # The status event already carries the note; the card says it in the thread.
    c.execute("UPDATE tasks SET note=?,version=version+1,updated_at=? WHERE id=?", (note, H.now(), row["id"]))
    H._recount(c, row["owner_id"])


def _reads(c, row):
    """Whether the card's person can still read its task (a task made private, or a manager moved, may stop that)."""
    return _H().task_private_readable(c, row["person"], {"id": row["task_id"]})


def access_changed(c, task_id):
    """The task was made private (hubdb): a card whose person cannot read it now is retired (a credential card's request
    cancelled) and opened again for the first fixer who can (`person_for`). A credential card is the bot's to ask again
    (a new request, through the API) and a Google card only its mailbox's person can fix, so those stay retired; the
    task stops waiting on them, and its bot is told when nothing else holds it."""
    if not _present(c):
        return
    H = _H()
    row = H.task(c, task_id)
    if not row:
        return
    live, args = _live()
    gone = [w for w in H._rows(c.execute(f"SELECT * FROM task_reconnects r WHERE r.task_id=? AND {live} ORDER BY r.created_at",
                                         (task_id, *args))) if not _reads(c, w)]
    for wait in gone:
        _retire(c, wait, refresh=False)
        person = person_for(c, None, wait["bot"], row) if wait["service"] == "login" else None
        if person:
            open(c, task_id, wait["bot"], wait["key"], wait["service"], wait["label"], person,
                 json.loads(wait["fix"] or "{}"), owner=wait["owner"])
    if gone:
        from . import task_holds
        names = ", ".join(dict.fromkeys(task_holds.name(c, w["person"]) for w in gone))
        task_holds.refresh(c, task_id, f"{names} cannot see this task now")


def _close(c, wait, ended, at=None):
    """End an open card (fixed, cancelled or retired) and say so on its message. A retired card owes no wake."""
    H = _H()
    at = at or H.now()
    if c.execute("UPDATE task_reconnects SET fixed_at=?,ended=?,woke_at=? WHERE task_id=? AND key=? AND fixed_at IS NULL",
                 (at, ended, at if ended == "retired" else None, wait["task_id"], wait["key"])).rowcount != 1:
        return False
    if wait.get("message_id"):
        c.execute("UPDATE messages SET refs=json_set(refs,'$.reconnect.status',?,'$.reconnect.fixed_at',?) "
                  "WHERE id=? AND json_extract(refs,'$.reconnect') IS NOT NULL", (ended, at, wait["message_id"]))
    return True


def _retire(c, wait, refresh=True, cleared=None):
    """The card stopped standing (its task ended or changed hands, its bot was archived): it asks nothing now, and a
    credential card's request is cancelled, so nobody saves a credential the task no longer needs."""
    if not _close(c, wait, "retired"):
        return
    request = json.loads(wait.get("fix") or "{}").get("request")
    if request and _H()._has_table(c, "credential_requests"):
        c.execute("UPDATE credential_requests SET status='cancelled',updated_at=? WHERE id=? AND status='pending'",
                  (_H().now(), request))
    if refresh:
        from . import task_holds
        task_holds.refresh(c, wait["task_id"], cleared)
    if wait["key"].startswith("login:"):
        _unfold_hold(c, wait["bot"])


def _standing(c, wait):
    """Whether a fix can go to this card's task now: "live", "gone" (retire it) or "unreachable" (its owner cannot
    take the wake now: the card stays open for a later fix or the task's next move)."""
    H = _H()
    row = H.task(c, wait["task_id"])
    if (not row or row["status"] not in H.ACTIVE_STATUSES or row["owner_id"] != wait["owner"]
            or any((H.bot(c, slug) or {}).get("state") in (None, "archived") for slug in {wait["bot"], H.actor_id(row["owner_id"])})):
        return "gone"
    if not H._actor_exists(c, row["owner_id"]) or not H.task_private_readable(c, row["owner_id"], row):
        return "unreachable"
    return "live"


def fixed(c, key, bot=None, why=""):
    """The connection works again: every open card on `key` (only `bot`'s, when given) says so, and each task goes
    back to work with one wake once nothing else holds it (`deliver`). Returns the woken task ids."""
    H = _H()
    if not _present(c):
        return []
    where, args = ("AND bot=?", (key, bot)) if bot else ("", (key,))
    rows = H._rows(c.execute(f"SELECT * FROM task_reconnects WHERE key=? AND fixed_at IS NULL {where} "
                             "ORDER BY created_at, task_id", args))
    done, at = [], H.now()
    for wait in rows:
        standing = _standing(c, wait)
        if standing == "gone":
            _retire(c, wait)
        elif standing == "live" and _close(c, wait, "fixed", at):
            done.append(wait)
    _settle_requests(c, [w for w in done if w["service"] == "secret"], at)
    woken = [t for t in dict.fromkeys(w["task_id"] for w in done) if deliver(c, t, why=why)]
    _release_sign_in_holds(c, {w["bot"] for w in done if w["key"].startswith("login:")})
    return woken


def _settle_requests(c, waits, at):
    """A secret saved through one task's card is saved for every task that asked for it: their cards stop asking."""
    if not waits or not _H()._has_table(c, "credential_requests"):
        return
    for wait in waits:
        request = json.loads(wait["fix"] or "{}").get("request")
        if request:
            c.execute("UPDATE credential_requests SET status='saved',updated_at=? WHERE id=? AND status='pending'", (at, request))


def dropped(c, request_id):
    """The person cancelled the credential card a task waits on: the wait ends, and the bot is told it was not saved."""
    H = _H()
    if not request_id or not _present(c):
        return []
    rows = H._rows(c.execute("SELECT * FROM task_reconnects WHERE fixed_at IS NULL AND json_extract(fix,'$.request')=?",
                             (request_id,)))
    closed = [w for w in rows if _close(c, w, "cancelled")]
    return [w["task_id"] for w in closed if deliver(c, w["task_id"], why="cancelled")]


def _said(c, wait):
    if wait["ended"] == "cancelled":
        from . import task_holds
        return f"{task_holds.name(c, wait['person'])} cancelled the card for {wait['label']}: it was not saved."
    return f"{wait['label']} is " + ("saved" if wait["service"] == "secret" else "reconnected") + "."


def _claim_owed(c, task_id, owner):
    """The fixed or cancelled cards on the task whose wake is still owed to `owner`, claimed for this one wake: a
    card opened for another owner is that owner's news, never the task's next owner's."""
    H = _H()
    owed = H._rows(c.execute(f"SELECT * FROM task_reconnects WHERE task_id=? AND owner=? AND {OWED} ORDER BY fixed_at, key",
                             (task_id, owner)))
    at = H.now()
    return [w for w in owed if c.execute(f"UPDATE task_reconnects SET woke_at=? WHERE task_id=? AND key=? AND {OWED}",
                                         (at, task_id, w["key"])).rowcount == 1]


def _unclaim(c, waits):
    for w in waits:
        c.execute("UPDATE task_reconnects SET woke_at=NULL WHERE task_id=? AND key=?", (w["task_id"], w["key"]))


def deliver(c, task_id, why="", cleared=None):
    """The task's owed reconnect wake, once nothing holds it: back to the working step it held, and ONE wake with
    every owed line. Another open card or hold keeps it owed: the last to clear delivers it (`cleared` names a hold
    task_holds.refresh just saw clear). Answers whether it woke the task."""
    H = _H()
    row = H.task(c, task_id)
    if not row or row["status"] not in H.ACTIVE_STATUSES or pending(c, task_id):
        return False
    if row["status"] == "waiting" and H.waiting_for(c, row):
        from . import task_holds
        task_holds.refresh(c, task_id)
        return False
    if not c.execute(f"SELECT 1 FROM task_reconnects WHERE task_id=? AND owner=? AND {OWED} LIMIT 1",
                     (task_id, row["owner_id"])).fetchone():
        return False
    if not H._actor_exists(c, row["owner_id"]) or not H.task_private_readable(c, row["owner_id"], row):
        return False
    owed = _claim_owed(c, task_id, row["owner_id"])
    if not owed:
        return False
    said = " ".join(_said(c, w) for w in owed)
    if row["status"] == "waiting":
        # Back to the working step it held, without the generic assignment wake: the wake below tells it once.
        note = f"{cleared}; nothing left to wait on" if cleared else said
        step = H._one(c, "SELECT status FROM task_steps WHERE id=?", (row.get("step_id"),))
        kept = row.get("step_id") if step and step["status"] in H.ACTIVE_STATUSES and step["status"] != "waiting" else None
        H._set_status(c, H.KEEPER, row, "open", step=kept, note=note, skip_step_wake=(row["owner_id"],))
        c.execute("UPDATE tasks SET note=?,version=version+1 WHERE id=?", (note, row["id"]))
        row = H.task(c, row["id"])
        H._recount(c, row["owner_id"])
    keys = [w["key"] for w in owed]
    H.event(c, H.KEEPER, "task.reconnected", row["id"], {"keys": keys, "why": why})
    head = "Reconnected" if all(w["ended"] == "fixed" for w in owed) else "Reconnect card closed"
    text = f"{head}: {row['title']}\n{said}" + (f" {cleared}." if cleared else "") + " Continue."
    if not H._wake(c, row, row["owner_id"], text, refs={WAKE_REF: keys[0], "reconnects": keys},
                   trigger="status_or_relation", source=keys[0]):
        _unclaim(c, owed)
        return False
    return True


def fold_into(c, task_row, body, refs):
    """A wake to a task's owner while a reconnect wake is owed and no card still stands (a milestone, a hold that
    cleared by its own path): the owed lines ride on it, so the bot hears them once and is not woken twice
    (H._wake). Returns (body, refs)."""
    if (refs or {}).get(WAKE_REF) or not _present(c) or not c.execute(
            f"SELECT 1 FROM task_reconnects WHERE task_id=? AND owner=? AND {OWED} LIMIT 1",
            (task_row["id"], task_row.get("owner_id"))).fetchone():
        return body, refs
    # Another hold still stands (a child, a question, another card): the wake its last clearing sends carries the news.
    from . import task_holds
    row = _H().task(c, task_row["id"])
    if not row or row["status"] == "waiting" and task_holds.holds(c, row):
        return body, refs
    owed = _claim_owed(c, task_row["id"], task_row.get("owner_id"))
    if not owed:
        return body, refs
    return (f"{body}\n{' '.join(_said(c, w) for w in owed)}",
            {**(refs or {}), "reconnects": [w["key"] for w in owed]})


def _release_sign_in_holds(c, bots):
    """A bot held for failed runs whose cause was the sign-in just fixed goes again now, not at its next try."""
    if not bots or not _H()._has_table(c, "run_backoff"):
        return
    from . import run_backoff
    for bot in sorted(bots):
        state = run_backoff.get(c, bot)
        if state.get("held_at") and state.get("error_class") == run_backoff.SIGN_IN:
            run_backoff.release(c, _H().KEEPER, bot)


def moved(c, task_id, field, old, new):
    """A task's status or owner changed (H._task_event): an unfinished task's cards stand only with the owner they
    were opened for; an ended task's cards are retired. The task's reason follows its next refresh or the stranded
    sweep: the owner column is written after this event."""
    if old == new or field not in ("status", "owner") or not _present(c):
        return
    H = _H()
    if field == "status" and new in H.ACTIVE_STATUSES:
        return
    rows = H._rows(c.execute("SELECT * FROM task_reconnects WHERE task_id=? AND fixed_at IS NULL", (task_id,)))
    for wait in rows:
        if field == "status" or wait["owner"] != new:
            _retire(c, wait, refresh=False)
    # A fix still owed as a wake is news for the owner it was opened for, on a task still going: not after that.
    c.execute(f"UPDATE task_reconnects SET woke_at=? WHERE task_id=? AND {OWED}"
              + ("" if field == "status" else " AND owner<>?"), (H.now(), task_id) + (() if field == "status" else (new,)))


def bot_gone(c, bot):
    """The bot was archived (settings_admin.archive_bot): the cards for its accounts ask nothing now. A task another
    bot owns that waited only on one (BotOps asking for its credential) goes back to work."""
    if not _present(c):
        return
    for wait in _H()._rows(c.execute("SELECT * FROM task_reconnects WHERE bot=? AND fixed_at IS NULL", (bot,))):
        _retire(c, wait, cleared=f"{_bot_name(c, bot)} was archived")


# ------------------------------------------------------------------------------------------------ reads
def pending(c, task_id):
    """The standing card on this task, as `view` draws it, or None."""
    if not _present(c):
        return None
    live, args = _live()
    for row in c.execute(f"SELECT * FROM task_reconnects r WHERE r.task_id=? AND {live} ORDER BY r.created_at",
                         (task_id, *args)).fetchall():
        if _reads(c, row):
            return view(dict(row))
    return None


def tasks_for(c, person):
    """The waiting tasks with a standing card `person` fixes, oldest card first (hubdb.needs_you)."""
    if not _present(c):
        return []
    H = _H()
    live, args = _live()
    found = sorted((r["created_at"], r["task_id"]) for r in c.execute(
        f"SELECT r.task_id, min(r.created_at) AS created_at, r.person FROM task_reconnects r WHERE r.person=? AND {live} "
        "GROUP BY r.task_id", (person, *args)).fetchall() if _reads(c, r))
    rows = (H.task(c, task_id) for _, task_id in found)
    return [t for t in rows if t and t["status"] == "waiting"]


def hold(c, task_id):
    """The standing card as a hold (task_holds.holds, hubdb.wait_detail): {"why", "on", "what"}, or None."""
    card = pending(c, task_id)
    if not card:
        return None
    return {"why": f"{card['label']} to be reconnected" if card["service"] != "secret" else f"{card['label']} to be saved",
            "on": card["person"], "what": ("save " if card["service"] == "secret" else "reconnect ") + card["label"]}


def for_tasks(c, task_ids, person):
    """{task id: the oldest standing card `person` fixes on it} for the tasks among `task_ids` that have one: Needs
    you's cards in one read. Per person: two people may each have a card on one task."""
    ids = [t for t in dict.fromkeys(task_ids) if t]
    if not ids or not _present(c):
        return {}
    live, args = _live()
    out = {}
    for part in _H().chunks(ids):
        marks = ",".join("?" * len(part))
        for row in c.execute(f"SELECT * FROM task_reconnects r WHERE r.task_id IN ({marks}) AND r.person=? AND {live} "
                             "ORDER BY r.created_at", (*part, person, *args)).fetchall():
            if row["task_id"] not in out and _reads(c, row):
                out[row["task_id"]] = view(dict(row))
    return out


def card_of(c, message_id):
    """Whether this message is a reconnect card (a credential card in a task's thread is one)."""
    if not message_id or not _present(c):
        return False
    return c.execute("SELECT 1 FROM task_reconnects WHERE message_id=? LIMIT 1", (message_id,)).fetchone() is not None


def bot_open(c, bot, prefix):
    """Whether the bot has a standing card on a task it owns whose key starts with `prefix` ("login:"): that cause
    already has a person."""
    if not _present(c):
        return False
    live, args = _live()
    return any(_reads(c, r) for r in c.execute(
        f"SELECT r.task_id, r.person FROM task_reconnects r WHERE r.bot=? AND r.owner=? AND r.key LIKE ? AND {live}",
        (bot, _H().bot_actor(bot), prefix.replace("%", "") + "%", *args)).fetchall())


def request_of(c, task_id, key):
    """The credential request the task's standing card on `key` is, if it is still pending: the card to reuse."""
    if not _present(c) or not _H()._has_table(c, "credential_requests"):
        return None
    live, args = _live()
    row = c.execute(f"SELECT q.*, r.person AS card_person FROM task_reconnects r JOIN credential_requests q "
                    f"ON q.id=json_extract(r.fix,'$.request') WHERE r.task_id=? AND r.key=? AND q.status='pending' "
                    f"AND {live}", (task_id, key, *args)).fetchone()
    if not row or not _reads(c, {"person": row["card_person"], "task_id": task_id}):
        return None
    found = dict(row)
    found.pop("card_person")
    return found


def waited_requests(c, bot, env):
    """The pending credential requests for `bot`'s `env` that a standing card waits on: a newer ask leaves them be."""
    if not _present(c):
        return set()
    live, args = _live()
    return {r["request"] for r in c.execute(
        f"SELECT json_extract(r.fix,'$.request') AS request, r.task_id, r.person FROM task_reconnects r "
        f"WHERE r.bot=? AND r.key=? AND {live}", (bot, secret_key(bot, env), *args)).fetchall() if _reads(c, r)}


# ------------------------------------------------------------------------------------------------ the hooks
DEFAULT_PROFILE = "_default"    # the computer's own login: profile names are [a-z0-9-], so no profile is called this


def login_key(runner_id, runtime, profile=""):
    return f"login:{runner_id}:{runtime}:{profile or DEFAULT_PROFILE}"


def login_refused(c, settings, bot, runner_id, runtime, task_id, profile=""):
    """A run on `task_id` ended because the provider refused the computer's sign-in or key for `runtime` on `profile`
    ("" for the computer's own login)."""
    if not task_id or not runtime:
        return None
    task = _H().task(c, task_id)
    machine = (c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone() or [runner_id])[0]
    label = f"{runtime_name(runtime)} on {machine}" + (f" ({profile})" if profile else "")
    return open(c, task_id, bot, login_key(runner_id, runtime, profile), "login", label,
                person_for(c, settings, bot, task) if task else None,
                {"kind": "login", "runner": runner_id, "runtime": runtime, "machine": machine, "profile": profile or ""})


def login_works(c, runner_id, logins):
    """The computer's report, or a completed run on it: each (runtime, profile) of `logins` is signed in there again."""
    if not _present(c):
        return []
    woken = []
    for runtime, profile in logins:
        woken += fixed(c, login_key(runner_id, runtime, profile), why="signed_in")
    return woken


def _login_state(readiness, runtime, profile):
    """The computer's report row for one login (runner-rs/src/readiness.rs): its own login is `profiles[""]`, or the
    runtime's row when it reports only that one; a named profile is its own `profiles` row, or the own login's when
    it is the computer's named home (`computer_profile`) or shares that login (`shared_login`). {} when unreported."""
    state = ((readiness or {}).get("runtimes") or {}).get(runtime)
    state = state if isinstance(state, dict) else {}
    rows = state.get("profiles") if isinstance(state.get("profiles"), dict) else None
    own = (rows.get("") if rows is not None else state) or {}
    if not profile:
        return own
    row = (rows or {}).get(profile)
    if isinstance(row, dict) and row:
        return own if row.get("shared_login") is True else row
    return own if profile == (readiness or {}).get("computer_profile") else {}


def login_report(c, runner_id, readiness):
    """A computer's heartbeat changed what it reports: the logins it now says are ready end their cards. A
    runtime-wide ready says nothing of a named profile (`_login_state`)."""
    if not _present(c):
        return []
    ready = []
    for (fix,) in c.execute("SELECT DISTINCT fix FROM task_reconnects WHERE fixed_at IS NULL AND key LIKE ?",
                            (f"login:{runner_id}:%",)).fetchall():
        fix = json.loads(fix or "{}")
        runtime, profile = fix.get("runtime"), fix.get("profile") or ""
        if _login_state(readiness, runtime, profile).get("authenticated") == "ready":
            ready.append((runtime, profile))
    return login_works(c, runner_id, list(dict.fromkeys(ready)))


def _accounts(values):
    return list(dict.fromkeys(str(a).strip().lower() for a in values or () if a and str(a).strip()))


def google_report(c, service, refused, listed=()):
    """One connector health report for `service` (mail or calendar): `refused` are the accounts whose Google sign-in
    it was refused, `listed` every account it reports failing for any reason. A refused account's tasks get a card;
    an account this service refused before and no longer lists works again for it (`google_good`)."""
    H = _H()
    ensure(c)
    refused = _accounts(refused)
    listed = set(_accounts(listed)) | set(refused)
    for account in refused:
        c.execute("INSERT INTO google_signin_refusals(account,service,created_at) VALUES(?,?,?) "
                  "ON CONFLICT(account,service) DO UPDATE SET created_at=excluded.created_at", (account, service, H.now()))
    opened = google_failing(c, refused)
    good = [a for (a,) in c.execute("SELECT account FROM google_signin_refusals WHERE service=?", (service,)).fetchall()
            if a not in listed]
    google_good(c, service, good)
    return opened


def google_good(c, service, accounts):
    """`service` just worked for these accounts (a clean report, a mailbox synced, a calendar published): what it
    refused for them is cleared, and an account no service still refuses is reconnected. Returns the woken tasks."""
    if not _present(c):
        return []
    woken = []
    for account in _accounts(accounts):
        c.execute("DELETE FROM google_signin_refusals WHERE account=? AND service=?", (account, service))
        if not c.execute("SELECT 1 FROM google_signin_refusals WHERE account=? LIMIT 1", (account,)).fetchone():
            woken += fixed(c, "google:" + account, why=service)
    return woken


def google_connected(c, account):
    """The person connected the mailbox again (backend/google_connect.py): every refusal for it is past."""
    if not account or not _present(c):
        return []
    account = str(account).strip().lower()
    c.execute("DELETE FROM google_signin_refusals WHERE account=?", (account,))
    return fixed(c, "google:" + account, why="connected")


def google_failing(c, accounts):
    """The connector says these mailboxes' Google sign-in was refused: the message bot that reads each one stops on
    the tasks it is working, or worked in the last RECENT_S."""
    from . import routines
    H = _H()
    accounts = set(_accounts(accounts))
    if not accounts:
        return []
    opened = []
    since = H.shift(H.now(), seconds=-RECENT_S)
    for box in routines.message_bot_mailboxes(c):
        address = str(box["address"]).lower()
        if address not in accounts:
            continue
        bot = box["bot"]
        for (task_id,) in c.execute(
                "SELECT DISTINCT u.task_id FROM turns u JOIN tasks t ON t.id=u.task_id WHERE u.bot=? AND t.owner_id=? "
                "AND u.started_at>? AND (u.finished_at IS NULL OR u.finished_at>?)",
                (bot, H.bot_actor(bot), H.shift(since, hours=-6), since)).fetchall():
            row = open(c, task_id, bot, "google:" + address, "google", f"Google ({address})",
                       H.human_actor(box["person_id"]), {"kind": "google", "account": address})
            if row:
                opened.append(task_id)
    return opened


def secret_stored(c, bot, env):
    """The variable `env` now reaches `bot` (stored for it, or granted to it)."""
    if not env or not bot:
        return []
    return fixed(c, secret_key(bot, env), bot=bot, why="credential")
