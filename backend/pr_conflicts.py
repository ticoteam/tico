# pyright: strict
"""A merge conflict on a task's open PR before its pipeline's Merge stage (docs/pipelines-v3.md, "GitHub").

GitHub says a PR conflicts with its base branch; backend/github.py keeps that on the task's link (`mergeable`
'conflict', by webhook or by its read of PRs whose base moved). On a stage before the one that queues the PRs, fixing it
is the job of the bot that can push to the PR: the bot that built it, which is the owner the task has in the stage
that opens its PR (Engineering's Build). A reviewer holds a read-only grant and cannot rebase (Acme, Nov 11: the
conflict went to Reviewer 2 and nothing moved for 40 minutes).

- `standing`: the conflict is one more thing the task needs to leave its stage (task_requirements.standings adds it,
  first), not met, and that bot's turn (waiting_on). It is never a stored requirement: it lasts while GitHub says the
  PR conflicts, on whatever stage before Merge the task is in. The task stays in its stage. With no bot that built it
  (a person owns Build), it is the task's owner's, as before.
- `tell`: that bot's one wake per PR and conflicting commit, saying what to do; a bot that holds the stage (a
  reviewer) gets one quiet line that it is not its to fix. Nothing else is new: the never-stuck ladder tries the bot
  again and then the manager bot like any bot's turn, and a bot that cannot run is Paused (its fixer, BotOps).

From the stage that queues the PRs on, GitHub's merge queue says it (a removal, the Merge stage owner's) and nothing
here adds to it. A pipeline with no such stage keeps the owner's wake (backend/github.py).
"""

import json
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from . import hubdb_core, read_cache

if TYPE_CHECKING:
    import sqlite3

    from . import task_requirements as R

Row = dict[str, Any]

_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot

