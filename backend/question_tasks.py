"""Question tasks (spec D8): a bot's decision for a person is a task the person owns, with the question on it.

`hub task question "<q>" --on <person>` (backend/media.py task_question) files it: under the bot's task when asked from
one (a subtask the task is blocked on, as hubdb._ask_in_subtask files a card), else on its own. The question lives in
the task's fields (`questions`, `if_no_answer`, `review`, `spend`; the migration names them), never in an ask message.
Answering is the close: a pick, a Yes / No or a note closes it done with `answer` recorded, a decline closes it
declined, and the bot hears it once (on its task through hubdb._subtask_wake, else as the task's requester).

Every surface reads it through `as_ask`, the question shaped as the ask a question card already draws (Needs you,
Decide, the task page, the Ask panel), next to the old ask messages until those move over (step 6c). One open question
task per topic per person and bot (`open_for`, `fold`). ask_deadlines and still_needed settle them on their dates.
"""

import json

from . import hubdb as H
from . import task_relations as TR

JSON_FIELDS = ("questions", "if_no_answer", "review", "spend", "answer")
FIRST_ID = "decision"


def _load(value):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _dump(value):
    return None if value in (None, {}, []) else json.dumps(value)


def fields(row):
    """The question fields of a task row, decoded ({field: value or None})."""
    return {k: _load((row or {}).get(k)) for k in JSON_FIELDS}


def is_question(row):
    return bool(row and row.get("questions"))


def is_open(row):
    """A question task still waiting for its answer."""
    return is_question(row) and not row.get("answer") and row.get("status") in H.ACTIVE_STATUSES


def asked_at(row):
    """The question's clock: when it was asked, or last asked again (a new wording folded in)."""
    return row.get("asked_at") or row.get("created_at")


def as_ask(row):
    """The question task as the ask a question card reads: its id is the task's, from the bot that asked (the task's
    requester) to the person who owns it, its refs the stored question, and its answer target the task itself."""
    f = fields(row)
    questions = f["questions"] or []
    refs = {"questions": questions, "task": row["id"], "task_question": True, "target": {"task": row["id"]}}
    if f["if_no_answer"]:
        refs["if_no_answer"] = f["if_no_answer"]
    if row.get("question_topic"):
        refs["topic"] = row["question_topic"]
    review = f["review"] or {}
    if review.get("files"):
        refs["review"] = {"files": review["files"], **({"pick": True} if review.get("pick") else {})}
        refs["attachments"] = review.get("attachments") or []
    if f["spend"]:
        refs["spend"] = f["spend"]
    if questions and questions[0].get("id") != FIRST_ID:
        # Reworded in place (fold): an answer names the current question id (task_review.checked_answers).
        refs["asked_again_at"] = asked_at(row)
    return {"id": row["id"], "kind": "ask", "question_task": True, "sender_id": row["requester_id"],
            "recipient_id": row["owner_id"], "body": (questions[0].get("question") if questions else row["title"]),
            "created_at": asked_at(row), "conversation_id": row.get("conversation_id"), "refs": refs,
            "read_at": None, "delivered_at": None}


def store(conn, task_id, ask, topic=None):
    """Put the checked question (task_review.check_ask's shape) on the task, its clock started now."""
    review = ask.get("review")
    if review:
        review = {**review, "attachments": ask.get("attachments") or []}
    conn.execute("UPDATE tasks SET questions=?, if_no_answer=?, review=?, spend=?, question_topic=?, asked_at=?, "
                 "answer=NULL WHERE id=?",
                 (json.dumps(ask["questions"]), _dump(ask.get("if_no_answer")), _dump(review), _dump(ask.get("spend")),
                  topic, H.now(), task_id))


