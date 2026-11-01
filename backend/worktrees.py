"""Task worktree authorization and computer lifecycle reports (task_links v2 schema)."""
import json
import re
import sqlite3
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from fastapi import Request
from pydantic import Field
from clients import git_repository, sessions

from . import hubdb as H, repositories as R, runner_wire, task_relations as TR, worktree_waits as waits
from .auth import validate_identity
from .live_privacy import ROOM_PREFIX
from .models import Contract
from .roadmap_flow import READY_FROM
from .store import Problem, readiness_document

CLOSED = ('done', 'closed', 'declined')
PR_FINISHED = ('merged', 'closed', 'shipped')
RESTORE_PROGRESS = ('attached_pending', 'initializing', 'checkout_ready', 'setup_running',
                    'setup_failed', 'ready', 'legacy_present', 'unverified')
# How far a modern checkout has got. A computer builds its heartbeat report from the record it read on an
# earlier pass, so a report can echo a state the record has since moved past; it never moves a record back.
CHECKOUT_PROGRESS = {'queued': 0, 'attached_pending': 0, 'initializing': 1, 'checkout_ready': 2,
                     'setup_running': 3, 'setup_failed': 3, 'ready': 4}
IDENTITY = ('expected_head', 'checkout_target', 'expected_base')
# A computer reports each failed worktree action (restore, prepare, remove) with this prefix until that action succeeds.
ACTION_FAILED = 'Worktree action failed;'
REMOVAL = ('cleanup_requested', 'release_requested', 'delete_requested')


def ready(state, detail):
    """The computer's acknowledgement that checkout and setup finished, naming what it checked out."""
    return (state == 'present' and detail.get('checkout_state') == 'ready' and not detail.get('setup_pending')
            and all(detail.get(key) for key in IDENTITY))


def current_error(state, detail):
    """The worktree's failure still in force. A failed restore or prepare is over once the worktree is set up and
    acknowledged ready, though a computer keeps repeating its last action error until another action succeeds; a
    refused removal stays while removal is asked, and a failure seen when inspecting the tree is always current."""
    error = str(detail.get('error') or '') or (str(detail['pending_expired'])
                                               if state == 'pending' and detail.get('pending_expired') else '')
    if error.startswith(ACTION_FAILED) and ready(state, detail) and not any(detail.get(key) for key in REMOVAL):
        return ''
    return error


def refused_cleanup(state, detail):
    """A cleanup the computer refused that is still asked. A computer keeps its action errors in memory, so after a
    restart its report has none; the stored error stays, or the backoff before the next attempt would be skipped."""
    return (state != 'removed' and str(detail.get('error') or '').startswith(ACTION_FAILED)
            and detail.get('cleanup_attempts', 0) > 0 and any(detail.get(key) for key in REMOVAL))


def stale_checkout_report(detail, reported_state):
    current = detail.get('checkout_state')
    if current == 'ready' and reported_state == 'unverified':
        return all(detail.get(key) for key in IDENTITY)
    return (current in CHECKOUT_PROGRESS and reported_state in CHECKOUT_PROGRESS
            and CHECKOUT_PROGRESS[reported_state] < CHECKOUT_PROGRESS[current])


def restore_requested(row, detail):
    if (row['state'] == 'removed' and detail.get('removed_by') == 'cleanup'
            and detail.get('restore_on_reopen')):
        return True
    checkout_state = detail.get('checkout_state')
    progress_recorded = (checkout_state in RESTORE_PROGRESS
                         or detail.get('setup_pending') and checkout_state != 'queued')
    return (row['state'] == 'pending' and row['added_by'].startswith('human:')
            and not progress_recorded)


