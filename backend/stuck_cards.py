# pyright: strict
"""The stuck rule: no card on a pipeline stays stuck without someone hearing (docs/tasks.md, Stuck cards).

A pipeline rule for every pipeline that has a manager bot (task_types.manager). Plain code in the Heartbeat (step
`stuck_cards`, every 10 minutes); no model is called.

- Stuck: an open card with no progress for the pipeline's `stuck_after_hours` (default STUCK_AFTER_HOURS), or a waiting
  card with nothing tracked to wait on (hold cause `stranded`) at once. Progress is a stage or status move, a new owner,
  a turn of the card's owner on it (its checkpoint, or a run going now), a comment, note or room message from anyone but
  Tico and the manager, a receipt or a stage decision, and a hold that began or changed to a new reason
  (task_holds.reason_changed_at), and the end of its owner bot's usage limit. A card whose owner bot is in a usage
  limit is not stuck (the limit ends on its own; Health shows it), and a manager in one is woken once it is back. Progress on a card's open child tasks (any pipeline) counts as the card's own. A card
  that waits on a named person with a question or an approval asked under PERSON_WAIT_HOURS ago is not stuck, nor is a
  snoozed card, one On ice, or one with an open child that is stuck on its own pipeline (that manager hears it).
- At stuck: the pipeline's manager bot is woken once for the stuck spell, on the card, with its hold, and asked to unblock,
  reassign, split or close it and to leave one line saying which. A spell is one row of `stuck_spells`; it ends when the
  card makes progress, finishes or leaves the pipeline. The manager's own comments do not end it, so it is not woken
  again for the same spell.
- Still stuck at `ask_person_after_hours` (default ASK_PERSON_AFTER_HOURS): one question to the pipeline's owner (else the
  company owner) on their Needs you, a subtask of the card, asked by Tico. One per card, ever. When it is answered or
  expires, the manager hears the answer once on the card.
- The question closes after the pipeline's question expiry days like every question to a person (backend/ask_deadlines.py).
"""

from typing import TYPE_CHECKING, Any

from . import hubdb
from . import pipeline_manager as PM
from . import pipeline_rights
from . import question_tasks as QT

if TYPE_CHECKING:
    import sqlite3

# hubdb is untyped: its functions are Any here (as in pipeline_manager.py), not partially unknown.
H: Any = hubdb
Row = dict[str, Any]

STUCK_AFTER_HOURS = 4
ASK_PERSON_AFTER_HOURS = 24
PERSON_WAIT_HOURS = 24  # a question or approval to a person younger than this is a wait, not stuck
WAKES_PER_PASS = 10  # manager wakes and person questions per pipeline per pass; the rest go on the next pass
STEP = "stuck_cards"
# Task events that are progress whoever made them, and those that are progress unless Tico or the manager made them.
MOVES = ("step", "status", "owner", "type")
WORDS = ("comment", "note", "subtask", "children", "verdict", "link")


def settings(row: Row) -> dict[str, int]:
    """A pipeline's stuck rule settings, the defaults standing in for any not set."""
    from .ask_deadlines import EXPIRE_DAYS

    return {
        "stuck_after_hours": int(row.get("stuck_after_hours") or STUCK_AFTER_HOURS),
        "ask_person_after_hours": int(row.get("ask_person_after_hours") or ASK_PERSON_AFTER_HOURS),
        "question_expiry_days": int(row.get("question_expiry_days") or EXPIRE_DAYS),
    }


def defaults() -> dict[str, int]:
    from .ask_deadlines import EXPIRE_DAYS

    return {
        "stuck_after_hours": STUCK_AFTER_HOURS,
        "ask_person_after_hours": ASK_PERSON_AFTER_HOURS,
        "question_expiry_days": EXPIRE_DAYS,
    }


def _pipelines(c: "sqlite3.Connection") -> list[Row]:
    return H._rows(
        c.execute(
            "SELECT id, name, manager, pipeline_owner_id, stuck_after_hours, ask_person_after_hours, "
            "question_expiry_days FROM task_types WHERE manager IS NOT NULL AND manager<>'' ORDER BY id"
        )
    )


def _cards(c: "sqlite3.Connection", pipeline_id: str) -> list[Row]:
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    return H._rows(
        c.execute(
            f"SELECT t.* FROM {H.TASKS} t WHERE t.type_id=? AND t.status IN ({marks}) AND NOT {H.ON_ICE % 't'} "
            "AND t.questions IS NULL "  # a question task is no card: it expires on its own (backend/ask_deadlines.py)
            "ORDER BY t.created_at, t.id",
            (pipeline_id, *H.ACTIVE_STATUSES),
        )
    )