def file(conn, bot, card, parent):
    """A new question task under `parent`: marked as the card the bot filed for the person (hubdb.ask_card), and the
    bot's own task blocked on it, so the answer opens that task again and the bot hears it once (hubdb._answered)."""
    if not parent:
        return
    H.event(conn, bot, H.ASK_CARD, card["id"], {"for": parent["id"], "to": card["owner_id"]})
    if parent["owner_id"] == bot and card["id"] not in TR.blocker_ids(conn, parent["id"]):
        TR.add_blocker(conn, bot, parent["id"], card["id"], note="asked in a question task")


def open_for(conn, bot, person, *, parent=None, topic=None, private=False):
    """`bot`'s open question task for `person` that a new question on the same topic joins, or None: with a topic key,
    the one with that key under any task; without one, one under the same parent with no key either. Never across
    privacy: a private question joins only a private one, and only under the same parent."""
    topic = H.question_topic(topic)
    if not topic and not parent:
        return None
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for found in conn.execute(f"SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND questions IS NOT NULL "
                              f"AND answer IS NULL AND status IN ({marks}) ORDER BY created_at, rowid",
                              (person, bot, *H.ACTIVE_STATUSES)).fetchall():
        card = H.task(conn, found[0])
        if bool(H.task_private(conn, card)) != bool(private):
            continue
        same_parent = bool(parent) and TR.parent_of(conn, card["id"]) == parent
        if private and not same_parent:
            continue
        if H.question_topic(card.get("question_topic")) == topic and (topic or same_parent):
            return card
    return None


def earlier(conn, card):
    """The wordings this question task had before, oldest first ({question, at})."""
    rows = conn.execute("SELECT detail FROM events WHERE action=? AND target=? ORDER BY created_at, rowid",
                        (H.QUESTION_FOLDED, card["id"])).fetchall()
    return [{"question": d.get("was"), "at": d.get("at")} for d in (H._json(r[0], {}) or {} for r in rows)
            if d.get("was")]


def fold(conn, bot, card, question, ask, *, title=None, body=None):
    """A new wording of an open question task (open_for): the person keeps one task, with the latest question and
    options and the earlier wordings listed under its body. Nothing new is sent: no second Needs-you item, no
    notice, no run. Its clock restarts, so a default goes ahead the team's days after the latest wording."""
    old = fields(card)["questions"] or []
    was = str((old[0].get("question") if old else card["title"]) or "").strip()
    before = [*earlier(conn, card), {"question": was, "at": asked_at(card)}]
    # Each wording is its own question id ("decision-2"...): a pick made on the wording before is refused.
    questions = [{**q, "id": f"{FIRST_ID}-{len(before) + 1}"} if not i else q for i, q in enumerate(ask["questions"])]
    store(conn, card["id"], {**ask, "questions": questions},
          topic=card.get("question_topic") or H.question_topic(ask.get("topic")))
    kept = before[-H.EARLIER_ASKS_KEPT:]
    lines = "\n".join(f"- {e['question']} ({str(e.get('at') or '')[:10]})" for e in reversed(kept))
    text = (body or question).strip() + f"\n\nAsked before:\n{lines}"
    new_title = title or question
    conn.execute("UPDATE tasks SET title=?, body=?, version=version+1, updated_at=? WHERE id=?",
                 (new_title, text, H.now(), card["id"]))
    if new_title != card["title"]:
        H._task_event(conn, card["id"], bot, "title", card["title"], new_title, "")
    H._task_event(conn, card["id"], bot, "body", card.get("body"), text, "Asked again in new words")
    H.event(conn, bot, H.QUESTION_FOLDED, card["id"],
            {"to": card["owner_id"], "topic": card.get("question_topic"), "asks": len(before) + 1, "was": was,
             "at": asked_at(card)})
    return H.task(conn, card["id"])


def record(conn, task_id, value):
    conn.execute("UPDATE tasks SET answer=? WHERE id=?", (json.dumps(value), task_id))