def _demoted_legacy_report(row, detail):
    """Recognize a legacy present row demoted by a heartbeat without checkout fields.

    The old backend persisted these report fields before changing present to pending. They are
    history for the upgraded runner to re-verify, not checkout completion proof by themselves.
    """
    last_commit = detail.get('last_commit')
    dirty_files = detail.get('dirty_files')
    return (row['state'] == 'pending'
            and detail.get('checkout_state') in (None, 'unverified')
            and not detail.get('setup_pending')
            and not any(detail.get(key) for key in ('expected_head', 'checkout_target', 'expected_base'))
            and bool(row['branch'])
            and detail.get('current_branch') == row['branch']
            and isinstance(last_commit, str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', last_commit)
            and type(dirty_files) is int and dirty_files >= 0)


class Create(Contract):
    repo: str = Field(max_length=200)


class Attach(Contract):
    path: str = Field(max_length=1000)
    repo: str | None = Field(default=None, max_length=200)
    branch: str | None = Field(default=None, max_length=200)


class SessionTask(Contract):
    bot: str = Field(min_length=1, max_length=100)
    task: str = Field(min_length=1, max_length=100)


class SessionTasks(Contract):
    sessions: list[SessionTask] = Field(max_length=500)


class Update(Contract):
    state: Literal['pending', 'present', 'missing', 'removed', 'unknown'] | None = None
    path: str | None = Field(default=None, max_length=1000)
    computer_id: str | None = None
    branch: str | None = Field(default=None, max_length=200)
    cleanup: bool = False
    setup_pending: bool | None = None
    checkout_state: Literal['queued', 'attached_pending', 'initializing', 'checkout_ready', 'setup_running', 'setup_failed', 'ready', 'legacy_present', 'unverified'] | None = None
    expected_head: str | None = Field(default=None, pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')
    checkout_target: str | None = Field(default=None, max_length=300)
    expected_base: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    snapshot_skipped: str | None = Field(default=None, max_length=300)
    restore_source: str | None = Field(default=None, max_length=100)
    skipped_files: list[str] | None = Field(default=None, max_length=100)


def relative(path):
    p = PurePosixPath(path)
    if not path or p.is_absolute() or '..' in p.parts or '\\' in path or any(ord(ch) < 32 for ch in path) or str(p) in ('.', 'repos') or p.parts[0].lower() == 'repos':
        raise Problem('worktree_path', 'Use a worktree path inside the team workspace, outside repos', 422)
    return str(p)


def supported(c):
    # No runtime schema changes: the tasks migration owns these columns.
    return 'computer_id' in {r[1] for r in c.execute('PRAGMA table_info(task_links)')}


LIMIT = 100           # live worktrees per bot; the computer's free-disk floor can stop it sooner
CLEANUP_TRIES = 6     # a refused worktree cleanup is asked again after 2, 4, ... 64 minutes, then left
READINGS = ('last_activity', 'size_mb', 'activity_at')     # a worktree report's readings that move as a bot works
READINGS_EVERY = 300
PENDING_HOURS = 6     # a checkout or setup with no progress this long has failed; its owner is told and may retry


def live_turn(c, task_id, owner, memo=None):
    """The owner bot is in a turn on this task now: a leased or running attempt that carries the task. Only the turns
    of the bot that owns both the tree and the task count: a reviewer's or another bot's turn on the task, or a turn of
    a bot the task has since moved away from, neither holds the tree nor shields it from the setup deadline."""
    if not task_id or not H.is_bot(owner or ''):
        return False
    task = c.execute('SELECT owner_id FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task or task[0] != owner:
        return False
    memo = {} if memo is None else memo
    key = ("live_turn", owner)
    if key not in memo:
        memo[key] = _live_tasks(c, H.actor_id(owner), memo)
    return task_id in memo[key]


def _live_tasks(c, bot, memo):
    from .task_privacy import attempt_tasks
    try:
        rows = c.execute("SELECT id FROM attempts WHERE bot_id=? AND state IN ('leased','running')", (bot,)).fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise                   # a fault must not release a checkout under its running turn
        return frozenset()          # a local hub has no attempts table
    return frozenset(tid for row in rows for tid in attempt_tasks(c, row[0], memo))


def live_turn_cache(c, computer):
    """Prepare provenance without the write lock; the caller validates its source clock before using it."""
    from . import provenance_clock
    memo = {("provenance_clock",): provenance_clock.current(c)}
    owners = {row['owner_id'] for row in inventory(c, computer) if H.is_bot(row['owner_id'] or '')}
    for owner in owners:
        memo[("live_turn", owner)] = _live_tasks(c, H.actor_id(owner), memo)
    return memo


def start_refusal(c, task):
    """Why no new worktree starts for this task now, or '': a coding step cannot run on a finished task, one not
    started (On Deck, or any task still `open`) or one blocked by a task whose code is not merged yet (`unmerged`). A
    task being worked (`doing`), or one at a review or waiting while its fix-forward or a review fix runs, gets one; so
    does reassigned work."""
    if task['status'] in CLOSED:
        return f"This task is {task['status']}: a finished task gets no new worktree."
    if task['status'] == 'open':
        return 'This task has not started: move it to In Progress first.'
    for row in TR.live_blockers(c, task['id'], H.ACTIVE_STATUSES):
        if why := unmerged(c, row):
            name = (f"#{row['number']} " if row.get('number') else '') + (row.get('title') or row['id'])
            return f'This task is blocked by {name}, {why}: it gets a worktree once that code is merged.'
    return ''


def unmerged(c, blocker):
    """Why an open blocker's code is not on main yet, in plain words, or '' when it is: a worktree builds from source,
    so a blocker counts once it is merged, not once it ships. Merged means a ready status, its type's Pending Release
    step or a later one, or a merged pull request with none still open (one closed unmerged was abandoned). The cutoff
    is the Pending Release step itself: a step with a ready status may come before work that still changes code."""
    if blocker['status'] == 'ready':
        return ''
    step = c.execute('SELECT name,position FROM task_steps WHERE id=?', (blocker.get('step_id'),)).fetchone()
    release = c.execute('SELECT min(position) FROM task_steps WHERE type_id=? AND lower(trim(name))=?',
                        (blocker.get('type_id'), READY_FROM)).fetchone()[0] if step else None
    if release is not None and step['position'] >= release:
        return ''
    prs = c.execute("SELECT number,url,coalesce(state,'open') AS state FROM task_links WHERE task_id=? AND kind='pr' "
                    "ORDER BY created_at,rowid", (blocker['id'],)).fetchall()
    waiting = [pr for pr in prs if pr['state'] not in ('merged', 'closed')]
    if not waiting and any(pr['state'] == 'merged' for pr in prs):
        return ''
    where = f"still at {step['name']}" if step else f"still {blocker['status']}"
    if not waiting:
        return f'{where} with no merged pull request'
    names = ', '.join(f"#{pr['number']}" if pr['number'] else pr['url'] for pr in waiting)
    return f"{where} with pull request {names} not merged yet"


def released_event(c, row, detail):
    """One `worktree.released` event per tree, with why: the task's run history and Health read it."""
    reason = detail.get('release_reason') or 'cleanup'
    data = {'link': row['id'], 'path': row['path'], 'reason': reason}
    if reason == 'task finished' and (detail.get('dirty_files') or detail.get('ahead')) \
            and not detail.get('snapshot_skipped'):
        data['wip_branch'] = 'wip/' + row['task_id'][:8] + '-' + row['id'][:6]     # runner/worktrees.py wip_branch
    if detail.get('stale_push_cleared'):
        data['reason'] += f" (stale push {detail['stale_push_cleared'][:12]} cleared)"
    H.event(c, H.KEEPER, 'worktree.released', row['task_id'], data)


def cancel_pending(c, row, detail, reason):
    """End a `pending` worktree nothing was checked out for (queued, or an attach not yet confirmed): no computer is
    asked, nothing on disk is touched, its place frees at once. A reserved wait for it ends once with the reason."""
    detail = {**detail, 'removed_by': 'cancelled', 'release_reason': 'pending cancelled', 'cancelled': reason}
    # Guarded on the stored state: serving waits and the heartbeat's own pass can both reach one reservation.
    if not c.execute("UPDATE task_links SET state='removed',detail=?,updated_at=? WHERE id=? AND state='pending'",
                     (json.dumps(detail), H.now(), row['id'])).rowcount:
        return
    if wait := waits.preparing(c, row['id']):
        waits._finish(c, wait, 'cancelled', reason)
    released_event(c, row, detail)


def checked_out(detail):
    """A pending checkout that may have a folder on disk with its recorded proof: it resumes, never starts over."""
    return (detail.get('checkout_state') in ('initializing', 'checkout_ready', 'setup_running', 'setup_failed')
            and all(detail.get(key) for key in IDENTITY))


def expire_pending(c, row, detail):
    """A checkout or setup that made no progress for PENDING_HOURS has failed: it would otherwise hold a place
    forever. The tree keeps its place and says why, its computer stops preparing it, a reserved wait ends, and its
    owner is told once how to try again on the same worktree."""
    reason = f'Setup did not finish within {PENDING_HOURS} h'
    detail['pending_expired'] = reason
    if wait := waits.preparing(c, row['id']):
        waits._finish(c, wait, 'failed', reason)
    retry = ('hub task worktree setup ' if checked_out(detail) else 'hub task worktree add ') + (row['repo'] or '')
    if row.get('bot_state', 'active') == 'active':
        H._wake(c, H.task(c, row['task_id']), row['owner_id'],
                f"Tico stopped preparing this task's {row['repo'] or ''} worktree at {row['path']}: {reason}. Its place "
                f"stays this task's; run `{retry}` to try again on the same worktree.",
                refs={'wake': 'blocker'}, trigger='other', source=row['id'])


def held_reason(c, row, detail, memo=None):
    """Why a tree whose pull requests are finished is not released yet, or '': its bot is in a turn on the task (a
    fix-forward or review fix starting), or its pushed branch has moved past the head of the last finished pull
    request on it (fix-forward commits after a merge). Both clear on their own: the turn ends, or a new pull request
    carries the commits and finishes."""
    if live_turn(c, row['task_id'], row.get('owner_id'), memo):
        return 'held: live turn'
    last = c.execute("SELECT number,detail,coalesce(pr_merged_at,updated_at) AS finished_at FROM task_links WHERE task_id=? AND kind='pr' AND lower(repo)=lower(?) "
                     f"AND branch=? AND state IN {PR_FINISHED} ORDER BY coalesce(pr_merged_at,updated_at,created_at) DESC LIMIT 1",
                     (row['task_id'], row['repo'] or '', row['branch'] or '')).fetchone() if row['branch'] else None
    pr_head = json.loads(last['detail'] or '{}').get('head_sha') if last else None
    # The computer's committed head, once it has nothing unpushed, is the branch as it is now. A push webhook's head
    # that differs from the merged head holds too (a push from elsewhere), but only until the computer's first report
    # after that delivery: a clean tree with nothing unpushed at the merged head proves it stale (a delayed or
    # replayed delivery), and it is cleared and named in the release.
    runner = detail.get('last_commit') if detail.get('ahead') == 0 else None
    if pr_head and runner and runner != pr_head:
        return f"held: new commits since PR #{last['number']}"
    pushed = detail.get('pushed')
    if pr_head and pushed and pushed != pr_head:
        seen = detail.get('runner_report_at') or ''
        if (seen > (detail.get('pushed_last_at') or detail.get('pushed_at') or '') and runner == pr_head
                and not detail.get('dirty_files')):
            detail['stale_push_cleared'] = detail.pop('pushed')
            return ''
        return f"held: new commits since PR #{last['number']}"
    return ''


def inventory(c, computer):
    if not supported(c):
        return []
    owner = "coalesce(json_extract(l.detail,'$.owner'),CASE WHEN l.added_by LIKE 'bot:%' THEN l.added_by ELSE t.owner_id END)"
    return [dict(r) for r in c.execute(f"SELECT l.*,{owner_id} AS owner_id,t.status AS task_status,"
            "coalesce(b.state,'active') AS bot_state FROM task_links l JOIN tasks t ON t.id=l.task_id "
            # 'bot:'||slug = owner exactly when owner starts with 'bot:' and slug = its rest. Written that way the
            # join seeks bots' slug key; the expression form scanned every bot for each worktree link kept.
            f"LEFT JOIN bots b ON b.id=substr({owner},5) AND substr({owner},1,4)='bot:' "
            "WHERE l.kind='worktree' AND l.computer_id=? AND (coalesce(l.state,'unknown')<>'removed' "
            "OR (t.status NOT IN ('done','closed','declined') AND b.state='active'))", (computer,))]


def attached(c, task_id, computer, bot):
    """The task's worktrees this bot's turn on this computer works in: its own, set up, not removed. A claimed
    task turn carries them (backend/execution.py) so the runner adds each to the session with its repo's rules."""
    if not supported(c) or not task_id or not computer:
        return []
    owner = "coalesce(json_extract(l.detail,'$.owner'),CASE WHEN l.added_by LIKE 'bot:%' THEN l.added_by ELSE t.owner_id END)"
    rows = c.execute(f"SELECT l.repo,l.branch,l.path,l.detail FROM task_links l JOIN tasks t ON t.id=l.task_id "
                     f"WHERE l.task_id=? AND l.kind='worktree' AND l.computer_id=? AND l.state='present' AND {owner}=? "
                     "ORDER BY l.created_at", (task_id, computer, "bot:" + bot)).fetchall()
    return [{"repo": r["repo"] or "", "branch": r["branch"] or "", "path": r["path"]} for r in rows
            if r["path"] and not json.loads(r["detail"] or "{}").get("setup_pending")]


def allocation_has_pr(row, detail, prs):
    """A finished PR can release working trees only from the allocation it was opened for.

    A revived link keeps its original creation date and branch, so its allocation needs its own timestamp.
    Legacy links use creation; missing PR branch/repo metadata keeps its established compatibility behavior.
    """
    since = H.parse_ts(detail.get('allocation_started') or row['created_at'])
    if since is None:
        return False

    def at_or_after(value):
        # GitHub merge times can omit fractions that allocation timestamps include.
        timestamp = H.parse_ts(value)
        return timestamp is not None and timestamp >= since

    return any(at_or_after(pr['created_at']) and (not pr['pr_merged_at'] or at_or_after(pr['pr_merged_at']))
               and (not pr['repo'] or not row['repo'] or pr['repo'].casefold() == row['repo'].casefold())
               and (not pr['branch'] or not row['branch'] or pr['branch'] == row['branch']) for pr in prs)


def holdings(c, owner):
    """The bot's live worktrees, each with what clears it: `merged` or `closed` (its pull requests are finished and the
    computer releases it once the bot is idle), `merge_ready` (an approved, passing pull request), `task_finished`,
    or `in_progress`. A worktree the computer kept says why in `kept`."""
    rows = c.execute("SELECT l.id,l.task_id,l.path,l.repo,l.branch,l.detail,l.created_at,coalesce(l.updated_at,l.created_at) AS updated_at,t.title,t.status FROM task_links l "
                     "JOIN tasks t ON t.id=l.task_id WHERE l.kind='worktree' AND coalesce(l.state,'unknown')<>'removed' "
                     "AND coalesce(json_extract(l.detail,'$.owner'),t.owner_id)=? ORDER BY l.created_at", (owner,)).fetchall()
    out = []
    for row in rows:
        prs = c.execute("SELECT url,state,review_state,checks,mergeable,created_at,pr_merged_at,repo,branch FROM task_links WHERE task_id=? AND kind='pr'",
                        (row['task_id'],)).fetchall()
        states = [p['state'] for p in prs]
        detail = json.loads(row['detail'] or '{}')
        if row['status'] in CLOSED:
            clear, why = 'task_finished', 'task ' + row['status']
        elif (prs and all(s in PR_FINISHED for s in states)
              and (row['status'] != 'doing' or allocation_has_pr(row, detail, prs))):
            clear = 'merged' if any(s in ('merged', 'shipped') for s in states) else 'closed'
            why = 'pull request ' + clear
        elif any(p['state'] == 'open' and p['review_state'] == 'approved' and p['checks'] != 'failing'
                 and p['mergeable'] != 'conflict' for p in prs):
            clear, why = 'merge_ready', 'pull request approved, ready to merge'
        else:
            clear, why = 'in_progress', 'in progress'
        item = {'link_id': row['id'], 'task_id': row['task_id'], 'task': row['title'], 'path': row['path'],
                'repo': row['repo'], 'branch': row['branch'], 'clear': clear, 'why': why, 'updated_at': row['updated_at'],
                'pull_requests': [{'url': p['url'], 'state': p['state']} for p in prs]}
        if detail.get('error') and (detail.get('cleanup_requested') or detail.get('release_requested')):
            item['kept'] = detail['error']
        elif detail.get('held') or detail.get('pr_error'):
            item['kept'] = detail.get('held') or detail['pr_error']
        out.append(item)
    order = {'merged': 0, 'closed': 1, 'task_finished': 2, 'merge_ready': 3, 'in_progress': 4}
    return sorted(out, key=lambda w: order[w['clear']])


def preparable(c, row, detail):
    """A worktree reserved for a waiting task (backend/worktree_waits.py): its computer checks it out and runs setup
    while the bot is idle, then reports it present. A failed attempt is not repeated; the task is told instead."""
    return (row['state'] == 'pending' and bool(detail.get('wait_id')) and not detail.get('error')
            and not detail.get('pending_expired')
            and detail.get('checkout_state') in ('queued', 'initializing', 'checkout_ready', 'setup_running')
            and bool(waits.preparing(c, row['id'])))


def heartbeat(c, who, reports, capable, default_org="", live_tasks=None):
    if not supported(c):
        return []
    rows = {r['id']: r for r in inventory(c, who.runner_id)}
    for report in reports or []:
        row = rows.get(report.link_id)
        if not row:
            continue
        if report.repo and row['repo'] is None:
            connection = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
            grants = R.access(c, H.actor_id(row['owner_id']), connection['org'] if connection else default_org)['effective']
            grant = next((r for r in grants if r['full_name'].lower() == report.repo.lower() and r['access'] == 'write'), None)
            if connection and not grant:
                c.execute("UPDATE task_links SET state='unknown',detail=?,updated_at=? WHERE id=?",
                          (json.dumps({**json.loads(row['detail'] or '{}'), 'error': 'This bot needs write access to the attached repository'}), H.now(), row['id']))
                continue
            if not connection:
                try:
                    git_repository.address(report.repo)
                except ValueError:
                    continue
            row['repo'] = grant['full_name'] if connection else report.repo
            c.execute('UPDATE task_links SET repo=? WHERE id=?', (row['repo'], row['id']))
        detail = json.loads(row['detail'] or '{}')
        before = (row['state'], row['branch'], dict(detail))
        # 0.2.x runners do not send checkout_state. Keep their established report semantics
        # for links that were already known to exist (including offline/unknown links), while
        # never using that compatibility path for a modern queued/setup-pending checkout.
        legacy_restore_receipt = (detail.get('legacy_restore_pending') is True
                                  or (row['added_by'] or '').startswith('human:'))
        legacy_restore_passthrough = (legacy_restore_receipt
                                      and row['state'] in ('present', 'missing', 'unknown')
                                      and report.state in ('present', 'missing')
                                      and detail.get('checkout_state') is None
                                      and report.checkout_state is None
                                      and detail.get('setup_pending'))
        legacy_passthrough = ((row['state'] in ('present', 'unknown', 'missing')
                               and report.state in ('present', 'missing')
                               and detail.get('checkout_state') is None and report.checkout_state is None
                               and not detail.get('setup_pending'))
                              or legacy_restore_passthrough)
        now = H.now()
        # A report that leaves a reading out (a removed checkout has no commit or activity) changes nothing:
        # detail keeps the last value, so comparing None with it would count as activity on every heartbeat.
        changed = any(getattr(report, key) is not None and detail.get(key) != getattr(report, key)
                      for key in ('last_commit', 'dirty_files', 'last_activity'))
        # A push changes none of the above; only the count of commits not on the remote drops.
        pushed = report.ahead < detail.get('ahead', 0)
        if (changed or pushed) and detail.get('release_requested'):
            # A released worktree kept for unsaved work is tried again once that work changes (pushed, saved).
            for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'):
                detail.pop(key, None)
        if changed or 'activity_at' not in detail:
            detail['activity_at'] = now
            detail.pop('stalled_woke', None)
        if detail.get('checkout_state') == 'legacy_present' and row['state'] not in ('present', 'unknown'):
            detail['checkout_state'] = 'unverified'
        legacy_complete = (detail.get('checkout_state') == 'legacy_present'
                           and row['state'] in ('present', 'unknown') and not detail.get('setup_pending'))
        initialization_pending = (not legacy_complete and not legacy_passthrough and
                                  (detail.get('checkout_state') != 'ready'
                                   or not detail.get('expected_head')
                                   or not detail.get('checkout_target')
                                   or not detail.get('expected_base')
                                   or detail.get('setup_pending')))
        if report.state == 'missing' and not initialization_pending:
            detail.setdefault('missing_since', now)
        else:
            detail.pop('missing_since', None)
            detail.pop('missing_woke', None)
        # Most reports have no overdue wake; read current task state only when one is due.
        if row['bot_state'] == 'active':
            for key, since, days, message in (
                ('missing_woke', detail.get('missing_since'), 1, 'Worktree missing for a day'),
                ('stalled_woke', detail.get('activity_at') if report.ahead else None, 3, 'Worktree has unpushed commits and no activity for three days')):
                if since and not detail.get(key) and (datetime.now(timezone.utc) - datetime.fromisoformat(since.replace('Z', '+00:00'))).total_seconds() >= days * 86400:
                    task = H._one(c, "SELECT * FROM tasks WHERE id=?", (row['task_id'],))
                    if task['status'] not in CLOSED:
                        H._wake(c, task, task['owner_id'], message + ': ' + row['path'], trigger='stall_check', source=row['id'])
                        detail[key] = True
        # A stamped report is stale when the record was added, attached, re-queued or patched after the
        # computer read it; heartbeat writes don't count, or a busy worktree's reports would always be stale.
        # An unstamped one (an older computer) is stale only when it would move the checkout back.
        stale = (report.record_updated < (detail.get('record_changed') or row['updated_at'] or '')
                 if report.record_updated is not None
                 else stale_checkout_report(detail, report.checkout_state))
        if report.state == 'removed' and not stale and detail.get('cleanup_requested'):
            if row['state'] != 'removed':
                released_event(c, row, detail)
            detail['removed_by'] = 'cleanup'
        reported = report.model_dump(exclude_none=True,
                                     exclude={'link_id', 'state', 'branch', 'checkout_state', 'record_updated',
                                              *(IDENTITY if stale else ())})
        detail.update(reported)
        if detail.get('pushed') and (detail.get('pushed_last_at') or '') >= (detail.get('runner_report_at') or ''):
            detail['runner_report_at'] = now        # the computer's first report after a push webhook's delivery
        if report.error is None and not refused_cleanup(report.state, detail):
            detail.pop('error', None)           # every report carries the computer's current error, if any
        legacy_present = (report.state == 'present' and report.checkout_state == 'legacy_present'
                          and not detail.get('setup_pending')
                          and (row['state'] == 'present' and detail.get('checkout_state') in (None, 'legacy_present')
                               or row['state'] == 'unknown' and detail.get('checkout_state') == 'legacy_present'
                               or _demoted_legacy_report(row, detail)))
        if (report.checkout_state is not None and not stale
                and (report.checkout_state != 'legacy_present' or legacy_present)):
            detail['checkout_state'] = report.checkout_state
        checkout_state = detail.get('checkout_state')
        completion_proven = ((checkout_state == 'ready' and bool(detail.get('expected_head'))
                              and bool(detail.get('checkout_target')) and bool(detail.get('expected_base'))
                              and not detail.get('setup_pending')) or legacy_present
                             or (checkout_state == 'legacy_present' and not detail.get('setup_pending')
                                 and row['state'] in ('present', 'unknown')))
        state = ((row['state'] if stale else 'removed') if report.state == 'removed' else
                 report.state if legacy_passthrough else
                 'pending' if not completion_proven else
                 row['state'] if stale and report.state in ('pending', 'present') else
                 'pending' if row['state'] == 'pending' and report.state == 'present' and report.checkout_state not in ('ready', 'legacy_present') else
                 'pending' if row['state'] == 'pending' and row['repo'] and report.state == 'missing' else report.state)
        detail['current_branch'] = report.branch
        if not current_error(state, detail):
            detail.pop('error', None)
        branch = report.branch if report.state == 'present' and row['branch'] is None else None
        # Every heartbeat reports every worktree; only a difference is written. A write is a task change that every
        # open board follows (backend/events.py), and a computer retrying a failed cleanup reports every few seconds.
        # A tree a bot is working in moves its activity and size on every beat: those alone are written at most every
        # READINGS_EVERY seconds, nothing decides on them sooner (the stall wake waits days).
        readings = ((state, row['branch'] or branch) == before[:2] and row['updated_at']
                    and row['updated_at'] > H.shift(now, seconds=-READINGS_EVERY)
                    and all(detail.get(k) == before[2].get(k) for k in set(detail) | set(before[2]) if k not in READINGS))
        if (state, row['branch'] or branch, detail) != before and not readings:
            c.execute('UPDATE task_links SET state=?,branch=coalesce(branch,?),detail=?,updated_at=? WHERE id=?',
                      (state, branch, json.dumps(detail), H.now(), row['id']))
            row['updated_at'] = H.now()
        row['state'] = state
        row['detail'] = json.dumps(detail)
        if branch:
            row['branch'] = branch
    # What this computer reported may have freed a place, or finished preparing a reserved worktree.
    hosted = [r[0] for r in c.execute("SELECT 'bot:' || bot FROM assignments WHERE runner_id=?", (who.runner_id,))]
    waits.serve(c, hosted, default_org)
    if not capable:
        c.execute("UPDATE task_links SET state='unknown' WHERE kind='worktree' AND computer_id=? AND state<>'removed' AND state<>'unknown'", (who.runner_id,))
        return []
    # Serving can revive a removed link from the inventory read before the reports. Use its new allocation
    # state and detail, or this cleanup pass can overwrite the reset with the retired allocation's flags.
    rows.update({r['id']: r for r in inventory(c, who.runner_id)})
    # PR metadata is unchanged by cleanup; load it once rather than once per checkout.
    task_prs = {}
    for part in H.chunks({row['task_id'] for row in rows.values()}):
        for pr in c.execute("SELECT task_id,state,created_at,pr_merged_at,repo,branch FROM task_links "
                            "WHERE kind='pr' AND task_id IN (%s)" % ','.join('?' * len(part)), part):
            task_prs.setdefault(pr['task_id'], []).append(pr)
    actions = []
    # This pass changes checkout records, not the tasks carried by active attempts. Share provenance only
    # inside this transaction: fifty finished trees must not walk the same hundred runs fifty times.
    live_tasks = {} if live_tasks is None else live_tasks
    for row in rows.values():
        detail = json.loads(row['detail'] or '{}')
        stored = dict(detail)
        pr_links = task_prs.get(row['task_id'], [])
        prs = [pr['state'] for pr in pr_links]
        finished = all(state in PR_FINISHED for state in prs)
        closed = row['task_status'] in CLOSED or row['bot_state'] == 'archived' or detail.get('delete_requested')
        # A worktree whose pull requests are all merged or closed has done its job even while its task waits on
        # something else (a release, an acceptance): it is released so it stops counting against the bot's limit.
        # The computer removes it only while the bot is idle and only when it is clean and pushed (runner/worktrees.py).
        # A `pending` one too: a reservation or checkout that never finished must not hold a place for work that is
        # done (one sat pending for days after its pull request shipped).
        # A tree coming back for a reopened task is new work starting: it is judged once it is present.
        released = (not closed and bool(prs) and finished
                    and (row['state'] == 'present' or row['state'] == 'pending' and not detail.get('restore_on_reopen')))
        # Its own branch's commits with no pull request linked are nobody's yet: the tree stays and says why.
        unlinked = released and any(m['link_id'] == row['id'] for m in H.unlinked_pushes(c, row['task_id']))
        held = (f"held: commits on {row['branch']} with no pull request linked" if unlinked
                else held_reason(c, row, detail, live_tasks) if released else '')
        if row['state'] == 'pending' and (released or closed) and waits.preparing(c, row['id']):
            held = held or 'held: reserved for a waiting turn'
        if held:
            released = False
            detail['held'] = held
        else:
            detail.pop('held', None)
        if released and row['task_status'] == 'doing' and not allocation_has_pr(row, detail, pr_links):
            released = False
        reason = ('task finished' if closed else
                  'pull request merged' if any(s in ('merged', 'shipped') for s in prs) else 'pull request closed')
        if (row['state'] == 'pending' and not held and (released or closed and (finished or detail.get('delete_requested')))
                and detail.get('checkout_state') in ('queued', 'attached_pending')
                and not (detail.get('current_branch') or detail.get('last_commit'))):   # nothing reported on disk
            cancel_pending(c, row, detail, 'No longer needed: ' + reason)
            continue
        # A tree released in this same pass is not told to retry its setup: the retry would only be removed again.
        if (row['state'] == 'pending' and not closed and not released and not detail.get('pending_expired')
                and detail.get('checkout_state') != 'setup_failed'
                and H.shift(row['updated_at'] or row['created_at'], hours=PENDING_HOURS) < H.now()
                and not live_turn(c, row['task_id'], row['owner_id'], live_tasks)):
            expire_pending(c, row, detail)
        if released and not detail.get('release_requested'):
            detail['release_requested'] = True
            for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'):
                detail.pop(key, None)
        elif not released and detail.get('release_requested') and row['state'] != 'removed':
            detail.pop('release_requested', None)
        # Only worktrees that actually existed when closed are eligible on reopening.
        if closed and row['state'] == 'present' and not detail.get('delete_requested'):
            detail['restore_on_reopen'] = True
        action = ('remove' if (closed and (finished or detail.get('delete_requested')) or released) and row['state'] != 'removed'
                  else 'restore' if not closed and restore_requested(row, detail)
                  else 'prepare' if not closed and preparable(c, row, detail) else None)
        if detail.get('delete_requested') and row['state'] == 'removed':
            c.execute('DELETE FROM task_links WHERE id=?', (row['id'],))
            continue
        if action == 'remove':
            detail['cleanup_requested'] = True
            detail['release_reason'] = reason
            # A refused cleanup (a worktree holding files it keeps) is asked again with backoff, then left for a
            # person: retried on every heartbeat it never succeeds and only churns the task (CLEANUP_TRIES).
            if detail.get('error'):
                now = H.now()
                if detail.get('cleanup_retry_at') and now < detail['cleanup_retry_at']:
                    action = None
                elif detail.get('cleanup_attempts', 0) >= CLEANUP_TRIES:
                    action = None
                    detail['cleanup_gave_up'] = True
                else:
                    detail['cleanup_attempts'] = detail.get('cleanup_attempts', 0) + 1
                    detail['cleanup_retry_at'] = H.shift(now, minutes=2 ** detail['cleanup_attempts'])
        elif detail.get('cleanup_requested') and row['state'] != 'removed':
            # Removal is no longer asked (the task reopened): the refused cleanup's record goes with it.
            for key in ('cleanup_requested', 'cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up', 'release_reason'):
                detail.pop(key, None)
        if detail != stored:
            c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), row['id']))
        if action and (action == 'remove' or row['repo'] and row['branch']):
            actions.append({'link_id': row['id'], 'action': action, 'branch': row['branch'], 'path': row['path'],
                            'task_id': row['task_id'], 'repo': row['repo'], 'owner_id': H.actor_id(row['owner_id']),
                            'prs_finished': bool(prs) and finished, **({'release': True} if released else {}),
                            **({'cleanup_retry_generation': detail['cleanup_retry_generation']}
                               if action == 'remove' and detail.get('cleanup_retry_generation') else {})})

    return actions


