# pyright: strict
"""A bot's wake about a task on the engine (docs/pipelines-v3.md, "Effects travel with the move").

A wake is a row in `messages` with a `wake_key`: `<task id>:<visit id>:<level>:<try>`. The key is unique, so writing the
same wake twice is one wake. It is written in the same transaction as the move that causes it. The body says what is
missing and the five ways a run may end. A bot has at most one wake queued per task: a second one carries the first's
words before its own, and the first is canceled, so one run starts and nothing said is lost. The wake remembers what
was folded into it (`refs.parts`), so taking one of them back (`cancel`) takes out its words alone.
"""

import json
import logging
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any, cast

from . import run_triggers
from . import task_requirements as R

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_log = logging.getLogger(__name__)
# run_triggers is untyped: its functions are Any here.
RT: Any = run_triggers

ENDINGS = (
    "Leave the task in one of five states before you stop:\n"
    "1. Done with the stage: what is missing is met, or you moved the task.\n"
    "2. Asking a person, on the task.\n"
    "3. Waiting on a task you filed.\n"
    "4. Snoozed, with a reason, to a date where there is one.\n"
    "5. Closed, with a reason.\n"
    "Anything else counts as stopped early and you are woken again."
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def key(task_id: str, visit_id: str, level: str = "owner", try_n: int = 0) -> str:
    return f"{task_id}:{visit_id}:{level}:{try_n}"


def filed_under(wake_key: str, task_id: str) -> bool:
    """Whether `wake_key` starts with `task_id`, the part `message_task` reads as its run's task."""
    return wake_key.split(":", 1)[0] == task_id


def body(conn: "sqlite3.Connection", task: Row, lead: str = "", *, to: str | None) -> str:
    """What a bot reads when it is woken for a task: the stage, the person's notes on the sign-off that let it in
    (stage_effects.notes_on_the_way_in), what earlier stages handed on (stage_inputs), what is missing, and how its run
    may end. Said to `to`, whom it wakes, in words kept in the task's thread: a task this one filed is named only as
    `told` allows, and the pages of the task that filed this one only when it is not private."""
    from . import stage_inputs
    from . import waiting_on as W

    stage: Row = task.get("stage") or {}
    pipeline: Row = task.get("pipeline") or {}
    from . import stage_effects

    lines = [lead.strip()] if lead.strip() else []
    lines.append(f"{task.get('title') or 'A task'} is in {stage.get('name', '?')} ({pipeline.get('name', '?')}).")
    # What the person said with the sign-off that let the task in here: theirs, for whoever acts on it next.
    lines.extend(stage_effects.notes_on_the_way_in(conn, task))
    shown = told(conn, task, to)
    # The repository the work is in, so the bot never has to ask a person which one (a filed Engineering task).
    if repos := stage_inputs.repository_line(stage_inputs.repositories(conn, str(task["id"]), shown)):
        lines.append(repos)
    handed = stage_inputs.of(conn, task, shown, lambda filer: not _h().task_private(conn, filer))
    if handed:
        lines.append("From earlier stages (read each before you act):")
        lines += stage_inputs.lines(conn, handed)
        if any(entry["kind"] == "pr" and entry.get("url") for entry in handed):
            lines.append(stage_inputs.READ_CHECKS)
        if one := stage_inputs.one_pr(handed):
            lines.append(one)
        lines.append(stage_inputs.IF_MISSING)
    standings = R.standings(conn, task)
    fresh = _unreviewed(conn, task, standings)
    if fresh:
        lines.append(f"{fresh} Say so on the page the person OKs.")
    missing = [s for s in standings if not s.met]
    if missing:
        lines.append("Missing to leave this stage:")
        for s in (W.unnamed(each, shown) for each in missing):
            waits = f" Waiting on {s.on}." if s.state == R.WAITING and s.on else ""
            said = s.line if s.line and s.line != s.name else ""
            lines.append(f"- {R.sentences(f'{s.name}: {said}' if said else s.name)}{waits}")
            if R.asked_to_decide(s, to):
                lines.append(f"  {R.decide_commands(str(task['id']), str(s.requirement['id']))}")
            elif R.asked_to_review(s, to):
                lines.append(f"  {R.review_commands(str(task['id']), str(s.requirement['id']))}")
    lines.append(f"Version {task.get('version')}. Send it with every command on this task.")
    lines.append(ENDINGS)
    return "\n".join(lines)


def _unreviewed(conn: "sqlite3.Connection", task: Row, standings: list[R.Standing]) -> str | None:
    """On a stage where a person OKs a page that is not a review (QA's page), the commits no bot reviewed
    (task_requirements.unreviewed), so the page the bot writes says it. Not when the ask already says it, nor once the
    page is OK'd, nor while the bot that wrote the newest review is here to review them before the person is asked
    (task_requirements._new_commits)."""
    page: list[R.Standing] = []
    for s in standings:
        settings: Row = s.requirement.get("settings") or {}
        if s.requirement.get("kind") != "page" or settings.get("at_head") or s.met:
            continue
        if s.requirement.get("person_id") or R.people_only(s.requirement):
            page.append(s)
    if not page or any(R.NOT_REVIEWED in s.line for s in page) or R.delta_reviewer(conn, task):
        return None
    return R.unreviewed(conn, task)


REVIEW_NEW_LEAD = (
    "Commits were pushed after the reviews. Review only those commits before the person is asked to OK the work."
)


def review_new(conn: "sqlite3.Connection", task_id: str) -> str | None:
    """Wake the bot asked to review the commits pushed after the reviews (task_requirements._new_commits), once per
    newest commits: after Jev looked at the page a person OKs, and after a new commit. Not while it has a live run on
    the task: it sees the ask in `tico task show`. The never-stuck ladder wakes it if this wake is lost."""
    from . import task_engine as E

    row: Row | None = E.task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or not row.get("visit"):
        return None
    for standing in R.unmet(conn, row):
        who = standing.on
        if not who or not R.asked_to_review(standing, who):
            continue
        if live_run(conn, row, who):
            return None
        heads = ",".join(f"{ref}@{sha[:12]}" for ref, sha in sorted(R.heads_now(conn, row).items()))
        visit = row["visit"]
        wake_key = f"{task_id}:{visit['id']}:review_new:{heads}"
        return add(conn, row, who, visit, body(conn, row, REVIEW_NEW_LEAD, to=who), wake_key=wake_key)
    return None


def told(conn: "sqlite3.Connection", task: Row, to: str | None) -> Callable[[str], bool]:
    """Whether a wake or a note to `to` about open task `task` may name a task it filed (waiting_on.unnamed): `to` may
    open it, and, since the words are kept in the task's thread for whoever reads the task later, a private one only
    when its words may reach them all (task_calls.hidden_filed, `kept`)."""
    from . import task_calls

    hidden = set(task_calls.hidden_filed(conn, task, to, kept=True))
    return lambda task_id: task_id not in hidden


# What a wake's refs hold that is the engine's or the run's own. A caller's refs never set one, and a folded wake never
# carries one into the wake it folds into.
ENGINE_REFS = (
    "task",
    "wake",
    "visit",
    "level",
    "try",
    "version",
    "quiet",
    "job",
    "trigger",
    "trigger_source",
    "source",
    "purpose",
    "parts",
)


def add(
    conn: "sqlite3.Connection",
    task: Row,
    target: str | None,
    visit: Row,
    text: str,
    *,
    level: str = "owner",
    try_n: int = 0,
    wake_key: str | None = None,
    quiet: bool = False,
    refs: Row | None = None,
    trigger: str | None = None,
    source: str | None = None,
) -> str | None:
    """Wake `target` about `task`, once per key. Returns the message id, or None when this wake was already written or
    there is nobody to tell. A wake still queued for `target` about this task is folded into this one: its words come
    first, what its caller attached comes along, and it is canceled once this one has queued its run. A quiet note
    starts no run and folds nothing. `refs` are a caller's own facts for the run it starts (an automation's run and
    step, the other work to reconcile with): any of them that is the engine's (`ENGINE_REFS`) is dropped, so `quiet`
    and `job` come only from the engine's own state. `trigger` and `source` say what the run is for, where the caller
    knows better than "work a task"."""
    H = _h()
    if not target:
        return None
    wake_key = wake_key or key(task["id"], visit["id"], level, try_n)
    if not filed_under(wake_key, task["id"]):
        # The run's task is read from the key's first part (message_task): a key filed under anything else names a
        # task that is not this one, and its run would be refused at every claim (#240, the "room:" keys). Refused
        # here, so nothing is queued.
        _log.warning("wake refused: key %r is not filed under task %s", wake_key, task["id"])
        return None
    if conn.execute("SELECT 1 FROM messages WHERE wake_key=?", (wake_key,)).fetchone():
        return None
    if not H._actor_exists(conn, target):
        return None
    if not H.task_private_readable(conn, target, task):
        return None  # the engine never adds a reader: who can see a private task does not change
    waiting: list[Any] = []
    carried: Row = {}
    parts: list[Row] = []
    if not quiet and H.is_bot(target):
        waiting = conn.execute(
            "SELECT j.id, m.body, m.refs, m.wake_key FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
            f"WHERE {_KEY} AND j.bot_id=? AND j.state='queued' ORDER BY m.created_at, m.rowid",
            (*_range(task["id"]), H.actor_id(target)),
        ).fetchall()
        for found in waiting:
            # What the folded wake's caller attached (a new task's automation run, the work to reconcile with) rides
            # on: the run that starts is still that work. The engine's own facts are this wake's.
            was: Row = H._json(found[2], {}) or {}
            trigger = trigger or (was.get("trigger") if was.get("trigger") != "task_turn" else None)
            source = source or was.get("trigger_source")
            carried.update({name: value for name, value in was.items() if name not in ENGINE_REFS})
            parts += _parts(was, found[3], found[1])
        if parts:
            parts.append({"key": wake_key, "text": text})
            text = _joined(parts)
    own: Row = {"task": task["id"], "wake": "task_turn", "visit": visit["id"], "level": level, "try": try_n}
    own["version"] = task.get("version")
    if quiet:
        own["quiet"] = True
    if parts:
        own["parts"] = parts  # what was folded into it, each with its key and its words (`cancel`)
    theirs: Row = {name: value for name, value in (refs or {}).items() if name not in ENGINE_REFS}
    if not theirs.get("wake_batch"):
        carried.pop("wake_batch", None)  # a wake that is not batchable is not held for what it folded (wake_presets)
    stamped: Row = RT.stamp({**carried, **theirs, **own}, trigger or "task_turn", source)
    message = H._write_message(
        conn,
        H.KEEPER,
        target,
        text,
        H.conversation(conn, task["conversation_id"]),
        "notice",
        stamped,
        None,
        None,
    )
    if not message:
        return None
    # `job` marks a wake that queued a run, so a wake whose job later went missing reads as lost (`lost`); one to a bot
    # that reads its own inbox never had a job.
    job = conn.execute("SELECT 1 FROM jobs WHERE message_id=?", (message["id"],)).fetchone() is not None
    if job:
        conn.execute(
            "UPDATE messages SET wake_key=?, refs=json_set(COALESCE(refs, '{}'), '$.job', json('true')) WHERE id=?",
            (wake_key, message["id"]),
        )
        for found in waiting:  # folded into this one
            conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (found[0],))
    else:
        # It queued no run (a rule held it): the wakes it would have folded stay queued, so the bot is still woken.
        conn.execute("UPDATE messages SET wake_key=? WHERE id=?", (wake_key, message["id"]))
    return message["id"]


