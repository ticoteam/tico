# pyright: strict
"""The Merge stage: Tico puts a task's PRs in GitHub's merge queue and takes them out (docs/pipelines-v3.md "GitHub",
the Engineering pipeline's Merge stage). Track C owns this file.

- Entering a stage whose `on_enter` is `{"queue_prs": true}` (`stage_effects.enter`) owes one `queue_pr` effect per open
  linked PR at its head (`queue`). The Heartbeat's effects step (`run`) carries it out with GitHub's GraphQL
  `enqueuePullRequest` (auto-merge where the repo has no queue). No bot runs a merge command. Effect key:
  `queue_pr:<visit>:<ref>@<head sha>`; a second owe of the same key is nothing. A PR whose fact comes in after the task
  entered is owed then (`fact_changed`).
- A failed GitHub call is tried again after RETRY_S; after TRIES failed calls, or when GitHub answers no, the effect is
  `failed`: "PR merged" stands NOT_MET on the owner with "GitHub refused the merge: <reason>".
- GitHub removes a PR from the queue without merging it: the fact is saved (task_facts.save_pr: `queue_state` removed,
  `removed_at`, `removed_reason`, `failing_check`, `failing_check_url`). "PR merged" stands NOT_MET on the stage owner
  with "Dropped from the merge queue: <reason>", and the owner gets ONE wake (`removed`), keyed
  `<task>:<visit>:queue_removed:<ref>:<removed_at>`. Tico never queues it again by itself. The third removal in one
  visit (any PR, counted from the `pr_queue_removed` history rows) goes up the never-stuck ladder instead: the
  pipeline's manager bot has it on its 3 tries; a removal after that goes to a person.
- The owner (Release Manager, who may push to PR branches) then: queues it again as it is (`requeue`: CLI
  `tico task queue <task>`, MCP `tico_task_queue`, `POST /api/v2/tasks/{tid}/queue`); or pushes its own fix commits to
  the PR branch and then queues again; or sends the task back to Fixes 2 with the ordinary move
  (`tico task move <task> --to "Fixes 2"`), where the Builder owns it again. A commit pushed in Merge keeps the task in
  Merge: nothing sends it back through Review; the merge queue's checks are the gate. The manager hears of the new
  commit once (`changed_after_ok`, C22).
- The task is closed or sent back while a PR is queued: `pull` owes `dequeue_pr` (`dequeue_pr:<visit>:<ref>`), carried
  out with `dequeuePullRequest` (auto-merge off). A PR GitHub merged first stays merged ("merged anyway" note to the
  manager, keyed `<task>:<visit>:merged_anyway:<ref>`).
- History: `pr_queued` (asked again, and GitHub accepted), `pr_dequeued`, `pr_queue_removed`.

Effects use the existing `effects` table (owed, done, failed, moot) with `repo`, `number`, `head_sha` and `next_try_at`.
Every function takes the caller's open write transaction and never commits, except `run`.
"""

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import sqlite3

    from .task_facts import GitHub

Row = dict[str, Any]

QUEUE = "queue_pr"
DEQUEUE = "dequeue_pr"
WAKE_REMOVED = "queue_removed"  # the stage owner's one wake per removal
WAKE_MERGED_ANYWAY = "merged_anyway"
WAKE_CHANGED = "changed_after_ok"
WAKE_REFUSED = "queue_refused"
DROPS = 3  # removals in one visit before the task goes up the ladder instead of back to the stage owner
RETRY_S = (60, 300, 900)  # a failed GitHub call is tried again after these; then the effect is `failed`
TRIES = len(RETRY_S)  # failed calls before the effect is `failed`
BATCH = 50  # effects carried out per pass at most


def queue_key(visit_id: str, ref: str, head_sha: str) -> str:
    return f"{QUEUE}:{visit_id}:{ref}@{head_sha}"


def dequeue_key(visit_id: str, ref: str) -> str:
    return f"{DEQUEUE}:{visit_id}:{ref}"


def removed_wake_key(task_id: str, visit_id: str, ref: str, removed_at: str) -> str:
    return f"{task_id}:{visit_id}:{WAKE_REMOVED}:{ref}:{removed_at}"


