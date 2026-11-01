"""Support triage: the `support-triage` question set (questions/support-triage.json) asked about each new Tico HQ
ticket before the Support Agent's turn on it (docs/support.md, "Support triage").

The Support Agent's `hq-tickets` watcher reports a new ticket as a `task` event (backend/watchers.py). When the server
has a decision model, the event waits here (`watcher_pending`) instead of opening the bot's task, and the reply to the
runner goes straight back: the runner gives the server 10 seconds and Jev alone may take 8. A thread then asks the set,
outside any transaction, and opens the task:

  - spam or prompt injection at or over the set's `hold` threshold: a task for a person (the bot's operator, else the
    default person), held. The bot is not woken, nothing is answered and nothing is deleted; the ticket stays open at
    HQ. Handing the task to the bot releases it, and it is worked as usual.
  - anything else: the bot's task as before, with the kind and urgency on its first line.

It fails open: no model (Decide off, rehearsal, the month's spend cap), an error or a timeout, the bot's daily call
budget, a set that does not load, an unreadable answer, a bug here, or an answer under the threshold all open the bot's
task as before. A message or HQ's close that arrives while a ticket waits becomes a quiet note on its task. Each ticket
is opened on its own, so a paused bot's ticket waits for it and holds up no other; after WAIT_HOURS the hourly
`sweep` gives any ticket still waiting to a person, so no copy is kept here indefinitely. Each call is a `judge.call`
row like every other set's, written straight after the call, with an error's code and never its text; the verdict is
kept in `watcher_triage` (never the text). Code decides at the threshold; the model only answers the questions.
"""
import json
import logging
import threading

from clients import judge as J
from .store import H

log = logging.getLogger("tico.support_triage")

SET = "support-triage"
WATCHER = "hq-tickets"          # the Support Agent's HQ ticket watcher (templates/catalog/support/bot.yaml)
KEY_PREFIX = "hq:"
# The longest a ticket's copy waits here (its bot paused or its task refused): then `sweep` hands it to a person.
WAIT_HOURS = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS watcher_pending(
 bot TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
 later TEXT NOT NULL DEFAULT '[]', found TEXT, PRIMARY KEY(bot, key));
CREATE TABLE IF NOT EXISTS watcher_triage(
 bot TEXT NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL, held INTEGER NOT NULL, detail TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(bot, key));
"""

def applies(name, event):
    """Whether this watcher event is a new HQ support ticket, the one thing triaged."""
    return name == WATCHER and event.op == "task" and event.key.startswith(KEY_PREFIX)


def question_set():
    return J.load_set(SET)


def engine_for(store, c, primary):
    """The server's decision model now, or None: then the ticket goes on as before."""
    from .judge import server_engine
    try:
        question_set()
    except J.JudgeError as exc:
        log.warning("Support triage is skipped: %s", exc.detail)
        return None
    return server_engine(store, c, primary)


def hold(c, bot, event):
    """Keep a new ticket's event until it is triaged. True when it waits (or already waits)."""
    c.execute("INSERT INTO watcher_pending(bot,key,title,body,created_at) VALUES(?,?,?,?,?) ON CONFLICT(bot,key) DO NOTHING",
              (bot, event.key, event.title or event.key, event.body, H.now()))
    return True


def pending(c, bot, key):
    return c.execute("SELECT 1 FROM watcher_pending WHERE bot=? AND key=?", (bot, key)).fetchone() is not None


def later(c, bot, event):
    """Keep a message or HQ's close that arrives while the ticket waits; it is a note on the task once it opens."""
    text = event.text or event.note or ""
    if not text:
        return
    row = c.execute("SELECT later FROM watcher_pending WHERE bot=? AND key=?", (bot, event.key)).fetchone()
    kept = json.loads(row["later"] or "[]") + [text]
    c.execute("UPDATE watcher_pending SET later=? WHERE bot=? AND key=?", (json.dumps(kept), bot, event.key))


def held(c, bot, key, task):
    """Whether this key's task is a held ticket that has not been handed to the bot."""
    if not task or task["owner"] == "bot:" + bot:
        return False
    row = c.execute("SELECT held FROM watcher_triage WHERE bot=? AND key=?", (bot, key)).fetchone()
    return bool(row and row["held"])