def ask(conn: "sqlite3.Connection", task: Row, requirement: Row | None, who: str | None = None) -> str | None:
    """Wake the bot an ask on this task names (an OK or an answer), once per ask and visit, keyed
    `<task>:<visit>:ask:<requirement>`: an ask added to the visit, or one the task's stage has (it holds for every
    visit of the stage). Only while that bot has a turn on the task (`waiting_on.turns`), so never while a person has
    it or it is snoozed. `who` is the bot the task waits on for it, when the requirement names nobody itself. A task
    entering a stage wakes each bot a stage's ask names beside the bot with the turn (task_engine._wake_turn); the
    never-stuck check calls this again for a bot that has a turn and was never woken for its ask: the ask it got while
    a person had the task reaches it once a person acts."""
    from . import waiting_on as W

    H = _h()
    visit: Row = task.get("visit") or {}
    if not requirement or requirement.get("withdrawn_at"):
        return None
    staged = requirement.get("stage_id")
    if (staged or requirement.get("visit_id")) != (task["stage"]["id"] if staged else visit.get("id")):
        return None
    bot = requirement.get("person_id") or who
    if not H.is_bot(bot) or task.get("status") != "open" or task.get("snooze") or bot not in W.turns(conn, task):
        return None
    line = requirement.get("line") or ""
    lead = (
        f"You are asked: {line}" if staged else f"{H.actor_name(conn, requirement.get('added_by_id'))} asks you: {line}"
    )
    return add(
        conn,
        task,
        bot,
        visit,
        body(conn, task, lead, to=bot),
        wake_key=f"{task['id']}:{visit['id']}:ask:{requirement['id']}",
    )


# Why a bot cannot run, as the rest of a sentence about it: "Builder has no computer". The never-stuck check reads this
# before it wakes anyone, and `waiting_on` shows the task as Paused with the cause. Every cause is a read of saved state:
# nothing here asks a computer. The three in COMPANY_WIDE stop every bot they touch and are whole sentences.
STATE_CAUSES = {"paused": "is paused", "quarantined": "is quarantined", "planned": "has not started yet"}
# A planned bot BotOps is still setting up (its setup task is open, or its go-live waits for the repository): its
# tasks start by themselves once it is live (backend/setup_live.py).
BEING_SET_UP = "is still being set up"
# An active bot whose onboarding a person has not marked complete takes no task (backend/lifecycle.py).
ONBOARDING = "is still onboarding: a person above it marks its onboarding complete"
REMOVED = "was removed"
NO_COMPUTER = "has no computer"
# A bot of a Tico Cloud company waits for its cloud machine's computer instead (backend/cloud_machines.py `coming`): on
# its way by itself (nobody is asked), or failed (a person replaces it in Settings > Computers). `cause_line` names the
# machine in place of CLOUD_WORDS.
CLOUD_WORDS = "its cloud computer"
CLOUD_STARTING = "is waiting for its cloud computer to start"
CLOUD_FAILED = "can't run: its cloud computer didn't start"
RUNS_FAILING = "keeps failing its runs"
LOGIN = "has a login to reconnect"
COMPANY_PAUSED = "The company is paused"
CLOUD_LOCKED = "Tico Cloud is locked"
OUT_OF_CREDIT = "Tico Cloud is out of credit"
COMPANY_WIDE = (COMPANY_PAUSED, CLOUD_LOCKED, OUT_OF_CREDIT)


