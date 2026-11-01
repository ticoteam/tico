"""What the app offers to send to Tico support on a person's behalf: a setup snapshot and one feedback question.

"Want help setting up?" (the setup wizard, Health, a bot in Needs setup) opens Support with `setup_snapshot()` as
the start of the message, marked `setup`. The snapshot is plain text the person reads and edits before pressing Send:
how many Getting started steps are done, whether a computer is online, whether a model is signed in, whether a first
bot is ready and how many bots wait in Needs setup. It carries no names (each step has a fixed label), only what
Getting started already shows them.

"How is Tico working for you?" is asked after the install's first bot result, and again 7 and 30 days after it
(`feedback_due()`), once per person per moment, and skippable. Anyone but the owner who joined after that first
result is asked 7 and 30 days after their own first action. An answer goes as a `feedback` request through the same route as any
other (backend/support.py), with only the words they wrote; a skip sends nothing. The question is never asked while support is
off, and never of a bot, a token or the Assistant.
"""
import json

from . import census, getting_started
from .statuses import PARKED_SQL
from .store import H, encode

PREFERENCE = "support.feedback"
QUESTION = "How is Tico working for you?"
# (moment, days after the first bot result)
MOMENTS = (("first_result", 0), ("day_7", 7), ("day_30", 30))
SPACING_DAYS = 3          # a later moment waits this long after the person last answered or skipped one
# Fixed labels: Getting started's own labels can name a bot ("Set up Payroll").
STEPS = {"signed_in": "signing in", "computer": "a computer online", "model": "a model signed in",
         "github": "GitHub", "botops": "BotOps active", "first_bot": "a first bot", "next_bot": "a bot waiting in Needs setup",
         "first_output": "a first approved output", "first_update": "a first update",
         "strategy": "a published strategy"}
ASKED_FROM = {"wizard": "the setup wizard", "health": "Health", "bot": "a bot in Needs setup"}
# (database file, actor) -> the person's first event time, kept once found. The read walks every event of the person
# (no index has actor then ts): 287 ms a call on TIDY.
JOINED = {}


def setup_snapshot(c, who, settings, auth, github, asked_from="health"):
    """The setup snapshot as the message a person starts from. Built from what this person's Getting started list
    shows them, so it says no more than they can already see."""
    view = getting_started.view(c, who, settings, auth, github)
    items = {item["id"]: item for item in view["items"]}
    left = [STEPS.get(item["id"], "another step") for item in view["items"] if not item["done"] and not item["skipped"]]
    yes = lambda key: "yes" if items[key]["done"] else "no"
    lines = ["I'd like help setting up Tico.", "", "Setup snapshot (asked from %s):" % ASKED_FROM.get(asked_from, "Tico"),
             "- Steps done: %d of %d" % (view["done"], view["total"]) + ("; left: " + ", ".join(left) if left else "")]
    if "computer" in items:
        lines.append("- Computer online: " + yes("computer"))
    if "model" in items:
        lines.append("- Model signed in: " + yes("model"))
    lines.append("- First bot ready: " + yes("first_output"))
    if "next_bot" not in items:              # what this person's Getting started shows: no bot rows for everyone
        return {"message": "\n".join(lines), "kind": "setup"}
    parked = c.execute("SELECT count(*) FROM bot_config bc JOIN bots b ON b.slug=bc.bot WHERE bc.onboarding_state IN "
                       + PARKED_SQL + " AND b.state!='archived'").fetchone()[0]
    lines.append("- Bots in Needs setup: %d" % parked)
    return {"message": "\n".join(lines), "kind": "setup"}


def support_on(settings):
    from .support import off_reason
    return not off_reason(settings)


def _asked(who):
    return who is not None and who.role in ("owner", "human") and not who.via and not who.via_token


def _load(c, who):
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (who.actor, PREFERENCE)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _joined(c, actor):
    path = next((r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main"), "")
    if (path, actor) not in JOINED:
        found = c.execute("SELECT min(ts) FROM events WHERE actor=?", (actor,)).fetchone()[0]
        if not found:
            return None
        if len(JOINED) >= 10000:
            JOINED.clear()
        JOINED[path, actor] = found
    return JOINED[path, actor]


def feedback_due(c, settings, who):
    """The moment to ask this person about now ({moment, question}), or None. Only the latest moment reached is
    asked: a person away for a month is asked once, not three times."""
    from .support import off_reason
    if not _asked(who) or off_reason(settings):
        return None
    first = census.first_result(c, settings)
    if not first:
        return None
    # The owner was there from the start. Someone who joined later is asked from their own first action, and not
    # about a first result they never saw.
    joined = first if who.role == "owner" else _joined(c, who.actor)
    if not joined:
        return None
    moments = MOMENTS if joined <= first else MOMENTS[1:]
    since, now = max(first, joined), H.now()
    reached = [moment for moment, days in moments if H.shift(since, days=days) <= now]
    if not reached:                          # a clock behind the computer that finished the turn
        return None
    record = _load(c, who)
    latest = reached[-1]
    if latest in record:
        return None
    last = max((str(v.get("at") or "") for v in record.values() if isinstance(v, dict)), default="")
    if last and H.shift(last, days=SPACING_DAYS) > now:
        return None
    return {"moment": latest, "question": QUESTION}


def answer_feedback(store, support, who, body):
    """Send the answer as a feedback request (or record the skip), then never ask about this moment again. HQ out of
    reach raises before anything is recorded, so the question stays for another try."""
    from .support import SupportTicket, clean_message
    ticket = None
    if body.answer.strip():
        when = "after the first bot result" if body.moment == "first_result" else body.moment.replace("day_", "day ")
        message = clean_message("%s (%s)\n\n%s" % (QUESTION, when, body.answer.strip()))
        ticket = support.file(who, SupportTicket(message=message, kind="feedback", include_ids=False, diagnostics=""),
                              "feedback:" + body.moment)
    with store.transaction() as c:
        record = _load(c, who)
        record[body.moment] = {"at": H.now(), "answered": ticket is not None}
        c.execute("INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(actor,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                  (who.actor, PREFERENCE, encode(record), H.now()))
        H.event(c, who.actor, "support.feedback", body.moment, {"answered": ticket is not None})
    return {"moment": body.moment, "answered": ticket is not None, "ticket": ticket}