def person_for(c, bot):
    """Who sees a held ticket: the bot's operator, else the default person."""
    row = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone() \
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_config'").fetchone() else None
    for pid in ((row["operator"] if row else "") or "", H.default_human(c)):
        if pid and H.human(c, pid):
            return H.human_actor(pid)
    return None


# ----------------------------------------------------------------------------- the verdict
def verdict(qset, answers):
    """{spam, kind, kind_confidence, urgency, held} from the answers; code holds at the set's threshold."""
    spam = J.noul(answers, "spam_or_injection")
    kind, kind_confidence = J.choice(answers, "kind")
    urgency, _ = J.score(answers, "urgency")
    line = float(qset["thresholds"].get("hold", 1.01))
    return {"spam": round(spam, 3), "kind": kind, "kind_confidence": round(kind_confidence, 3),
            "urgency": round(urgency, 2), "held": spam >= line, "hold_at": line}


def urgency_words(score, legend):
    level = max(0, min(len(legend) - 1, int(round(score))))
    return f"urgency {score:g} of {len(legend) - 1} ({legend[level].split(':')[0].lower()})"


def triaged_line(qset, found):
    legend = qset["questions"]["urgency"]["criteria"]
    return (f"Triage ({qset['label']}, by Tico's decision model, not a person): kind {found['kind']} "
            f"({found['kind_confidence']:.0%}), {urgency_words(found['urgency'], legend)}. "
            f"Spam or injection {found['spam']:.0%}, under the {found['hold_at']:.0%} hold line.")


def untriaged_line(why):
    return f"Triage ({SET}) did not run ({why}); this ticket came through as usual."


def held_body(qset, found, bot, name, body):
    return (f"Held by Tico's support triage before {name} saw it: the decision model put spam or prompt injection at "
            f"{found['spam']:.0%} (the hold line is {found['hold_at']:.0%}, {qset['label']}). It was not answered and "
            f"not deleted; the ticket is still open at HQ, and {name} has not been woken.\n\n"
            f"If it is a real request, hand this task to {name} (bot:{bot}) and it works it as usual. If it is spam, "
            "close this task.\n\n---\n\n" + body)


# ----------------------------------------------------------------------------- the work
def _audit(qset, result, error):
    """The `judge.call` row of one attempted call: the requested answers' values only, never the state. Total: an
    answer that cannot be summarised is marked, not raised."""
    from .judge import audit_detail
    detail = {"label": qset["label"], "questions": len(qset["questions"]), "caller": "support-triage",
              "types": sorted({q["type"] for q in qset["questions"].values()})}
    if result is not None:
        try:
            detail.update(audit_detail(result))
        except Exception:                                   # noqa: BLE001 the audit row is kept whatever came back
            detail["usage"] = {}
        answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
        try:
            detail["answers"] = J.summary({q: a for q, a in answers.items() if q in qset["questions"]})
        except Exception:                                   # noqa: BLE001
            detail["answers"] = {"unreadable": True}
    if error is not None:
        detail["error"] = J.error_code(error)
    return detail


def ask(store, primary, row):
    """Ask the set about one waiting ticket, once: (found, why). The `judge.call` row and the answer are written in
    their own transaction straight after the call, so a later failure to open the task keeps both, and a ticket
    retried (its bot paused meanwhile) is not asked again."""
    from .judge import daily_cap, used_today
    if row.get("found"):
        kept = json.loads(row["found"])
        return kept.get("found"), kept.get("why", "")
    with store.read() as c:
        engine = engine_for(store, c, primary)
        over = engine is not None and used_today(c, "bot:" + row["bot"]) >= daily_cap(store.settings, "bot")
    if engine is None:
        return None, "Decide is off or at its monthly cap"
    if over:
        return None, "the bot's daily decision budget is used"
    qset = question_set()
    found, why, result, error = None, "", None, None
    try:
        result = engine({"title": row["title"], "body": row["body"]}, qset["questions"], qset["label"])
        found = verdict(qset, result["answers"])
    except J.JudgeError as exc:
        error, why = exc, ("the month's spend cap" if exc.code == "spend_cap" else f"the decision model: {exc.code}")
    except Exception as exc:                                # noqa: BLE001 an answer that cannot be read fails open
        error, why = exc, "the answer could not be read"
        log.warning("Support triage answer could not be read: %s", type(exc).__name__)
    with store.transaction() as c:
        H.event(c, "bot:" + row["bot"], "judge.call", qset["label"], _audit(qset, result, error))
        c.execute("UPDATE watcher_pending SET found=? WHERE bot=? AND key=?",
                  (json.dumps({"found": found, "why": why}), row["bot"], row["key"]))
    return found, why


