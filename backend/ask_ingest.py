"""Ask ingest: what a bot may put on a person's Needs you (`questions/person-ask-ingest.json`, asked through the
server's Decide engine, `backend/judge.py` `server_engine`; spec: Tasks Owner, ask-ingest.md v2).

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
    also meets one of the criteria and recommends; 10B or more urgency 3. Urgency 3 always passes.

Sent back means the write succeeds but the item stays with the bot that filed it: the reply says why, with the fix
and the bot's manager from the org chart. It is never a refusal and counts toward no quarantine. Mode Off asks
nothing; Rehearsal (the default) asks and records what On would have done, and changes nothing.

Fail open: Off, no decision model, rehearsal install, the month's spend cap, the day's call cap, an error, no answer
within `DEADLINE` seconds, a malformed answer, or an answer the outcome depends on under its threshold all let the
item through as before. Each call is one `judge.call` row (label, model, tokens, latency, cost, the answers and the
outcome); never the item's text. Each gated item is one `ask_ingest_items` row (ids, outcome and reason; never text).

Bounded: at most `CONCURRENCY` calls run at once in this process (`call_slots.Calls`, a pool of its own apart
from the rule checks'). With every slot in use, or the breaker open after repeated failures, the item fails open
at once (`busy`, `breaker`) and no call is made, so no `judge.call` row is written. A call still running at
`DEADLINE` is cut off there (`J.within`) and keeps its slot until it really ends.
"""

import json
import logging
import threading
import weakref

from clients import judge as J
from .call_slots import NEUTRAL, REFUSED, Calls
from .judge import audit_detail, daily_cap, over_spend_cap, server_engine, used_today
from .store import H, Problem