def _h() -> Any:
    from . import hubdb

    return hubdb


def _facts() -> Any:
    from . import task_facts

    return task_facts


def _engine() -> Any:
    """task_engine, untyped here so its private helpers (`_refuse`, `_event`, `_tell_manager`) can be called."""
    from . import task_engine

    return task_engine


def queues_prs(stage: Row | None) -> bool:
    """Whether a stage's on-enter action is "queue the PRs to merge"."""
    action: Any = (stage or {}).get("on_enter")
    if isinstance(action, str):
        try:
            action = json.loads(action)
        except ValueError:
            return False
    return isinstance(action, dict) and cast(Row, action).get("queue_prs") is True


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def effects(conn: "sqlite3.Connection", visit_id: str, ref: str | None = None, kind: str = QUEUE) -> list[Row]:
    """A visit's effects of one kind, oldest first; for one PR when `ref` is given."""
    found = _rows(
        conn, "SELECT * FROM effects WHERE visit_id=? AND kind=? ORDER BY created_at, rowid", (visit_id, kind)
    )
    if ref is None:
        return found
    return [e for e in found if e["repo"] and _facts().pr_ref(str(e["repo"]), int(e["number"])) == ref]


def newest(conn: "sqlite3.Connection", visit_id: str, ref: str) -> Row | None:
    """The newest `queue_pr` effect of a visit for one PR that is not moot (what "PR merged" reads), or None."""
    found = [e for e in effects(conn, visit_id, ref) if e["state"] != "moot"]
    found.sort(key=lambda e: (str(e["updated_at"]), str(e["created_at"])))
    return found[-1] if found else None


def _owe(
    conn: "sqlite3.Connection", visit_id: str, kind: str, key: str, repo: str, number: int, head: str | None
) -> Row | None:
    """Owe one effect, once per key: the row written, or None when that key was owed before."""
    H = _h()
    ts = H.now()
    effect_id = H.new_id()
    written = conn.execute(
        "INSERT OR IGNORE INTO effects (id, visit_id, kind, key, state, tries, created_at, updated_at, repo, number, "
        "head_sha, next_try_at) VALUES (?, ?, ?, ?, 'owed', 0, ?, ?, ?, ?, ?, NULL)",
        (effect_id, visit_id, kind, key, ts, ts, repo, int(number), head),
    ).rowcount
    if not written:
        return None
    return _rows(conn, "SELECT * FROM effects WHERE id=?", (effect_id,))[0]


def _owe_queue(conn: "sqlite3.Connection", visit_id: str, link: Row) -> Row | None:
    fact = link["fact"]
    if not fact or fact["state"] != "open" or not fact.get("head_sha"):
        return None
    repo, number, head = str(link["repo"]), int(link["number"]), str(fact["head_sha"])
    return _owe(conn, visit_id, QUEUE, queue_key(visit_id, _facts().pr_ref(repo, number), head), repo, number, head)