def cause_line(conn: "sqlite3.Connection", actor: str | None, cause: str) -> str:
    """A cause from `cannot_run` as one sentence: "Builder has no computer", "Tico Cloud is locked"."""
    if cause in COMPANY_WIDE:
        return cause
    if cause in (CLOUD_STARTING, CLOUD_FAILED):
        from . import cloud_machines

        machine = cloud_machines.coming(conn, str(_h().actor_id(actor)))
        if machine:
            cause = cause.replace(CLOUD_WORDS, machine["label"])
    return f"{_h().actor_name(conn, actor)} {cause}"


# Where a person sees, or fixes, what keeps a bot from running: a page of the app, for a link beside the line.
COMPUTERS_PAGE = "#/settings/computers"
BILLING_PAGE = "#/settings/billing"
CAUSE_PAGES = {
    NO_COMPUTER: COMPUTERS_PAGE,
    CLOUD_STARTING: COMPUTERS_PAGE,
    CLOUD_FAILED: COMPUTERS_PAGE,
    OUT_OF_CREDIT: BILLING_PAGE,
    CLOUD_LOCKED: BILLING_PAGE,
}


def cause_href(cause: str | None) -> str | None:
    """The page for a cause from `cannot_run` (CAUSE_PAGES), or None."""
    return CAUSE_PAGES.get(cause or "")


def cannot_run(conn: "sqlite3.Connection", actor: str | None, seen: dict[str, str | None] | None = None) -> str | None:
    """Why this bot cannot run now, in a few words, or None when it can. A paused bot's tasks say Paused and carry on
    by themselves when it can run again; nobody is woken meanwhile. `seen` keeps each bot's answer for one pass."""
    H = _h()
    if not H.is_bot(actor):
        return None
    bot: str = H.actor_id(actor)
    if seen is not None and bot in seen:
        return seen[bot]
    cause = _cause(conn, bot)
    if seen is not None:
        seen[bot] = cause
    return cause


def _cause(conn: "sqlite3.Connection", bot: str) -> str | None:
    from . import (
        bot_settings,
        cloud_access,
        company_pause,
        gateway_connection,
        harnesses,
        reconnects,
        run_backoff,
        setup_live,
        statuses,
    )

    row = _read(conn, _BOT_STATE, (bot,))
    if not row:
        return REMOVED
    state = row[0] or "active"
    if state == "planned" and cast("Any", setup_live).being_set_up(conn, bot):
        return BEING_SET_UP
    if state != "active":
        return STATE_CAUSES.get(state, f"is {state}")
    if company_pause.record(conn) is not None:
        return COMPANY_PAUSED
    # These modules are untyped: what they return is Any here.
    cloud: Any = cloud_access
    config: Row = cast("Any", bot_settings).read_in_snapshot(conn, bot) or {}
    access = cloud.access(conn)
    if cloud.locked(conn, access):
        return CLOUD_LOCKED
    if cloud.frozen(conn, access) and cast("Any", gateway_connection).is_tico(config.get("model")):
        return OUT_OF_CREDIT  # a bot on its own subscription or keys still runs
    kinds: Any = harnesses
    if kinds.resolve_harness(config) not in kinds.EXTERNAL_HARNESSES:
        # A computer that is offline is not a cause: a cloud computer sleeps until work is queued for it.
        placed = _read(conn, _PLACED, (bot,))
        if not placed or placed[0]:
            return _computer_cause(conn, bot) if not placed else NO_COMPUTER
    onboarding = _read(conn, _ONBOARDING, (bot,))
    if onboarding and cast("Any", statuses).is_parked(onboarding[0] or ""):
        return ONBOARDING
    held: Row = cast("Any", run_backoff).get(conn, bot) or {}
    if held.get("held_at"):
        return RUNS_FAILING  # held until a person releases it (backend/run_backoff.py)
    if cast("Any", reconnects).bot_open(conn, bot, "login:"):
        return LOGIN  # its computer's sign-in was refused: a person reconnects it (backend/reconnects.py)
    return None


def _computer_cause(conn: "sqlite3.Connection", bot: str) -> str:
    """A bot on no computer: on a Tico Cloud company, what its cloud machine is doing (`cloud_machines.coming`), else
    NO_COMPUTER. A machine that cannot start while the company is out of credit is the company's cause."""
    from . import cloud_machines

    machine = cast("Any", cloud_machines).coming(conn, bot)
    if not machine:
        return NO_COMPUTER
    if machine["status"] == "out_of_credit":
        return OUT_OF_CREDIT
    return CLOUD_FAILED if machine["status"] == "failed" else CLOUD_STARTING


# `_cause`'s reads of one bot, kept for the snapshot (`_read`); `prime_bots` reads them for many bots at once.
_BOT_STATE = "SELECT state FROM bots WHERE id=?"
_PLACED = "SELECT r.revoked_at FROM assignments a JOIN runners r ON r.id = a.runner_id WHERE a.bot=?"
_ONBOARDING = "SELECT onboarding_state FROM bot_config WHERE bot=?"


def prime_bots(conn: "sqlite3.Connection", actors: Collection[str | None]) -> None:
    """Read what `cannot_run` asks of each of these bots (its state, its settings, its computer, its run back-off, its
    sign-in cards) in a few statements for all of them, and keep it in the snapshot's read cache: a pass over many tasks then asks
    "can this bot run" for each bot without reading it again. Nothing without a read cache."""
    from . import bot_settings, read_cache, reconnects, run_backoff

    RC: Any = read_cache
    H = _h()
    bots = sorted({str(H.actor_id(a)) for a in actors if H.is_bot(a)})
    if not bots or RC.cache(conn) is None:
        return
    listed = (json.dumps(bots),)
    states = {
        str(r[0]): r
        for r in conn.execute("SELECT id, state FROM bots WHERE id IN (SELECT value FROM json_each(?))", listed)
    }
    RC.prime(conn, _BOT_STATE, {(b,): ((states[b][1],) if b in states else None) for b in bots})
    settings = {
        str(r["bot_id"]): r
        for r in conn.execute("SELECT * FROM bot_settings WHERE bot_id IN (SELECT value FROM json_each(?))", listed)
    }
    RC.prime(conn, cast("Any", bot_settings).ROW_SQL_ONE, {(b,): settings.get(b) for b in bots})
    placed: dict[str, Any] = {}
    for r in conn.execute(
        "SELECT a.bot, r.revoked_at FROM assignments a JOIN runners r ON r.id = a.runner_id "
        "WHERE a.bot IN (SELECT value FROM json_each(?))",
        listed,
    ):
        placed.setdefault(str(r[0]), (r[1],))
    RC.prime(conn, _PLACED, {(b,): placed.get(b) for b in bots})
    # Whole rows, onboarding_state first: hubdb reads the same statement by column name.
    onboarding = {
        str(r["bot"]): r
        for r in conn.execute(
            "SELECT onboarding_state, bot FROM bot_config WHERE bot IN (SELECT value FROM json_each(?))", listed
        )
    }
    RC.prime(conn, _ONBOARDING, {(b,): onboarding.get(b) for b in bots})
    # Most bots have no back-off row: the miss is kept too, or each would be read again.
    backoff = {
        str(r["bot"]): r
        for r in conn.execute("SELECT * FROM run_backoff WHERE bot IN (SELECT value FROM json_each(?))", listed)
    }
    RC.prime(conn, cast("Any", run_backoff).GET_SQL, {(b,): backoff.get(b) for b in bots})
    cast("Any", reconnects).prime_open(conn, bots, "login:")


