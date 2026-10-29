"""A bot's work on a task stops because an account or a credential needs a person: one card in the task's thread.

The card says what broke in one line ("Sales Bot can't reach Google (ana@acme.example).") and carries the fix: the
person's own Connect Google, the computer's sign-in pop-up, or a credential card. The task waits on the card, a
recorded wait like a milestone's (hubdb.wait_detail, wake_rules.parked): it is in the fixer's Needs you under
connect (hubdb.needs_you), and no stall wake, stranded sweep or news spends turns on it meanwhile. It does not set
`waiting_on`: a bot naming a person there asks in a subtask instead, and this card is that ask. When the account
works again the card says "Reconnected" and the task's owner gets ONE wake on that task, through the one wake path
(H._wake, refs `reconnected`, an AWAITED kind in backend/wake_rules.py).

One row per task and connection key (`task_reconnects`), so a second failure on the same task reuses its card and
several tasks can wait on one key; Needs you folds them into one item (views.fold_reconnects). The keys:

- `google:<account>`: the connector's Google sign-in for that mailbox was refused (backend/connectors.py health);
  fixed by a clean report for it, or by the person connecting it again (backend/google_connect.py). The person is
  the mailbox's own: only they connect it.
- `login:<computer>:<runtime>`: the provider refused the computer's sign-in or key (`auth_rejected` on a run,
  backend/execution.py); fixed when that computer reports the runtime signed in, or a run on it completes.
- `secret:<ENV>`: the bot asked for a credential it lacks from a task (backend/credential_cards.py); fixed when
  that variable is stored for or granted to the bot (backend/credentials.py). Per bot: the grant is the bot's.

Anything else (GitHub, an MCP server's OAuth) is a new key with its own fix kind, opened and fixed through the same
`open` and `fixed`. The person is the bot's operator, else the company owner (usage_limits.recipients), except a
Google mailbox's own person.

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import json

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_reconnects(
  task_id TEXT NOT NULL, key TEXT NOT NULL, bot TEXT NOT NULL, service TEXT NOT NULL, label TEXT NOT NULL,
  person TEXT NOT NULL, fix_json TEXT NOT NULL, message_id TEXT, created TEXT NOT NULL, fixed_at TEXT, woke_at TEXT,
  PRIMARY KEY(task_id, key))""",
    "CREATE INDEX IF NOT EXISTS task_reconnects_open ON task_reconnects(key) WHERE fixed_at IS NULL",
)
WAKE_REF = "reconnected"
CARD_REF = "reconnect"
RECENT_S = 900          # a bot's task worked this recently is the one a connector failure stops


def _H():
    from . import hubdb
    return hubdb


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def _present(c):
    return _H()._has_table(c, "task_reconnects")


def person_for(c, settings, bot):
    """Who fixes the bot's accounts: its operator, else the company owner, as an actor, or None."""
    from . import usage_limits
    person = usage_limits.recipients(c, bot, getattr(settings, "owner_email", "") or "")
    return _H().human_actor(person["id"]) if person else None


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
    """The card as the thread draws it (message refs `reconnect`)."""
    return {"key": row["key"], "service": row["service"], "label": row["label"], "bot": row["bot"],
            "person": row["person"], "fix": json.loads(row["fix_json"] or "{}"),
            "status": "fixed" if row.get("fixed_at") else "open", "fixed_at": row.get("fixed_at")}