def provision(c, who, task, repo, path=None, branch=None, default_org='', brief=False):
    """The one path a task worktree is made or reused by: the owner bot's request, a person's, and a worktree wait
    being served (backend/worktree_waits.py) all go through it, so each checks permission, ownership and computer.
    `brief` refuses a bot at its limit without listing its worktrees: a wait asks on every heartbeat, and nobody reads
    that refusal."""
    if not supported(c):
        raise Problem('worktrees_unavailable', 'The server is too old for task worktrees', 409)
    bot = H.actor_id(task['owner_id'])
    connection = c.execute("SELECT * FROM github_app WHERE id='app'").fetchone()
    org = connection['org'] if connection else default_org
    grants = R.access(c, bot, org)['effective']
    grant = next((r for r in grants if repo and r['full_name'].lower() == repo.lower() and r['access'] == 'write'), None)
    if repo and connection and not grant:
        raise Problem('forbidden', 'This bot needs write access to this repository', 403)
    if repo and not connection:
        try:
            git_repository.address(repo)
        except ValueError as exc:
            raise Problem('worktree_repo', str(exc), 422) from None
    assigned = c.execute('SELECT r.id,r.readiness FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=? AND r.revoked_at IS NULL', (bot,)).fetchone()
    if not assigned or not readiness_document(assigned['readiness']).get('worktrees'):
        raise Problem('computer_update', 'Update this computer to use task worktrees', 409)
    if (who.role == 'bot' and who.runner_id != assigned['id']
            and not (who.actor != task['owner_id'] and H.task_represents(c, who.actor, task['owner_id']))):
        raise Problem('forbidden', 'Worktree belongs on the bot\'s computer', 403)
    attaching = path is not None
    short = task['id'][:8]
    repo = grant['full_name'] if connection and grant else repo
    path = relative(path or f'tasks/{short}/{git_repository.folder(repo)}')
    parts = PurePosixPath(path).parts
    if attaching and parts[0].casefold() == 'tasks' and (len(parts) < 3 or parts[1] != short):
        raise Problem('worktree_path', 'This worktree belongs to another task', 409)
    candidates = list(c.execute("SELECT * FROM task_links WHERE kind='worktree' AND (computer_id=? OR task_id=?)", (assigned['id'], task['id'])))
    canonical = unicodedata.normalize('NFC', path).casefold()
    existing = next((r for r in candidates if unicodedata.normalize('NFC', r['path'] or '').casefold() == canonical), None)
    suffix = ''
    if existing and not attaching and existing['task_id'] == task['id']:
        owner = json.loads(existing['detail'] or '{}').get('owner_id', task['owner_id'])
        if owner != task['owner_id'] and existing['state'] != 'removed':
            suffix = '-' + ((H.bot(c, bot) or {}).get('slug') or bot)   # a folder name people read: the slug now
            path = relative(path + suffix)
            canonical = unicodedata.normalize('NFC', path).casefold()
            existing = next((r for r in candidates if unicodedata.normalize('NFC', r['path'] or '').casefold() == canonical), None)
    if existing:
        owner = json.loads(existing['detail'] or '{}').get('owner_id', task['owner_id'])
        if existing['task_id'] != task['id'] or existing['path'] != path or owner != task['owner_id'] and existing['state'] != 'removed':
            raise Problem('worktree_path', 'This worktree belongs to another task', 409)
        if existing['state'] != 'removed':
            detail = json.loads(existing['detail'] or '{}')
            if existing['state'] == 'pending' and detail.get('pending_expired'):
                # A retry after the deadline starts the same worktree over: same link, path and branch.
                if why := start_refusal(c, task):
                    raise Problem('worktree_start', why, 409)
                # A folder already checked out keeps its phase and proof: the registered setup command
                # (`hub task worktree setup`, the one the expiry names) resumes it in place. Only a checkout that
                # never got that far is queued again.
                for key in ('pending_expired', 'error'):
                    detail.pop(key, None)
                if checked_out(detail):
                    detail['setup_pending'] = True
                elif detail.get('checkout_state') != 'attached_pending':
                    for key in IDENTITY:
                        detail.pop(key, None)
                    detail.update(checkout_state='queued', setup_pending=True)
                now = detail['record_changed'] = H.now()
                c.execute('UPDATE task_links SET detail=?,updated_at=? WHERE id=?',
                          (json.dumps(detail), now, existing['id']))
            return {'link_id': existing['id'], 'branch': existing['branch'], 'path': existing['path'],
                    'state': existing['state'],
                    'checkout_state': detail.get('checkout_state'), 'setup_pending': detail.get('setup_pending', False),
                    'expected_head': detail.get('expected_head'), 'checkout_target': detail.get('checkout_target'),
                    'expected_base': detail.get('expected_base')}
    # A new or restored tree only for work a coding step can run on now; an existing one is still answered above.
    if why := start_refusal(c, task):
        raise Problem('worktree_start', why, 409)
    count = c.execute("SELECT count(*) FROM task_links l JOIN tasks t ON t.id=l.task_id WHERE l.kind='worktree' AND coalesce(json_extract(l.detail,'$.owner'),t.owner_id)=? AND coalesce(l.state,'unknown')<>'removed'", (task['owner_id'],)).fetchone()[0]
    if count >= LIMIT and brief:
        raise Problem('worktree_limit', f'This bot already has {LIMIT} worktrees.', 409, extra={'limit': LIMIT})
    if count >= LIMIT:
        held = holdings(c, task['owner_id'])
        # Worded so a bot never removes a worktree by hand: finished ones free themselves.
        detail = f'This bot already has {LIMIT} worktrees.'
        merge = [w['path'] for w in held if w['clear'] == 'merge_ready']
        freeing = [w['path'] for w in held if w['clear'] in ('merged', 'closed', 'task_finished') and not w.get('kept')]
        kept = [f"{w['path']} ({w['kept']})" for w in held if w.get('kept')]
        if merge:
            detail += ' Merge these approved pull requests: ' + '; '.join(merge) + '.'
        if freeing:
            detail += (' These are finished and free themselves once you are idle; do not remove them: '
                       + '; '.join(freeing) + '.')
        if kept:
            detail += ' Kept for the reason given; save or discard that work: ' + '; '.join(kept) + '.'
        if not (merge or freeing or kept):
            # Nothing frees itself, and nothing is removed to make room: name the oldest idle ones to clean up.
            idle = [f"{w['path']} ({w['task']})" for w in sorted(held, key=lambda w: w['updated_at'] or '')[:3]]
            detail += ' Clean one up before making another. Oldest idle: ' + '; '.join(idle) + '.'
        raise Problem('worktree_limit', detail, 409, extra={'worktrees': held, 'limit': LIMIT})
    if existing:
        detail = json.loads(existing['detail'] or '{}')
        # This is a new allocation on the same link, not another attempt to release the retired tree.
        for key in (*REMOVAL, 'release_reason', 'removed_by', 'restore_on_reopen', 'cancelled',
                    'cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up', 'cleanup_retry_generation',
                    'pending_expired', 'error', 'held'):
            detail.pop(key, None)
        detail['allocation_started'] = H.now()
        detail['owner'] = task['owner_id']
        detail.update(checkout_state='attached_pending' if attaching else 'queued', setup_pending=not attaching)
        detail.pop('expected_head', None)
        detail.pop('checkout_target', None)
        detail.pop('expected_base', None)
        now = detail['record_changed'] = H.now()
        c.execute("UPDATE task_links SET state='pending',computer_id=?,detail=?,updated_at=? WHERE id=?",
                  (assigned['id'], json.dumps(detail), now, existing['id']))
        return {'link_id': existing['id'], 'branch': existing['branch'], 'path': existing['path'],
                'state': 'pending',
                'checkout_state': 'attached_pending' if attaching else 'queued',
                'setup_pending': not attaching, 'expected_head': None, 'checkout_target': None,
                'expected_base': None}
    slug = re.sub('[^a-z0-9]+', '-', task['title'].lower()).strip('-')[:50] or 'task'
    branch = branch or (None if attaching else f'tico/{short}-{slug}{suffix}')
    if branch is not None and (not re.fullmatch(r'[A-Za-z0-9_./-]+', branch) or branch.startswith('-') or '..' in branch or '@{' in branch or branch.endswith(('/', '.', '.lock')) or '//' in branch):
        raise Problem('worktree_branch', 'Invalid worktree branch', 422)
    link = uuid.uuid4().hex
    now = H.now()
    c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch,computer_id,path,updated_at) VALUES(?,?,'worktree',?,?,'pending',?,?,?,?,?,?,?)",
              (link, task['id'], 'worktree:' + link, repo or 'Worktree', who.actor, now, repo, branch, assigned['id'], path, now))
    c.execute('UPDATE task_links SET detail=? WHERE id=?',
              (json.dumps({'owner': task['owner_id'], 'checkout_state': 'attached_pending' if attaching else 'queued',
                           'setup_pending': not attaching, 'record_changed': now}), link))
    return {'link_id': link, 'branch': branch, 'path': path, 'state': 'pending',
            'checkout_state': 'attached_pending' if attaching else 'queued', 'setup_pending': not attaching,
            'expected_head': None, 'checkout_target': None, 'expected_base': None}