def _max(found: dict[str, str], key: str, value: Any) -> None:
    if value and (not found.get(key) or str(value) > found[key]):
        found[key] = str(value)


def _children(c: "sqlite3.Connection", ids: list[str]) -> list[Row]:
    """The open child tasks of these cards (task_relations kind 'parent', any pipeline), each with its card as
    `parent_task`. Question tasks are left out: a question to a person is a wait (_waits_on_person), not progress."""
    found: list[Row] = []
    status = ",".join("?" * len(H.ACTIVE_STATUSES))
    for part in H.chunks(ids):
        marks = ",".join("?" * len(part))
        found += H._rows(
            c.execute(
                "SELECT r.to_task AS parent_task, t.id, t.created_at, t.checkpoint_at, t.checkpoint_by_id, t.owner_id, "
                "t.conversation_id FROM task_relations r JOIN tasks t ON t.id=r.from_task "
                f"WHERE r.kind='parent' AND r.to_task IN ({marks}) AND t.status IN ({status}) AND t.questions IS NULL",
                (*part, *H.ACTIVE_STATUSES),
            )
        )
    return found


def progress(c: "sqlite3.Connection", cards: list[Row], manager: str | None) -> dict[str, str]:
    """{card id: when it last made progress}, in a few reads for all of them (see the module's list). A card's open child
    tasks count too: the work of a card often moves on its children (a Product card on its Dev tickets)."""
    out: dict[str, str] = {}
    subjects = [*cards, *_children(c, [card["id"] for card in cards])]
    card_of = {str(row["id"]): str(row.get("parent_task") or row["id"]) for row in subjects}
    for row in subjects:
        _max(out, card_of[row["id"]], row.get("created_at"))
        if row.get("checkpoint_by_id") and row.get("checkpoint_by_id") == row.get("owner_id"):
            _max(out, card_of[row["id"]], row.get("checkpoint_at"))
    quiet = (H.KEEPER, manager or "")
    rooms = {str(row["conversation_id"]): card_of[row["id"]] for row in subjects if row.get("conversation_id")}
    for part in H.chunks(list(card_of)):
        marks = ",".join("?" * len(part))
        moves = ",".join("?" * len(MOVES))
        words = ",".join("?" * len(WORDS))
        for task_id, at in c.execute(
            f"SELECT subject_id, max(created_at) FROM history_events WHERE subject_type='task' AND subject_id IN ({marks}) "
            f"AND (kind IN ({moves}) OR (kind IN ({words}) AND coalesce(actor_id,'') NOT IN (?,?))) GROUP BY subject_id",
            (*part, *MOVES, *WORDS, *quiet),
        ):
            _max(out, card_of[task_id], at)
        for table in ("requirement_attachments", "stage_decisions"):
            if H._has_table(c, table):
                for task_id, at in c.execute(
                    f"SELECT task_id, max(created_at) FROM {table} WHERE task_id IN ({marks}) GROUP BY task_id", part
                ):
                    _max(out, card_of[task_id], at)
        for task_id, began, changed, cleared in c.execute(
            f"SELECT task_id, created_at, reason_changed_at, cleared_at FROM task_holds WHERE task_id IN ({marks}) "
            "AND kind='blocked' AND released_at IS NULL",
            part,
        ):
            for at in (began, changed, cleared):
                _max(out, card_of[task_id], at)
    for part in H.chunks(list(rooms)):
        marks = ",".join("?" * len(part))
        for room, at in c.execute(
            f"SELECT conversation_id, max(created_at) FROM messages WHERE conversation_id IN ({marks}) "
            "AND sender_id NOT IN (?,?) GROUP BY conversation_id",
            (*part, *quiet),
        ):
            _max(out, rooms[room], at)
    return out


def _covered(c: "sqlite3.Connection", candidates: list[str], stuck_now: set[str]) -> set[str]:
    """The candidate cards with an open child that is stuck on its own pipeline: that pipeline's manager already hears
    about it, so its parent is not woken too."""
    return {str(row["parent_task"]) for row in _children(c, candidates) if row["id"] in stuck_now}