KIND = "conflict"  # the kind of the standing; no requirement row has it
LINE = "No merge conflict"
# The task's open PRs GitHub says conflict with their base branch, oldest link first.
_LINKS = (
    "SELECT task_id, id, url, title, repo, number FROM task_links WHERE task_id IN (%s) AND kind='pr' "
    "AND state IN ('open', 'draft') AND mergeable='conflict' ORDER BY created_at, id"
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _links_of(conn: "sqlite3.Connection", task_ids: list[str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {task_id: [] for task_id in task_ids}
    if task_ids:  # one statement however many (the ids go in as one JSON list)
        cursor = conn.execute(_LINKS % "SELECT value FROM json_each(?)", (json.dumps(task_ids),))
        names = [d[0] for d in cursor.description]
        for found in cursor.fetchall():
            row = dict(zip(names, found, strict=True))
            out[str(row["task_id"])].append(row)
    return out


def links(conn: "sqlite3.Connection", task_id: str) -> list[Row]:
    """The task's conflicting open PRs (one row each, however many links name it), read once in a snapshot."""
    found = read_cache.value(conn, ("pr_conflicts", task_id), lambda: _links_of(conn, [task_id])[task_id], shared=True)
    out: dict[str, Row] = {}
    for link in found:
        out.setdefault(ref(link), link)
    return list(out.values())


def prime(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> None:
    """Read the conflicting PRs of many tasks together, for the `links` reads that follow in this snapshot."""
    values = read_cache.cache(conn)
    if values is None:
        return
    unread = sorted({str(t) for t in task_ids if t and ("pr_conflicts", str(t)) not in values})
    for task_id, rows in _links_of(conn, unread).items():
        values.setdefault(("pr_conflicts", task_id), rows)


def ref(link: Row) -> str:
    """A link's PR as lines name it: `owner/name#12`."""
    from .item_sources import github as GS

    if link.get("repo") and link.get("number") is not None:
        return GS.pr_ref(str(link["repo"]), int(link["number"]))
    return str(link.get("title") or link.get("url") or "the pull request")


def applies(conn: "sqlite3.Connection", row: Row) -> bool:
    """Whether a conflict is this module's on the task's stage: a work stage tasks do not leave by hand, before a
    stage of its pipeline that queues the PRs."""
    from . import task_engine as E
    from .item_sources import github as GS

    stage: Row = row.get("stage") or {}
    if row.get("status") != "open" or stage.get("kind") != "work" or stage.get("by_hand"):
        return False
    work = [s for s in E.stages(conn, str(row["pipeline_id"])) if s["kind"] == "work"]
    at = next((i for i, s in enumerate(work) if s["id"] == stage.get("id")), None)
    return at is not None and any(GS.queues_prs(s) for s in work[at + 1 :])


def builder(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The bot that built the task's PR and may push to it: the owner the task has in the first stage of its pipeline
    that needs a PR open (Build). None when that is not a bot. The PR's author is not asked: a PR a bot opens through
    the company's GitHub App names the App, not the bot."""
    from . import task_engine as E
    from . import task_requirements as R

    for stage in E.stages(conn, str(row["pipeline_id"])):
        if stage["kind"] != "work":
            continue
        if any(
            r["kind"] == "item" and r.get("source") == "github" and r.get("condition") == "pr_open"
            for r in R.on_stage(conn, str(stage["id"]))
        ):
            who = E.owner_for(stage, row.get("assignee_id"))
            return str(who) if is_bot(who) else None
    return None


def standing(conn: "sqlite3.Connection", row: Row) -> "R.Standing | None":
    """The conflict as one more thing the open task needs to leave its stage, on the bot that built the PR (else the
    task's owner); None while no open PR of the task conflicts, or the stage is not before Merge."""
    from . import task_requirements as R

    if not row.get("visit") or not row.get("pipeline_id"):
        return None
    found = links(conn, str(row["id"]))
    if not found or not applies(conn, row):
        return None
    who = builder(conn, row) or row.get("owner_id")
    name = str(_h().actor_name(conn, who)) if who else "its owner"
    refs = ", ".join(ref(link) for link in found)
    requirement: Row = {
        "id": f"{KIND}:{row['id']}",
        "kind": KIND,
        "line": LINE,
        "stage_id": None,
        "visit_id": None,
        "person_id": None,
        "added_by_id": None,
        "can_push_past": 1,
        "who_oks": "none",
        "settings": {},
        "shows": None,
        "choices": None,
        "created_at": row["visit"]["entered_at"],
    }
    line = f"{refs} conflicts with its base branch: {name} rebases it on the default branch"
    return R.Standing(requirement, R.NOT_MET, str(who) if who else None, line)


def is_conflict(requirement: Row) -> bool:
    return requirement.get("kind") == KIND


def tell(conn: "sqlite3.Connection", task_id: str, pr: str, head: str) -> bool:
    """GitHub found PR `pr` conflicting at commit `head`: wake the bot the conflict is on (`standing`) once, keyed on
    the task's visit, the PR and the commit, with what to do; and give a bot that holds the stage and is not that bot
    one quiet line. False, telling no one, when the conflict is not this module's (`applies`): the caller wakes the
    owner as before."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import task_wakes
    from . import waiting_on as W

    row = E.task(conn, task_id)
    found = standing(conn, row) if row else None
    if not row or not found:
        return False
    H = _h()
    visit: Row = row["visit"]
    stage = str(row["stage"]["name"])
    key = f"{row['id']}:{visit['id']}:{KIND}:{pr}@{head}"
    who = found.on
    if is_bot(who):
        lead = (
            f"{pr} conflicts with its base branch. You built it: rebase its branch on the default branch, keep the "
            "work of both sides, push, and say so on this task. The task stays in "
            f"{stage}; you do not move it."
        )
        task_wakes.add(conn, row, who, visit, task_wakes.body(conn, row, lead, to=who), wake_key=key)
    holder = W.holder(conn, row)
    if is_bot(holder) and holder != who:
        name = H.actor_name(conn, who) if who else "Its owner"
        said = f"{pr} conflicts with its base branch. {name} is rebasing it: it is not yours to fix."
        if any(_at_head(r) for r in R.for_visit(conn, visit)):
            said += " A review is of the newest commit: once the new one is pushed, review that one."
        task_wakes.add(conn, row, holder, visit, said, wake_key=f"{key}:holder", quiet=True)
    return True


def _at_head(requirement: Row) -> bool:
    """A page requirement that holds a review of the PR's newest commit only (Review 1 and 2)."""
    settings: Row = requirement.get("settings") or {}
    return requirement.get("kind") == "page" and bool(settings.get("at_head"))
