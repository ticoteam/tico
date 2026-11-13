# pyright: strict
"""A bot whose computer keeps handing back its runs on one task before starting them, each time for the same reason.

A computer hands a run back before it starts when it cannot set the run up (execution.hand_back), and says why in the
run's `runner_error` diagnostic (runner-rs execute.rs). One hand-back is a hiccup: the job waits in the queue again
(execution.settle_unstarted). But on Acme (Nov 11) Reviewer 2's computer handed back every run on its Review 2 task with
"the bot's checkout's upstream could not be read", two minutes apart, and nothing said so: the task read "Queued", and
the job would have retried PRE_START_TRIES times before failing quietly, then been woken again.

So once SAME of one job's runs in a row come back for the same reason, no wait will clear it:

- the job fails now, and its run says why;
- BotOps gets one task per bot that names the reason and the task (upkeep is BotOps's, never a person's);
- the task waits on the bot as Paused with that cause (`task_wakes.CANT_START`, from `held_bots`) until BotOps closes
  its task. Then the never-stuck check wakes the bot on the task again, and a new run that fails the same way files
  again.

A computer that claims the run and then says nothing (never starts it, never renews its lease, never hands it back) gives
no reason to compare, so its lapses are not counted here. On Tico Team (Nov 12) the shared machine did that to
Performance Owner's runs ~344 times in a day: each job failed after PRE_START_TRIES lapses and the next wake started
over, with nobody told. So the expiry that spends a job's PRE_START_TRIES on lapses files here too (`file` with
`lapses`), with LAPSED_WHY as the reason, and the task waits on BotOps the same way.
"""

import json
from typing import Any, cast

from . import run_diagnostics
from .store import H as _H

H: Any = _H
RD: Any = run_diagnostics
HANDED_BACK: str = RD.HANDED_BACK

SAME = 3  # one job's runs in a row handed back for the same reason before BotOps gets it
EVENT = "task.cant_start"  # on the task: {bot, review (BotOps's task), reason, job}
TITLE = "Find why {bot}'s runs can't start"
REASON_CHARS = 500
BOTOPS_HAS_IT = "BotOps is checking why."  # ends the failed run's text: no person is asked (execution.last_stop)
# The reason a job's runs that only lapsed are filed with: the computer said nothing, so this is all that is known.
LAPSED_WHY = "it took each run and never started it, renewed its lease or handed it back"


def reason(c: Any, aid: str) -> str:
    """What run `aid`'s computer said stopped it from starting the run (its `runner_error`), or '' when it said nothing:
    a hand-back with no reason (Tico unreachable for a moment, a person's Stop) is a hiccup, never counted here."""
    for row in c.execute(
        "SELECT payload FROM attempt_events WHERE attempt_id=? AND kind='diagnostic' ORDER BY seq DESC LIMIT 20", (aid,)
    ):
        try:
            payload: Any = json.loads(row[0])
        except ValueError:
            continue
        said: Any = cast(dict[str, Any], payload).get("runner_error") if isinstance(payload, dict) else None
        if said:
            return str(RD.scrub(" ".join(str(said).split())))[:REASON_CHARS]
    return ""


def same_in_a_row(c: Any, job_id: str, why: str) -> int:
    """How many of the job's latest runs in a row its computer handed back before starting them, for `why`."""
    if not why:
        return 0
    count = 0
    for row in c.execute(
        "SELECT id,state,started_at,final_text FROM attempts WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
        (job_id, SAME),
    ):
        if (
            row["started_at"]
            or row["state"] != "expired"
            or not str(row["final_text"] or "").startswith(HANDED_BACK)
            or reason(c, row["id"]) != why
        ):
            break
        count += 1
    return count


def stuck(c: Any, row: Any) -> str:
    """The reason this handed-back run (its attempt already expired) is the SAME'th in a row on its job, or ''."""
    if not row["job_id"] or row["stop_requested_at"]:
        return ""
    why = reason(c, row["id"])
    return why if same_in_a_row(c, row["job_id"], why) >= SAME else ""