# ------------------------------------------------------------------------------------------------ writes
def open(c, task_id, bot, key, service, label, person, fix, message_id=None):
    """Card the task and set it waiting for `person` to fix it, once per task and key: a second failure finds the open row and
    changes nothing. `message_id` is a card already posted (a credential card); otherwise one is written to `person`
    in the task's thread. Only a live task the bot owns, and a person who can read it. Returns the row, or None."""
    H = _H()
    ensure(c)
    row = H.task(c, task_id) if task_id else None
    if (not row or row["owner"] != H.bot_actor(bot) or row["status"] not in H.ACTIVE_STATUSES or not person
            or not H.task_private_readable(c, person, row)):
        return None
    found = c.execute("SELECT * FROM task_reconnects WHERE task_id=? AND key=? AND fixed_at IS NULL",
                      (task_id, key)).fetchone()
    if found:
        return dict(found)
    c.execute("DELETE FROM task_reconnects WHERE task_id=? AND key=?", (task_id, key))
    record = {"task_id": task_id, "key": key, "bot": bot, "service": service, "label": label, "person": person,
              "fix_json": json.dumps(fix or {}), "message_id": message_id, "created": H.now(), "fixed_at": None}
    card = {CARD_REF: view(record)}
    if message_id:
        c.execute("UPDATE messages SET refs_json=json_set(coalesce(refs_json,'{}'),'$.reconnect',json(?)) WHERE id=?",
                  (json.dumps(card[CARD_REF]), message_id))
    else:
        sent = H._write_message(c, H.bot_actor(bot), person, line(c, bot, service, label, fix),
                                H.conversation(c, row["conversation_id"]), "say", {"task": task_id, **card}, None, None,
                                delivered_at=H.now())
        record["message_id"] = sent["id"] if sent else None
    c.execute("INSERT INTO task_reconnects(task_id,key,bot,service,label,person,fix_json,message_id,created) "
              "VALUES(:task_id,:key,:bot,:service,:label,:person,:fix_json,:message_id,:created)", record)
    _wait(c, row, f"Reconnect {label} so {_bot_name(c, bot)} can go on.")
    _fold_hold(c, bot)
    H.event(c, H.KEEPER, "task.reconnect_wait", task_id, {"key": key, "bot": bot, "person": person})
    return record


def _fold_hold(c, bot):
    """A bot already held for failed sign-ins has a Needs-you item of its own; this card asks for the same fix, so
    that item closes and the card stands for both. The hold stays until the fix (`_release_sign_in_holds`)."""
    if not _H()._has_table(c, "run_backoff"):
        return
    from . import bot_stops, run_backoff
    state = run_backoff.get(c, bot)
    if state.get("held_at") and state.get("error_class") == run_backoff.SIGN_IN and state.get("task_id"):
        c.execute("UPDATE run_backoff SET task_id=NULL WHERE bot=?", (bot,))
        bot_stops.close_item(c, state["task_id"], "A reconnect card on its task asks for the same fix.")


def _wait(c, row, note):
    """The task waits on its card: in the fixer's Needs you, out of the stall sweeps. The row is written first, so
    the hold's reason names it (task_holds.reason)."""
    H = _H()
    if row["status"] != "waiting":
        H._set_status(c, H.KEEPER, row, "waiting", note=note)
    else:
        from . import task_holds
        task_holds.refresh(c, row["id"])
    c.execute("UPDATE tasks SET note=?,version=version+1,updated=? WHERE id=?", (note, H.now(), row["id"]))
    H._task_event(c, row["id"], H.KEEPER, "note", row.get("note"), note, "")
    H._recount(c, row["owner"])


def fixed(c, key, bot=None, why=""):
    """The connection works again: every open card on `key` (only `bot`'s, when given) says so, its task goes back to
    work when nothing else holds it, and the task's owner gets one wake on it. Returns the woken task ids."""
    H = _H()
    if not _present(c):
        return []
    where, args = ("AND bot=?", (key, bot)) if bot else ("", (key,))
    rows = H._rows(c.execute(f"SELECT * FROM task_reconnects WHERE key=? AND fixed_at IS NULL {where} "
                             "ORDER BY created, task_id", args))
    woken, at = [], H.now()
    for wait in rows:
        # The claim and the wake are one write: a second report of the same fix finds nothing open.
        if c.execute("UPDATE task_reconnects SET fixed_at=?,woke_at=? WHERE task_id=? AND key=? AND fixed_at IS NULL",
                     (at, at, wait["task_id"], key)).rowcount != 1:
            continue
        if wait.get("message_id"):
            c.execute("UPDATE messages SET refs_json=json_set(refs_json,'$.reconnect.status','fixed',"
                      "'$.reconnect.fixed_at',?) WHERE id=? AND json_extract(refs_json,'$.reconnect') IS NOT NULL",
                      (at, wait["message_id"]))
        if _resume(c, wait, why):
            woken.append(wait["task_id"])
    _release_sign_in_holds(c, {w["bot"] for w in rows})
    return woken