def queue(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `queue_pr` effect for each open linked PR of the task at its saved head (`stage_effects.enter`). Returns
    the effects owed now. A PR whose fact is not read yet is owed when it comes in (`fact_changed`)."""
    visit = task.get("visit")
    if not visit or not queues_prs(task.get("stage")):
        return []
    owed = [_owe_queue(conn, str(visit["id"]), link) for link in _facts().prs_of(conn, str(task["id"]))]
    return [e for e in owed if e]


def failing_names(fact: Row) -> str:
    """The names of the checks that failed on a PR's newest commit, Tico's own left out."""
    checks: list[Row] = fact.get("checks") or []
    names = [str(c.get("name")) for c in checks if c.get("state") == "failure" and not c.get("tico")]
    return ", ".join(names) or "a check"


def requeue(conn: "sqlite3.Connection", task_id: str, by: str, *, seen_version: int | None = None) -> list[Row]:
    """Queue again: the stage owner's action on the Merge stage after GitHub removed a PR (or refused to queue it).
    Owes a new `queue_pr` for each such PR at its newest saved head: the old effect is moot, and one for that same head
    is owed again. Refuses `task_stage` (not on a stage whose on_enter queues PRs), `task_requirement` when no PR was
    removed or refused, while the PR's own checks on its newest commit are running ("The checks on <ref> are still
    running") or failing ("Checks failed on <ref>: <names>"), and when the caller may not; `task_changed` (a bot's
    stale `seen_version`), `task_ended`. Who may: the stage owner, the pipeline's manager, any person."""
    E = _engine()
    F = _facts()
    row: Row | None = E.task(conn, task_id)
    if not row:
        E._refuse(conn, by, "not-found", "No such task")
        return []

    def refuse(rule: str, detail: str) -> None:
        E._refuse(conn, by, rule, detail, task_id, row=row, then=("show",), version=row.get("version"))

    if row["status"] != "open":
        refuse("task_ended", f"The task is {row['stage']['name']}. Reopen it first.")
    E.check_version(conn, by, row, seen_version)
    pipeline: Row = row.get("pipeline") or {}
    manager = pipeline.get("manager_id")
    if not (E.is_human(by) or by in (row.get("owner_id"), manager)):
        refuse("task_requirement", "Only the stage owner, the pipeline's manager or a person can queue it again")
    if not queues_prs(row.get("stage")):
        refuse("task_stage", f"{row['stage']['name']} doesn't queue PRs")
    visit_id = str(row["visit"]["id"])
    again: list[Row] = []
    for link in F.prs_of(conn, task_id):
        fact = link["fact"]
        if not fact or fact["state"] != "open":
            continue
        ref = F.pr_ref(str(link["repo"]), int(link["number"]))
        last = newest(conn, visit_id, ref)
        if last and last["state"] == "owed":
            continue  # already queuing again
        if fact["queue_state"] == "removed" or (last and last["state"] == "failed"):
            again.append({**link, "ref": ref})
    if not again:
        refuse("task_requirement", "Nothing was taken out of the merge queue")
    for link in again:
        fact, ref = link["fact"], link["ref"]
        if fact["checks_state"] == "pending":
            refuse("task_requirement", f"The checks on {ref} are still running")
        if fact["checks_state"] == "failure":
            refuse("task_requirement", f"Checks failed on {ref}: {failing_names(fact)}")
    ts = _h().now()
    owed: list[Row] = []
    for link in again:
        ref, head = link["ref"], str(link["fact"]["head_sha"])
        key = queue_key(visit_id, ref, head)
        conn.execute(
            "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND key<>? "
            "AND lower(repo)=lower(?) AND number=? AND state IN ('owed', 'done', 'failed')",
            (ts, visit_id, QUEUE, key, link["repo"], int(link["number"])),
        )
        conn.execute(
            "UPDATE effects SET state='owed', tries=0, last_error=NULL, next_try_at=NULL, updated_at=? WHERE key=?",
            (ts, key),
        )
        _owe(conn, visit_id, QUEUE, key, str(link["repo"]), int(link["number"]), head)
        owed.append(_rows(conn, "SELECT * FROM effects WHERE key=?", (key,))[0])
        E._event(conn, task_id, by, "pr_queued", None, ref, "Queue again", {"ref": ref, "head": head, "again": True})
    conn.execute("UPDATE tasks SET version=?, updated_at=? WHERE id=?", (int(row.get("version") or 0) + 1, ts, task_id))
    E.changed(conn, task_id, by)
    return owed


def pull(conn: "sqlite3.Connection", task: Row, by: str) -> list[Row]:
    """Owe a `dequeue_pr` for each PR a `queue_pr` of the visit put in the queue (the task closed or went back). A
    `queue_pr` still owed is moot instead: nothing was queued yet (`run` takes out one GitHub queued meanwhile).
    Idempotent."""
    visit = task.get("visit")
    if not visit:
        return []
    visit_id = str(visit["id"])
    conn.execute(
        "UPDATE effects SET state='moot', updated_at=? WHERE visit_id=? AND kind=? AND state='owed'",
        (_h().now(), visit_id, QUEUE),
    )
    owed = [_owe_dequeue(conn, visit_id, e) for e in effects(conn, visit_id) if e["state"] == "done"]
    return [e for e in owed if e]