def _waits_on_person(c: "sqlite3.Connection", card: Row, at: str) -> bool:
    """Whether the card waits on a named person asked under PERSON_WAIT_HOURS ago: an open question task under it or
    blocking it, owned by a person, or an undecided approval on it for a person."""
    since = H.shift(at, hours=-PERSON_WAIT_HOURS)
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    if c.execute(
        "SELECT 1 FROM task_relations r JOIN tasks q ON q.id=r.from_task WHERE r.to_task=? AND r.kind IN ('parent','blocks') "
        f"AND q.questions IS NOT NULL AND q.answer IS NULL AND q.status IN ({marks}) AND q.owner_id LIKE 'human:%' "
        # This rule's own question (asked by Tico) keeps the spell going: it is not the card's work waiting on a person.
        "AND coalesce(q.requester_id,'')<>? AND coalesce(q.asked_at,q.created_at)>=? LIMIT 1",
        (card["id"], *H.ACTIVE_STATUSES, H.KEEPER, since),
    ).fetchone():
        return True
    return bool(
        c.execute(
            "SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL AND decision_owner LIKE 'human:%' "
            "AND coalesce(ready_since,created_at)>=? LIMIT 1",
            (card["id"], since),
        ).fetchone()
    )


def _running(c: "sqlite3.Connection", card: Row) -> bool:
    """Whether the card's owner bot has a run going on it now: that is progress."""
    if not H.is_bot(card.get("owner_id") or "") or not H._has_table(c, "attempts"):
        return False
    lanes, _root = H._session_lanes(c, card)
    return bool(
        c.execute(
            "SELECT 1 FROM attempts WHERE bot_id=? AND lane IN (?,?) AND state IN ('leased','running') LIMIT 1",
            (H.actor_id(card["owner_id"]), *lanes),
        ).fetchone()
    )


def _limit(c: "sqlite3.Connection", bot: str) -> tuple[bool, str | None]:
    """Whether the bot is in a usage limit now, and when its last one ended. A limit ends on its own and Health shows it,
    so a card whose owner waits one out is not stuck, and its clock starts again when the limit lifts."""
    now = c.execute("SELECT state FROM bot_status WHERE bot=?", (bot,)).fetchone()
    lifted = c.execute(
        "SELECT max(ended_at) FROM bot_status_history WHERE bot=? AND state='limited'", (bot,)
    ).fetchone()
    return bool(now and now[0] == "limited"), (lifted[0] if lifted else None)


def _hold(card: Row) -> str | None:
    """The card's hold in words, as the board shows it, or None."""
    return card.get("blocked_reason") if card.get("status") == "waiting" else None


def stuck(c: "sqlite3.Connection", card: Row, last: str | None, at: str, after_hours: int) -> str | None:
    """When this card's stuck clock began, or None when it is not stuck now."""
    from . import task_holds

    if H.TS.snoozed(card, at) or _waits_on_person(c, card, at):
        return None
    owner = card.get("owner_id") or ""
    if H.is_bot(owner):
        limited, lifted = _limit(c, H.actor_id(owner))
        if limited:
            return None
        if lifted and (not last or lifted > last):
            last = lifted
    stranded = card.get("status") == "waiting" and (
        card.get("blocked_kind") == "stranded" or (card.get("blocked_reason") or "").startswith(task_holds.NOTHING)
    )
    if not stranded and (not last or last > H.shift(at, hours=-after_hours)):
        return None
    if _running(c, card):
        return None
    # A child that waits on a named person is a wait for the card too: the person's clock runs on the child.
    if any(_waits_on_person(c, child, at) for child in _children(c, [card["id"]])):
        return None
    return last or card.get("created_at")


def _open_spells(c: "sqlite3.Connection", pipeline_id: str) -> dict[str, Row]:
    return {
        r["task_id"]: r
        for r in H._rows(
            c.execute("SELECT * FROM stuck_spells WHERE pipeline_id=? AND ended_at IS NULL", (pipeline_id,))
        )
    }


def _end(c: "sqlite3.Connection", spell: Row, at: str, why: str) -> None:
    c.execute("UPDATE stuck_spells SET ended_at=?, end_reason=? WHERE id=?", (at, why, spell["id"]))


