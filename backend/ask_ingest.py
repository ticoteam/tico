"""Ask ingest: what a bot may put on a person's Needs you (`questions/person-ask-ingest.json`, asked through the
server's Decide engine, `backend/judge.py` `server_engine`; spec: Tasks Owner, ask-ingest.md v1).

Gated: a bot's write that would put something on a person's Needs you, today a task filed with the person as its
owner (`app.task_create`). Not gated: anything a person writes (their Assistant included), approvals, the keeper's own
items and anything Tico files itself; they still count toward the person's load.

One call per item, outside the write's transaction (`preflight`), answers six typed questions; plain code then decides
inside the write (`apply`), from the answers, the person's settings (`settings`) and their load (`load`: the open items
on their Needs you, snoozed ones left out), in this order:

  - a status update: FYI, off their plate;
  - a hands-only chore: Hands needed, a group of its own with its own budget (default 5);
  - the bot's own work, or not clear: sent back;
  - otherwise the budget B (default 10): under B clear; B to 3B also meets one of the person's criteria; 3B to 10B
    also recommends and urgency 2 or more; 10B or more urgency 3. Urgency 3 always passes.

Sent back means the write succeeds but the item stays with the bot that filed it: the reply says why, with the fix
and the bot's manager from the org chart. It is never a refusal and counts toward no quarantine. Mode Off asks
nothing; Rehearsal (the default) asks and records what On would have done, and changes nothing.

Fail open: Off, no decision model, rehearsal install, the month's spend cap, the day's call cap, an error, no answer
within `DEADLINE` seconds, a malformed answer, or an answer the outcome depends on under its threshold all let the
item through as before. Each call is one `judge.call` row (label, model, tokens, latency, cost, the answers and the
outcome); never the item's text. Each gated item is one `ask_ingest_items` row (ids, outcome and reason; never text).
"""

import json
import logging
import threading

from clients import judge as J
from .judge import audit_detail, daily_cap, over_spend_cap, server_engine, used_today
from .store import H

LOG = logging.getLogger("tico.ask_ingest")
QUESTION_SET = "person-ask-ingest"
DEADLINE = 10
MAX_ITEMS = 100               # the person's open items offered as `covered` options, newest first
MAX_TEXT = 1500               # the item's text the model sees
MAX_CRITERIA, MAX_CRITERION = 10, 200
OFF, REHEARSAL, ON = "off", "rehearsal", "on"
MODES = (OFF, REHEARSAL, ON)
TEAM = "*"                    # the team default's row in ask_ingest_settings
DEFAULTS = {"mode": REHEARSAL, "budget": 10, "chores_budget": 5, "criteria": [
    "Strategy and positioning",
    "Words that go out under the company's name",
    "Architecture and product direction",
    "Spending over $200 a month, or a higher spending cap",
    "Anything that can't be undone or faces outside: legal, security, customer data, partners",
    "Hiring, new bots, retiring bots",
]}