def _owe_dequeue(conn: "sqlite3.Connection", visit_id: str, effect: Row) -> Row | None:
    repo, number = str(effect["repo"]), int(effect["number"])
    return _owe(conn, visit_id, DEQUEUE, dequeue_key(visit_id, _facts().pr_ref(repo, number)), repo, number, None)


def _on_queueing_stage(conn: "sqlite3.Connection", task_ids: list[str]) -> list[Row]:
    E = _engine()
    out: list[Row] = []
    for task_id in task_ids:
        row: Row | None = E.task(conn, task_id)
        if row and row["status"] == "open" and row.get("visit") and queues_prs(row.get("stage")):
            out.append(row)
    return out


def removed_line(fact: Row) -> str:
    """Why a PR left the merge queue, with the check that failed there when one did."""
    reason = str(fact.get("removed_reason") or "GitHub gave no reason")
    check = fact.get("failing_check")
    return f"Dropped from the merge queue: {reason} ({check})" if check else f"Dropped from the merge queue: {reason}"


def removed(conn: "sqlite3.Connection", fact: Row) -> int:
    """After a saved fact shows a PR removed from the queue: give each task on a queueing stage that links it the one
    `queue_removed` wake for this `removed_at`, and a `pr_queue_removed` history row. Returns how many wakes were
    added. Tico never queues it again by itself."""
    from . import task_wakes

    E = _engine()
    F = _facts()
    repo, number = str(fact["repo"]), int(fact["number"])
    ref = F.pr_ref(repo, number)
    added = 0
    for row in _on_queueing_stage(conn, F.linked_tasks(conn, repo, number)):
        task_id, visit = str(row["id"]), row["visit"]
        key = removed_wake_key(task_id, str(visit["id"]), ref, str(fact["removed_at"]))
        if conn.execute(
            "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
            "AND json_valid(data) AND json_extract(data, '$.key')=?",
            (task_id, key),
        ).fetchone():
            continue
        data = {
            "key": key,
            "visit": visit["id"],
            "ref": ref,
            "reason": fact.get("removed_reason"),
            "check": fact.get("failing_check"),
            "url": fact.get("failing_check_url"),
        }
        E._event(conn, task_id, E.SYSTEM, "pr_queue_removed", None, ref, removed_line(fact), data)
        E.changed(conn, task_id, E.SYSTEM)
        owner = row.get("owner_id")
        if not E.is_bot(owner):
            continue  # a person owner has it in Needs you
        drops = drops_in_visit(conn, task_id, str(visit["id"]))
        if drops >= DROPS:
            _up_the_ladder(
                conn, task_id, drops, f"GitHub took {ref} out of the merge queue {drops} times in this stage."
            )
            continue
        link = f" {fact['failing_check_url']}" if fact.get("failing_check_url") else ""
        lead = (
            f"GitHub took {ref} out of the merge queue. {removed_line(fact)}.{link} Queue it again with "
            f"`tico task queue {task_id}`, push a fix to the PR branch and then queue it again, or move it back."
        )
        fresh: Row = E.task(conn, task_id) or row
        body = task_wakes.body(conn, fresh, lead, to=str(owner))
        if task_wakes.add(conn, fresh, str(owner), visit, body, wake_key=key):
            added += 1
    return added