# A bot that can run may still be held on one task: its turn asked for a worktree it has no room for (D12). The wait
# wakes the task once the worktree is ready (backend/worktree_waits.py), so until then it is Paused, not the bot's turn.
WORKTREE_WAITING = "is waiting for a free worktree"
WORKTREE_RESERVED = "is waiting for its worktree to be set up"
# Or a run of it on the task stopped partway after it may have acted (its job is `uncertain`), and Tico holds the task's
# work until someone runs it again or leaves it (`POST jobs/{id}/reconcile`; execution.auto_reconcile resumes a safe one
# by itself, and hands the rest to BotOps with `job.held`). From the moment it stops the bot claims nothing more on the
# task (execution.unfenced_sql), so it is Paused, not the bot's turn: never a silent hold (Nov 12: a Codex reviewer
# stopped partway and its task still said it was the reviewer's turn for the 15 minutes before `job.held`).
RUN_HELD = "stopped partway: waiting on a person to run it again"
# Or its computer handed back its runs on the task before starting them, start_holds.SAME times for the same reason, and
# BotOps has a task to find why (backend/start_holds.py). Paused until BotOps closes it: never a silent "Queued" loop.
CANT_START = "can't start its runs here: BotOps is checking why"


def task_cause(
    conn: "sqlite3.Connection", task_id: str, actor: str | None, waits: dict[str, str] | None = None
) -> str | None:
    """Why this bot cannot run on this task though it can run: a run of it on the task held for a check, an open
    worktree wait of this task's, or None. `waits` is `worktree_holds` read once for a pass over many tasks."""
    H = _h()
    if not H.is_bot(actor):
        return None
    held = _read(conn, _held_runs_sql(), (task_id,))
    if held and held[0] and str(H.actor_id(actor)) in str(held[0]).split(","):
        return RUN_HELD
    from . import start_holds

    SH: Any = start_holds
    held = _read(conn, SH.held_sql(), (H.KEEPER, SH.EVENT, task_id))
    if held and held[0] and str(H.actor_id(actor)) in str(held[0]).split(","):
        return CANT_START
    if waits is None:
        from . import worktree_waits

        held = _read(conn, _TASK_WAIT, (task_id,)) if cast("Any", worktree_waits)._exists(conn) else None
        state = held[0] if held else None
    else:
        state = waits.get(task_id)
    return None if state is None else WORKTREE_RESERVED if state == "reserved" else WORKTREE_WAITING


def _held_runs_sql(many: bool = False) -> str:
    """The bots (slugs, comma-joined) with a run held on one task (`RUN_HELD`): a job still uncertain that nobody has
    chosen for yet (no `job_recovery` for its try; a saved choice waits only for its lane, and runs by itself). With
    `many`, (task, bots) for every task that has one. Few rows: jobs_uncertain holds only those."""
    task = str(_h().MESSAGE_TASK_SQL).replace("cv.", "uc.")
    return (
        f"SELECT {task + ', ' if many else ''}group_concat(DISTINCT u.bot_id) FROM jobs u "
        "JOIN messages m ON m.id = u.message_id JOIN conversations uc ON uc.id = m.conversation_id "
        "WHERE u.state = 'uncertain' AND NOT EXISTS (SELECT 1 FROM job_recovery r WHERE r.job_id = u.id AND "
        f"r.attempt_id IS u.attempt_id) {'GROUP BY 1' if many else f'AND {task} = ?'}"
    )


# One task's open worktree wait ("waiting" before "reserved"), as `worktree_holds` reads them for many.
_TASK_WAIT = "SELECT max(state) FROM worktree_waits WHERE state IN ('waiting','reserved') AND task_id=?"


def worktree_holds(conn: "sqlite3.Connection", task_ids: Collection[str] | None = None) -> dict[str, str]:
    """Task id -> state of its open worktree wait ("waiting" before "reserved": one still waiting for room says so).
    Every task's with no `task_ids`. One read; none when the table was never made."""
    from . import worktree_waits

    if not cast("Any", worktree_waits)._exists(conn):
        return {}
    sql = "SELECT task_id, max(state) FROM worktree_waits WHERE state IN ('waiting','reserved')"
    if task_ids is None:
        rows = conn.execute(sql + " GROUP BY task_id").fetchall()
    else:
        listed = json.dumps(list(task_ids))
        rows = conn.execute(
            sql + " AND task_id IN (SELECT value FROM json_each(?)) GROUP BY task_id", (listed,)
        ).fetchall()
    return {str(task): str(state) for task, state in rows}


class LiveBots(set[str]):
    """`live_bots`: the bots (slugs) with a live run, and for each the tasks its live runs name (`tasks`)."""

    tasks: dict[str, set[str]]


def live_bots(conn: "sqlite3.Connection", now: str | None = None) -> LiveBots:
    """The bots (slugs) with a live run on any task: one read, on the index of active runs (one_active_attempt_per_lane).
    The same read keeps, for each bot, the tasks its live runs name (`attempts.task_id`), from any conversation: a
    schedule's notice in the bot's own chat is still a run on its task. The never-stuck pass reads it once and hands it
    to `live_run`, which then reads nothing for any other bot. A pass starts no run, so the set holds for the whole
    pass; a run that ends during it only makes `live_run` read again."""
    H = _h()
    live = LiveBots()
    live.tasks = {}
    for bot, task_id in conn.execute(
        "SELECT j.bot_id, a.task_id FROM attempts a JOIN jobs j ON j.attempt_id = a.id "
        "WHERE a.state IN ('leased','running') AND a.lease_until > ?",
        (now or H.now(),),
    ).fetchall():
        live.add(str(bot))
        if task_id:
            live.tasks.setdefault(str(bot), set()).add(str(task_id))
    return live


def live_run(
    conn: "sqlite3.Connection",
    task: Row,
    actor: str | None,
    now: str | None = None,
    live: Collection[str] | None = None,
) -> bool:
    """This bot has a run on this task whose computer still checks in. A run is live only while its lease is renewed
    (every 90 seconds at most): one that is held, cut off or lost is over. `live` is `live_bots` read at the same
    `now`: a bot not in it has no live run on any task. A run is on the task as `_ON_TASK` says: a run in the task's
    conversation counts only when it names no task and no other open task has that conversation, so a run on another
    task there never hides this one's lost wake."""
    H = _h()
    if not H.is_bot(actor):
        return False
    if live is not None and H.actor_id(actor) not in live:
        return False
    if task["id"] in getattr(live, "tasks", {}).get(H.actor_id(actor), ()):
        return True  # a live run that names this task, from any conversation
    on_task = _on_task(conn, task)
    return bool(
        conn.execute(
            "SELECT 1 FROM (SELECT m.id FROM messages m WHERE "
            f"{_KEY} UNION SELECT id FROM messages WHERE conversation_id = ? "
            "UNION SELECT r.message_id FROM attempts t JOIN jobs r ON r.id = t.job_id WHERE t.task_id = ? "
            "AND t.state IN ('leased', 'running')) w "
            "CROSS JOIN jobs j ON j.message_id = w.id JOIN attempts a ON a.id = j.attempt_id "
            "JOIN messages m ON m.id = w.id "
            f"WHERE j.bot_id=? AND a.state IN ('leased', 'running') AND a.lease_until > ? AND {_ON_TASK_ROW} LIMIT 1",
            (
                *_range(task["id"]),
                on_task[-1] if on_task[-2] else "",  # the conversation's messages, only when it is this task's alone
                task["id"],  # and the runs that name this task, from any conversation
                H.actor_id(actor),
                now or H.now(),
                *on_task,
            ),
        ).fetchone()
    )


# The runs (attempts `a`) and the message each ran on (`m`), and which of them are on one task: the attempt or its
# message names the task (the attempt's task, the wake's key, the message's task), or, when neither names any task, the
# message is in the task's conversation and no other open task has that conversation. Bound by `_on_task`.
_RUNS = (
    "FROM attempts a LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN messages m ON m.id = COALESCE(a.message_id, "
    "j.message_id) "
)