# Outcomes. THROUGH reaches the person as today; HANDS reaches them in the Hands needed group; the rest stay off.
THROUGH, HANDS, FYI, SENT_BACK, MERGED, FAILED_OPEN = "through", "hands", "fyi", "sent_back", "merged", "failed_open"
OFF_PLATE = (FYI, SENT_BACK, MERGED)
DECISION, CHORE, STATUS, OWN_WORK, SETUP = "decision", "chore", "status", "own_work", "setup"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_ingest_settings(
 person TEXT PRIMARY KEY, mode TEXT NOT NULL, budget INTEGER NOT NULL, chores_budget INTEGER NOT NULL,
 criteria_json TEXT NOT NULL, updated TEXT NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ask_ingest_items(
 id TEXT PRIMARY KEY, person TEXT NOT NULL, asker TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
 message_id TEXT, mode TEXT NOT NULL, outcome TEXT NOT NULL, would TEXT NOT NULL, reason TEXT NOT NULL, load INTEGER NOT NULL,
 call_id TEXT, created TEXT NOT NULL, let_through_at TEXT, let_through_by TEXT);
CREATE INDEX IF NOT EXISTS ask_ingest_items_person ON ask_ingest_items(person, created);
CREATE INDEX IF NOT EXISTS ask_ingest_items_ref ON ask_ingest_items(ref);
"""


# ---- settings ---------------------------------------------------------------------------------------------------

def _row_settings(row):
    try:
        criteria = [str(x) for x in json.loads(row["criteria_json"] or "[]")][:MAX_CRITERIA]
    except (TypeError, ValueError):
        criteria = list(DEFAULTS["criteria"])
    return {"mode": row["mode"] if row["mode"] in MODES else DEFAULTS["mode"], "budget": int(row["budget"]),
            "chores_budget": int(row["chores_budget"]), "criteria": criteria}


def team_default(c):
    row = c.execute("SELECT * FROM ask_ingest_settings WHERE person=?", (TEAM,)).fetchone()
    return {**_row_settings(row), "own": True} if row else {**DEFAULTS, "criteria": list(DEFAULTS["criteria"]),
                                                             "own": False}


def settings(c, person):
    """What bots may put on this person: {mode, budget, chores_budget, criteria, own} (own: their own, not the team
    default). Read at each item, so a change applies to the next item and nothing already on the plate moves."""
    row = c.execute("SELECT * FROM ask_ingest_settings WHERE person=?", (person,)).fetchone()
    return {**_row_settings(row), "own": True} if row else {**team_default(c), "own": False}


def clean(body):
    """{mode, budget, chores_budget, criteria} checked, or ValueError with a sentence for the person."""
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in MODES:
        raise ValueError("mode is off, rehearsal or on")
    try:
        budget, chores = int(body.get("budget")), int(body.get("chores_budget"))
    except (TypeError, ValueError):
        raise ValueError("budget and chores_budget are whole numbers")
    if not 1 <= budget <= 200 or not 1 <= chores <= 100:
        raise ValueError("budget is 1 to 200 and chores_budget 1 to 100")
    criteria = [" ".join(str(x).split()) for x in body.get("criteria") or []]
    criteria = [x for x in criteria if x]
    if len(criteria) > MAX_CRITERIA or any(len(x) > MAX_CRITERION for x in criteria):
        raise ValueError(f"up to {MAX_CRITERIA} criteria lines of at most {MAX_CRITERION} characters")
    return {"mode": mode, "budget": budget, "chores_budget": chores, "criteria": criteria}


def save(c, actor, person, body):
    """Save a person's own settings (person = their actor) or the team default (person = TEAM)."""
    value = clean(body)
    c.execute("INSERT INTO ask_ingest_settings(person,mode,budget,chores_budget,criteria_json,updated,updated_by) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(person) DO UPDATE SET mode=excluded.mode, budget=excluded.budget, "
              "chores_budget=excluded.chores_budget, criteria_json=excluded.criteria_json, updated=excluded.updated, "
              "updated_by=excluded.updated_by",
              (person, value["mode"], value["budget"], value["chores_budget"], json.dumps(value["criteria"]), H.now(),
               actor))
    H.event(c, actor, "ask_ingest.settings", person, {k: v for k, v in value.items() if k != "criteria"}
            | {"criteria": len(value["criteria"])})
    return settings(c, person) if person != TEAM else team_default(c)


# ---- who is gated, and the person's load ------------------------------------------------------------------------

def gated(who, assistant_bot=""):
    """Whether this caller's write is gated: a bot writing as itself. A person, their Assistant acting for them
    (`via`), BotOps acting for a person and the keeper are not."""
    return (who.role == "bot" and not who.via and str(who.actor).startswith("bot:")
            and H.actor_id(who.actor) != assistant_bot)


def hands_ids(c, person):
    """Open tasks on this person's plate that the gate put under Hands needed."""
    return {r[0] for r in c.execute(
        "SELECT i.ref FROM ask_ingest_items i JOIN tasks t ON t.id=i.ref WHERE i.person=? AND i.outcome=? "
        f"AND t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))})", (person, HANDS, *H.ACTIVE_STATUSES))}


def load(c, person):
    """(decisions, chores): the open items on the person's Needs you, snoozed ones left out (hubdb.needs_you), with
    the approvals waiting on them and the bots' loose questions to them; chores are those under Hands needed."""
    plate = H.needs_you(c, person)
    ids = {t["id"] for t in plate["tasks"]} | {t["id"] for t in plate["waiting"]}
    approvals = sum(1 for a in plate["approvals"] if a.get("decision_owner") == person)
    loose = len(H.loose_asks_to(c, person))
    chores = len(ids & hands_ids(c, person))
    return len(ids) - chores + approvals + loose, chores


def open_items(c, person):
    """{option name: (task id, title)} for the person's open items a new one may be the same as, newest first."""
    plate = H.needs_you(c, person)
    rows = sorted(plate["tasks"] + plate["waiting"], key=lambda t: t.get("created") or "", reverse=True)[:MAX_ITEMS]
    return {f"item-{n}": (t["id"], t.get("title") or "") for n, t in enumerate(rows, 1)}


def manager_of(c, bot_actor):
    """The asking bot's manager from the org chart (`bot_config.reports_to`): (actor, name), or (None, '')."""
    row = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (H.actor_id(bot_actor),)).fetchone()
    target = (row["reports_to"] if row else "") or ""
    if not target:
        return None, ""
    actor = target if H.actor_kind(target) else H.bot_actor(target)
    if H.is_bot(actor):
        found = H.bot(c, H.actor_id(actor))
        return (actor, (found or {}).get("display_name") or H.actor_id(actor)) if found else (None, "")
    return actor, H.person_name(c, H.actor_id(actor)) or H.actor_id(actor)