def install(app, store, auth, mutate):
    def check(c, who, tid):
        validate_identity(c, who)
        task_id = auth.resolve_task(c, who, tid)
        row = auth.task(c, who, task_id)
        human = who.role == 'owner' or who.role == 'human' and H.can_move(c, who.actor)
        if not H.is_bot(row['owner_id']) or not (human or H.task_represents(c, who.actor, row['owner_id'])):
            raise Problem('forbidden', "Only the task's owner bot, its managers or a human mover manages its worktrees", 403)
        return row

    @app.post('/api/v2/tasks/{tid}/worktrees')
    def create(request: Request, tid: str, body: Create):
        who = request.state.identity
        def work(c):
            task = check(c, who, tid)
            try:
                return provision(c, who, task, body.repo, default_org=store.settings.github_owner)
            except Problem as problem:
                # The bot's own turn at its limit: the wait is kept (backend/worktree_waits.py) with the refusal, so
                # it is returned rather than raised, which would roll it back; a retry with the same key replays it.
                wait = waits.record(c, who, task, body.repo, problem)
                if not wait:
                    raise
                return {'_worktree_wait': waits.refusal(problem, wait)}
        result = mutate(request, body, work)
        if isinstance(result, dict) and '_worktree_wait' in result:
            refused = result['_worktree_wait']
            raise Problem('worktree_limit', refused['detail'], 409,
                          extra={'worktrees': refused['worktrees'], 'limit': LIMIT, 'wait': refused['wait']})
        return result

    @app.post('/api/v2/tasks/{tid}/worktrees/attach')
    def attach(request: Request, tid: str, body: Attach):
        who = request.state.identity
        def work(c):
            task = check(c, who, tid)
            path = relative(body.path)
            repo = body.repo
            return provision(c, who, task, repo, path, body.branch, default_org=store.settings.github_owner)
        return mutate(request, body, work)

    @app.patch('/api/v2/tasks/{tid}/links/{link_id}')
    def update(request: Request, tid: str, link_id: str, body: Update):
        who = request.state.identity
        def work(c):
            validate_identity(c, who)
            task_id = auth.resolve_task(c, who, tid) if who.role != 'runner' else tid
            if not supported(c):
                raise Problem('worktrees_unavailable', 'The server is too old for task worktrees', 409)
            link = c.execute("SELECT * FROM task_links WHERE id=? AND task_id=? AND kind='worktree'", (link_id, task_id)).fetchone()
            if not link:
                raise Problem('not_found', 'No such task worktree', 404)
            if who.role == 'runner':
                if link['computer_id'] != who.runner_id:
                    raise Problem('forbidden', 'Worktree belongs to another computer', 403)
            else:
                check(c, who, tid)
                if who.role != 'bot' or link['computer_id'] != who.runner_id:
                    raise Problem('forbidden', 'The computer or owner bot confirms its worktree', 403)
            if body.computer_id and body.computer_id != who.runner_id:
                raise Problem('forbidden', 'Cannot attach on another computer', 403)
            detail = json.loads(link['detail'] or '{}')
            checkout_state = body.checkout_state if body.checkout_state is not None else detail.get('checkout_state')
            setup_pending = body.setup_pending if body.setup_pending is not None else detail.get('setup_pending', False)
            legacy_restore_report = (who.role == 'runner' and body.state == 'present'
                                     and body.checkout_state is None and body.setup_pending is True
                                     and link['state'] == 'pending' and link['added_by'].startswith('human:')
                                     and detail.get('checkout_state') in (None, 'queued'))
            # A runner from before checkout records adds a worktree, then reports it present with only its path
            # (and runs setup itself, reporting setup_pending after). Accept that one report on the link it just
            # added, still queued, as legacy_present with setup still pending; it never certifies a ready checkout.
            # The same old clients also attach an existing tree (bare present on an attached_pending link; attach runs
            # no setup) and close both add and attach with one more bare present, which confirms what is recorded.
            bare_present = (body.state == 'present' and body.checkout_state is None and body.setup_pending is None
                            and body.expected_head is None and body.checkout_target is None and body.expected_base is None
                            and (who.role == 'runner' or link['added_by'] == who.actor))
            legacy_add_report = bare_present and link['state'] == 'pending' and detail.get('checkout_state') == 'queued'
            legacy_attach_report = (bare_present and link['state'] == 'pending'
                                    and detail.get('checkout_state') == 'attached_pending')
            legacy_confirm = bare_present and link['state'] == 'present' and detail.get('checkout_state') == 'legacy_present'
            if body.state == 'present' and (checkout_state != 'ready' or setup_pending
                    or not body.expected_head or not body.checkout_target or not body.expected_base) \
                    and not (legacy_restore_report or legacy_add_report or legacy_attach_report or legacy_confirm):
                raise Problem('worktree_not_ready', 'Checkout and setup must complete before a worktree is reported present', 409)
            if body.cleanup and who.role != 'runner':
                raise Problem('forbidden', 'Only the computer reports cleanup', 403)
            if body.cleanup and body.state == 'removed':
                if link['state'] != 'removed':
                    released_event(c, dict(link), detail)
                detail['removed_by'] = 'cleanup'
            elif body.state == 'removed':
                detail.pop('restore_on_reopen', None)
                detail['removed_by'] = who.actor
                detail.pop('cleanup_requested', None)
            if legacy_restore_report:
                # Released 0.2.x runners report a restored human worktree as present while
                # setup_pending, without the newer checkout proof fields. Accept that legacy
                # receipt so the runner stops repeating restore, but preserve the incomplete
                # setup state and do not manufacture a completion marker.
                for key in ('checkout_state', 'expected_head', 'checkout_target', 'expected_base'):
                    detail.pop(key, None)
                detail['legacy_restore_pending'] = True
            if legacy_add_report or legacy_attach_report:
                for key in ('expected_head', 'checkout_target', 'expected_base'):
                    detail.pop(key, None)
                detail.update(checkout_state='legacy_present', setup_pending=legacy_add_report)
            if body.setup_pending is not None:
                detail['setup_pending'] = body.setup_pending
            if body.checkout_state is not None:
                detail['checkout_state'] = body.checkout_state
            if body.expected_head is not None:
                detail['expected_head'] = body.expected_head
            if body.checkout_target is not None:
                detail['checkout_target'] = body.checkout_target
            if body.expected_base is not None:
                detail['expected_base'] = body.expected_base
            if body.skipped_files is not None:
                detail['skipped_files'] = [str(name)[:1000] for name in body.skipped_files]
            for key in ('snapshot_skipped', 'restore_source'):
                if getattr(body, key) is not None:
                    detail[key] = getattr(body, key)
            if not current_error(body.state or link['state'], detail):
                detail.pop('error', None)       # a successful setup supersedes the failed action before it
            if body.checkout_state is not None or body.state in ('present', 'removed'):
                detail.pop('pending_expired', None)     # the retry is making progress again
            fields = body.model_dump(exclude_none=True, exclude={'cleanup', 'setup_pending', 'checkout_state', 'expected_head', 'checkout_target', 'expected_base', 'skipped_files', 'snapshot_skipped', 'restore_source'})
            now = detail['record_changed'] = H.now()
            fields['detail'] = json.dumps(detail)
            if 'path' in fields:
                fields['path'] = relative(fields['path'])
                if fields['path'] != link['path']:
                    raise Problem('worktree_path', 'Attach a new worktree to change its path', 409)
            if 'branch' in fields and fields['branch'] != link['branch']:
                raise Problem('worktree_branch', 'Attach a new worktree to change its branch', 409)
            fields['updated_at'] = now
            c.execute('UPDATE task_links SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?', (*fields.values(), link_id))
            # A worktree set up, failed or removed may be what a waiting task needs, or the place it waits for.
            owner = detail.get('owner') or H.task(c, task_id)['owner_id']
            waits.serve(c, [owner], store.settings.github_owner)
            # Only a computer or its bot changes a link here: the answer keeps the runner wire's names (backend/runner_wire.py).
            return runner_wire.worktree(dict(c.execute('SELECT * FROM task_links WHERE id=?', (link_id,)).fetchone()))
        return mutate(request, body, work)

    @app.post('/api/v2/runners/me/worktrees/{link_id}/token')
    def token(request: Request, link_id: str):
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            if who.role != 'runner':
                raise Problem('forbidden', 'A registered computer is required', 403)
            link = next((r for r in inventory(c, who.runner_id) if r['id'] == link_id), None)
            if not link:
                raise Problem('not_found', 'No worktree on this computer', 404)
            if not link['repo']:
                raise Problem('worktree_repo', 'Repository not identified yet', 409)
            assigned = c.execute('SELECT 1 FROM assignments WHERE bot=? AND runner_id=?', (H.actor_id(link['owner_id']), who.runner_id)).fetchone()
            detail = json.loads(link['detail'] or '{}')
            closed = link['task_status'] in CLOSED or link['bot_state'] == 'archived' or detail.get('delete_requested')
            prs = [r[0] for r in c.execute("SELECT state FROM task_links WHERE task_id=? AND kind='pr'", (link['task_id'],))]
            removing = link['state'] != 'removed' and (closed and (all(state in PR_FINISHED for state in prs) or detail.get('delete_requested'))
                                                      or detail.get('release_requested'))
            restoring = not closed and restore_requested(link, detail)
            if not assigned and not (removing or restoring):
                raise Problem('forbidden', 'No worktree action on this computer', 403)
            service = app.state.github_app
            connection = service.row(c)
            if not connection:
                return {'configured': False, 'token': None}
            grants = R.access(c, H.actor_id(link['owner_id']), connection['org'])['effective']
            if not any(r['full_name'].lower() == link['repo'].lower() and r['access'] == 'write' for r in grants):
                raise Problem('forbidden', 'This bot needs write access to save its worktree', 403)
        value, expires = service.mint([link['repo']], {'contents': 'write', 'metadata': 'read'})
        return {'token': value, 'expires_at': expires}

    @app.post('/api/v2/runners/me/session-tasks')
    def session_tasks(request: Request, body: SessionTasks):
        """The status of each task a bot's session checkout on this computer is kept for (runner-rs/src/sessions.rs),
        so the computer can take away the checkouts of finished tasks between turns. Only a task this computer ran
        a turn of that bot in that task's lane is answered; any other is left out, and the computer keeps it."""
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            if who.role != 'runner':
                raise Problem('forbidden', 'A registered computer is required', 403)
            out = []
            for item in body.sessions:
                if item.task.startswith(sessions.PRIVATE_FOLDER):
                    # A private meeting's own checkout (clients/sessions.py PRIVATE_LANE): done once the meeting ends.
                    conv = item.task[len(sessions.PRIVATE_FOLDER):]
                    if c.execute("SELECT 1 FROM attempts WHERE runner_id=? AND bot_id=? AND lane=?",
                                 (who.runner_id, item.bot, sessions.private_lane(conv))).fetchone():
                        room = c.execute("SELECT room_key FROM conversations WHERE id=?", (conv,)).fetchone()
                        key = (room["room_key"] if room else "") or ""
                        meeting = key.startswith(ROOM_PREFIX) and c.execute(
                            "SELECT state FROM live_meetings WHERE id=?", (key[len(ROOM_PREFIX):],)).fetchone()
                        out.append({'bot_id': item.bot, 'task': item.task,
                                    'status': 'open' if meeting and meeting['state'] != 'ended' else 'done'})
                    continue
                row = c.execute("SELECT t.status FROM tasks t WHERE t.id=? AND EXISTS (SELECT 1 FROM attempts a "
                                "WHERE a.runner_id=? AND a.bot_id=? AND a.lane=?)",
                                (item.task, who.runner_id, item.bot, 'task:' + item.task)).fetchone()
                if row:
                    out.append({'bot_id': item.bot, 'task': item.task, 'status': row['status']})
            return {'sessions': out}

    @app.get('/api/v2/runners/me/worktrees')
    def listing(request: Request):
        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            if who.role != 'runner':
                raise Problem('forbidden', 'A registered computer is required', 403)
            return {'worktrees': [runner_wire.worktree(row) for row in inventory(c, who.runner_id)]}
