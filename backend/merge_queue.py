# pyright: strict
"""The Merge stage: Tico puts a task's PRs in GitHub's merge queue and takes them out (docs/pipelines-v3.md "GitHub",
the Engineering pipeline's Merge stage). Track C owns this file.

- Entering a stage whose `on_enter` is `{"queue_prs": true}` (`stage_effects.enter`) owes one `queue_pr` effect per open
  linked PR at its head (`queue`). The Heartbeat's effects step (`run`) carries it out with GitHub's GraphQL
  `enqueuePullRequest` (auto-merge where the repo has no queue). No bot runs a merge command. Effect key:
  `queue_pr:<visit>:<ref>@<head sha>`; a second owe of the same key is nothing.
- GitHub removes a PR from the queue without merging it: the fact is saved (task_facts.save_pr: `queue_state` removed,
  `removed_at`, `removed_reason`, `failing_check`, `failing_check_url`). "PR merged" stands NOT_MET on the stage owner
  with "Dropped from the merge queue: <reason>", and the owner gets ONE wake (`removed`), keyed
  `<task>:<visit>:queue_removed:<ref>:<removed_at>`. Tico never queues it again by itself.
- The owner then queues it again (`requeue`: CLI `tico task queue <task>`, MCP `hub_task_queue` {task, seen_version},
  `POST /api/v2/tasks/{tid}/queue`), or sends the task back to Fixes 2 with the ordinary move (`tico task move <task>
  --to "Fixes 2"`), where the Builder owns it again.
- The task is closed or sent back while a PR is queued: `pull` owes `dequeue_pr` (`dequeue_pr:<visit>:<ref>`), carried
  out with `dequeuePullRequest` (auto-merge off). A PR GitHub merged first stays merged ("merged anyway" wake, keyed
  `<task>:<visit>:merged_anyway:<ref>`).

Effects use the existing `effects` table (owed, done, failed, moot) with `repo`, `number`, `head_sha` and `next_try_at`.
Every function takes the caller's open write transaction and never commits, except `run`.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

    from .task_facts import GitHub

Row = dict[str, Any]

QUEUE = "queue_pr"
DEQUEUE = "dequeue_pr"
WAKE_REMOVED = "queue_removed"  # the stage owner's one wake per removal
WAKE_MERGED_ANYWAY = "merged_anyway"
RETRY_S = (60, 300, 900)  # a failed GitHub call is tried again after these; then the effect is `failed`


def queue_key(visit_id: str, ref: str, head_sha: str) -> str:
    return f"{QUEUE}:{visit_id}:{ref}@{head_sha}"


def dequeue_key(visit_id: str, ref: str) -> str:
    return f"{DEQUEUE}:{visit_id}:{ref}"


def removed_wake_key(task_id: str, visit_id: str, ref: str, removed_at: str) -> str:
    return f"{task_id}:{visit_id}:{WAKE_REMOVED}:{ref}:{removed_at}"


def queue(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `queue_pr` effect for each open linked PR of the task at its saved head (`stage_effects.enter`). Returns
    the effects owed now. Track C."""
    raise NotImplementedError("merge_queue.queue: track C")


def requeue(conn: "sqlite3.Connection", task_id: str, by: str, *, seen_version: int | None = None) -> list[Row]:
    """Queue again: the stage owner's action on the Merge stage after GitHub removed a PR. Owes a new `queue_pr` for
    each removed PR (its key carries the head, and the old effect for that head is moot first). Refuses `task_stage`
    (not on a stage whose on_enter queues PRs), `task_requirement` (no PR was removed), `task_changed` (a bot's stale
    `seen_version`), `task_ended`. Who may: the stage owner, the pipeline's manager, any person. Track C."""
    raise NotImplementedError("merge_queue.requeue: track C")


def pull(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `dequeue_pr` for each PR a `queue_pr` of the visit put in the queue (the task closed or went back).
    Idempotent. Track C."""
    raise NotImplementedError("merge_queue.pull: track C")


def removed(conn: "sqlite3.Connection", fact: Row) -> int:
    """After a saved fact shows a PR removed from the queue: give each task on a queueing stage that links it the one
    `queue_removed` wake for this `removed_at`. Returns how many wakes were added. Track C."""
    raise NotImplementedError("merge_queue.removed: track C")


def run(store: Any, github: "GitHub", now: str | None = None) -> int:
    """The effects step on the Heartbeat: carry out owed `queue_pr` / `dequeue_pr` effects with no transaction held
    during GitHub calls, record each in a short write, and save what GitHub said as a fact. Returns how many were
    carried out. Track C."""
    raise NotImplementedError("merge_queue.run: track C")