def _hours(since: str, at: str) -> int:
    began, now = H.parse_ts(since), H.parse_ts(at)
    return max(0, int((now - began).total_seconds() // 3600)) if began and now else 0


# The Head of Engineering's templates (backend/idea_triage.py engineering_bot). It runs on its schedule, and a stuck card
# reaches it on that sweep, so a stuck card is never a wake for it (docs/tasks.md, Stuck cards).
HEAD_OF_ENGINEERING = ("engineering-lead", "engineering-team-manager")


def hears_on_sweep(c: "sqlite3.Connection", manager: str) -> bool:
    """True when the manager is the Head of Engineering: a stuck card is left to its hourly sweep, with no wake."""
    row = c.execute("SELECT template FROM bot_settings WHERE bot_id=?", (manager,)).fetchone()
    return bool(row) and row[0] in HEAD_OF_ENGINEERING


def _wake_manager(c: "sqlite3.Connection", card: Row, manager: str, spell: Row, at: str) -> bool:
    if hears_on_sweep(c, manager):
        return False
    hold = spell.get("hold_reason") or "No hold"
    text = (
        f"Stuck: {card['title']}\nNo progress in {_hours(spell['stuck_since'], at)} h. {hold}.\n"
        "Unblock it, reassign it, split it or close it, and leave one line on the card saying which."
    )
    sent = H._wake(
        c, card, H.bot_actor(manager), text, {"wake": STEP}, trigger="schedule", source="stuck_cards.manager"
    )
    if sent is not None:
        c.execute("UPDATE stuck_spells SET manager_woken_at=? WHERE id=?", (at, spell["id"]))
    return sent is not None


def person_for(c: "sqlite3.Connection", pipeline: Row, card: Row) -> str | None:
    """Who gets the 24 h question: the pipeline's owner when a person who may read the card, else the company owner."""
    for who in (pipeline_rights.canonical(c, pipeline.get("pipeline_owner_id")), pipeline_rights.company_owner(c)):
        if who and H.is_human(who) and H.human(c, H.actor_id(who)) and H.task_private_readable(c, who, card):
            return who
    return None


def _ask_person(
    c: "sqlite3.Connection", pipeline: Row, card: Row, manager: str | None, spell: Row, at: str, days: int
) -> bool:
    """One question to the pipeline's person, due when it expires (`days`), so the weekday send-back
    (backend/still_needed.py) leaves it open as long as the expiry does."""
    person = person_for(c, pipeline, card)
    if not person:
        return False
    bot: Row = (H.bot(c, manager) or {}) if manager else {}
    who = str(bot.get("name") or manager) if manager else None
    hold = spell.get("hold_reason") or "It has no hold"
    text = (
        f"{card['title']} has not moved in {_hours(spell['stuck_since'], at)} h. {hold}."
        + (f" {who} was asked to unblock it and has not." if who and spell.get("manager_woken_at") else "")
        + " Unblock, reassign, split or close it, or say what it needs."
    )
    made = H.task_create(
        c,
        H.KEEPER,
        f"Stuck: {card['title']}"[:200],
        text,
        person,
        due=H.shift(at, days=days),
        parent_id=card["id"],
        lint=False,
        deduplicate=False,
        quiet_notice=True,
        trigger="schedule",
        trigger_source="stuck_cards.person",
    )
    QT.store(
        c,
        made["id"],
        {"questions": [{"id": QT.FIRST_ID, "header": "", "question": text, "why": "", "options": [], "multi": False}]},
    )
    c.execute(
        "UPDATE stuck_spells SET person_id=?, person_asked_at=?, question_task_id=? WHERE id=?",
        (person, at, made["id"], spell["id"]),
    )
    return True


def _asked_before(c: "sqlite3.Connection", task_id: str) -> bool:
    found = c.execute(
        "SELECT 1 FROM stuck_spells WHERE task_id=? AND question_task_id IS NOT NULL LIMIT 1", (task_id,)
    ).fetchone()
    return found is not None


def _relay(c: "sqlite3.Connection", at: str, done: dict[str, list[str]]) -> None:
    """A stuck question that was answered or expired: its pipeline's manager hears the answer once, on the card."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    for spell in H._rows(
        c.execute(
            "SELECT s.*, q.status AS question_status FROM stuck_spells s JOIN tasks q ON q.id=s.question_task_id "
            f"WHERE s.answer_relayed_at IS NULL AND q.status NOT IN ({marks})",
            H.ACTIVE_STATUSES,
        )
    ):
        c.execute("UPDATE stuck_spells SET answer_relayed_at=? WHERE id=?", (at, spell["id"]))
        card: Row | None = H.task(c, spell["task_id"])
        question: Row | None = H.task(c, spell["question_task_id"])
        pipeline = c.execute("SELECT id, manager FROM task_types WHERE id=?", (spell["pipeline_id"],)).fetchone()
        manager = PM.manager_bot(c, dict(pipeline)) if pipeline else None
        if not card or card["status"] not in H.ACTIVE_STATUSES or not manager:
            continue
        if hears_on_sweep(c, manager):
            continue  # the answer is on the question task, and the Head of Engineering reads it on its sweep
        answer: Row = (QT.fields(question).get("answer") or {}) if question else {}
        said = str(answer.get("text") or (question or {}).get("note") or "").strip()
        name = H.person_name(c, H.actor_id(spell["person_id"])) if spell.get("person_id") else "The person"
        if not answer or answer.get("by") == H.KEEPER:
            text = f"Stuck: {card['title']}\nThe question to {name} about this card closed with no answer. Decide it yourself."
        else:
            text = f"Stuck: {card['title']}\n{name} answered: {said or spell['question_status']}. Act on it."
        if H._wake(
            c, card, H.bot_actor(manager), text, {"wake": STEP}, trigger="schedule", source="stuck_cards.answer"
        ):
            done["relayed"].append(card["id"])


def sweep(c: "sqlite3.Connection", when: str | None = None) -> dict[str, list[str]]:
    """One pass over every pipeline with a manager. Returns {"woken", "asked", "relayed", "ended"}: card ids."""
    at: str = when or H.now()
    done: dict[str, list[str]] = {"woken": [], "asked": [], "relayed": [], "ended": []}
    if not H._has_table(c, "stuck_spells"):
        return done
    # First every pipeline's stuck cards, then what to do: a card whose open child is stuck on its own pipeline is left
    # to that pipeline's manager, whichever pipeline this pass reads first.
    plans: list[tuple[Row, dict[str, int], str | None, list[Row], dict[str, Row], dict[str, str | None]]] = []
    for pipeline in _pipelines(c):
        rule = settings(pipeline)
        manager = PM.manager_bot(c, pipeline)
        cards = _cards(c, pipeline["id"])
        live = {card["id"] for card in cards}
        spells = _open_spells(c, pipeline["id"])
        for task_id, spell in spells.items():
            if task_id not in live:
                _end(c, spell, at, "left")  # finished, closed, On ice or moved to another pipeline
                done["ended"].append(task_id)
        last = progress(c, cards, H.bot_actor(manager) if manager else None)
        found = {card["id"]: stuck(c, card, last.get(card["id"]), at, rule["stuck_after_hours"]) for card in cards}
        plans.append((pipeline, rule, manager, cards, spells, found))
    stuck_now = {task_id for plan in plans for task_id, since in plan[5].items() if since}
    covered = _covered(c, sorted(stuck_now), stuck_now)
    for pipeline, rule, manager, cards, spells, found in plans:
        woken = asked = 0
        for card in cards:
            spell = spells.get(card["id"])
            since = None if card["id"] in covered else found[card["id"]]
            if spell and (since is None or since > spell["stuck_since"]):
                reason = "a subtask is stuck" if card["id"] in covered else "moved"
                _end(c, spell, at, reason if since is None else "moved, then stopped again")
                done["ended"].append(card["id"])
                spell = None
            if since is None:
                continue
            if spell is None:
                spell = {
                    "id": H.new_id(),
                    "task_id": card["id"],
                    "pipeline_id": pipeline["id"],
                    "stuck_since": since,
                    "started_at": at,
                    "hold_reason": _hold(card),
                    "manager_id": manager,
                    "manager_woken_at": None,
                }
                c.execute(
                    "INSERT INTO stuck_spells (id, task_id, pipeline_id, stuck_since, started_at, hold_reason, manager_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    tuple(
                        spell[k]
                        for k in (
                            "id",
                            "task_id",
                            "pipeline_id",
                            "stuck_since",
                            "started_at",
                            "hold_reason",
                            "manager_id",
                        )
                    ),
                )
            # A manager in a usage limit is woken on a later pass, once it can act: a wake queued behind the limit is
            # out of date by the time it runs.
            if manager and not spell.get("manager_woken_at") and woken < WAKES_PER_PASS and not _limit(c, manager)[0]:
                if _wake_manager(c, card, manager, spell, at):
                    spell["manager_woken_at"] = at
                    woken += 1
                    done["woken"].append(card["id"])
            # The person hears at ask_person_after_hours with no progress, and never before the manager had the hours
            # between the two (a card already stuck for days when this ships reaches its manager first).
            grace = max(0, rule["ask_person_after_hours"] - rule["stuck_after_hours"])
            due = max(
                H.shift(spell["stuck_since"], hours=rule["ask_person_after_hours"]),
                H.shift(spell["started_at"], hours=grace),
            )
            if due <= at and asked < WAKES_PER_PASS and not _asked_before(c, card["id"]):
                if _ask_person(c, pipeline, card, manager, spell, at, rule["question_expiry_days"]):
                    asked += 1
                    done["asked"].append(card["id"])
    _relay(c, at, done)
    return done