# `_ON_TASK` asked of one run row (`a` and its message `m`): what `live_run` checks of the few live runs it finds.
_ON_TASK_ROW = (
    "(a.task_id = ? OR (m.wake_key >= ? AND m.wake_key < ?) OR json_extract(m.refs, '$.task') = ? OR (? AND a.task_id "
    "IS NULL AND m.wake_key IS NULL AND json_extract(m.refs, '$.task') IS NULL AND m.conversation_id = ?))"
)


def _runs_of(k: str) -> str:
    """The runs on each task of the table `k` (columns t, lo, hi, t2, alone, conv: what `_on_task` gives) as rows
    (t, rid): the attempt's rowid. One indexed lookup for each way a run is on a task (`_RUNS`): the attempt names the
    task (attempts_task), or the message it ran on, its own or its job's, has the task's wake key (messages_wake_key)
    or names the task in its refs (messages_refs_task), or, naming no task, is in the task's conversation while no other
    open task has it (messages_conversation). Each finds the task's own runs, never every run of the bot: asked as one
    condition joined across the three tables (an OR), SQLite read every run of the bot for each task, and a pass over
    10,000 tasks took 148 s."""
    named = "json_extract(y.refs, '$.task')"
    ways = (
        f"y.wake_key >= {k}.lo AND y.wake_key < {k}.hi",
        f"{named} = {k}.t2 AND {named} IS NOT NULL",
        f"{k}.alone AND y.conversation_id = {k}.conv AND y.wake_key IS NULL AND {named} IS NULL",
    )
    # CROSS JOIN keeps this order (task, message, job, run), and `+` keeps a test for "names none" off an index: else
    # SQLite may start from every run with no message or no task.
    alone = " AND +x.task_id IS NULL"
    out = [f"SELECT {k}.t AS t, x.rowid AS rid FROM {k} CROSS JOIN attempts x ON x.task_id = {k}.t"]
    for n, way in enumerate(ways):
        extra = alone if n == 2 else ""
        found = f"SELECT {k}.t, x.rowid FROM {k} CROSS JOIN messages y ON {way} CROSS JOIN "
        out.append(f"{found}attempts x ON x.message_id = y.id{extra}")
        out.append(
            f"{found}jobs z ON z.message_id = y.id CROSS JOIN attempts x ON x.job_id = z.id AND +x.message_id IS NULL{extra}"
        )
    return " UNION ".join(out)


# A run (`a`) is on the task: bound by `_on_task`'s six values.
_ON_TASK = (
    f"a.rowid IN (WITH p(t, lo, hi, t2, alone, conv) AS (SELECT ?, ?, ?, ?, ?, ?) SELECT rid FROM ({_runs_of('p')}))"
)
# How many other open tasks share a task's conversation (`_on_task`).
_SHARING = "SELECT COUNT(*) FROM tasks WHERE conversation_id=? AND status='open' AND id<>?"


def _on_task(conn: "sqlite3.Connection", task: Row) -> tuple[object, ...]:
    """The values `_ON_TASK` takes for this task."""
    conversation = task.get("conversation_id") or ""
    sharing = _read(conn, _SHARING, (conversation, task["id"]))
    alone = bool(conversation) and not (sharing and sharing[0])
    return (task["id"], *_range(task["id"]), task["id"], 1 if alone else 0, conversation)


def last_run(conn: "sqlite3.Connection", task: Row, actor: str | None) -> Row | None:
    """This bot's newest run on this task that started: its `state` and when it ended (`finished_at`). None when it
    never ran for it. A lease that never started is no run."""
    H = _h()
    if not H.is_bot(actor):
        return None
    found = _read(conn, _LAST_RUN, (H.actor_id(actor), *_on_task(conn, task)))
    return {"state": found[0], "finished_at": found[1]} if found else None


_LAST_RUN = (
    f"SELECT a.state, a.finished_at {_RUNS}WHERE +a.bot_id=? AND a.started_at IS NOT NULL AND {_ON_TASK} "
    "ORDER BY a.rowid DESC LIMIT 1"
)


def _marks(task: Row) -> tuple[int, int, int]:
    """Where the task's visit last changed, by order: the newest attempts row, the newest messages row and the newest
    start (`run_starts`) there were then (`visits.last_change_attempt`, `last_change_message`, `last_change_start`,
    written by triggers whenever `last_change_at` is). A run or a wake with a larger rowid came after the change, and a
    run with a larger start started after it, whatever the clock said when it was written."""
    visit: Row = task.get("visit") or {}
    return (
        int(visit.get("last_change_attempt") or 0),
        int(visit.get("last_change_message") or 0),
        int(visit.get("last_change_start") or 0),
    )


def started(conn: "sqlite3.Connection", attempt: str) -> None:
    """A run started: it takes the next start in order (`run_starts`), in the transaction that marks it started. The
    run counts for the change that was current then (`runs_since`), whenever its lease was written."""
    conn.execute("INSERT OR IGNORE INTO run_starts (attempt_id) VALUES (?)", (attempt,))


# A run started since the change: its start is newer than the visit's (`run_starts`), or, for a run that started with
# no start row (before there were any, or written straight into attempts), its attempt row is newer. Each run's start
# is looked up for that run (run_starts.attempt_id is unique), never gathered into a list of every start since the
# mark: the runs read are the task's own (`_ON_TASK`). Bound: the start mark, then the attempt mark.
_STARTED_SINCE = (
    "a.started_at IS NOT NULL AND COALESCE((SELECT s.id > ? FROM run_starts s WHERE s.attempt_id = a.id), a.rowid > ?)"
)


def runs_since(conn: "sqlite3.Connection", task: Row, *, starting: bool = False, besides: str | None = None) -> int:
    """How many bot runs started on this task since its visit last changed, whoever ran: the count the never-stuck
    ladder holds to 7. Only a run that started counts (`started_at`): a lease that failed before it began ran nothing.
    With `starting`, every lease still live that has not started yet counts too, whenever it was claimed: if it starts,
    it starts after this change. "Since" is by order, not by the clock: a run belongs to the change that was current
    when it started (`started`), so a lease claimed before a change and started after it is the new change's.
    `besides` leaves one run out: the one asking whether it may start."""
    attempt_mark, _, start_mark = _marks(task)
    counted, args = (
        f"({_STARTED_SINCE})",
        list[object]([start_mark, attempt_mark]),
    )
    if starting:
        counted = f"({counted} OR (a.state = 'leased' AND a.started_at IS NULL AND a.lease_until > ?))"
        args.append(_h().now())
    if besides:
        counted += " AND a.id <> ?"
        args.append(besides)
    found = _read(conn, f"SELECT COUNT(*) {_RUNS}WHERE {counted} AND {_ON_TASK}", (*args, *_on_task(conn, task)))
    return int(found[0]) if found else 0


# Bot runs one visit may start between two changes: the owner's start and 3 tries, then the manager bot's 3 tries
# (R1). Every run on the task counts, whoever it was for and whatever woke it.
RUNS = 7