LOG = logging.getLogger("tico.ask_ingest")
QUESTION_SET = "person-ask-ingest"
DEADLINE = 10
CONCURRENCY = 8               # calls at once, per process; the rule checks have their own pool
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
# A judged call with no item row carries a named reason, one `ask_ingest.unrecorded` event on the call: `joined` (a
# blocker's repeat joined an open ask), `held` (the rule check held the write), `target` (sent to someone else than the
# verdict's person), `not_applied` (the write did not put the item on the person), `folded` (a question folded into an
# open one), `refused` (the write raised: a refusal), `error` (it raised otherwise). A replay's call is `replay`.
UNRECORDED = "ask_ingest.unrecorded"
REPLAY = "replay"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_ingest_settings(
 person TEXT PRIMARY KEY, mode TEXT NOT NULL, budget INTEGER NOT NULL, chores_budget INTEGER NOT NULL,
 criteria TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ask_ingest_items(
 id TEXT PRIMARY KEY, person TEXT NOT NULL, asker TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
 message_id TEXT, mode TEXT NOT NULL, outcome TEXT NOT NULL, would TEXT NOT NULL, reason TEXT NOT NULL, load INTEGER NOT NULL,
 call_id TEXT, created_at TEXT NOT NULL, let_through_at TEXT, let_through_by TEXT, merged_into TEXT, passed_at TEXT);
CREATE INDEX IF NOT EXISTS ask_ingest_items_person ON ask_ingest_items(person, created_at);
CREATE INDEX IF NOT EXISTS ask_ingest_items_ref ON ask_ingest_items(ref);
CREATE INDEX IF NOT EXISTS ask_ingest_items_merged ON ask_ingest_items(merged_into) WHERE merged_into IS NOT NULL;
"""


# ---- settings ---------------------------------------------------------------------------------------------------

def _row_settings(row):
    try:
        criteria = [str(x) for x in json.loads(row["criteria"] or "[]")][:MAX_CRITERIA]
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
    c.execute("INSERT INTO ask_ingest_settings(person,mode,budget,chores_budget,criteria,updated_at,updated_by) "
              "VALUES(?,?,?,?,?,?,?) ON CONFLICT(person) DO UPDATE SET mode=excluded.mode, budget=excluded.budget, "
              "chores_budget=excluded.chores_budget, criteria=excluded.criteria, updated_at=excluded.updated_at, "
              "updated_by=excluded.updated_by",
              (person, value["mode"], value["budget"], value["chores_budget"], json.dumps(value["criteria"]), H.now(),
               actor))
    H.event(c, actor, "ask_ingest.settings", person, {k: v for k, v in value.items() if k != "criteria"}
            | {"criteria": len(value["criteria"])})
    return settings(c, person) if person != TEAM else team_default(c)


# ---- who is gated, and the person's load ------------------------------------------------------------------------

def gated(who, assistant_bot=""):
    """Gate a bot writing as itself or through BotOps. A person, their Assistant acting for them
    (`via`), BotOps acting for a person and the keeper are not."""
    return (who.role == "bot" and who.via in (None, "", "botops") and str(who.actor).startswith("bot:")
            and H.actor_id(who.actor) != assistant_bot)


def hands_ids(c, person):
    """The items the gate put under Hands needed on this person's plate: open tasks (a wait's own card, when the
    wait asked in one) and questions outside any task, by message id (the id Needs you lists them under)."""
    return {r[0] for r in c.execute(
        "SELECT i.ref FROM ask_ingest_items i LEFT JOIN tasks t ON t.id=i.ref WHERE i.person=? AND i.outcome=? "
        f"AND (i.kind='loose' OR t.status IN ({','.join('?' * len(H.ACTIVE_STATUSES))}))",
        (person, HANDS, *H.ACTIVE_STATUSES))}


def load(c, person):
    """(decisions, chores): the open items on the person's Needs you, snoozed ones left out (hubdb.needs_you), with
    the approvals waiting on them and the bots' loose questions to them; chores are those under Hands needed.
    Read before the item is written, so it never counts itself."""
    plate = H.needs_you(c, person)
    ids = {t["id"] for t in plate["tasks"]} | {t["id"] for t in plate["waiting"]}
    ids |= {m["id"] for m in H.loose_asks_to(c, person)}
    approvals = sum(1 for a in plate["approvals"] if a.get("decision_owner") == person)
    chores = len(ids & hands_ids(c, person))
    return len(ids) - chores + approvals, chores


def open_items(c, person):
    """{option name: (task id, title)} for the person's open items a new one may be the same as, newest first."""
    plate = H.needs_you(c, person)
    rows = sorted(plate["tasks"] + plate["waiting"], key=lambda t: t.get("created_at") or "", reverse=True)[:MAX_ITEMS]
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

_CALLS, _CALLS_LOCK = weakref.WeakKeyDictionary(), threading.Lock()


def calls_for(store):
    """The store's ask ingest pool, `CONCURRENCY` slots (`call_slots.Calls`); the rule checks' pool is apart."""
    with _CALLS_LOCK:
        calls = _CALLS.get(store)
        if calls is None:
            calls = _CALLS[store] = Calls(CONCURRENCY, "Ask ingest")
        return calls