def drops_in_visit(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> int:
    """How many times GitHub took a PR of the task out of the merge queue during this visit, any PR: its
    `pr_queue_removed` history rows, one per removal."""
    found = conn.execute(
        "SELECT COUNT(*) FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pr_queue_removed' "
        "AND json_valid(data) AND json_extract(data, '$.visit')=?",
        (task_id, visit_id),
    ).fetchone()
    return int(found[0]) if found else 0


def _up_the_ladder(conn: "sqlite3.Connection", task_id: str, drops: int, why: str) -> None:
    """The DROPS-th removal in a visit: the never-stuck ladder's next level has the task instead of the stage owner,
    the pipeline's manager bot on its 3 tries (else a person). Any removal after that: a person, so a PR that keeps
    falling out is never queued round and round by bots."""
    NS: Any = _never_stuck()
    row: Row | None = _engine().task(conn, task_id)
    if not row or not row.get("visit"):
        return
    done = {"woken": 0, "to_manager": 0, "to_person": 0}
    manager = NS._manager(conn, row, {})
    if drops == DROPS and manager and manager != row.get("owner_id"):
        NS._to_manager(conn, row, manager, why, _h().now(), done)
    else:
        NS._to_person(conn, row, row["visit"].get("level") or "owner", done)


def _never_stuck() -> Any:
    from . import never_stuck

    return never_stuck


def fact_changed(conn: "sqlite3.Connection", task_ids: list[str], after: Row, before: Row | None) -> None:
    """What a changed PR fact means on the Merge stage, for the open tasks that link it: a PR whose fact came in after
    the task entered is owed its `queue_pr` now (never once one was owed for it in this visit: Tico never queues a PR
    again by itself), and a new commit tells the pipeline's manager once ("Changed after OK", C22)."""
    E = _engine()
    F = _facts()
    repo, number = str(after["repo"]), int(after["number"])
    ref = F.pr_ref(repo, number)
    for row in _on_queueing_stage(conn, task_ids):
        visit_id = str(row["visit"]["id"])
        if not effects(conn, visit_id, ref):
            _owe_queue(conn, visit_id, {"repo": repo, "number": number, "fact": after})
        head = after.get("head_sha")
        if before and before.get("head_sha") and head and head != before["head_sha"] and after["state"] == "open":
            E._tell_manager(conn, row, f"Changed after OK: {ref} has a new commit", f"{WAKE_CHANGED}:{head}")


# ----------------------------------------------------------------------------- the effects step
def due(conn: "sqlite3.Connection", now: str, limit: int = BATCH) -> list[Row]:
    """Owed effects whose next try is due, oldest first, with the task each is for."""
    return _rows(
        conn,
        "SELECT e.*, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id WHERE e.state='owed' "
        "AND e.repo IS NOT NULL AND (e.next_try_at IS NULL OR e.next_try_at <= ?) "
        "ORDER BY e.created_at, e.rowid LIMIT ?",
        (now, limit),
    )


def run(store: Any, github: "GitHub", now: str | None = None) -> int:
    """The effects step on the Heartbeat: carry out owed `queue_pr` / `dequeue_pr` effects with no transaction held
    during GitHub calls, record each in a short write, and save what GitHub said as a fact. Returns how many were
    carried out (done, or failed for good)."""
    F = _facts()
    at: str = now or _h().now()
    with store.read() as conn:
        owed = due(conn, at)
    count = 0
    for effect in owed:
        repo, number = str(effect["repo"]), int(effect["number"])
        version: int = F.version_now()
        try:
            if effect["kind"] == QUEUE:
                github.ready(repo, number)  # a Builder's draft reaches Merge: GitHub queues no draft
                said = github.enqueue(repo, number, str(effect["head_sha"]))
            else:
                said = github.dequeue(repo, number)
        except Exception as exc:  # noqa: BLE001 - GitHub down or refusing the call: tried again later
            with store.transaction() as conn:
                count += _failed_call(conn, effect, str(exc) or type(exc).__name__, at)
            continue
        with store.transaction() as conn:
            count += _record(conn, effect, said, version)
    return count


def _failed_call(conn: "sqlite3.Connection", effect: Row, reason: str, now: str) -> int:
    """A GitHub call that failed: tried again after RETRY_S; after TRIES failed calls the effect is `failed`."""
    H = _h()
    tries = int(effect["tries"] or 0) + 1
    if tries < TRIES:
        conn.execute(
            "UPDATE effects SET tries=?, last_error=?, next_try_at=?, updated_at=? WHERE id=? AND state='owed'",
            (tries, reason[:500], H.shift(now, seconds=RETRY_S[tries - 1]), H.now(), effect["id"]),
        )
        return 0
    return _give_up(conn, effect, tries, reason)


def _give_up(conn: "sqlite3.Connection", effect: Row, tries: int, reason: str) -> int:
    """The effect is `failed`. For a `queue_pr` "PR merged" says GitHub refused, on the stage owner, told once."""
    E = _engine()
    written = conn.execute(
        "UPDATE effects SET state='failed', tries=?, last_error=?, next_try_at=NULL, updated_at=? "
        "WHERE id=? AND state='owed'",
        (tries, reason[:500], _h().now(), effect["id"]),
    ).rowcount
    if not written:
        return 0
    if effect["kind"] != QUEUE:
        return 1
    row: Row | None = E.task(conn, str(effect["task_id"]))
    visit: Row = (row.get("visit") or {}) if row else {}
    if row and row["status"] == "open" and visit.get("id") == effect["visit_id"]:
        ref = _facts().pr_ref(str(effect["repo"]), int(effect["number"]))
        E.changed(conn, str(row["id"]), E.SYSTEM)
        E.tell_owner(
            conn,
            str(row["id"]),
            f"GitHub refused the merge of {ref}: {reason}. Queue it again with `tico task queue {row['id']}` once "
            "it can merge, or move it back.",
            f"{WAKE_REFUSED}:{ref}:{effect['head_sha']}",
        )
    return 1


def _record(conn: "sqlite3.Connection", effect: Row, said: Row, version: int) -> int:
    """Record what GitHub said to one effect, and save it as the PR's fact."""
    E = _engine()
    F = _facts()
    repo, number = str(effect["repo"]), int(effect["number"])
    ref: str = F.pr_ref(repo, number)
    task_id = str(effect["task_id"])
    found = conn.execute("SELECT state FROM effects WHERE id=?", (effect["id"],)).fetchone()
    state = str(found[0]) if found else "moot"
    if not said.get("ok"):
        if state != "owed":
            return 0
        return _give_up(conn, effect, int(effect["tries"] or 0) + 1, str(said.get("reason") or "GitHub said no"))
    how = str(said.get("how") or "")
    conn.execute(
        "UPDATE effects SET state=CASE WHEN state='owed' THEN 'done' ELSE state END, tries=tries+1, last_error=NULL, "
        "next_try_at=NULL, updated_at=? WHERE id=?",
        (_h().now(), effect["id"]),
    )
    if effect["kind"] == QUEUE:
        if how in F.QUEUED:
            F.save_and_settle(conn, repo, number, {"queue_state": how}, version)
            data = {"ref": ref, "head": effect["head_sha"], "how": how}
            E._event(conn, task_id, E.SYSTEM, "pr_queued", None, ref, None, data)
            if state == "moot":  # the task left the stage while GitHub was queuing it: take it out again
                _owe_dequeue(conn, str(effect["visit_id"]), effect)
        else:  # merged at once, where the repo has neither a queue nor auto-merge: read in full on the next pass
            conn.execute("UPDATE pr_facts SET read_at='' WHERE repo=? AND number=?", (repo, number))
        return 1
    if how == "merged":
        conn.execute("UPDATE pr_facts SET read_at='' WHERE repo=? AND number=?", (repo, number))
        _merged_anyway(conn, task_id, str(effect["visit_id"]), ref)
    elif how == "dequeued":
        F.save_and_settle(conn, repo, number, {"queue_state": "none"}, version)
        E._event(conn, task_id, E.SYSTEM, "pr_dequeued", ref, None, None, {"ref": ref})
    return 1


def _merged_anyway(conn: "sqlite3.Connection", task_id: str, visit_id: str, ref: str) -> None:
    """GitHub merged a PR before Tico could take it out of the queue: it stays merged. The pipeline's manager bot hears
    of it once, keyed on the visit that queued it."""
    from . import task_wakes

    E = _engine()
    row: Row | None = E.task(conn, task_id)
    if not row:
        return
    pipeline: Row = row.get("pipeline") or {}
    manager = pipeline.get("manager_id")
    visit = _rows(conn, "SELECT * FROM visits WHERE id=?", (visit_id,))
    if not E.is_bot(manager) or not visit:
        return
    task_wakes.add(
        conn,
        row,
        str(manager),
        visit[0],
        f"{row.get('title') or 'A task'}: {ref} merged anyway. GitHub merged it before Tico could take it out of the "
        "merge queue.",
        wake_key=f"{task_id}:{visit_id}:{WAKE_MERGED_ANYWAY}:{ref}",
        quiet=True,
    )