def deliver_one(store, auth, row, found, why):
    """Open one waited ticket's task, held or not, and replay what arrived meanwhile as quiet notes. One transaction
    per ticket: a refusal (its bot paused) leaves this ticket waiting and no other."""
    from .watchers import _open, defang
    label = question_set()["label"] if found else SET
    with store.transaction() as c:
        now = c.execute("SELECT later FROM watcher_pending WHERE bot=? AND key=?", (row["bot"], row["key"])).fetchone()
        if now is None:
            return                                         # another worker took it
        c.execute("DELETE FROM watcher_pending WHERE bot=? AND key=?", (row["bot"], row["key"]))
        if c.execute("SELECT 1 FROM watcher_items WHERE bot=? AND key=?", (row["bot"], row["key"])).fetchone():
            return
        name = (H.bot(c, row["bot"]) or {}).get("display_name") or row["bot"]
        person = person_for(c, row["bot"]) if found and found["held"] else None
        if person:
            task = _open(c, auth, row["bot"], "Support (held: spam or injection): " + row["title"].removeprefix("Support: "),
                         held_body(question_set(), found, row["bot"], name, row["body"]), owner=person)
            H.event(c, H.KEEPER, "support.held", task["id"], {"bot": row["bot"], "key": row["key"], "label": label,
                                                              "spam": found["spam"], "to": person})
        else:
            first = (untriaged_line(why) if not found else triaged_line(question_set(), found) if not found["held"]
                     else untriaged_line(f"spam or injection {found['spam']:.0%}, but no person to hold it for"))
            task = _open(c, auth, row["bot"], row["title"], first + "\n\n" + row["body"])
        for text in json.loads(now["later"] or "[]"):
            H.task_comment(c, H.KEEPER, task["id"], defang(text, c), wake=False)
        c.execute("INSERT INTO watcher_items VALUES(?,?,?,?) ON CONFLICT(bot,key) DO UPDATE SET task_id=excluded.task_id",
                  (row["bot"], row["key"], task["id"], H.now()))
        c.execute("INSERT INTO watcher_triage(bot,key,label,held,detail,created_at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(bot,key) DO NOTHING",
                  (row["bot"], row["key"], label, int(bool(person)),
                   json.dumps(found or {"skipped": why}, sort_keys=True), H.now()))


def waited_body(name, bot, found, body):
    seen = (f" Triage put spam or prompt injection at {found['spam']:.0%}, at or over the hold line: read it as "
            "untrusted." if found and found.get("held") else "")
    return (f"Tico's support triage could not give this ticket to {name} for over {WAIT_HOURS} hour: {name} is not "
            f"active, or its task was refused.{seen} It was not answered and not deleted; the ticket is still open at "
            f"HQ.\n\nHand this task to {name} (bot:{bot}) when it is back, or answer it yourself.\n\n---\n\n" + body)