def _ask(calls, engine, state, questions, label):
    """The engine's answer within DEADLINE seconds: (result, None), or (None, error code): `timeout`, and `busy` or
    `breaker` at once, asking nothing. The deadline is the call's own too (`J.within`), so an abandoned call stops at
    it, and its slot ends with it."""
    refused, probe = calls.admit()
    if refused:
        return None, refused
    box = {}

    def work():
        try:
            with J.within(DEADLINE):
                box["result"] = engine(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = exc.code
        except Exception as exc:    # a fake, a provider bug: the write never breaks on it
            LOG.warning("Ask ingest call failed: %s", type(exc).__name__)
            box["error"] = "error"
        finally:
            calls.slots.release()
    worker = threading.Thread(target=work, name="ask-ingest", daemon=True)
    try:
        worker.start()
    except RuntimeError:                    # no thread to be had: the slot goes back and the item fails open
        calls.slots.release()
        calls.done(None, probe)
        return None, "busy"
    worker.join(DEADLINE)
    if worker.is_alive():
        calls.done(False, probe)
        return None, "timeout"
    error = box.get("error")
    answered = error is None and box.get("result") is not None
    calls.done(None if error in NEUTRAL else answered, probe)
    return box.get("result"), error


def _bot_line(c, actor):
    return (H.bot(c, H.actor_id(actor)) or {}).get("display_name") or H.actor_id(actor)


def _criteria(own):
    return {f"line-{n}": line for n, line in enumerate(own["criteria"], 1)}


def _judge(store, engine, qset, own, items, asker, person_name, title, text, kind, replay=False):
    """One call on the set for one item, and its judge.call row (never the item's text): (answers, call id, error)."""
    criteria = _criteria(own)
    questions = {qid: qset["questions"][qid] for qid in ("clear", "kind", "urgency", "recommends")}
    if criteria:
        questions["meets"] = J.with_options(qset["questions"]["meets"], criteria)
    if items:
        questions["covered"] = J.with_options(qset["questions"]["covered"], {name: t for name, (_, t) in items.items()})
    state = {"item": {"title": str(title or "")[:300], "text": str(text or "")[:MAX_TEXT]},
             "asker": asker, "person": person_name}
    result, error = _ask(calls_for(store), engine, state, questions, qset["label"])
    if error in REFUSED:
        return None, None, error            # no call was made: no judge.call row, and the day's count is kept
    if result is not None and not isinstance(result, dict):
        result, error = None, "shape"
    answers = None
    if result is not None:
        try:
            answers = read(J.check_answers(result.get("answers"), questions), qset["thresholds"], criteria, items)
        except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError):
            error = "shape"
    detail = {"label": qset["label"], "questions": len(questions), "options": len(items),
              "criteria": len(criteria), "kind": kind, "mode": "replay" if replay else own["mode"]}
    if result is not None:
        detail.update(audit_detail(result), answers=_summary(result.get("answers"), questions))
    if error:
        detail["error"] = error
    with store.transaction() as c:
        call = H.event(c, H.KEEPER, "judge.call", qset["label"], detail)
    return answers, call["id"], error


def preflight(store, primary, who, target, title, text, kind="task"):
    """Ask the set about one item for the person `target` names, outside any transaction. Returns the verdict
    `apply` reads: {person, asker, kind, mode, answers (typed values, None where under threshold), items, criteria,
    call, error}; None when the write is not gated (a person's, not to a person, mode Off). Never raises."""
    verdict = None
    try:
        if not target or not gated(who, store.settings.assistant_bot):
            return None
        with store.read() as c:
            person = H.resolve_actor(c, target)
            if not person or not H.is_human(person) or not H.human(c, H.actor_id(person)):
                return None
            # The verdict exists before anything that can raise (the settings read included), so a failure after the
            # person is known still lands a failed_open row (PR 1798's contract).
            verdict = {"person": person, "asker": who.actor, "kind": kind, "mode": None, "answers": None,
                       "items": {}, "criteria": {}, "call": None, "error": None}
            own = settings(c, person)
            if own["mode"] == OFF:
                return None
            verdict["mode"] = own["mode"]
            try:
                qset = J.load_set(QUESTION_SET)
            except J.JudgeError as exc:
                LOG.warning("Ask ingest question set failed: %s", exc.code)
                return {**verdict, "error": exc.code or "error"}
            engine = server_engine(store, c, primary)
            if engine is None:
                # No decision model at all is Decide off: nothing to record. The month's cap is a failure kept.
                return {**verdict, "error": "spend_cap"} if over_spend_cap(c, store.settings) else None
            if used_today(c, H.KEEPER) >= daily_cap(store.settings, "bot"):
                return {**verdict, "error": "budget"}
            items = open_items(c, person)
            asker = _bot_line(c, who.actor)
            person_name = H.person_name(c, H.actor_id(person)) or H.actor_id(person)
        answers, call, error = _judge(store, engine, qset, own, items, asker, person_name, title, text, kind)
        verdict.update(answers=answers, call=call, error=error)
        return {**verdict, "items": {n: i for n, (i, _) in items.items()}, "criteria": _criteria(own)}
    except Exception as exc:          # the gate never stops the write
        LOG.warning("Ask ingest preflight failed: %s", type(exc).__name__)
        if verdict is not None:
            return {**verdict, "error": (exc.code if isinstance(exc, J.JudgeError) else None) or "error"}
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
    if answers["covered"] not in (None, "new"):
        return MERGED, "covered"
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
        ok, band = _all(meets, answers["recommends"]), "recommends"
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
    "recommends": "{name} has {load} open items: at this load an ask needs one of their criteria and your recommendation",
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
        fix = f"Decide it yourself, {ask}or ask again with the line of their criteria it meets and your recommendation."
    return f"Not sent to {name}: {why}. {fix} It stays on your task; nothing was refused."