def _resume(c, wait, why):
    H = _H()
    row = H.task(c, wait["task_id"])
    if not row or row["status"] not in (*H.ACTIVE_STATUSES, "blocked"):
        return False
    said = f"{wait['label']} is " + ("saved" if wait["service"] == "secret" else "reconnected") + ". Continue."
    if row["status"] == "waiting" and not H.waiting_for(c, row):
        # Back to the working step it held, without the generic assignment wake: the wake below tells it once.
        step = H._one(c, "SELECT status FROM task_steps WHERE id=?", (row.get("step_id"),))
        kept = row.get("step_id") if step and step["status"] in H.ACTIVE_STATUSES and step["status"] != "waiting" else None
        H._set_status(c, H.KEEPER, row, "open", step=kept, note=said, skip_step_wake=(row["owner"],))
        c.execute("UPDATE tasks SET note=?,version=version+1 WHERE id=?", (said, row["id"]))
        row = H.task(c, row["id"])
        H._recount(c, row["owner"])
    elif row["status"] == "waiting":
        from . import task_holds
        task_holds.refresh(c, row["id"])
    H.event(c, H.KEEPER, "task.reconnected", row["id"], {"key": wait["key"], "bot": wait["bot"], "why": why})
    H._wake(c, row, row["owner"], f"Reconnected: {row['title']}\n{said}", refs={WAKE_REF: wait["key"]},
            trigger="status_or_relation", source=wait["key"])
    return True


def _release_sign_in_holds(c, bots):
    """A bot held for failed runs whose cause was the sign-in just fixed goes again now, not at its next try."""
    if not bots or not _H()._has_table(c, "run_backoff"):
        return
    from . import run_backoff
    for bot in sorted(bots):
        state = run_backoff.get(c, bot)
        if state.get("held_at") and state.get("error_class") == run_backoff.SIGN_IN:
            run_backoff.release(c, _H().KEEPER, bot)


# ------------------------------------------------------------------------------------------------ reads
def pending(c, task_id):
    """The open card on this task, as `view` draws it, or None."""
    if not _present(c):
        return None
    row = c.execute("SELECT * FROM task_reconnects WHERE task_id=? AND fixed_at IS NULL ORDER BY created LIMIT 1",
                    (task_id,)).fetchone()
    return view(dict(row)) if row else None


def tasks_for(c, person):
    """The live tasks with an open card `person` fixes, oldest card first (hubdb.needs_you)."""
    if not _present(c):
        return []
    H = _H()
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    return H._rows(c.execute(
        "SELECT t.* FROM tasks t JOIN (SELECT task_id, min(created) AS created FROM task_reconnects "
        "WHERE person=? AND fixed_at IS NULL GROUP BY task_id) r ON r.task_id=t.id "
        f"WHERE t.status IN ({marks}) ORDER BY r.created, t.id", (person, *H.ACTIVE_STATUSES)))


def hold(c, task_id):
    """The open card as a hold (task_holds.holds, hubdb.wait_detail): {"why", "on", "what"}, or None."""
    card = pending(c, task_id)
    if not card:
        return None
    return {"why": f"{card['label']} to be reconnected" if card["service"] != "secret" else f"{card['label']} to be saved",
            "on": card["person"], "what": ("save " if card["service"] == "secret" else "reconnect ") + card["label"]}


def for_tasks(c, task_ids):
    """{task id: its open card} for the tasks among `task_ids` that have one: Needs you's cards in one read."""
    ids = [t for t in dict.fromkeys(task_ids) if t]
    if not ids or not _present(c):
        return {}
    out = {}
    for part in _H().chunks(ids):
        marks = ",".join("?" * len(part))
        for row in c.execute(f"SELECT * FROM task_reconnects WHERE fixed_at IS NULL AND task_id IN ({marks}) "
                             "ORDER BY created", part):
            out.setdefault(row["task_id"], view(dict(row)))
    return out


def card_of(c, message_id):
    """Whether this message is a reconnect card (a credential card in a task's thread is one)."""
    if not message_id or not _present(c):
        return False
    return c.execute("SELECT 1 FROM task_reconnects WHERE message_id=? LIMIT 1", (message_id,)).fetchone() is not None


def bot_open(c, bot, prefix):
    """Whether the bot has an open card whose key starts with `prefix` ("login:", or "" for any): its cause already has
    a person."""
    if not _present(c):
        return False
    return c.execute("SELECT 1 FROM task_reconnects WHERE bot=? AND fixed_at IS NULL AND key LIKE ? LIMIT 1",
                     (bot, prefix.replace("%", "") + "%")).fetchone() is not None