# ---- the call ---------------------------------------------------------------------------------------------------

def _ask(engine, state, questions, label):
    """The engine's answer within DEADLINE seconds: (result, None), (None, error code) or (None, 'timeout')."""
    box = {}

    def work():
        try:
            box["result"] = engine(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = exc.code
        except Exception as exc:    # a fake, a provider bug: the write never breaks on it
            LOG.warning("Ask ingest call failed: %s", type(exc).__name__)
            box["error"] = "error"
    worker = threading.Thread(target=work, name="ask-ingest", daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        return None, "timeout"
    return box.get("result"), box.get("error")


def _bot_line(c, actor):
    return (H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor)


def preflight(store, primary, who, target, title, text, kind="task"):
    """Ask the set about one item for the person `target` names, outside any transaction. Returns the verdict
    `apply` reads: {person, asker, kind, mode, answers (typed values, None where under threshold), items, criteria,
    call, error}; None when the write is not gated (a person's, not to a person, mode Off). Never raises."""
    try:
        if not target or not gated(who, store.settings.assistant_bot):
            return None
        with store.read() as c:
            person = H.resolve_actor(c, target)
            if not person or not H.is_human(person) or not H.human(c, H.actor_id(person)):
                return None
            own = settings(c, person)
            if own["mode"] == OFF:
                return None
            verdict = {"person": person, "asker": who.actor, "kind": kind, "mode": own["mode"], "answers": None,
                       "items": {}, "criteria": {}, "call": None, "error": None}
            try:
                qset = J.load_set(QUESTION_SET)
            except J.JudgeError as exc:
                LOG.warning("Ask ingest is off: %s", exc.detail)
                return None
            engine = server_engine(store, c, primary)
            if engine is None:
                # No decision model at all is Decide off: nothing to record. The month's cap is a failure kept.
                return {**verdict, "error": "spend_cap"} if over_spend_cap(c, store.settings) else None
            if used_today(c, H.KEEPER) >= daily_cap(store.settings, "bot"):
                return {**verdict, "error": "budget"}
            items = open_items(c, person)
            asker = _bot_line(c, who.actor)
            person_name = H.person_name(c, H.actor_id(person)) or H.actor_id(person)
        criteria = {f"line-{n}": line for n, line in enumerate(own["criteria"], 1)}
        questions = {qid: qset["questions"][qid] for qid in ("clear", "kind", "urgency", "recommends")}
        if criteria:
            questions["meets"] = J.with_options(qset["questions"]["meets"], criteria)
        if items:
            questions["covered"] = J.with_options(qset["questions"]["covered"],
                                                  {name: t for name, (_, t) in items.items()})
        state = {"item": {"title": str(title or "")[:300], "text": str(text or "")[:MAX_TEXT]},
                 "asker": asker, "person": person_name}
        result, error = _ask(engine, state, questions, qset["label"])
        if result is not None and not isinstance(result, dict):
            result, error = None, "shape"
        answers = None
        if result is not None:
            try:
                answers = read(J.check_answers(result.get("answers"), questions), qset["thresholds"], criteria, items)
            except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError):
                error = "shape"
        detail = {"label": qset["label"], "questions": len(questions), "options": len(items),
                  "criteria": len(criteria), "kind": kind, "mode": own["mode"]}
        if result is not None:
            detail.update(audit_detail(result), answers=_summary(result.get("answers"), questions))
        if error:
            detail["error"] = error
        with store.transaction() as c:
            call = H.event(c, H.KEEPER, "judge.call", qset["label"], detail)
        return {**verdict, "answers": answers, "items": {n: i for n, (i, _) in items.items()},
                "criteria": criteria, "call": call["id"], "error": error}
    except Exception as exc:          # the gate never stops the write
        LOG.warning("Ask ingest preflight failed: %s", type(exc).__name__)
        return None


def _summary(answers, questions):
    try:
        return J.summary({qid: answers[qid] for qid in questions if qid in answers})
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}