def apply(c, verdict):
    """Decide one gated item inside its write, before it lands: called before the item is written, so the load is
    the plate as it was. Returns {mode, outcome, would, reason, text, load, acts}: `acts` is True when the write must
    keep the item off the person's plate (mode On and sent back or FYI). The write then calls `record` with the
    item's id. Reads only."""
    person, asker = verdict["person"], verdict["asker"]
    try:
        own = settings(c, person)          # the settings at this item, not at the call
        decisions, chores = load(c, person)
        error = verdict.get("error")
    except Exception as exc:
        # A read that fails inside the write does not stop the write: the item is a failed open, and it acts on no
        # plate (the mode is the verdict's, or Rehearsal when even that is unknown).
        LOG.warning("Ask ingest read failed inside the write: %s", type(exc).__name__)
        own = {"mode": verdict.get("mode") or REHEARSAL, "budget": DEFAULTS["budget"],
               "chores_budget": DEFAULTS["chores_budget"]}
        decisions, chores = 0, 0
        error = verdict.get("error") or "error"
    if error:
        would, reason = FAILED_OPEN, error
    else:
        would, reason = decide(verdict["answers"], own["budget"], own["chores_budget"], decisions, chores)
    mode = own["mode"]
    name = H.person_name(c, H.actor_id(person)) or H.actor_id(person)
    outcome = would if mode == ON or would == FAILED_OPEN else THROUGH
    text, merged_into = "", None
    if would == MERGED:
        merged_into = (verdict.get("items") or {}).get(verdict["answers"]["covered"])
        open_item = H.task(c, merged_into) if merged_into else None
        if not open_item or open_item["status"] not in H.ACTIVE_STATUSES:
            would, reason, merged_into = FAILED_OPEN, "covered_gone", None     # it ended since the call
            outcome = would
        else:
            text = (f"Not sent to {name}: they already have this open as \u201c{open_item['title']}\u201d. Their answer "
                    "there comes back to you here. Nothing was refused.")
    if would == SENT_BACK:
        _, manager_name = manager_of(c, asker)
        text = message(reason, name, decisions, chores, manager_name)
    elif would == FYI:
        text = f"Not put on {name}'s plate: it reads as a status update, listed for them as FYI. Nothing was refused."
    if mode != ON and text:
        text = "Rehearsal, nothing changed. With the filter on: " + text
    return {"mode": mode, "outcome": outcome, "would": would, "reason": reason, "text": text, "load": decisions,
            "merged_into": merged_into, "acts": mode == ON and outcome in OFF_PLATE}


def matches(verdict, person):
    """Whether a preflight verdict is about the person the write put the item on."""
    return bool(verdict) and verdict["person"] == person