# ------------------------------------------------------------------------------------------------ the hooks
def login_refused(c, settings, bot, runner_id, runtime, task_id):
    """A run on `task_id` ended because the provider refused the computer's sign-in or key for `runtime`."""
    if not task_id or not runtime:
        return None
    machine = (c.execute("SELECT label FROM runners WHERE id=?", (runner_id,)).fetchone() or [runner_id])[0]
    label = f"{runtime_name(runtime)} on {machine}"
    return open(c, task_id, bot, f"login:{runner_id}:{runtime}", "login", label, person_for(c, settings, bot),
                {"kind": "login", "runner": runner_id, "runtime": runtime, "machine": machine})


def login_works(c, runner_id, runtimes):
    """The computer's report, or a completed run on it: each of `runtimes` is signed in there again."""
    if not _present(c):
        return []
    woken = []
    for runtime in runtimes:
        woken += fixed(c, f"login:{runner_id}:{runtime}", why="signed_in")
    return woken


def login_report(c, runner_id, readiness):
    """A computer's heartbeat changed what it reports: the logins it now says are ready end their cards."""
    if not _present(c) or not c.execute("SELECT 1 FROM task_reconnects WHERE fixed_at IS NULL AND key LIKE ? LIMIT 1",
                                        (f"login:{runner_id}:%",)).fetchone():
        return []
    ready = [name for name, state in ((readiness or {}).get("runtimes") or {}).items()
             if isinstance(state, dict) and state.get("authenticated") == "ready"]
    return login_works(c, runner_id, ready)


def google_report(c, refused):
    """A connector report: `refused` are the mailboxes whose Google sign-in it was refused just now. Their message
    bots' tasks get a card; a card whose mailbox neither connector (mail, calendar) still reports refused is fixed."""
    opened = google_failing(c, refused)
    failing = set()
    for (detail,) in c.execute("SELECT detail_json FROM service_health WHERE service IN "
                               "('connector:mail','connector:calendar') AND last_error IS NOT NULL").fetchall():
        detail = json.loads(detail or "{}")
        if "signin" not in detail:
            return opened       # a report from before it named its accounts: nothing says which still fail
        failing |= {str(a).lower() for a in detail["signin"]}
    google_works(c, failing)
    return opened


def google_failing(c, accounts):
    """The connector says these mailboxes' Google sign-in was refused: the message bot that reads each one stops on
    the tasks it is working, or worked in the last RECENT_S."""
    from . import routines
    H = _H()
    accounts = {str(a).strip().lower() for a in accounts if a}
    if not accounts:
        return []
    ensure(c)
    opened = []
    since = H.shift(H.now(), seconds=-RECENT_S)
    for box in routines.message_bot_mailboxes(c):
        address = str(box["address"]).lower()
        if address not in accounts:
            continue
        bot = box["bot"]
        for (task_id,) in c.execute(
                "SELECT DISTINCT u.task_id FROM turns u JOIN tasks t ON t.id=u.task_id WHERE u.bot=? AND t.owner=? "
                "AND u.started>? AND (u.finished IS NULL OR u.finished>?)",
                (bot, H.bot_actor(bot), H.shift(since, hours=-6), since)).fetchall():
            row = open(c, task_id, bot, "google:" + address, "google", f"Google ({address})",
                       H.human_actor(box["person_id"]), {"kind": "google", "account": address})
            if row:
                opened.append(task_id)
    return opened


def google_works(c, accounts_failing):
    """Every open Google card whose mailbox is not among `accounts_failing` is reconnected."""
    if not _present(c):
        return []
    failing = {str(a).lower() for a in accounts_failing}
    woken = []
    for (key,) in c.execute("SELECT DISTINCT key FROM task_reconnects WHERE fixed_at IS NULL AND key LIKE 'google:%'"
                            ).fetchall():
        if key[len("google:"):] not in failing:
            woken += fixed(c, key, why="connector")
    return woken


def secret_stored(c, bot, env):
    """The variable `env` now reaches `bot` (stored for it, or granted to it)."""
    if not env or not bot:
        return []
    return fixed(c, "secret:" + env, bot=bot, why="credential")
