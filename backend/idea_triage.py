"""Idea triage: a new idea from the public roadmap's suggestion form, routed before it is recorded
(`questions/idea-triage.json`, asked through the server's Decide engine, `backend/judge.py` `server_engine`).

A bug or small change at the set's `engineering` threshold goes to the active Head of Engineering; everything else goes
where every idea went before, the roadmap's suggestions receiver (`roadmaps.suggestions_receiver`, Head of Product).
When the idea is about the same matter as an open roadmap item at the `covered` threshold, that item is named on the
new task and related to it, so whoever triages it sees the link. Routing only: nothing is sent, decided or published,
and a feature always reaches product, which still triages it.

Fail open: off (the default; `TICO_DECISIONS_IDEA_TRIAGE=1` turns it on), no decision model, rehearsal, the month's
spend cap, the daily call budget, an error, no answer within `DEADLINE` seconds, or an answer under its threshold all
leave the idea with the receiver exactly as before. Each call is one `judge.call` row (label, model, tokens, latency,
cost, the answers and the route); never the idea's text."""

import logging
import threading

from clients import judge as J
from .judge import audit_detail, daily_cap, over_spend_cap, server_engine, used_today
from .store import H

LOG = logging.getLogger("tico.idea_triage")
QUESTION_SET = "idea-triage"
# How long the visitor's request waits for an answer: Jev's one 8 s try, then a little for the company's model.
DEADLINE = 12
# Open roadmap items offered as `covered` options, most recently updated first (TypeSafe takes 255 options).
MAX_ITEMS = 100
ENGINEERING, PRODUCT = "engineering", "product"


def engineering_bot(c):
    """The active Head of Engineering, or None."""
    row = c.execute("SELECT bc.bot FROM bot_config bc JOIN bots b ON b.slug=bc.bot "
                    "WHERE json_extract(bc.config,'$.template')='engineering-lead' AND b.state='active' "
                    "ORDER BY bc.bot LIMIT 1").fetchone()
    return row[0] if row else None


def open_items(c, product_type):
    """{option name: (task id, title)} for the open roadmap items an idea can be covered by. Private tasks stay out:
    the new task's readers would see the link."""
    rows = c.execute("SELECT id,title FROM tasks WHERE type_id=? AND private=0 "
                     "AND status NOT IN ('done','closed','declined') ORDER BY updated_at DESC,id LIMIT ?",
                     (product_type, MAX_ITEMS)).fetchall()
    return {f"item-{n}": (row[0], row[1] or "") for n, row in enumerate(rows, 1)}


def _product(reason, **extra):
    return {"route": PRODUCT, "reason": reason, "covered": None, **extra}


def _ask(engine, state, questions, label):
    """The engine's answer within DEADLINE seconds: (result, None), (None, error code) or (None, 'timeout'). A call
    still running at the deadline finishes on its own thread; its cost is metered all the same."""
    box = {}

    def work():
        try:
            box["result"] = engine(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = exc.code
        except Exception as exc:    # a fake, a provider bug: routing never breaks on it
            LOG.warning("Idea triage call failed: %s", type(exc).__name__)
            box["error"] = "error"
    worker = threading.Thread(target=work, name="idea-triage", daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        return None, "timeout"
    return box.get("result"), box.get("error")


def _summary(answers, questions):
    """The audit's answers: only the questions asked, and nothing when they are malformed, so writing the row never
    stops the idea from reaching product."""
    try:
        return J.summary({qid: answers[qid] for qid in questions if qid in answers})
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}


def triage(store, primary, product_type, title, summary):
    """Where a new idea goes: {"route": "engineering" | "product", "reason", "covered": None | {"task", "title",
    "confidence"}, "engineering_bot" (with route engineering), "kind": (choice, confidence)}. Never raises; anything
    short of a confident answer routes to product, as before."""
    settings = store.settings
    if not settings.decisions_idea_triage:
        return _product("off")
    try:
        qset = J.load_set(QUESTION_SET)
    except J.JudgeError as exc:
        LOG.warning("Idea triage is off: %s", exc.detail)
        return _product("question_set")
    with store.read() as c:
        engine = server_engine(store, c, primary)
        if engine is None:
            return _product("spend_cap" if over_spend_cap(c, settings) else "unconfigured")
        if used_today(c, H.KEEPER) >= daily_cap(settings, "bot"):
            return _product("budget")
        engineer = engineering_bot(c)
        items = open_items(c, product_type) if product_type else {}
    questions = {"kind": qset["questions"]["kind"]}
    if items:
        questions["covered"] = J.with_options(qset["questions"]["covered"],
                                              {name: title for name, (_, title) in items.items()})
    state = {"idea": {"title": title, "summary": summary}}
    result, error = _ask(engine, state, questions, qset["label"])
    if result is not None and not isinstance(result, dict):
        result, error = None, "shape"
    out = _product(error) if error else None
    if result is not None:
        try:
            answers = J.check_answers(result.get("answers"), questions)
            kind = J.choice(answers, "kind")
            found = J.choice(answers, "covered") if "covered" in questions else ("new", 0.0)
        except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError):
            out = _product("shape")
        else:
            thresholds = qset["thresholds"]
            if kind[0] == ENGINEERING and kind[1] >= thresholds.get("engineering", 1.01):
                out = ({"route": ENGINEERING, "reason": ENGINEERING, "covered": None, "engineering_bot": engineer}
                       if engineer else _product("no_engineering_bot"))
            else:
                out = _product(PRODUCT if kind[0] == PRODUCT else "below_threshold")
            out["kind"] = kind
            if found[0] in items and found[1] >= thresholds.get("covered", 1.01):
                task_id, item_title = items[found[0]]
                out["covered"] = {"task": task_id, "title": item_title, "confidence": round(found[1], 3)}
    detail = {"label": qset["label"], "questions": len(questions), "types": ["choice"],
              "options": len(items), "route": out["route"], "reason": out["reason"],
              "covered": (out["covered"] or {}).get("task")}
    if result is not None:
        detail.update(audit_detail(result), answers=_summary(result.get("answers"), questions))
    if error:
        detail["error"] = error
    with store.transaction() as c:
        H.event(c, H.KEEPER, "judge.call", qset["label"], detail)
    return out


def preface(outcome):
    """The line the new task opens with, above the visitor's untrusted text; empty when triage said nothing."""
    if not outcome or "kind" not in outcome:
        return ""
    choice, confidence = outcome["kind"]
    lines = [f"Idea triage ({QUESTION_SET}, routing only): "
             + (f"a bug or small change ({confidence:.0%}), sent to engineering." if outcome["route"] == ENGINEERING
                else f"for product ({choice} {confidence:.0%}).")]
    if outcome["covered"]:
        covered = outcome["covered"]
        lines.append(f"Probably covered by the open roadmap item “{covered['title']}” "
                     f"(task {covered['task']}, {covered['confidence']:.0%}); it is linked as related.")
    return "\n".join(lines) + "\n\n"