def read(answers, thresholds, criteria, items):
    """The typed answers, each None when under its threshold: clear and recommends True/False, kind an option,
    meets a criterion line or 'none', covered an item option or 'new', urgency 0-3."""
    def yes_no(qid):
        p = J.noul(answers, qid)
        at = thresholds.get(qid, 0.7)
        return True if p >= at else False if p <= 1 - at else None

    def pick(qid, default):
        if qid not in answers:
            return default
        value, confidence = J.choice(answers, qid)
        return value if confidence >= thresholds.get(qid, 0.7) else None

    level, confidence = J.score(answers, "urgency")
    urgency = max(0, min(3, int(level + 0.5))) if confidence >= thresholds.get("urgency", 0.6) else None
    meets = pick("meets", "none")
    covered = pick("covered", "new")
    return {"clear": yes_no("clear"), "kind": pick("kind", None), "recommends": yes_no("recommends"),
            "urgency": urgency, "meets": meets if meets in criteria or meets in ("none", None) else None,
            "covered": covered if covered in items or covered in ("new", None) else None}


# ---- the decision -----------------------------------------------------------------------------------------------

def _all(*values):
    """Three-valued and: False if any is False, None (unsure) if any is None, else True."""
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def decide(answers, budget, chores_budget, decisions, chores):
    """(outcome, reason) from the typed answers, the person's budgets and load. Plain code; the spec's order.
    An outcome that would keep the item off the plate but rests on an answer under its threshold fails open."""
    if answers is None:
        return FAILED_OPEN, "no_answer"
    urgent = answers["urgency"] == 3
    kind = answers["kind"]
    if kind == STATUS:
        return FYI, "status"
    if kind == CHORE:
        if chores < chores_budget or urgent:
            return HANDS, "chore"
        return SENT_BACK, "chores_full"
    if kind == OWN_WORK:
        return (THROUGH, "urgent") if urgent else (SENT_BACK, "own_work")
    clear = answers["clear"]
    if clear is False:
        return (THROUGH, "urgent") if urgent else (SENT_BACK, "unclear")
    if urgent:
        return THROUGH, "urgent"
    meets = None if answers["meets"] is None else answers["meets"] != "none"
    urgency = answers["urgency"]
    if decisions < budget:
        ok, band = clear, "under_budget"
    elif decisions < 3 * budget:
        ok, band = _all(clear, meets), "criteria"
    elif decisions < 10 * budget:
        ok, band = _all(meets, answers["recommends"], None if urgency is None else urgency >= 2), "recommends"
    else:
        ok, band = (None if urgency is None else False), "urgent_only"
    if ok is None:
        return FAILED_OPEN, "below_threshold"
    return (THROUGH, band) if ok else (SENT_BACK, band)