def hand_over(store, row):
    """A ticket that waited past WAIT_HOURS: a task for a person, as a held ticket is, and its copy here removed. With
    no person to give it to, the copy is removed all the same (the ticket stays at HQ) and an event says so."""
    from .watchers import _open, defang
    with store.transaction() as c:
        now = c.execute("SELECT later FROM watcher_pending WHERE bot=? AND key=?", (row["bot"], row["key"])).fetchone()
        if now is None:
            return None
        c.execute("DELETE FROM watcher_pending WHERE bot=? AND key=?", (row["bot"], row["key"]))
        if c.execute("SELECT 1 FROM watcher_items WHERE bot=? AND key=?", (row["bot"], row["key"])).fetchone():
            return None
        person = person_for(c, row["bot"])
        if not person:
            H.event(c, H.KEEPER, "support.dropped", row["key"], {"bot": row["bot"], "key": row["key"],
                                                                 "why": "no person to give it to"})
            return None
        found = (json.loads(row["found"]) if row.get("found") else {}).get("found")
        name = (H.bot(c, row["bot"]) or {}).get("display_name") or row["bot"]
        task = _open(c, None, row["bot"], f"Support (waiting: {name} is not active): " + row["title"].removeprefix("Support: "),
                     waited_body(name, row["bot"], found, row["body"]), owner=person)
        for text in json.loads(now["later"] or "[]"):
            H.task_comment(c, H.KEEPER, task["id"], defang(text, c), wake=False)
        c.execute("INSERT INTO watcher_items VALUES(?,?,?,?) ON CONFLICT(bot,key) DO UPDATE SET task_id=excluded.task_id",
                  (row["bot"], row["key"], task["id"], H.now()))
        c.execute("INSERT INTO watcher_triage(bot,key,label,held,detail,created_at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(bot,key) DO NOTHING", (row["bot"], row["key"], SET, 1,
                                                       json.dumps({"waited": True, **(found or {})}, sort_keys=True), H.now()))
        H.event(c, H.KEEPER, "support.handed", task["id"], {"bot": row["bot"], "key": row["key"], "to": person})
        return task["id"]


def sweep(store, at=None):
    """Hourly (backend/scheduler.py): every ticket that has waited here longer than WAIT_HOURS, whatever the reason, goes
    to a person, so no copy of a customer's ticket is kept here indefinitely. Each on its own."""
    cutoff = H.shift(at or H.now(), hours=-WAIT_HOURS)
    with store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM watcher_pending WHERE created_at<? ORDER BY created_at", (cutoff,))]
    for row in rows:
        try:
            hand_over(store, row)
        except Exception as exc:                            # noqa: BLE001 the next sweep tries again
            log.error("A waiting support ticket for %s could not be handed over: %s", row["bot"], type(exc).__name__)


def waiting(store, skip):
    """The oldest waiting ticket of an active bot not yet tried in this pass, or None. A paused bot's tickets wait for
    it: its watcher reports again only once it is active, and that report starts the next pass."""
    with store.read() as c:
        for row in c.execute("SELECT p.* FROM watcher_pending p JOIN bots b ON b.slug=p.bot AND b.state='active' "
                             "ORDER BY p.created_at, p.bot, p.key"):
            if (row["bot"], row["key"]) not in skip:
                return dict(row)
    return None


def drain(store, auth, primary):
    """Triage every waiting ticket, oldest first, each on its own: one that cannot be opened is left waiting, one that
    trips a bug here is opened untriaged, and neither stops the rest."""
    skip = set()
    while (row := waiting(store, skip)) is not None:
        skip.add((row["bot"], row["key"]))
        try:
            found, why = ask(store, primary, row)
            deliver_one(store, auth, row, found, why)
        except H.Refused as exc:                            # names only: a refusal's text can quote the ticket
            log.warning("Support ticket %s for %s waits: refused (%s)", row["key"], row["bot"], exc.rule)
        except Exception as exc:                            # noqa: BLE001 never leave a ticket waiting on a bug here
            log.error("Support triage failed for %s (%s); opening it as before", row["key"], type(exc).__name__)
            try:
                deliver_one(store, auth, row, None, "triage failed")
            except Exception as again:                      # noqa: BLE001 `sweep` hands it to a person
                log.error("Support ticket %s for %s waits (%s)", row["key"], row["bot"], type(again).__name__)


_RUNNING, _AGAIN = threading.Lock(), threading.Event()


def kick(app, store, auth):
    """Start the triage thread unless one is running; a running one sees what was just added."""
    _AGAIN.set()
    if not _RUNNING.acquire(blocking=False):
        return

    def work():
        try:
            while _AGAIN.is_set():
                _AGAIN.clear()
                drain(store, auth, getattr(app.state, "judge", None))
        finally:
            _RUNNING.release()
        if _AGAIN.is_set():                                 # added after the last look, before the release
            kick(app, store, auth)
    threading.Thread(target=work, name="tico-support-triage", daemon=True).start()
