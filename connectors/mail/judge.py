"""The judge for mail: judge the listing before a thread is opened, and gate a draft before the reviewer.

Both read a question set from the hub's `questions/` (`mail-triage`, `mail-draft-gate`) and make
the one call every bot has (`clients/judge.py`): inside a bot turn through the hub with the
turn's own credential, so the key stays on the server and the call is audited there; with a
`TYPESAFE_API_KEY` of its own, directly; with neither, not at all, and the pass runs the way it
did before. `MAIL_DECISIONS=none` (formerly `MAIL_JUDGE`) switches it off, which is what the tests set.

Nothing here changes a label, archives a message or writes a draft. A judgment is one line on
the brief listing and one field in the JSON, plus a one-word suggestion computed here from the
set's thresholds, so the rule "what the answers do" is written once and read by every inbox
bot. The bot still opens the threads it acts on and still decides; under the thresholds the
suggestion is `read`, which means what it meant yesterday.

The draft gate is advisory: its flags are recorded with the draft and printed, and the second
reviewer keeps the last word. A month of `gate` audit lines beside the reviewer's verdicts is
how that stays or changes (docs/mail-service.md).
"""

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HUB = Path(__file__).resolve().parents[2]
if str(HUB) not in sys.path:  # `python -m connectors.mail` already has it
    sys.path.insert(0, str(HUB))
from clients import judge as J
from . import db

ENV_VAR = "MAIL_DECISIONS"
OLD_ENV_VAR = "MAIL_JUDGE"  # deprecated spelling, still read
TRIAGE, GATE = "mail-triage", "mail-draft-gate"
PARALLEL = 8
SNIPPET_CHARS = 400
THREAD_CHARS = 6000
DRAFT_CHARS = 6000
ENGINE = None  # the test seam: a callable, or None


def engine(env=None):
    """The engine this process may use, or None."""
    env = os.environ if env is None else env
    if ENGINE is not None:
        return ENGINE
    if ((env.get(ENV_VAR) or env.get(OLD_ENV_VAR) or "").strip().lower()) == "none":
        return None
    return J.from_env(env)


def unavailable_hint():
    return (
        "Decisions run inside a bot turn through the hub (HUB_API_URL and HUB_TOKEN, which the "
        "runner sets) or with TYPESAFE_API_KEY in the environment; MAIL_DECISIONS=none turns it off."
    )


# ---------------------------------------------------------------- the listing


def message_state(m, mailbox=""):
    """What `mail-triage` judges: the brief listing's fields, never the body."""
    return {
        "mailbox": mailbox,
        "from": m.get("from") or "",
        "from_name": m.get("from_header") or "",
        "to": list(m.get("to") or []),
        "cc": list(m.get("cc") or []),
        "date": m.get("date") or "",
        "subject": m.get("subject") or "",
        "snippet": str(m.get("snippet") or "")[:SNIPPET_CHARS],
        "unsubscribe": bool(m.get("unsubscribe")),
        "list_id": bool(str(m.get("list_id") or "").strip()),
        "attachments": [a.get("name") for a in (m.get("attachments") or []) if isinstance(a, dict)],
        "is_internal": bool(m.get("is_internal")),
        "is_calendar_invite": bool(m.get("is_calendar_invite")),
        "labels": list(m.get("labels") or []),
    }


def suggest(j, t):
    """One word from the set's thresholds: what a careful inbox bot would do with these answers.

    Protections first, then the bucket at its own confidence, else `read`: open the thread and
    decide the way the playbook always said. Legal protects on its own. Money protects only when
    someone is asking: on the first real pass payroll runs, receipts and usage
    notices scored money at 0.9 with nobody asking anything, the same trap the bare "invoice"
    rule fell into (registry/mail-rules.yaml, payment-risk-words)."""
    if j["legal"] >= t.get("legal", 0.5):
        return "needs-owner"
    if j["money"] >= t.get("money", 0.8) and j["is_ask"] >= t.get("is_ask", 0.6):
        return "needs-owner"
    bucket, conf = j["bucket"], j["confidence"]
    if bucket == "needs-owner" and conf >= t.get("needs_owner", 0.7):
        return "needs-owner"
    if bucket == "archive" and conf >= t.get("archive", 0.85):
        return "archive"
    if bucket == "reply" and conf >= t.get("reply", 0.7) and j["is_ask"] >= t.get("is_ask", 0.6):
        return "reply"
    if bucket == "route" and conf >= t.get("route", 0.7):
        return "route"
    return "read"