REASONS = {
    "unclear": "it doesn't say what you want decided",
    "own_work": "it is work you can do yourself",
    "chores_full": "{name} already has {chores} hands-only chores waiting",
    "criteria": "{name} has {load} open items and this doesn't meet their criteria",
    "recommends": "{name} has {load} open items: at this load an ask needs one of their criteria, your recommendation "
                  "and a reason it can't wait past today",
    "urgent_only": "{name} has {load} open items: at this load only something that can't wait (an outage, a security "
                   "exposure, money being lost) reaches them",
}


def message(reason, name, load_now, chores, manager_name):
    """The fixed sentence a sent-back item's asker reads, with the fix. Never model prose."""
    why = REASONS.get(reason, "it didn't pass {name}'s filter").format(name=name, load=load_now, chores=chores)
    ask = f"ask {manager_name}, " if manager_name else ""
    if reason == "unclear":
        fix = "Put the one thing you want decided, and why it is theirs, in the title and first lines, then file it again."
    elif reason == "own_work":
        fix = f"Do it yourself, {ask}or hand it to a bot that can."
    else:
        fix = (f"Decide it yourself, {ask}or ask again with your recommendation, what you do if there is no answer by "
               "a date, and the line of their criteria it meets.")
    return f"Not sent to {name}: {why}. {fix} It stays on your task; nothing was refused."


def apply(c, verdict):
    """Decide one gated item inside its write, before it lands. Returns {mode, outcome, would, reason, text, load,
    acts}: `acts` is True when the write must keep the item off the person's plate (mode On and sent back or FYI).
    The write then calls `record` with the item's id."""
    person, asker = verdict["person"], verdict["asker"]
    own = settings(c, person)          # the settings at this item, not at the call
    decisions, chores = load(c, person)
    if verdict.get("error"):
        would, reason = FAILED_OPEN, verdict["error"]
    else:
        would, reason = decide(verdict["answers"], own["budget"], own["chores_budget"], decisions, chores)
    mode = own["mode"]
    outcome = would if mode == ON or would == FAILED_OPEN else THROUGH
    name = H.person_name(c, H.actor_id(person)) or H.actor_id(person)
    text = ""
    if would == SENT_BACK:
        _, manager_name = manager_of(c, asker)
        text = message(reason, name, decisions, chores, manager_name)
    elif would == FYI:
        text = f"Not put on {name}'s plate: it reads as a status update, listed for them as FYI. Nothing was refused."
    if mode != ON and text:
        text = "Rehearsal, nothing changed. With the filter on: " + text
    return {"mode": mode, "outcome": outcome, "would": would, "reason": reason, "text": text, "load": decisions,
            "acts": mode == ON and outcome in (SENT_BACK, FYI)}


def record(c, verdict, decided, ref, message_id=None):
    """One `ask_ingest_items` row for the gated item `ref` (a task id; `message_id` the question asked on it), and
    the call's `decision.outcome`."""
    c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,message_id,mode,outcome,would,reason,load,call_id,"
              "created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (H.new_id(), verdict["person"], verdict["asker"], verdict["kind"], ref, message_id, decided["mode"],
               decided["outcome"], decided["would"], decided["reason"], decided["load"], verdict.get("call"), H.now()))
    if verdict.get("call"):
        H.event(c, H.KEEPER, "decision.outcome", verdict["call"],
                {"label": QUESTION_SET, "verdict": decided["would"],
                 "action": decided["outcome"] if decided["mode"] == ON else "rehearsal"})


