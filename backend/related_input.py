# pyright: strict
"""Related input: a requirement kind, `related_input` (docs/pipelines-v3.md, "Related input").

Jev reads a task's pitch and spec page against the company's bots (name, job, team) and scores how much the work
touches each bot's area, 0 to 1. The top MOST (2) at or over the bar (0.8) are each woken once on the task: "comment
from your area, one round". The requirement is met when each picked bot has commented, when none cleared the bar,
or when `wait_hours` (4) have passed since the task entered the stage. It never blocks: a timeout is met, with a note.

    standing(conn, task, requirement)   where it stands, from tables only (task_requirements.KINDS)
    candidates(conn, task)              the bots Jev is asked about
    run(store, now) -> int              the Heartbeat step: owe, score, ask, and close what is met

Never candidates: the task's owner and assignee, the pipeline's manager, every bot that holds one of the pipeline's
stages (its fixed owner, or one it picks from: the reviewers and QA), the General Manager and the helpers, a bot that
cannot run, and on a private task a bot that cannot read it already. The engine never adds a reader (task_wakes.add),
so a private task asks only bots who can see it.

The requirement waits on Jev (waiting_on state `jev`) while Jev scores and while the picked bots comment: nobody is
chased and no ladder runs. Each step is a row in `related_inputs`, one per visit and requirement. Jev writes no prose,
so each score's "why" is the bot's own job line. The picks and their scores are one line in the task's history, and how
it ended is another.

Settings on the requirement (task_requirements.SETTINGS): `bar` (0 to 1, default 0.8), `most` (1 to 5, default 2) and
`wait_hours` (more than 0, up to 72, default 4).
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, cast

from clients import judge as J_

from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger("tico.related_input")
J: Any = J_

KIND = "related_input"
QUESTION_SET = "related-bots"
BAR, MOST, WAIT_HOURS = 0.8, 2, 4.0
MOST_AT_MOST, WAIT_HOURS_AT_MOST = 5, 72
BATCH = 5  # visits scored in one beat at most: each is a model call
TRIES = 3  # failed calls before the requirement is met without picks
RETRY_S = (60, 300, 900)
CALL_S = 90
PITCH_CHARS, SPEC_CHARS, JOB_CHARS, WHY_CHARS = 2_000, 12_000, 300, 120
SHOWN = 5  # scores the history line keeps, highest first
HELPERS = ("bot:botops", "bot:strategy-owner")  # they work for people; they are not anyone's area
PICKED, NONE, FAILED = "picked", "none", "failed"
# The engine a test sets: (state, questions, label) -> {"answers": {...}, ...}. None asks the server's Jev.
ENGINE: "Callable[[Row, Row, str], Row] | None" = None
ASK = (
    'Related input: Jev picked you for "{title}" ({score}): it touches your area. Read the task and its spec page, '
    "then comment on the task once from your area: what it changes for you, risks, and anything to add or change. "
    'tico task comment {task} "<your comment>". One round only: don\'t move, take or edit the task.'
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def settings_of(requirement: Row) -> tuple[float, int, float]:
    """(bar, most, wait_hours) of a requirement, each its default when unset or out of range."""
    found: Row = requirement.get("settings") or {}
    bar, most, wait = found.get("bar"), found.get("most"), found.get("wait_hours")
    ok_bar = isinstance(bar, (int, float)) and not isinstance(bar, bool) and 0 < bar <= 1
    ok_most = isinstance(most, int) and not isinstance(most, bool) and 1 <= most <= MOST_AT_MOST
    ok_wait = isinstance(wait, (int, float)) and not isinstance(wait, bool) and 0 < wait <= WAIT_HOURS_AT_MOST
    return (
        float(cast("float", bar)) if ok_bar else BAR,
        int(cast("int", most)) if ok_most else MOST,
        float(cast("float", wait)) if ok_wait else WAIT_HOURS,
    )


def check(settings: Row, label: str) -> list[str]:
    """What is wrong with a related_input requirement's settings (pipeline_settings), one line each."""
    out: list[str] = []
    bar, most, wait = settings.get("bar"), settings.get("most"), settings.get("wait_hours")
    if "bar" in settings and (not isinstance(bar, (int, float)) or isinstance(bar, bool) or not 0 < bar <= 1):
        out.append(f"The bar on {label} is a number over 0, up to 1.")
    if "most" in settings and (not isinstance(most, int) or isinstance(most, bool) or not 1 <= most <= MOST_AT_MOST):
        out.append(f"Most asked on {label} is a whole number from 1 to {MOST_AT_MOST}.")
    if "wait_hours" in settings and (
        not isinstance(wait, (int, float)) or isinstance(wait, bool) or not 0 < wait <= WAIT_HOURS_AT_MOST
    ):
        out.append(f"Wait on {label} is a number of hours over 0, up to {WAIT_HOURS_AT_MOST}.")
    return out