def may_start(conn: "sqlite3.Connection", message: str | Row, attempt: str | None = None) -> bool:
    """Whether a queued run about a task may start now. The claim of a job calls it in its write transaction before it
    leases the run, and the start of that run calls it again (`attempt`: that run's own lease is not counted as one
    about to start). `message` is the job's message, by id (a job's id is its message's) or as a row.

    Every automatic start tied to a task on the engine is held: the engine's wakes, and any other message a person did
    not write (a scheduled occurrence's notice, a CI nudge, a task's news), retries included. Such a run may start only
    while the task is open, not snoozed and not at level person, while its bot has a turn on it (`waiting_on.turns`;
    Late's one look for the manager bot and the owner's wake that what it filed ended, `FILED`, need none), and while the visit has started fewer than 7 runs since it last
    changed, the leases about to start included. An engine wake must also be about the task's open visit. When the
    run is held its queued job is canceled, here. True for a person's own message (their words always get an answer)
    and anything about no task on the engine. A message in a person's name they did not type themselves (the
    Assistant, BotOps, a token: hubdb.delegated) gets past a snooze, a closed task and the turns, as theirs would,
    but not a person's level or the 7 runs: lifting those is the person's own act. A quiet message is held like any other once it has a job."""
    from . import task_engine as E

    H = _h()
    message_id = message if isinstance(message, str) else str(message.get("message_id") or message.get("id") or "")
    found = conn.execute(
        "SELECT m.wake_key, m.recipient_id, m.refs, m.sender_id, c.task_id FROM messages m "
        "LEFT JOIN conversations c ON c.id = m.conversation_id WHERE m.id=?",
        (message_id,),
    ).fetchone()
    if not found:
        return True
    refs: Row = H._json(found[2], {}) or {}
    # Quiet only keeps a message from queueing a run: a job that exists anyway (a deferred delivery's deadline) is held
    # like any other.
    said = {"sender_id": found[3], "refs": refs}
    if H.typed_by_person(said):
        return True
    if found[3] == H.KEEPER and refs.get("command") and refs.get("goal_action") == "clear":
        # The server's "/goal clear" (chat_goals.clear_task_goal) does no work on the task: it stops the harness
        # pursuing a task goal that ended, so it reaches the old owner's session even when the task is no longer
        # theirs. Only the server's own: refs a bot sends are not trusted to skip the hold.
        return True
    parts = str(found[0] or "").split(":")
    task = E.task(conn, parts[0]) if found[0] else None
    if task is None:
        named = message_task(conn, message_id)
        task = E.task(conn, named) if named else None
    if task is None:
        return True  # about no task on the engine: not the engine's to hold
    visit: Row = task.get("visit") or {}
    if refs.get("wake") == "task_turn" and refs.get("visit") != visit.get("id"):
        allowed = False
    else:
        kind = parts[2] if found[0] and task["id"] == parts[0] and len(parts) > 2 else ""
        allowed = _may_start(conn, task, str(found[1] or ""), kind, attempt, H.in_person_name(said))
    if allowed:
        return True
    conn.execute("UPDATE jobs SET state='canceled' WHERE message_id=? AND state='queued'", (message_id,))
    return False


def message_task(conn: "sqlite3.Connection", message_id: str | None, carried: bool = True) -> str | None:
    """The task a run on this message is about: the task its wake key names, else the one its refs name, else, for the
    keeper's carrier of another message (threads._carrier: `refs.carries`, no task of its own), the task of the message
    it carries, else the task its conversation is. The carried message comes before the conversation: a carrier in task
    A's chat of a post about task B is B's run. The claim (`may_start`, the attempt's `task_id`), the start and the
    count of runs all read the task this way, so a carrier is held and counted as the message it carries."""
    H = _h()
    found = conn.execute(
        "SELECT m.wake_key, m.refs, m.sender_id, c.task_id FROM messages m "
        "LEFT JOIN conversations c ON c.id = m.conversation_id WHERE m.id=?",
        (message_id or "",),
    ).fetchone()
    if not found:
        return None
    if found[0]:
        return str(found[0]).split(":", 1)[0]
    refs: Row = H._json(found[1], {}) or {}
    named = H.message_task_id({"refs": refs})
    if not named and carried and found[2] == H.KEEPER and refs.get("carries"):
        named = message_task(conn, str(refs["carries"]), carried=False)
    named = named or H.message_task_id({}, {"task_id": found[3]})
    return str(named) if named else None


def _may_start(
    conn: "sqlite3.Connection", task: Row, bot: str, kind: str, attempt: str | None, named: bool = False
) -> bool:
    from . import waiting_on as W

    if named:  # in a person's name: only their level and the 7 runs hold it (may_start)
        if not task.get("visit"):
            return True
        if (task["visit"].get("level") or "owner") == "person":
            return False
        return runs_since(conn, task, starting=True, besides=attempt) < RUNS
    if task["status"] != "open" or task.get("snooze") or not task.get("visit"):
        return False
    if (task["visit"].get("level") or "owner") == "person":
        return False
    pipe: Row = task.get("pipeline") or {}
    late = kind == "late" and bot == pipe.get("manager_id")
    filed = kind == FILED and bot == task.get("owner_id")
    if not (late or filed) and bot not in W.turns(conn, task):
        return False
    return runs_since(conn, task, starting=True, besides=attempt) < RUNS


# The wake key's kind for "what this task filed has ended" (task_engine._ended): its owner bot's run may start though
# it has no turn, since the task waits on a person's OK that the filed review was about.
FILED = "filed"
# A job in one of these states may still start its run: its wake is not lost.
LIVE_JOBS = ("queued", "leased", "running", "input", "stalled")


def lost(conn: "sqlite3.Connection", task: Row, actor: str | None, *, whole_visit: bool = False) -> Row | None:
    """The wake about this task that `actor` was owed and never got: its newest one in this visit since the visit last
    changed (by order; with `whole_visit`, in the whole visit), when it queued a run and that run never started (its
    job is gone, or ended with no run of it started), and no run of this bot on the task started on a wake written
    after it. `id`, `body`, and `refs`: what woke it, which a wake written again keeps. None when nothing was lost."""
    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    found = _read(
        conn, _LOST, (*_range(f"{task['id']}:{visit.get('id')}"), 0 if whole_visit else _marks(task)[1], actor)
    )
    if not found or not found[3] or found[4] in LIVE_JOBS:
        return None
    taken = _read(conn, _TAKEN, (H.actor_id(actor), found[0], *_on_task(conn, task), found[2]))
    if taken:
        return None
    return {"id": str(found[0]), "body": str(found[1] or ""), "refs": H._json(found[5], {}) or {}}


_LOST = (
    "SELECT m.id, m.body, m.rowid, json_extract(m.refs, '$.job'), "
    "(SELECT j.state FROM jobs j WHERE j.id = m.id), m.refs FROM messages m "
    "WHERE m.wake_key >= ? AND m.wake_key < ? AND m.rowid > ? AND +m.recipient_id=? "
    "AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 ORDER BY m.rowid DESC LIMIT 1"
)
# A run of the bot on the task that started on that wake's job, or on a wake written after it.
_TAKEN = (
    f"SELECT 1 {_RUNS}WHERE +a.bot_id=? AND a.started_at IS NOT NULL AND (a.rowid IN (SELECT x.rowid FROM attempts x "
    f"WHERE x.job_id=?) OR ({_ON_TASK} "
    "AND m.rowid > ?)) LIMIT 1"
)


def inbox(conn: "sqlite3.Connection", task: Row, actor: str | None) -> Row | None:
    """The newest wake about this task in this visit since it last changed (by order), when `actor` is a bot that reads
    its own inbox (an outside agent: its wakes queue no job). `id`, and `read_at`: None while it has not read it. An
    intact wake it has not read is on its way, as a queued one is; once read, the bot took it. None for a bot whose
    wakes queue a run, or when nothing was written for it since the change."""
    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    found = _read(conn, _inbox_sql(), (*_range(f"{task['id']}:{visit.get('id')}"), _marks(task)[1], actor))
    return {"id": str(found[0]), "read_at": found[1]} if found else None