def _judgment(result, chosen):
    a = result["answers"]
    bucket, conf = J.choice(a, "bucket")
    urgency, _ = J.score(a, "urgency")
    j = {
        "set": chosen["label"],
        "bucket": bucket,
        "confidence": round(conf, 2),
        "is_ask": round(J.noul(a, "is_ask"), 2),
        "money": round(J.noul(a, "money"), 2),
        "legal": round(J.noul(a, "legal"), 2),
        "urgency": round(urgency, 2),
        "ms": result.get("ms", 0),
    }
    j["suggest"] = suggest(j, chosen["thresholds"])
    return j


def judge_message(m, chosen, engine, mailbox=""):
    """One message's judgment, or `{"error": code}` when the call failed; never raises."""
    try:
        return _judgment(engine(message_state(m, mailbox), chosen["questions"], chosen["label"]), chosen)
    except J.JudgeError as exc:
        return {"set": chosen["label"], "error": exc.code, "detail": exc.detail[:200]}


def _cache_key(m, chosen, mailbox):
    payload = [mailbox, m.get("id"), chosen["label"], chosen["questions"], message_state(m, mailbox)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _run(msgs, chosen, engine, mailbox, conn=None):
    """Read SQLite on this thread, call the judge in parallel for misses, then write on this thread."""
    results = [None] * len(msgs)
    misses = []
    for idx, m in enumerate(msgs):
        key = _cache_key(m, chosen, mailbox) if conn is not None else None
        cached = db.get_judgment(conn, key) if key else None
        if cached is not None:
            results[idx] = ({"answers": cached, "ms": 0}, None, True)
        else:
            misses.append((idx, m, key))

    def one(item):
        _idx, m, _key = item
        try:
            return engine(message_state(m, mailbox), chosen["questions"], chosen["label"]), None
        except J.JudgeError as exc:
            return None, exc

    if misses:
        with ThreadPoolExecutor(min(PARALLEL, len(misses))) as pool:
            fresh = list(pool.map(one, misses))
        for (idx, _m, key), (result, error) in zip(misses, fresh):
            results[idx] = (result, error, False)
            if result is not None and key:
                db.put_judgment(conn, key, result["answers"])
    return results


def triage(msgs, engine, mailbox="", conn=None):
    """Judge every message of a listing, several at a time, and put `judgment` on each.
    Returns the summary the audit and the JSON carry."""
    chosen = J.load_set(TRIAGE)
    results = _run(msgs, chosen, engine, mailbox, conn)
    judged = [
        _judgment(result, chosen)
        if result is not None
        else {"set": chosen["label"], "error": error.code, "detail": error.detail[:200]}
        for result, error, _cached in results
    ]
    for m, j in zip(msgs, judged):
        m["judgment"] = j
    errors = sum(1 for j in judged if j.get("error"))
    counts = {}
    for j in judged:
        if not j.get("error"):
            counts[j["suggest"]] = counts.get(j["suggest"], 0) + 1
    return {
        "set": chosen["label"],
        "count": len(judged) - errors,
        "errors": errors,
        "cached": sum(cached for _result, _error, cached in results),
        "suggested": counts,
        "ms": max([j.get("ms", 0) for j in judged] or [0]),
        "by_message": {m["id"]: (j.get("suggest") or "error") for m, j in zip(msgs, judged)},
    }


def render_judgment(j):
    """The one line under a message on the brief listing."""
    if not j:
        return ""
    if j.get("error"):
        return f"  decision: unavailable ({j['error']})"
    return (
        f"  decision: {j['suggest']}  ({j['bucket']} {j['confidence']:.2f}; ask {j['is_ask']:.2f}, "
        f"money {j['money']:.2f}, legal {j['legal']:.2f}, urgency {j['urgency']:.1f})"
    )


# ---------------------------------------------------------------- the rules


def judge_sets(msgs, set_ids, engine, mailbox="", conn=None):
    """What a `judge` rule condition reads (rules.py): for every set the rules name, judge every
    message from its listing fields and put `judgments[set] = {question: {value, confidence}}`
    on it. A failed call leaves `judge_errors[set]` instead, and the rule does not fire.
    Every set a message still needs is asked in one call about it (`_ask_sets`), so the message is
    read once however many sets the rules name. Returns one summary per set for the audit."""
    sets = {sid: J.load_set(sid) for sid in set_ids}
    stats = {sid: {"label": chosen["label"], "count": 0, "errors": 0, "cached": 0} for sid, chosen in sets.items()}
    todo = []  # (message, {set id: cache key}) for what is not cached yet
    for m in msgs:
        missing = {}
        for sid, chosen in sets.items():
            key = _cache_key(m, chosen, mailbox) if conn is not None else None
            cached = db.get_judgment(conn, key) if key else None
            if cached is None:
                missing[sid] = key
                continue
            m.setdefault("judgments", {})[sid] = J.summary(cached)
            stats[sid]["count"] += 1
            stats[sid]["cached"] += 1
        if missing:
            todo.append((m, missing))

    def one(entry):
        m, missing = entry
        return _ask_sets(engine, message_state(m, mailbox), {sid: sets[sid] for sid in missing})

    fresh = []
    if todo:
        with ThreadPoolExecutor(min(PARALLEL, len(todo))) as pool:
            fresh = list(pool.map(one, todo))
    for (m, missing), answered in zip(todo, fresh):
        for sid, key in missing.items():
            answers, error = answered[sid]
            if error:
                stats[sid]["errors"] += 1
                m.setdefault("judge_errors", {})[sid] = error.code
                continue
            m.setdefault("judgments", {})[sid] = J.summary(answers)
            stats[sid]["count"] += 1
            if key:
                db.put_judgment(conn, key, answers)
    return stats


def _ask_sets(engine, state, sets):
    """{set id: (answers, None) or (None, JudgeError)}: every set's questions about one message, in one call, each
    question under `<set id>/<question id>`; one call per set only when together they do not fit in one."""
    if len(sets) > 1:
        questions = {f"{sid}/{qid}": q for sid, chosen in sets.items() for qid, q in chosen["questions"].items()}
        labels = "+".join(chosen["label"] for chosen in sets.values())
        if J.fits(state, questions):
            try:
                label = labels if len(labels) <= J.MAX_LABEL else next(iter(sets.values()))["label"]
                result = engine(state, questions, label)
            except J.JudgeError as exc:
                return {sid: (None, exc) for sid in sets}
            answers = result["answers"]
            return {
                sid: ({qid: answers[f"{sid}/{qid}"] for qid in chosen["questions"]}, None)
                for sid, chosen in sets.items()
            }
    out = {}
    for sid, chosen in sets.items():
        try:
            out[sid] = (engine(state, chosen["questions"], chosen["label"])["answers"], None)
        except J.JudgeError as exc:
            out[sid] = (None, exc)
    return out


# ---------------------------------------------------------------- the draft


def draft_gate(engine, body, subject="", thread="", mailbox="", employee=""):
    """`mail-draft-gate` over the draft and the thread it answers. Advisory; never raises."""
    chosen = J.load_set(GATE)
    state = {
        "mailbox": mailbox,
        "employee": employee,
        "subject": subject or "",
        "thread": (thread or "")[:THREAD_CHARS],
        "draft": (body or "")[:DRAFT_CHARS],
    }
    try:
        result = engine(state, chosen["questions"], chosen["label"])
    except J.JudgeError as exc:
        return {
            "set": chosen["label"],
            "available": False,
            "error": exc.code,
            "ok": None,
            "flags": [],
            "probabilities": {},
        }
    probabilities = {qid: round(J.noul(result["answers"], qid), 2) for qid in chosen["questions"]}
    flag = chosen["thresholds"].get("flag", 0.5)
    flags = [qid for qid, p in probabilities.items() if p >= flag]
    return {
        "set": chosen["label"],
        "available": True,
        "ok": not flags,
        "flags": flags,
        "probabilities": probabilities,
        "flag_at": flag,
        "ms": result.get("ms", 0),
    }


def render_gate(g):
    if not g:
        return ""
    if not g.get("available"):
        return f"  gate:    unavailable ({g.get('error', '?')}); the reviewer decides"
    if g["ok"]:
        return f"  gate:    ok ({g['set']})"
    named = ", ".join(f"{q} {g['probabilities'][q]:.2f}" for q in g["flags"])
    return f"  gate:    flagged {named} ({g['set']}; advisory, the reviewer decides)"