def _label_said(questions, text):
    """The option a note names, as {question id: [label]} and what it says besides: "Yes." or "Yes. Ship it Friday"
    (Decide's Yes / No) picks Yes; anything else is a note."""
    if len(questions) != 1:
        return {}, text
    q = questions[0]
    folded = text.casefold()
    for option in q.get("options") or []:
        label = str(option.get("label") or "").strip()
        key = label.casefold()
        if key and (folded.rstrip(".! ") == key or folded.startswith(key + ".") or folded.startswith(key + " -")):
            rest = text[len(label):].lstrip(".!- ").strip()
            return {q["id"]: [label]}, rest or None
    return {}, text


def closed(conn, actor, row, status, note):
    """A question task moved to done or declined with no answer recorded (a status write, Decide's Yes / No, the
    Assistant's close): the note is the answer. A note naming an option picks it; done with nothing said on a Yes / No
    is a yes; declined is the person declining the question."""
    if not is_question(row) or status not in ("done", "declined"):
        return
    if conn.execute("SELECT answer FROM tasks WHERE id=?", (row["id"],)).fetchone()[0]:
        return                  # answered first (answer, settle): that answer stands
    questions = fields(row)["questions"] or []
    text = str(note or "").strip()
    answers, other = ({}, text or None) if status == "declined" else _label_said(questions, text)
    if status == "done" and not text and len(questions) == 1:
        yes = next((o["label"] for o in questions[0].get("options") or [] if o["label"].casefold() == "yes"), None)
        answers = {questions[0]["id"]: [yes]} if yes else {}
    record(conn, row["id"], {"target": {"task": row["id"]}, "answers": answers, "other": other,
                             "dismiss": status == "declined", "by": actor, "at": H.now(),
                             "text": text or ("Declined." if status == "declined" else "Done.")})


def answer(c, auth, who, task_id, body, warn=""):
    """A structured answer to a question task (POST /tasks/{id}/answers with target {task}): checked as a comment
    ask's is (task_review.checked_answers), recorded as `answer`, and the task closed with it as the note: done, or
    declined when dismissed. Files under review get their verdicts, a picked spend its approval."""
    from .store import Problem
    from . import file_reviews
    from . import task_privacy as privacy
    from .task_review import answer_words, checked_answers, readable_text
    auth.task(c, who, task_id)
    row = H.task(c, task_id)
    if not is_question(row):
        raise Problem("validation", "The target has no structured question", 422)
    if row["requester_id"] == who.actor:
        raise Problem("forbidden", "The asker cannot answer their own question", 403)
    asked = as_ask(row)
    questions = {q["id"]: q for q in asked["refs"]["questions"]}
    body, passed = checked_answers(questions, asked["refs"], body)
    given = {"target": {"task": task_id}, "answers": body.answers, "other": body.other, "dismiss": body.dismiss,
             "by": who.actor}
    previous = _load(row.get("answer"))
    if previous:
        # A retry with a new transport key records nothing twice; a different answer to a closed question is late.
        if all(previous.get(k) == v for k, v in given.items()):
            return {"answer": previous, "comments": H.task_comments(c, task_id, actor=privacy.actor(who)),
                    "woke": False}
        raise Problem("answered", "This question was already answered", 409)
    if row["status"] not in H.ACTIVE_STATUSES:
        raise Problem("answered", "This question is closed", 409)
    by = H.human(c, H.actor_id(who.actor)) if H.is_human(who.actor) else H.bot(c, H.actor_id(who.actor))
    name = readable_text((by or {}).get("name") or H.actor_id(who.actor))
    text = answer_words(name, questions, body, passed)
    if body.other:
        text += " " + body.other
    if warn:
        text = warn + "\n\n" + text
    value = {**given, "at": H.now(), **({"not": {k: v for k, v in passed.items() if v}} if any(passed.values()) else {})}
    review, spend = asked["refs"].get("review"), asked["refs"].get("spend")
    labels = [] if body.dismiss else [label for picks in body.answers.values() for label in picks]
    if review and not body.dismiss:
        value["reviewed"] = file_reviews.verdicts(review, labels, body.other)
        file_reviews.require_readable(c, auth, who, value["reviewed"])
    if spend and spend["label"] in labels:
        value["spend_approval"] = file_reviews.spend_approval(c, who, asked, task_id, spend, [])
    text, refs = H.answer_text(asked, body.answers, text=text, passed=passed, refs={"answer": value})
    value = {**refs["answer"], "text": text}
    record(c, task_id, value)
    H.task_update(c, who.actor, task_id, status="declined" if body.dismiss else "done", note=text)
    if value.get("reviewed"):
        file_reviews.record(c, who, task_id, task_id, task_id, value["reviewed"])
    return {"answer": value, "comments": H.task_comments(c, task_id, actor=privacy.actor(who)), "woke": True}