def _reads_inbox() -> str:
    from . import enqueue

    return str(cast("Any", enqueue).external_bot_sql("m.recipient_id"))


def _inbox_sql() -> str:
    """`inbox`'s read: the newest wake of one visit since a mark to one bot that reads its own inbox."""
    return (
        f"SELECT m.id, m.read_at FROM messages m WHERE {_KEY} AND m.rowid > ? AND +m.recipient_id=? "
        "AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 "
        f"AND json_extract(m.refs, '$.job') IS NULL AND {_reads_inbox()} ORDER BY m.rowid DESC LIMIT 1"
    )


def told_in_visit(conn: "sqlite3.Connection", task: Row, actor: str | None) -> str | None:
    """Whether this bot was told about the task's open visit: "told" when it got a wake that queued a run or sits in
    its own inbox, "lost" when its newest such wake never ran (the never-stuck check writes it again, once per level,
    try and change), None when it got none at all."""
    H = _h()
    if not H.is_bot(actor):
        return None
    visit: Row = task.get("visit") or {}
    found = conn.execute(
        f"SELECT 1 FROM messages m WHERE {_KEY} AND +m.recipient_id=? "
        "AND COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0 LIMIT 1",
        (*_range(f"{task['id']}:{visit.get('id')}"), actor),
    ).fetchone()
    if not found:
        return None
    return "lost" if lost(conn, task, actor, whole_visit=True) else "told"


def queued(conn: "sqlite3.Connection", task_id: str, actor: str | None) -> bool:
    """A wake for this task is waiting for this bot: queued or just claimed, held ones included."""
    H = _h()
    if not H.is_bot(actor):
        return False
    return bool(_read(conn, _QUEUED, (*_range(task_id), H.actor_id(actor))))


_QUEUED = (
    "SELECT 1 FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
    "WHERE m.wake_key >= ? AND m.wake_key < ? AND j.bot_id=? AND j.state IN ('queued', 'leased') LIMIT 1"
)


def drop(conn: "sqlite3.Connection", task_id: str, keep: str | Collection[str] | None = None) -> int:
    """Cancel every wake still queued for this task, whoever it is for: the turn they were about is over. With `keep`
    (a bot, or several), those bots' wakes stay queued. A run that already took its wake is left alone. Returns how
    many."""
    H = _h()
    keeping = [keep] if isinstance(keep, str) else list(keep or ())
    kept = sorted({str(H.actor_id(bot)) for bot in keeping if H.is_bot(bot)})
    cursor = conn.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND message_id IN "
        f"(SELECT id FROM messages m WHERE {_KEY})"
        + (f" AND bot_id NOT IN ({','.join('?' * len(kept))})" if kept else ""),
        (*_range(task_id), *kept),
    )
    return cursor.rowcount


def _parts(refs: Row, wake_key: object, body: object) -> list[Row]:
    """What one queued wake is made of: the wakes folded into it, or itself alone."""
    folded: list[Row] | None = refs.get("parts")
    return list(folded) if folded else [{"key": str(wake_key or ""), "text": str(body or "")}]


def _joined(parts: list[Row]) -> str:
    return "\n\n".join(str(part.get("text") or "") for part in parts)


def cancel(conn: "sqlite3.Connection", wake_key: str) -> int:
    """Take one wake back by its key, while no run has taken it yet. A wake that was folded into another is taken out
    of it alone: the wake that holds it keeps the rest of its words, and is canceled only when nothing is left. Returns
    how many jobs it canceled."""
    H = _h()
    rows = conn.execute(
        "SELECT j.id, m.id, m.wake_key, m.refs, m.body FROM messages m CROSS JOIN jobs j ON j.message_id = m.id "
        f"WHERE {_KEY} AND j.state='queued'",
        _range(wake_key.split(":", 1)[0]),
    ).fetchall()
    for job_id, message_id, own_key, raw, body in rows:
        refs: Row = H._json(raw, {}) or {}
        parts = _parts(refs, own_key, body)
        rest = [part for part in parts if part.get("key") != wake_key]
        if len(rest) == len(parts):
            continue
        if not rest:
            return conn.execute("UPDATE jobs SET state='canceled' WHERE id=? AND state='queued'", (job_id,)).rowcount
        refs["parts"] = rest
        conn.execute("UPDATE messages SET body=?, refs=? WHERE id=?", (_joined(rest), json.dumps(refs), message_id))
        return 0
    return 0


# A wake key's prefix read as a range on the wake-key index (`messages_wake_key`): every key that starts `<prefix>:`
# sorts at or after it and before `<prefix>;`. A LIKE cannot use the index, so each read scanned every job of the bot.
_KEY = "m.wake_key >= ? AND m.wake_key < ?"


def _range(prefix: str) -> tuple[str, str]:
    return f"{prefix}:", f"{prefix};"


# ----------------------------------------------------------------------------- reads for a pass over many tasks
def _read(conn: "sqlite3.Connection", sql: str, args: tuple[object, ...]) -> Any:
    """One row through the snapshot's read cache (`read_cache.one`): a pass over many tasks fills it first (`prime`),
    and any write empties it, so a row read here is never older than the last write."""
    from . import read_cache

    return cast("Any", read_cache).one(conn, sql, args)


def _listed(*columns: str) -> str:
    """A list sent as one JSON value, as a table `k` of these columns: one read for many tasks."""
    picked = ", ".join(f"json_extract(value, '$[{n}]')" for n in range(len(columns)))
    return f"WITH k({', '.join(columns)}) AS MATERIALIZED (SELECT {picked} FROM json_each(?)) "


_ON_COLUMNS = ("t", "lo", "hi", "t2", "alone", "conv")


def _visit_id(row: Row) -> object:
    visit: Row | None = row.get("visit")
    return visit.get("id") if visit else None