def record(c, verdict, decided, ref, message_id=None):
    """One `ask_ingest_items` row for the gated item `ref` (a task id; `message_id` the question asked on it), and
    the call's `decision.outcome`."""
    merged_into = decided.get("merged_into") if decided["outcome"] == MERGED else None
    c.execute("INSERT INTO ask_ingest_items(id,person,asker,kind,ref,message_id,mode,outcome,would,reason,load,call_id,"
              "created_at,merged_into) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (H.new_id(), verdict["person"], verdict["asker"], verdict["kind"], ref, message_id, decided["mode"],
               decided["outcome"], decided["would"], decided["reason"], decided["load"], verdict.get("call"), H.now(),
               merged_into))
    if merged_into:
        # The open item names who else asked; quiet, so nobody wakes for it.
        H.task_comment(c, H.KEEPER, merged_into, f"{_bot_line(c, verdict['asker'])} asked the same thing; your answer "
                       "here goes to them too.", wake=False)
    if verdict.get("call"):
        H.event(c, H.KEEPER, "decision.outcome", verdict["call"],
                {"label": QUESTION_SET, "verdict": decided["would"],
                 "action": decided["outcome"] if decided["mode"] == ON else "rehearsal"})


def skipped(c, verdict, why):
    """A judged item that lands no row (a repeat that joined an open ask, a write held or sent elsewhere): its judge call
    is named `why`, so the readout reconciles it. A verdict with no call judged nothing, so it needs no reason."""
    if verdict and verdict.get("call"):
        H.event(c, H.KEEPER, UNRECORDED, verdict["call"], {"label": QUESTION_SET, "why": why})


def judged(store, verdicts, run):
    """`run()`, the write these verdicts (one, a list, or None) were judged for. A write that raises lands no row, so each
    of its calls is named: `refused` for a refusal (a Problem or a Refused), `error` otherwise. The naming is its own
    transaction, so it outlives the write's rollback; the error then goes on as it was."""
    try:
        return run()
    except Exception as exc:
        why = "refused" if isinstance(exc, (Problem, H.Refused)) else "error"
        judged_calls = [v for v in (verdicts if isinstance(verdicts, list) else [verdicts]) if v and v.get("call")]
        if judged_calls:
            with store.transaction() as c:
                for verdict in judged_calls:
                    skipped(c, verdict, why)
        raise


def _in_chunks(ids, size=500):
    ids = list(ids)
    for n in range(0, len(ids), size):
        yield ids[n:n + size]


def reconcile(c, start, end):
    """The window's judge calls on the question set, each one accounted for: `landed` (an item row carries its call),
    a named reason (`replay`, or the `ask_ingest.unrecorded` reason on it), or `unexplained` (neither: a gap to find).
    `no_call` counts item rows the gate wrote with no call (busy, breaker, no decision model). By construction
    calls == landed + sum(reasons) + unexplained."""
    found = H._rows(c.execute("SELECT id, detail FROM events WHERE action='judge.call' AND target LIKE ? "
                              "AND created_at>=? AND created_at<?", (f"{QUESTION_SET}@%", start, end)))
    modes = {}
    for row in found:
        try:
            modes[row["id"]] = (json.loads(row["detail"] or "{}") or {}).get("mode")
        except (TypeError, ValueError, AttributeError):
            modes[row["id"]] = None
    rows, why = set(), {}
    for chunk in _in_chunks(modes):
        marks = ",".join("?" * len(chunk))
        rows |= {r[0] for r in c.execute(f"SELECT call_id FROM ask_ingest_items WHERE call_id IN ({marks})", chunk)}
        for r in c.execute(f"SELECT target, detail FROM events WHERE action=? AND target IN ({marks})",
                           (UNRECORDED, *chunk)):
            try:
                why.setdefault(r[0], (json.loads(r[1] or "{}") or {}).get("why") or "unknown")
            except (TypeError, ValueError, AttributeError):
                why.setdefault(r[0], "unknown")
    out = {"calls": len(modes), "landed": 0, "reasons": {}, "unexplained": 0, "no_call": c.execute(
        "SELECT COUNT(*) FROM ask_ingest_items WHERE created_at>=? AND created_at<? AND call_id IS NULL",
        (start, end)).fetchone()[0]}
    for call_id, mode in modes.items():
        if call_id in rows:
            out["landed"] += 1
            continue
        named = why.get(call_id) or (REPLAY if mode == "replay" else None)
        if named:
            out["reasons"][named] = out["reasons"].get(named, 0) + 1
        else:
            out["unexplained"] += 1
    out["reasons"] = dict(sorted(out["reasons"].items()))
    return out