# ----------------------------------------------------------------------------- reading
def _visit_id(task: Row) -> str:
    visit: Row = task.get("visit") or {}
    return str(visit.get("id") or "")


def _row(conn: "sqlite3.Connection", visit_id: str, requirement_id: str) -> Row | None:
    cursor = conn.execute(
        "SELECT * FROM related_inputs WHERE visit_id=? AND requirement_id=?", (visit_id, requirement_id)
    )
    found = cursor.fetchone()
    if not found:
        return None
    row = dict(zip([d[0] for d in cursor.description], found, strict=True))
    for key in ("scores", "picks"):
        row[key] = json.loads(row[key]) if row.get(key) else []
    return row


def state_of(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """The newest related input row of a task, for the task page and tests."""
    found = conn.execute(
        "SELECT visit_id, requirement_id FROM related_inputs WHERE task_id=? ORDER BY owed_at DESC, rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return _row(conn, str(found[0]), str(found[1])) if found else None


def commented(conn: "sqlite3.Connection", task_id: str, bots: list[str], since: str) -> set[str]:
    """Which of `bots` wrote a comment on the task at `since` or later (one not deleted)."""
    if not bots:
        return set()
    rows = conn.execute(
        "SELECT DISTINCT sender_id FROM messages WHERE sender_id IN (SELECT value FROM json_each(?)) "
        "AND created_at >= ? AND deleted_at IS NULL AND json_extract(refs, '$.task') = ?",
        (json.dumps(bots), since, task_id),
    ).fetchall()
    return {str(found[0]) for found in rows}


def _names(conn: "sqlite3.Connection", bots: list[str]) -> str:
    H = _h()
    names = [str(H.actor_name(conn, bot)) for bot in bots]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _hours(wait: float) -> str:
    whole = int(wait) if float(wait).is_integer() else wait
    return f"{whole} hour" if whole == 1 else f"{whole} hours"


def standing(conn: "sqlite3.Connection", task: Row, requirement: Row, answer: Row | None = None) -> "R.Standing":
    """Where a related_input requirement stands for the task's open visit. Reads tables and the clock only."""
    H = _h()
    visit: Row = task["visit"]
    bar, _most, wait = settings_of(requirement)
    until = str(H.shift(visit["entered_at"], hours=wait))
    over = str(H.now()) >= until
    row = _row(conn, str(visit["id"]), str(requirement["id"]))
    if not row or not row.get("scored_at"):
        if over:
            return R.Standing(requirement, R.MET, line=f"{_hours(wait)} passed before Jev picked anyone")
        return R.Standing(requirement, R.WAITING, R.JEV, "Jev is picking related bots")
    if row["outcome"] == FAILED:
        return R.Standing(requirement, R.MET, line="Jev couldn't pick related bots, so nobody was asked")
    picks: list[str] = [str(bot) for bot in row["picks"]]
    if not picks:
        return R.Standing(requirement, R.MET, line=str(row.get("done_line") or f"No bot cleared the bar ({bar:g})"))
    done = commented(conn, str(task["id"]), picks, str(row.get("asked_at") or row["scored_at"]))
    left = [bot for bot in picks if bot not in done]
    if not left:
        return R.Standing(requirement, R.MET, line=f"Comments from {_names(conn, picks)}")
    if over:
        return R.Standing(requirement, R.MET, line=f"{_hours(wait)} passed: no comment from {_names(conn, left)}")
    return R.Standing(
        requirement, R.WAITING, R.JEV, f"Waiting on comments from {_names(conn, left)}", since=row.get("asked_at")
    )


def asked(conn: "sqlite3.Connection", task: Row, bot: str) -> bool:
    """Whether `bot` was picked on the task's open visit and the stage still waits on its comment: its one wake may
    start a run though it has no turn (task_wakes._may_start)."""
    visit: Row = task.get("visit") or {}
    for (found,) in conn.execute(
        "SELECT picks FROM related_inputs WHERE visit_id=? AND done_at IS NULL AND picks IS NOT NULL",
        (visit.get("id"),),
    ).fetchall():
        if bot in json.loads(found or "[]"):
            return True
    return False


# ----------------------------------------------------------------------------- candidates
def _held(conn: "sqlite3.Connection", pipeline_id: str) -> set[str]:
    """Every bot that holds a stage of the pipeline: its fixed owner, or one it picks from (reviewers, QA)."""
    from . import stage_pick
    from . import task_engine as E

    H = _h()
    out: set[str] = set()
    for stage in E.stages(conn, pipeline_id):
        if stage.get("kind") != "work":
            continue
        pick = stage_pick.config(stage)
        if pick:
            out.update(pick.bots)
        elif H.is_bot(stage.get("owner")):
            out.add(str(stage["owner"]))
    return out


def _teams(conn: "sqlite3.Connection") -> dict[str, str]:
    """{bot id: its team's name} for bots in a team."""
    from . import people
    from . import views

    P: Any = people
    V: Any = views
    try:
        return cast("dict[str, str]", P.bot_departments(V.entries(conn, shared=True), V.roster(conn, shared=True)))
    except Exception as exc:  # noqa: BLE001 - a team name is a hint for Jev; without it the bots are still asked about
        LOG.warning("Teams could not be read for related input: %s", type(exc).__name__)
        return {}


def candidates(conn: "sqlite3.Connection", task: Row) -> list[Row]:
    """The bots Jev is asked about, by slug: [{actor, name, job, team}]."""
    from . import task_engine as E
    from . import task_wakes
    from .statuses import PARKED_SQL

    H = _h()
    pipeline: Row = task.get("pipeline") or E.pipeline(conn, str(task.get("pipeline_id") or "")) or {}
    skip = {str(task.get("owner_id") or ""), str(task.get("assignee_id") or ""), str(pipeline.get("manager_id") or "")}
    skip |= _held(conn, str(task.get("pipeline_id") or ""))
    skip |= {H.GENERAL_MANAGER, *HELPERS}
    teams = _teams(conn)
    seen: dict[str, str | None] = {}
    out: list[Row] = []
    for bot_id, slug, name, job in conn.execute(
        "SELECT b.id, b.slug, b.display_name, COALESCE(bc.description, '') FROM bots b "
        "LEFT JOIN bot_config bc ON bc.bot = b.id WHERE b.state='active' "
        f"AND COALESCE(bc.onboarding_state, '') NOT IN {PARKED_SQL} ORDER BY b.slug, b.id"
    ).fetchall():
        actor = str(H.bot_actor(bot_id))
        if actor in skip or task_wakes.cannot_run(conn, actor, seen):
            continue
        if not H.task_private_readable(conn, actor, task):
            continue  # the engine never adds a reader: a private task asks only bots who can see it
        out.append(
            {
                "actor": actor,
                "name": str(name or slug or bot_id),
                "job": " ".join(str(job or "").split())[:JOB_CHARS],
                "team": str(teams.get(str(bot_id)) or ""),
            }
        )
    return out


def _spec_text(conn: "sqlite3.Connection", task: Row) -> tuple[str, bool]:
    """The text of the newest page attached to the task in an earlier stage of its pipeline (the Spec page), and
    whether it was cut. ("", False) when there is none or it has no text Tico reads."""
    from . import pipeline_judges as PJ

    stage: Row = task.get("stage") or {}
    found = conn.execute(
        "SELECT p.file_id, p.version FROM requirement_pages p JOIN visits v ON v.id = p.visit_id "
        "JOIN stages s ON s.id = v.stage_id WHERE v.task_id=? AND p.replaced_at IS NULL AND s.pipeline_id=? "
        "AND s.position < ? ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1",
        (task["id"], task.get("pipeline_id"), int(stage.get("position") or 0)),
    ).fetchone()
    if not found:
        return "", False
    version = conn.execute(
        "SELECT mime, digest, size FROM bot_file_versions WHERE file_id=? AND version=?", (found[0], int(found[1]))
    ).fetchone()
    if not version:
        return "", False
    text, cut = PJ.text_of(conn, {"mime": version[0], "blob_digest": version[1], "blob_size": version[2]})
    text = text or ""
    return text[:SPEC_CHARS], bool(cut) or len(text) > SPEC_CHARS


def _profile(bot: Row) -> str:
    said = bot["name"]
    if bot["job"]:
        said += f": {bot['job']}"
    if bot["team"]:
        said += f" (team: {bot['team']})"
    return said


def questions_for(qset: Row, bots: list[Row]) -> Row:
    """One noul per bot, `rel_<n>`: the set's `related` question, finished with that bot's name, job and team."""
    base: Row = qset["questions"]["related"]
    instructions: Row = dict(base["instructions"])
    out: Row = {}
    for n, bot in enumerate(bots):
        asked = {**instructions, "question": f"{instructions['question']}`bots.b{n}`, {_profile(bot)}"}
        out[f"rel_{n}"] = {**base, "instructions": asked}
    return out


def _state(task: Row, spec: str, cut: bool, bots: list[Row]) -> Row:
    work: Row = {
        "title": " ".join(str(task.get("title") or "").split()),
        "pitch": str(task.get("body") or "")[:PITCH_CHARS],
        "spec": spec,
    }
    if cut:
        work["spec_truncated"] = True
    return {"work": work, "bots": {f"b{n}": _profile(bot) for n, bot in enumerate(bots)}}


def pick(scores: list[Row], bar: float, most: int) -> list[Row]:
    """The top `most` scores at `bar` or over, highest first (a tie keeps the bot list's order)."""
    ranked = sorted(scores, key=lambda s: -float(s["score"]))
    return [s for s in ranked if float(s["score"]) >= bar][:most]


# ----------------------------------------------------------------------------- the pass
# Open visits to a stage with a related_input requirement that have no row yet: owed now.
_NEW = (
    "SELECT v.id, r.id, v.task_id FROM visits v JOIN tasks t ON t.id = v.task_id AND t.status='open' "
    "JOIN requirements r ON r.kind='related_input' AND r.withdrawn_at IS NULL "
    "AND (r.stage_id = v.stage_id OR r.visit_id = v.id) "
    "WHERE v.left_at IS NULL AND NOT EXISTS (SELECT 1 FROM related_inputs x WHERE x.visit_id = v.id "
    "AND x.requirement_id = r.id) ORDER BY v.entered_at, v.id LIMIT ?"
)
# Rows owed a score, due now, on a visit still open.
_OWED = (
    "SELECT x.visit_id, x.requirement_id, x.task_id FROM related_inputs x JOIN visits v ON v.id = x.visit_id "
    "AND v.left_at IS NULL WHERE x.scored_at IS NULL AND x.done_at IS NULL "
    "AND (x.next_try_at IS NULL OR x.next_try_at <= ?) ORDER BY x.owed_at, x.rowid LIMIT ?"
)
# Rows not yet closed, on a visit still open: each is looked at for being met.
_OPEN = (
    "SELECT x.visit_id, x.requirement_id, x.task_id FROM related_inputs x JOIN visits v ON v.id = x.visit_id "
    "AND v.left_at IS NULL WHERE x.done_at IS NULL ORDER BY x.owed_at, x.rowid"
)


def _engine(store: Any) -> "Callable[[Row, Row, str], Row] | None":
    if ENGINE is not None:
        return ENGINE
    from . import judge

    JM: Any = judge
    with store.read() as c:
        return JM.server_engine(store, c, JM.primary_engine(store.settings, store))


def _ask(
    store: Any, calls: list[tuple[list[Row], Row, Row]], label: str
) -> tuple[dict[str, float] | None, str | None, Row]:
    """Every call for one task, within CALL_S each: ({actor: score}, None, detail), or (None, why it failed, detail)."""
    asks = _engine(store)
    detail: Row = {"label": label, "calls": len(calls), "bots": sum(len(chunk) for chunk, _q, _s in calls)}
    if asks is None:
        return None, "unconfigured", detail
    scores: dict[str, float] = {}
    cost = 0.0
    try:
        for chunk, questions, state in calls:
            with J.within(CALL_S):
                result: Row = asks(state, questions, label)
            answers = J.check_answers(result.get("answers"), questions)
            cost += float(result.get("cost_usd") or 0)
            for n, bot in enumerate(chunk):
                scores[str(bot["actor"])] = round(min(1.0, max(0.0, float(J.noul(answers, f"rel_{n}")))), 3)
            from . import judge

            JM: Any = judge
            detail.update(JM.audit_detail(result))
    except J.JudgeError as exc:
        return None, str(J.error_code(exc)), detail
    except Exception as exc:  # noqa: BLE001 - a fake, a provider bug: tried again, and the stage never blocks
        LOG.warning("The related input check failed: %s", type(exc).__name__)
        return None, "error", detail
    detail["cost_usd"] = round(cost, 6)
    return scores, None, detail


def _calls(qset: Row, task: Row, spec: str, cut: bool, bots: list[Row]) -> list[tuple[list[Row], Row, Row]]:
    """[(bots, questions, state)]: every bot in one call, unless they do not fit beside the work in one (J.pack)."""

    def build(chunk: list[Row]) -> tuple[Row, Row]:
        return questions_for(qset, chunk), _state(task, spec, cut, chunk)

    packed: Any = J.pack(bots, build)
    if packed:
        return [(list(chunk), questions, state) for chunk, questions, state in packed]
    return [(bots, *build(bots))]


def run(store: Any, now: str | None = None) -> int:
    """The Heartbeat step: owe a row for each new visit to a related input stage, score up to BATCH owed ones (one Jev
    call each, with no transaction held), ask the picks, and close each row whose requirement is met, nudging its task
    on. Returns how many visits were scored or closed."""
    from . import task_engine as E

    H = _h()
    at = str(now or H.now())
    with store.transaction() as c:
        for visit_id, requirement_id, task_id in c.execute(_NEW, (BATCH * 4,)).fetchall():
            c.execute(
                "INSERT OR IGNORE INTO related_inputs (visit_id, requirement_id, task_id, owed_at) VALUES (?,?,?,?)",
                (visit_id, requirement_id, task_id, at),
            )
    jobs: list[tuple[Row, Row, list[Row], str, bool]] = []
    with store.read() as c:
        for visit_id, requirement_id, task_id in c.execute(_OWED, (at, BATCH)).fetchall():
            task = E.task(c, str(task_id))
            requirement = R.get(c, str(requirement_id))
            if not task or not requirement or _visit_id(task) != visit_id:
                continue
            if standing(c, task, requirement).met:
                continue  # its time ran out before Jev answered: closed below, and nobody is asked late
            spec, cut = _spec_text(c, task)
            jobs.append(
                ({"visit_id": visit_id, "requirement_id": requirement_id}, task, candidates(c, task), spec, cut)
            )
    done = 0
    qset: Row | None = None
    for key, task, bots, spec, cut in jobs:
        scores: dict[str, float] | None = {}
        why: str | None = None
        detail: Row = {}
        if bots:
            qset = qset or cast("Row", J.load_set(QUESTION_SET))
            scores, why, detail = _ask(store, _calls(qset, task, spec, cut, bots), str(qset["label"]))
        if why:
            detail["error"] = why
        with store.transaction() as c:
            call_id = str(H.event(c, H.KEEPER, "judge.call", detail.get("label"), detail)["id"]) if bots else None
            if scores is None:
                _failed(c, key, why or "error", at)
            else:
                _scored(c, key, task, bots, scores, at, call_id)
                done += 1
    with store.transaction() as c:
        for visit_id, requirement_id, task_id in c.execute(_OPEN).fetchall():
            done += _close(c, str(visit_id), str(requirement_id), str(task_id), at)
    return done


def _failed(conn: "sqlite3.Connection", key: Row, why: str, at: str) -> None:
    """A call that failed: tried again RETRY_S later, and after TRIES the row is scored as failed (met, nobody asked)."""
    H = _h()
    row = _row(conn, key["visit_id"], key["requirement_id"])
    if not row or row.get("scored_at"):
        return
    tries = int(row["tries"]) + 1
    if tries >= TRIES:
        conn.execute(
            "UPDATE related_inputs SET tries=?, last_error=?, scored_at=?, outcome=?, next_try_at=NULL "
            "WHERE visit_id=? AND requirement_id=?",
            (tries, why, at, FAILED, key["visit_id"], key["requirement_id"]),
        )
        return
    later = H.shift(at, seconds=RETRY_S[min(tries, len(RETRY_S)) - 1])
    conn.execute(
        "UPDATE related_inputs SET tries=?, last_error=?, next_try_at=? WHERE visit_id=? AND requirement_id=?",
        (tries, why, later, key["visit_id"], key["requirement_id"]),
    )


def _scored(
    conn: "sqlite3.Connection",
    key: Row,
    task: Row,
    bots: list[Row],
    scores: dict[str, float],
    at: str,
    call_id: str | None,
) -> None:
    """Record Jev's scores and the picks, write the history line, and wake each pick once on the task."""
    from . import task_engine as E
    from . import task_wakes

    H = _h()
    row = _row(conn, key["visit_id"], key["requirement_id"])
    now_task = E.task(conn, str(task["id"]))
    requirement = R.get(conn, key["requirement_id"])
    if not row or row.get("scored_at") or not now_task or not requirement:
        return
    if _visit_id(now_task) != key["visit_id"] or now_task["status"] != "open":
        return
    bar, most, _wait = settings_of(requirement)
    ranked: list[Row] = [
        {
            "bot": bot["actor"],
            "name": bot["name"],
            "score": scores.get(bot["actor"], 0.0),
            "why": bot["job"][:WHY_CHARS] or (f"team: {bot['team']}" if bot["team"] else ""),
        }
        for bot in bots
    ]
    ranked.sort(key=lambda s: -float(s["score"]))
    picks = pick(ranked, bar, most)
    actors = [str(s["bot"]) for s in picks]
    if not bots:
        line = (
            "No other bot can read this private task, so nobody was asked"
            if H.task_private(conn, now_task)
            else "No other bot to ask"
        )
    elif not picks:
        top = ranked[0]
        line = f"No bot cleared the bar ({bar:g}); the closest was {top['name']} ({float(top['score']):.2f})"
    else:
        line = None
    conn.execute(
        "UPDATE related_inputs SET scored_at=?, outcome=?, scores=?, picks=?, asked_at=?, call_id=?, tries=tries+1, "
        "next_try_at=NULL, done_line=? WHERE visit_id=? AND requirement_id=?",
        (
            at,
            PICKED if picks else NONE,
            json.dumps(ranked),
            json.dumps(actors),
            at if picks else None,
            call_id,
            line,
            key["visit_id"],
            key["requirement_id"],
        ),
    )
    said = "; ".join(f"{s['name']} {float(s['score']):.2f}" for s in ranked[:SHOWN])
    named = ", ".join(f"{s['name']} ({float(s['score']):.2f})" for s in picks)
    note = f"Related input: Jev picked {named}" if picks else f"Related input: {line}"
    data: Row = {"requirement": key["requirement_id"], "picks": actors, "bar": bar, "scores": ranked[:SHOWN]}
    E._event(conn, str(task["id"]), E.SYSTEM, "related_input", None, PICKED if picks else NONE, note, data)  # pyright: ignore[reportPrivateUsage]
    LOG.info("related input for task %s: %s", task["id"], said or "no bots")
    visit: Row = now_task["visit"]
    for score in picks:
        text = ASK.format(
            title=now_task.get("title") or "this task", score=f"{float(score['score']):.2f}", task=task["id"]
        )
        task_wakes.add(
            conn,
            now_task,
            str(score["bot"]),
            visit,
            text,
            wake_key=f"{task['id']}:{visit['id']}:{task_wakes.RELATED}:{H.actor_id(score['bot'])}",
        )


def _close(conn: "sqlite3.Connection", visit_id: str, requirement_id: str, task_id: str, at: str) -> int:
    """A row whose requirement is met is closed with its line in the task's history, once, and the task is nudged on.
    Returns 1 when it closed."""
    from . import task_engine as E

    task = E.task(conn, task_id)
    requirement = R.get(conn, requirement_id)
    if not task or not requirement or _visit_id(task) != visit_id or task["status"] != "open":
        return 0
    found = standing(conn, task, requirement)
    if not found.met:
        return 0
    conn.execute(
        "UPDATE related_inputs SET done_at=?, done_line=? WHERE visit_id=? AND requirement_id=? AND done_at IS NULL",
        (at, found.line, visit_id, requirement_id),
    )
    row = _row(conn, visit_id, requirement_id) or {}
    if row.get("outcome") != NONE:  # a stage that asked nobody said so when Jev scored
        note, data = f"Related input: {found.line}", {"requirement": requirement_id}
        E._event(conn, task_id, E.SYSTEM, "related_input", None, "done", note, data)  # pyright: ignore[reportPrivateUsage]
    if E.advance(conn, task_id) is None:
        E.changed(conn, task_id, E.SYSTEM)
    return 1