def prime(conn: "sqlite3.Connection", tasks: "Collection[Row]", bots: dict[str, set[str]]) -> None:
    """Read together what `queued`, `inbox`, `lost`, `last_run`, `runs_since` and `task_cause` read for each of `tasks`
    (`task_engine.task()` rows) and each of its bots (`bots`: task id -> actors), and keep it in the snapshot's read cache, where those
    functions find it: a pass over every open task reads a fixed number of statements, not a few a task. Each read is
    the one-task read over a list, so the cache holds what each one-task read returns, "nothing" included. A
    connection with no read cache keeps nothing; a write empties it."""
    from . import read_cache

    RC: Any = read_cache
    if not tasks or RC.cache(conn) is None:
        return
    H = _h()
    rows = {str(task["id"]): task for task in tasks}
    asked = {t: sorted(str(bot) for bot in bots.get(t, ()) if H.is_bot(bot)) for t in rows}
    slugs = {actor: str(H.actor_id(actor)) for each in asked.values() for actor in each}
    shares = conn.execute(
        _listed("t", "c") + "SELECT k.t, (SELECT COUNT(*) FROM tasks WHERE conversation_id=k.c AND status='open' "
        "AND id<>k.t) FROM k",
        (json.dumps([[t, row.get("conversation_id") or ""] for t, row in rows.items()]),),
    ).fetchall()
    RC.prime(conn, _SHARING, {(rows[t].get("conversation_id") or "", t): (n,) for t, n in shares})
    on = {t: _on_task(conn, row) for t, row in rows.items()}  # from the counts just kept
    visits = {t: f"{t}:{_visit_id(row)}" for t, row in rows.items()}

    queued = {
        (str(found[0]), str(found[1]))
        for found in conn.execute(
            _listed("t", "lo", "hi") + "SELECT DISTINCT k.t, j.bot_id FROM k JOIN messages m ON m.wake_key >= k.lo "
            "AND m.wake_key < k.hi CROSS JOIN jobs j ON j.message_id = m.id WHERE j.state IN ('queued', 'leased')",
            (json.dumps([[t, *_range(t)] for t in rows]),),
        ).fetchall()
    }
    RC.prime(
        conn,
        _QUEUED,
        {(*_range(t), slugs[a]): (1,) if (t, slugs[a]) in queued else None for t in rows for a in asked[t]},
    )

    def newest(select: str, marks: dict[str, list[int]], where: str) -> dict[tuple[str, int, str], tuple[Any, ...]]:
        """The newest wake since each mark of each task's visit, by recipient: (task, mark, recipient) -> columns."""
        listed = [[t, *_range(visits[t]), mark] for t, each in marks.items() for mark in sorted(set(each))]
        found = conn.execute(
            _listed("t", "lo", "hi", "mark") + f"SELECT * FROM (SELECT k.t, k.mark, m.recipient_id, {select}, "
            "ROW_NUMBER() OVER (PARTITION BY k.t, k.mark, m.recipient_id ORDER BY m.rowid DESC) AS n FROM k "
            f"JOIN messages m ON m.wake_key >= k.lo AND m.wake_key < k.hi WHERE m.rowid > k.mark AND {where}) "
            "WHERE n = 1",
            (json.dumps(listed),),
        ).fetchall()
        return {(str(row[0]), int(row[1]), str(row[2])): tuple(row[3:-1]) for row in found}

    quiet = "COALESCE(json_extract(m.refs, '$.quiet'), 0) = 0"
    mark = {t: _marks(row)[1] for t, row in rows.items()}
    unread = newest(
        "m.id, m.read_at",
        {t: [mark[t]] for t in rows},
        f"{quiet} AND json_extract(m.refs, '$.job') IS NULL AND {_reads_inbox()}",
    )
    RC.prime(
        conn,
        _inbox_sql(),
        {(*_range(visits[t]), mark[t], a): unread.get((t, mark[t], a)) for t in rows for a in asked[t]},
    )
    lost = newest(
        "m.id, m.body, m.rowid, json_extract(m.refs, '$.job'), (SELECT j.state FROM jobs j WHERE j.id = m.id), m.refs",
        {t: [mark[t], 0] for t in rows},
        quiet,
    )
    RC.prime(
        conn,
        _LOST,
        {(*_range(visits[t]), m, a): lost.get((t, m, a)) for t in rows for m in {mark[t], 0} for a in asked[t]},
    )
    # Whether a run took each lost wake: only for one whose job ended with no run started (`lost`).
    unsure = [
        (t, slugs[a], found)
        for (t, _, a), found in lost.items()
        if a in asked[t] and found[3] and found[4] not in LIVE_JOBS
    ]
    taken = conn.execute(
        _runs_on("i", "slug", "job", *_ON_COLUMNS, "rid") + "SELECT k.i, EXISTS (SELECT 1 FROM attempts x WHERE "
        "x.job_id = k.job AND +x.bot_id = k.slug AND x.started_at IS NOT NULL) OR EXISTS (SELECT 1 FROM on_k o JOIN r "
        "ON r.rid = o.rid WHERE o.t = k.t AND r.bot_id = k.slug AND r.started_at IS NOT NULL AND r.mrid > k.rid) FROM k",
        (json.dumps([[i, slug, found[0], *on[t], found[2]] for i, (t, slug, found) in enumerate(unsure)]),),
    ).fetchall()
    took = {int(i): bool(yes) for i, yes in taken}
    RC.prime(
        conn,
        _TAKEN,
        {
            (slug, found[0], *on[t], found[2]): (1,) if took.get(i) else None
            for i, (t, slug, found) in enumerate(unsure)
        },
    )

    ran = {
        (str(found[0]), str(found[1])): (found[2], found[3])
        for found in conn.execute(
            _runs_on(*_ON_COLUMNS) + "SELECT t, bot_id, state, finished_at FROM (SELECT o.t, r.bot_id, r.state, "
            "r.finished_at, ROW_NUMBER() OVER (PARTITION BY o.t, r.bot_id ORDER BY r.rid DESC) AS n FROM on_k o "
            "JOIN r ON r.rid = o.rid WHERE r.started_at IS NOT NULL) WHERE n = 1",
            (json.dumps([list(on[t]) for t in rows]),),
        ).fetchall()
    }
    RC.prime(conn, _LAST_RUN, {(slugs[a], *on[t]): ran.get((t, slugs[a])) for t in rows for a in asked[t]})

    # `_STARTED_SINCE` for each task, over the runs on it.
    since = {t: _marks(row) for t, row in rows.items()}
    counts = dict(
        conn.execute(
            _runs_on("am", "sm", *_ON_COLUMNS) + "SELECT k.t, COUNT(*) FROM k JOIN on_k o ON o.t = k.t JOIN r ON "
            "r.rid = o.rid WHERE r.started_at IS NOT NULL AND COALESCE((SELECT s.id > k.sm FROM run_starts s WHERE "
            "s.attempt_id = r.id), r.rid > k.am) GROUP BY k.t",
            (json.dumps([[since[t][0], since[t][2], *on[t]] for t in rows]),),
        ).fetchall()
    )
    RC.prime(
        conn,
        f"SELECT COUNT(*) {_RUNS}WHERE ({_STARTED_SINCE}) AND {_ON_TASK}",
        {(*_since_args(since[t]), *on[t]): (counts.get(t, 0),) for t in rows},
    )


def _runs_on(*columns: str) -> str:
    """`_listed` with two more tables: `on_k`, each task of `k` with the runs on it (`rid`), as `_ON_TASK` places them
    (`_runs_of`: indexed lookups from each task), and `r`, those runs with the message each ran on (`mrid`). Only the
    listed tasks' runs are read, never every run."""
    return (
        _listed(*columns)[:-1] + f", on_k AS MATERIALIZED ({_runs_of('k')}), r AS MATERIALIZED (SELECT a.rowid AS rid, "
        f"a.id, a.bot_id, a.state, a.finished_at, a.started_at, a.job_id, m.rowid AS mrid {_RUNS}WHERE a.rowid IN "
        "(SELECT rid FROM on_k)) "
    )


def prime_holds(conn: "sqlite3.Connection", task_ids: list[str]) -> None:
    """Read the open worktree waits of these tasks in one statement (`worktree_holds`) and keep each task's in the
    snapshot's read cache, where `task_cause` finds it."""
    from . import read_cache, worktree_waits

    RC: Any = read_cache
    if not task_ids or RC.cache(conn) is None:
        return
    held = dict(conn.execute(_held_runs_sql(many=True)).fetchall())
    RC.prime(conn, _held_runs_sql(), {(t,): (held.get(t),) for t in task_ids})
    from . import start_holds

    SH: Any = start_holds
    keys = (_h().KEEPER, SH.EVENT)
    cant = dict(conn.execute(SH.held_sql(many=True), keys).fetchall())
    RC.prime(conn, SH.held_sql(), {(*keys, t): (cant.get(t),) for t in task_ids})
    if not cast("Any", worktree_waits)._exists(conn):
        return
    holds = worktree_holds(conn, task_ids)
    RC.prime(conn, _TASK_WAIT, {(t,): (holds.get(t),) for t in task_ids})


def _since_args(marks: tuple[int, int, int]) -> tuple[int, ...]:
    """The two values `_STARTED_SINCE` binds, from `_marks`."""
    attempt_mark, _, start_mark = marks
    return (start_mark, attempt_mark)