def withdraw_loose(c, asker, message_id, person):
    """A sent-back question outside any task leaves the person's Needs you at once (`loose_asks_to` reads open
    asks only); it stays in the bot's thread, superseded, as a withdrawn task question does."""
    c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (H.now(), message_id))
    H.event(c, asker, "ask.withdrawn", message_id, {"to": person, "by": "ask_ingest"})
    H.recount(c, [asker])


def reply(decided):
    """What the write's reply says of the gate: only when it has something to tell the asker (sent back, FYI, or
    what On would have done); an item that went through, or failed open, reads as before."""
    if not decided or not decided["text"]:
        return {}
    return {"ingest": {k: decided[k] for k in ("mode", "outcome", "would", "reason", "text")}}


# ---- what the person reads and sets -------------------------------------------------------------------------------

def filtered(c, person, since):
    """What the gate kept off this person's plate since `since`, or would have in Rehearsal, newest first: each with
    the asking bot, the outcome and reason, and the item's title when the person may read it."""
    out = []
    for row in H._rows(c.execute(
            "SELECT * FROM ask_ingest_items WHERE person=? AND created>=? AND would IN (?,?,?) "
            "ORDER BY created DESC LIMIT 200", (person, since, *OFF_PLATE))):
        if row["kind"] == "loose":
            said = H.message(c, row["ref"])
            if not said or said.get("to_actor") != person or said.get("deleted_at"):
                continue
            task, title = None, (said.get("body") or "").strip().split("\n")[0][:300]
        else:
            task = H.task(c, row["ref"])
            if not task or not H.task_private_readable(c, person, task):
                continue
            title = task.get("title") or ""
        out.append({"id": row["id"], "kind": row["kind"], "task": task["id"] if task else None,
                    "message": row["message_id"], "title": title, "asker": row["asker"],
                    "asker_name": _bot_line(c, row["asker"]), "outcome": row["outcome"], "would": row["would"],
                    "reason": row["reason"], "mode": row["mode"], "created": row["created"],
                    "let_through_at": row["let_through_at"]})
    return out


def let_through(c, person, item_id):
    """The person puts a filtered item on their plate as first meant, in one step, and the asker hears it once: a
    task handed to them, a withdrawn question asked again, a wait set. Returns the item; Problem-free errors are
    ValueError (no such item of theirs) or LookupError (nothing to let through)."""
    row = c.execute("SELECT * FROM ask_ingest_items WHERE id=? AND person=?", (item_id, person)).fetchone()
    if not row:
        raise ValueError("no filtered item of yours with that id")
    row = dict(row)
    if row["let_through_at"]:
        return row
    if row["mode"] != ON or row["outcome"] not in (SENT_BACK, FYI):
        raise LookupError("it already reached you")
    asker, name = row["asker"], H.person_name(c, H.actor_id(person)) or H.actor_id(person)
    task = H.task(c, row["ref"]) if row["kind"] != "loose" else None
    if row["kind"] != "loose" and (not task or task["status"] not in H.ACTIVE_STATUSES):
        raise LookupError("that task has ended")
    if row["kind"] == "task":
        H.task_update(c, asker, task["id"], owner=person)
    elif row["kind"] == "wait":
        H.task_update(c, asker, task["id"], status="waiting", waiting_on=person)
    else:
        c.execute("UPDATE messages SET superseded_at=NULL WHERE id=?", (row["message_id"],))
        H.recount(c, [asker, person])
    told = f"{name} let this through their filter: it is on their Needs you as you first asked."
    if task:
        H.task_comment(c, H.KEEPER, task["id"], told, wake=False)
    else:
        H.say(c, H.KEEPER, asker, told, kind="notice", refs={"ask_ingest": row["id"]})
    at = H.now()
    c.execute("UPDATE ask_ingest_items SET let_through_at=?, let_through_by=? WHERE id=?", (at, person, row["id"]))
    H.event(c, person, "ask_ingest.let_through", row["ref"], {"item": row["id"], "kind": row["kind"],
                                                               "outcome": row["outcome"], "asker": asker})
    return {**row, "let_through_at": at, "let_through_by": person}