def _task(c: Any, row: Any) -> Any:
    task_id = row["task_id"]
    if not task_id and row["job_id"]:
        from .task_wakes import message_task

        job = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
        task_id = job and message_task(c, job["message_id"])
    return H.task(c, task_id) if task_id else None


def file(c: Any, row: Any, why: str, computer: str, lapses: int = 0) -> str | None:
    """Give BotOps the bot's runs that cannot start: one open task per bot, a comment on it naming each further task,
    and the hold on this run's task (EVENT). Answers BotOps's task id, or None when there is no BotOps to give it to
    (the bot is BotOps, or the company has none): then the job still fails, and the task is not held. `lapses` is how
    many runs its computer took and went silent on (execution.expire), when that, not SAME hand-backs, is why."""
    maintainer = H.FLEET_MAINTAINER
    bot = row["bot_id"]
    if bot == maintainer or not H.bot(c, maintainer):
        return None
    info: dict[str, Any] = H.bot(c, bot) or {}
    label: str = info.get("slug") or bot
    name: str = info.get("display_name") or label
    owner = H.bot_actor(maintainer)
    title = TITLE.format(bot=label)[:200]
    task = _task(c, row)
    if task and task["status"] != "open":
        task = None
    if not task:
        about = "its work"
    elif H.task_private_readable(c, owner, task):
        about = f'the task "{task["title"]}" ({task["id"]})'
    else:
        about = "a task you cannot read"
    said = (
        f"{name}'s computer ({computer}) took {lapses} runs on {about} and never started them: {why}"
        if lapses
        else f"{name}'s computer ({computer}) handed back its last {SAME} runs on {about} before starting them, each "
        f"time saying: {why}"
    )
    review = c.execute(
        "SELECT id FROM tasks WHERE owner_id=? AND requester_id=? AND title=? AND status='open' AND instr(body,?)>0 "
        "ORDER BY created_at DESC LIMIT 1",
        (owner, H.KEEPER, title, H.BOT_REVIEW_ID.format(id=bot)),
    ).fetchone()
    try:
        if review:
            review_id = review["id"]
            if not task or not _held(c, task["id"], bot, review_id):
                H.task_comment(c, H.KEEPER, review_id, said + ". Tico stopped retrying it too.")
        else:
            made = H.task_create(
                c,
                H.KEEPER,
                title,
                f"{said}.\n\nTico stopped retrying: a wait will not clear this. The task waits on {name}, paused, until "
                f"you close this one. Fix the cause on {name}'s computer or in its repository (its Runs show each "
                f"run's reason), then close this task: Tico wakes {name} on its work again, and files here again if "
                "it still cannot start.\n\n" + H.BOT_REVIEW_ID.format(id=bot),
                owner,
                lint=False,
                deduplicate=False,
            )
            review_id = made["id"]
    except H.Refused:
        return None
    if task:
        H.event(c, H.KEEPER, EVENT, task["id"], {"bot": bot, "review": review_id, "reason": why, "job": row["job_id"]})
    return review_id


def _held(c: Any, task_id: str, bot: str, review_id: str) -> bool:
    return bool(
        c.execute(
            "SELECT 1 FROM events WHERE actor=? AND action=? AND target=? AND json_extract(detail,'$.bot')=? "
            "AND json_extract(detail,'$.review')=? LIMIT 1",
            (H.KEEPER, EVENT, task_id, bot, review_id),
        ).fetchone()
    )


def held_sql(many: bool = False) -> str:
    """The bots (ids, comma-joined) whose runs on one task cannot start while BotOps's task about it is open. With
    `many`, (task, bots) for every task that has one. Few rows: only tasks a hold was filed on."""
    return (
        f"SELECT {'e.target, ' if many else ''}group_concat(DISTINCT json_extract(e.detail,'$.bot')) FROM events e "
        "JOIN tasks r ON r.id = json_extract(e.detail,'$.review') AND r.status = 'open' "
        f"WHERE e.actor = ? AND e.action = ? {'GROUP BY e.target' if many else 'AND e.target = ?'}"
    )
