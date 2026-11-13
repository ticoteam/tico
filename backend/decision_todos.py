# pyright: strict
"""The release sweep of decisions filed as to-dos (Tasks spec "Bots ask people a question with choices", AC-11).

Since that release a bot's to-do for a person that is really a decision is refused, and the bot asks it as a question
with its choices (backend/ask_ingest.py, reason `decision`). The to-dos filed before it stay on people's lists as "Say
when the work is done". Once per team, each one still open is judged by the same ask check (`ask_ingest.preflight`,
one call each, no new question): a decision is closed with a note, and the bot that filed it is woken to ask it on its
own task. A chore, a status, anything the check is not sure of, and every to-do the check could not judge (Decide off,
no model, the cap) stays as it is.

What is swept: open to-dos (the general pipeline's tasks, no product pipeline's) a bot filed for a person on the roster, made before the sweep first ran,
except the ones Tico itself files for the Strategy Owner (backend/goal_asks.py), which the server keeps.
Each judged to-do is one `decision_todos.judged` event (its id and outcome, never its text), so a restart goes on
where it stopped and asks nothing twice. A failed call is left for the next pass; once nothing is left, the sweep is
marked done in `registry_metadata` and never runs again.
"""

import json
import logging
import sqlite3
import threading
from typing import Any, cast

from . import ask_ingest as _ask_ingest
from . import hubdb as _hubdb

H: Any = _hubdb
A: Any = _ask_ingest
LOG = logging.getLogger("tico.decision_todos")

MARK = "release.decision_todos"  # registry_metadata: {"since": when it first ran, "done": when nothing was left}
JUDGED = "decision_todos.judged"  # one event per judged to-do
PER_PASS = 20  # calls one pass makes at most
NOTE = (
    "Closed by Tico: this is a decision for {person}, so {bot} asks it as a question on its own task, with why it "
    "matters, its choices and its recommendation."
)
TOLD = (
    "Your to-do “{title}” for {person} was a decision, so Tico closed it. Ask it as a question on your own "
    "task: tico task ask <task> --to {handle}, with why it matters, 2 to 4 choices each saying what happens, and your "
    "recommendation."
)


class _Bot:
    """The filing bot, as the ask check reads a writer (`ask_ingest.gated`)."""

    def __init__(self, actor: str) -> None:
        self.actor, self.role, self.via = actor, "bot", None


def state(c: Any) -> dict[str, Any]:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (MARK,)).fetchone()
    try:
        found: Any = json.loads(row[0]) if row else {}
    except ValueError:
        found = {}
    return cast("dict[str, Any]", found) if isinstance(found, dict) else {}


def _save(c: Any, value: dict[str, Any]) -> None:
    c.execute("INSERT OR REPLACE INTO registry_metadata(key, value) VALUES(?,?)", (MARK, json.dumps(value)))


def candidates(c: Any, since: str) -> list[dict[str, Any]]:
    """The open to-dos a bot filed for a person before `since` that no pass has judged, oldest first."""
    rows = c.execute(
        "SELECT t.id, t.title, t.body, t.requester_id, t.owner_id FROM tasks t "
        "WHERE t.status='open' AND t.pipeline_id=? AND t.requester_id LIKE 'bot:%' AND t.owner_id LIKE 'human:%' "
        "AND t.created_at<? "
        "AND NOT EXISTS (SELECT 1 FROM goal_asks g WHERE g.task_id=t.id) "
        "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.action=? AND e.target=t.id) "
        "ORDER BY t.created_at, t.id",
        (H.GENERAL_PIPELINE, since, JUDGED),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        task = {k: row[k] for k in row.keys()}
        if H.human(c, H.actor_id(task["owner_id"])):
            out.append(task)
    return out


def _judged(c: Any, task_id: str, outcome: str) -> None:
    H.event(c, H.KEEPER, JUDGED, task_id, {"outcome": outcome})


def _close(c: Any, task: dict[str, Any]) -> bool:
    """Close one decision to-do with the note, and wake the bot that filed it to ask it. False when it changed since."""
    row: Any = H.task(c, task["id"])
    if not row or row["status"] != "open" or row["owner_id"] != task["owner_id"]:
        return False
    person = H.person_name(c, H.actor_id(task["owner_id"])) or H.actor_id(task["owner_id"])
    bot = H.actor_name(c, task["requester_id"]) or H.actor_id(task["requester_id"])
    H.task_close(c, H.KEEPER, task["id"], NOTE.format(person=person, bot=bot), row.get("version"))
    told = TOLD.format(title=task["title"], person=person, handle=H.actor_id(task["owner_id"]))
    H.say(c, H.KEEPER, task["requester_id"], told, kind="notice", refs={"task": task["id"], "needs_action": True})
    return True


def sweep(store: Any, primary: Any = None, limit: int = PER_PASS) -> dict[str, Any]:
    """One pass: judge up to `limit` to-dos and close the decisions. Returns {judged, closed, left, done}. Never asks
    after the sweep is done."""
    with store.transaction() as c:
        found = state(c)
        if found.get("done"):
            return {"judged": 0, "closed": [], "left": 0, "done": True}
        if not found.get("since"):
            found["since"] = H.now()
            _save(c, found)
        todo = candidates(c, found["since"])
    judged, failed = 0, 0
    closed: list[str] = []
    for task in todo[:limit]:
        verdict: Any = A.preflight(
            store, primary, _Bot(task["requester_id"]), task["owner_id"], task["title"], task["body"] or ""
        )
        if verdict and verdict.get("error"):
            failed += 1  # not judged: the next pass asks again
            continue
        answers: Any = verdict.get("answers") if verdict else None
        kind: str | None = str(answers.get("kind")) if answers and answers.get("kind") else None
        with store.transaction() as c:
            outcome = "decision" if kind == A.DECISION else str(kind or "not_judged")
            if kind == A.DECISION:
                if _close(c, task):
                    closed.append(task["id"])
                else:
                    outcome = "changed"
            _judged(c, task["id"], outcome)
        judged += 1
    left = len(todo) - judged
    done = left == 0
    if done:
        with store.transaction() as c:
            _save(c, {**state(c), "done": H.now()})
    return {"judged": judged, "closed": closed, "left": left, "done": done, "failed": failed}


_RUNNING = threading.Lock()


def tick(store: Any, primary: Any = None) -> bool:
    """From the scheduler's pass: start one sweep pass on a thread of its own unless one is running or the sweep is
    done. Off in rehearsal and on test servers (tests call `sweep`)."""
    settings = store.settings
    if settings.rehearsal or settings.test_identities or store.__dict__.get("decision_todos_done"):
        return False
    with store.read() as c:
        if state(c).get("done"):
            store.__dict__["decision_todos_done"] = True
            return False
    if not _RUNNING.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            if sweep(store, primary)["done"]:
                store.__dict__["decision_todos_done"] = True
        except sqlite3.Error as exc:  # the next pass tries again; the ask check itself never raises (preflight)
            LOG.warning("Decision to-do sweep failed: %s", type(exc).__name__)
        finally:
            _RUNNING.release()

    threading.Thread(target=run, name="decision-todos", daemon=True).start()
    return True