def on_reply(c, actor, task_id, text):
    """A person's answer on an open item the gate merged repeats into (hubdb.task_comment, hubdb.answer): passed once
    to each bot that asked the same thing, on its own task (waking it) or, for a question outside any task, as a
    message. The Strategy Bot's merge (backend/strategy_merge.py) relays the same way for its own merged items."""
    if not H.is_human(actor) or not task_id or not str(text or "").strip():
        return
    try:
        rows = H._rows(c.execute("SELECT * FROM ask_ingest_items WHERE merged_into=? AND person=? AND outcome=? "
                                 "AND passed_at IS NULL AND let_through_at IS NULL", (task_id, actor, MERGED)))
    except Exception:        # a bare hubdb connection without this module's tables (hubdb-only tests, upgrades)
        return
    if not rows:
        return
    item = H.task(c, task_id) or {}
    name = H.person_name(c, H.actor_id(actor)) or H.actor_id(actor)
    answer = str(text).strip()[:2000]
    for row in rows:
        # The person's item is named only to a bot that may read it; the answer is what was asked for.
        on = f' on \u201c{item.get("title") or ""}\u201d' if item and H.task_private_readable(c, row["asker"], item) else ""
        said = f'{name} answered this{on}: "{answer}"'
        if row["kind"] == "loose":
            H.say(c, H.KEEPER, row["asker"], said, kind="notice", refs={"ask_ingest": row["id"]})
        else:
            own = H.task(c, row["ref"])
            if not own or own["status"] not in H.ACTIVE_STATUSES:
                continue
            H.task_comment(c, H.KEEPER, own["id"], said, wake=True)
        c.execute("UPDATE ask_ingest_items SET passed_at=? WHERE id=?", (H.now(), row["id"]))


def withdraw_loose(c, asker, message_id, person):
    """A sent-back question outside any task leaves the person's Needs you at once (`loose_asks_to` reads open
    asks only); it stays in the bot's thread, superseded, as a withdrawn task question does."""
    c.execute("UPDATE messages SET superseded_at=? WHERE id=?", (H.now(), message_id))
    H.event(c, asker, "ask.withdrawn", message_id, {"to": person, "by": "ask_ingest"})
    H.recount(c, [asker])


def reply(*decided):
    """What the write's reply says of the gate: only when it has something to tell the asker (sent back, FYI, or
    what On would have done); an item that went through, or failed open, reads as before. A write that put two
    items on people (a handoff and a wait) reads as the first with something to say, with both sentences."""
    said = [d for d in decided if d and d["text"]]
    if not said:
        return {}
    return {"ingest": {**{k: said[0][k] for k in ("mode", "outcome", "would", "reason")},
                       "text": " ".join(d["text"] for d in said)}}


# ---- what the person reads and sets -------------------------------------------------------------------------------