def take_recommendation(c, auth, who, row, version=None):
    """"Do what you recommend" on a question task: its recommended option picked as if by hand, or the task done with
    the bot's default as the note."""
    from .store import Problem
    from . import models as M
    from .views import recommendation
    if row["owner_id"] != who.actor:
        raise Problem("forbidden", "Only the person it asked takes its recommendation", 403)
    if not is_open(row):
        raise Problem("answered", "This question was already answered", 409)
    if version is not None and row["version"] != version:
        raise Problem("version_conflict", "Task changed; fetch it and retry", 409)
    asked = as_ask(row)
    rec = recommendation(asked, who.actor)
    if not rec:
        raise Problem("no_recommendation", "This question carries no recommendation to take", 409)
    if rec["option"]:
        question = asked["refs"]["questions"][0]
        return answer(c, auth, who, row["id"], M.TaskAnswer(target={"task": row["id"]},
                                                            answers={question["id"]: [rec["option"]]}))
    H.task_update(c, who.actor, row["id"], status="done", note="Yes, do what you recommend: " + rec["do"])
    return {"answer": _load(H.task(c, row["id"]).get("answer")), "woke": True}


# ---- dates: ask_deadlines and still_needed run on question tasks --------------------------------------------------

def open_rows(conn):
    """Open question tasks a bot put to a person, oldest question first."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows = H._rows(conn.execute(
        f"SELECT * FROM {H.TASKS} t WHERE questions IS NOT NULL AND answer IS NULL AND status IN ({marks}) "
        "AND owner_id LIKE 'human:%' AND requester_id LIKE 'bot:%' ORDER BY coalesce(asked_at, created_at)",
        tuple(H.ACTIVE_STATUSES)))
    return [r for r in rows if not H.TS.snoozed(r)]


def settle(conn, row, text, *, answers=None):
    """The bot's default went ahead on its date: the keeper answers the question task with it and closes it done.
    The bot hears it once (hubdb._answered on its task, or as the requester)."""
    record(conn, row["id"], {"target": {"task": row["id"]}, "answers": answers or {}, "other": None, "dismiss": False,
                             "by": H.KEEPER, "at": H.now(), "text": text, "if_no_answer": "went_ahead"})
    H.task_update(conn, H.KEEPER, row["id"], status="done", note=text)
    H.event(conn, H.KEEPER, "ask.went_ahead", row["id"], {"to": row["owner_id"], "bot": row["requester_id"],
                                                         "task": row["id"]})


def expire(conn, row, text):
    """Its date passed and nothing goes ahead on its own: the question task closes, and the bot hears why."""
    H.task_close(conn, H.KEEPER, row["id"], text, strict=False)
    H.event(conn, H.KEEPER, "ask.expired", row["id"], {"to": row["owner_id"], "bot": row["requester_id"],
                                                      "task": row["id"]})


def send_back(conn, row, why):
    """No answer and no date: the question task closes quietly and goes on its bot's roundup (still_needed), with no
    wake of its own; the bot's task blocked on it is unblocked without one."""
    H.task_close(conn, H.KEEPER, row["id"], why, quiet=True, strict=False,
                 closing_tasks=tuple(TR.blocked_ids(conn, row["id"])))