def outcome_counts(c, start, end):
    """For the decisions readout (backend/decide_readout.py): the gated items in the window by what the gate decided
    ({outcome: n}, Rehearsal's would-have counted under its own key), and how many people let through."""
    outcomes, rehearsal, let = {}, {}, 0
    for row in c.execute("SELECT mode, outcome, would, let_through_at FROM ask_ingest_items WHERE created>=? AND "
                         "created<?", (start, end)):
        if row["mode"] == ON or row["would"] == FAILED_OPEN:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        else:
            rehearsal[row["would"]] = rehearsal.get(row["would"], 0) + 1
        let += bool(row["let_through_at"])
    return {"outcomes": dict(sorted(outcomes.items())), "rehearsal": dict(sorted(rehearsal.items())),
            "let_through": let}


def install(app, store, auth):
    from fastapi import Request
    from pydantic import Field
    from .models import Contract
    from .store import Problem

    def person_of(who):
        if who.role not in ("owner", "human") or not H.is_human(who.actor):
            raise Problem("identity", "What bots may put on you is a person's own setting", 403)
        return who.actor

    @app.get("/api/v2/ask-ingest/settings")
    def ingest_settings(request: Request):
        """The caller's "What bots may put on you" and the team default."""
        who = request.state.identity
        auth.domain(who)
        person = person_of(who)
        with store.read() as c:
            return {"settings": settings(c, person), "team": team_default(c), "modes": list(MODES),
                    "may_edit_team": auth.company_role(who) in ("owner", "admin")}

    class Settings(Contract):
        mode: str = Field(max_length=20)
        budget: int
        chores_budget: int
        criteria: list[str] = Field(default_factory=list, max_length=MAX_CRITERIA)
        team: bool = False

    @app.put("/api/v2/ask-ingest/settings")
    def ingest_settings_save(request: Request, body: Settings):
        """Save the caller's own settings, or with `"team": true` the team default (an owner or admin). The next item
        uses them; nothing already on a plate moves."""
        who = request.state.identity
        auth.domain(who)
        person = person_of(who)
        target = TEAM if body.team else person
        if target == TEAM and auth.company_role(who) not in ("owner", "admin"):
            raise Problem("identity", "Only an owner or admin sets the team default", 403)
        with store.transaction() as c:
            try:
                return {"settings": save(c, who.actor, target, body.model_dump()), "team": target == TEAM}
            except ValueError as exc:
                raise Problem("validation", str(exc), 422)

    @app.post("/api/v2/ask-ingest/items/{item_id}/let-through")
    def ingest_let_through(request: Request, item_id: str):
        """Let it through: no confirm; the asker is told once. Doing it again changes nothing."""
        who = request.state.identity
        auth.domain(who)
        person = person_of(who)
        with store.transaction() as c:
            try:
                return {"item": let_through(c, person, item_id)}
            except ValueError as exc:
                raise Problem("not_found", str(exc), 404)
            except LookupError as exc:
                raise Problem("state", str(exc), 409)

    @app.get("/api/v2/ask-ingest/filtered")
    def ingest_filtered(request: Request, hours: int = 24):
        """Filtered for you: what the gate kept off the caller's plate (or would have, in Rehearsal)."""
        who = request.state.identity
        auth.domain(who)
        person = person_of(who)
        if not 1 <= hours <= 24 * 31:
            raise Problem("validation", "hours is 1 to 744", 422)
        with store.read() as c:
            items = filtered(c, person, H.shift(H.now(), hours=-hours))
        return {"items": items, "count": len(items)}