def filtered(c, person, since):
    """What the gate kept off this person's plate since `since`, or would have in Rehearsal, newest first: each with
    the asking bot, the outcome and reason, and the item's title when the person may read it."""
    out = []
    for row in H._rows(c.execute(
            "SELECT * FROM ask_ingest_items WHERE person=? AND created_at>=? AND would IN (?,?,?) "
            "ORDER BY created_at DESC LIMIT 200", (person, since, *OFF_PLATE))):
        if row["kind"] == "loose":
            said = H.message(c, row["ref"])
            if not said or said.get("recipient_id") != person or said.get("deleted_at"):
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
                    "reason": row["reason"], "mode": row["mode"], "created_at": row["created_at"],
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
    if row["mode"] != ON or row["outcome"] not in OFF_PLATE:
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
        if row["kind"] == "task_question":
            # A task question also holds its task on the person (media.task_question); the gate skipped that too.
            H.task_update(c, asker, task["id"], status="waiting", waiting_on=person)
        elif row["kind"] == "task_ask" and task["status"] in ("open", "doing", "review"):
            H.task_update(c, asker, task["id"], status="waiting")      # as hubdb.task_ask parks it
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
    for row in c.execute("SELECT mode, outcome, would, let_through_at FROM ask_ingest_items WHERE created_at>=? AND "
                         "created_at<?", (start, end)):
        if row["mode"] == ON or row["would"] == FAILED_OPEN:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        else:
            rehearsal[row["would"]] = rehearsal.get(row["would"], 0) + 1
        let += bool(row["let_through_at"])
    return {"outcomes": dict(sorted(outcomes.items())), "rehearsal": dict(sorted(rehearsal.items())),
            "let_through": let}


REPLAY_PAGE = 25


def _next_day(day):
    import datetime as dt
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat() + "T00:00:00"
_ACTIVE_AT = ("owner=? AND created_at<? AND (done_at IS NULL OR done_at>?) AND (closed_at IS NULL OR closed_at>?) "
              "AND COALESCE(snoozed_until,'')<=?")


def replay_items(c, person, start, end, assistant_bot=None):
    """What bots put on this person between `start` and `end`, oldest first, as the gate would have seen each: tasks
    filed for them and questions asked of them (on a task or loose). Today's owner stands in for the owner then."""
    skip = H.bot_actor(assistant_bot) if assistant_bot else ""
    out = [{"kind": "task", "ref": r["id"], "asker": r["requester_id"], "title": r["title"] or "", "text": r["body"] or "",
            "created_at": r["created_at"]}
           for r in c.execute("SELECT id,title,body,requester_id,created_at FROM tasks WHERE owner_id=? AND created_at>=? AND "
                              "created_at<? AND requester_id LIKE 'bot:%' AND requester_id<>?", (person, start, end, skip))]
    for m in H._rows(c.execute("SELECT * FROM messages WHERE recipient_id=? AND kind='ask' AND created_at>=? AND created_at<? "
                               "AND sender_id LIKE 'bot:%' AND sender_id<>?", (person, start, end, skip))):
        msg = H.message(c, m["id"]) or m
        tid = H.message_task_id(msg)
        title = (H.task(c, tid) or {}).get("title", "") if tid else ""
        body = str(m.get("body") or "")
        out.append({"kind": "question" if tid else "loose", "ref": tid or m["id"], "asker": m["sender_id"],
                    "title": title or body.strip().split("\n")[0][:300], "text": body, "created_at": m["created_at"]})
    return sorted(out, key=lambda it: it["created_at"])


def load_at(c, person, at):
    """Roughly how full the person's plate was just before `at`: their tasks open then, snoozed ones left out."""
    return c.execute(f"SELECT COUNT(*) FROM tasks WHERE {_ACTIVE_AT}", (person, at, at, at, at)).fetchone()[0]


def open_items_at(c, person, at, leave_out=""):
    """The person's items open just before `at`, as the gate would have offered them as `covered`, the replayed
    item itself left out."""
    rows = c.execute(f"SELECT id,title FROM tasks WHERE {_ACTIVE_AT} AND id<>? ORDER BY created_at DESC LIMIT ?",
                     (person, at, at, at, at, leave_out, MAX_ITEMS)).fetchall()
    return {f"item-{n}": (r["id"], r["title"] or "") for n, r in enumerate(rows, 1)}


def _shown(c, viewer, person, it):
    """Whether `viewer` may read a replayed item's title: the person always; anyone else (an owner or admin) only a
    task they may read, never a bot's question to the person outside any task."""
    if viewer in (None, person):
        return True
    if it["kind"] == "loose":
        return False
    task = H.task(c, it["ref"])
    return bool(task) and H.task_private_readable(c, viewer, task)


def replay(store, primary, person, start, end, offset=0, limit=REPLAY_PAGE, viewer=None):
    """Rehearsal over a past day: each bot item put on `person` in [start, end) judged as it would have been, with
    the person's settings now and their plate just before it, decided as if the filter were On. Writes only the
    judge.call rows; nothing on any plate moves. A `viewer` other than the person reads only the titles they may
    read. Returns {items, total, next_offset}, or raises LookupError with no model."""
    with store.read() as c:
        own = settings(c, person)
        everything = replay_items(c, person, start, end, store.settings.assistant_bot)
        page = everything[offset:offset + limit]
        qset = J.load_set(QUESTION_SET)
        engine = server_engine(store, c, primary)
        if engine is None:
            raise LookupError("no decision model is on, or the month's spend cap is reached")
        if used_today(c, H.KEEPER) + len(page) > daily_cap(store.settings, "bot"):
            raise LookupError("today's decision calls would pass the daily cap")
        person_name = H.person_name(c, H.actor_id(person)) or H.actor_id(person)
        prepared = [(it, load_at(c, person, it["created_at"]), open_items_at(c, person, it["created_at"], it["ref"]),
                     _bot_line(c, it["asker"]), _shown(c, viewer, person, it)) for it in page]
    out = []
    for it, plate, items, asker, shown in prepared:
        answers, _, error = _judge(store, engine, qset, own, items, asker, person_name, it["title"], it["text"],
                                   it["kind"], replay=True)
        would, reason = (FAILED_OPEN, error) if error else decide(answers, own["budget"], own["chores_budget"], plate, 0)
        out.append({"kind": it["kind"], "ref": it["ref"] if shown else None, "title": it["title"] if shown else "",
                    "private": not shown, "asker": it["asker"], "asker_name": asker, "created_at": it["created_at"],
                    "load": plate, "would": would, "reason": reason})
    nxt = offset + len(page)
    return {"items": out, "total": len(everything), "next_offset": nxt if nxt < len(everything) else None}


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

    @app.delete("/api/v2/ask-ingest/settings")
    def ingest_settings_reset(request: Request):
        """Back to the team default: the caller's own settings are removed; the next item uses the team's."""
        who = request.state.identity
        auth.domain(who)
        person = person_of(who)
        with store.transaction() as c:
            c.execute("DELETE FROM ask_ingest_settings WHERE person=?", (person,))
            H.event(c, who.actor, "ask_ingest.settings", person, {"reset": True})
            return {"settings": settings(c, person)}

    class Replay(Contract):
        person: str = Field(max_length=200)
        date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
        offset: int = Field(default=0, ge=0)
        limit: int = Field(default=REPLAY_PAGE, ge=1, le=REPLAY_PAGE)

    @app.post("/api/v2/ask-ingest/replay")
    def ingest_replay(request: Request, body: Replay):
        """Rehearsal over one past day (UTC) for a person, a page at a time, before anyone turns the filter On: what
        On would have done with each bot item. The person, or an owner or admin. Nothing on any plate moves."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            person = H.resolve_actor(c, body.person)
        if not person or not H.is_human(person):
            raise Problem("not_found", "no such person", 404)
        if person != who.actor and auth.company_role(who) not in ("owner", "admin"):
            raise Problem("identity", "A replay is the person's own, or an owner's or admin's", 403)
        start = f"{body.date}T00:00:00"
        try:
            end = _next_day(body.date)
        except ValueError:
            raise Problem("validation", "date is YYYY-MM-DD", 422)
        try:
            return replay(store, app.state.judge, person, start, end, body.offset, body.limit, viewer=who.actor)
        except LookupError as exc:
            raise Problem("state", str(exc), 409)

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
