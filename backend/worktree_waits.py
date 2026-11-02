"""A task waiting for room for a worktree: its bot holds the most worktrees it may (backend/worktrees.py LIMIT).

A bot that waits for room inside its turn never goes idle, so its finished worktrees, which its computer frees only while
the bot is idle, never free. So the bot's own turn asking for a worktree it has no room for records one wait for that
task and repository, and the refusal tells the bot to end its turn. When a place frees, the server reserves the worktree
through the normal path (worktrees.provision: permission, ownership and computer checked again), the bot's computer
checks it out and runs its setup while the bot is idle (runner/worktrees.py `prepare`), and only once the worktree is
present with setup done does the server wake the task, once, naming the worktree. The task's own session resumes with
that message; nothing of the turn that asked is replayed. A failed checkout or setup wakes it once with the reason.

Triggers: the computer's heartbeat and its worktree reports (a worktree removed, released or set up), and the
scheduler's sweep for an event that was lost. Each pass is one write transaction and serves a bot's waits oldest first,
so repeated requests, events and sweeps give one wait, one reservation and one wake.

A wait ends when its task finishes or changes owner, the bot is archived or loses the repository or its computer, or a
person stops the turn that asked. A reserved worktree is then an ordinary worktree of its task: the usual cleanup
rules take it, and unsaved work in it is never thrown away.
"""
import json
import logging
import time
import uuid
from types import SimpleNamespace

from . import hubdb as H
from .store import Problem, readiness_document

OPEN = ('waiting', 'reserved')
CLOSED_TASK = ('done', 'closed', 'declined')
SWEEP_EVERY = 60          # seconds between the scheduler's sweeps while anything waits
_last = {'at': 0.0}
log = logging.getLogger('tico.worktree_waits')

SCHEMA = ("CREATE TABLE IF NOT EXISTS worktree_waits(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, owner TEXT NOT NULL, "
          "repo TEXT NOT NULL, generation INTEGER NOT NULL, attempt_id TEXT, state TEXT NOT NULL, link_id TEXT, "
          "reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT)")


def ensure(c):
    """Unversioned and idempotent, like the task trash: an older release never reads the table."""
    c.execute(SCHEMA)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS worktree_waits_open ON worktree_waits(task_id, repo COLLATE NOCASE) "
              "WHERE state IN ('waiting','reserved')")
    c.execute("CREATE INDEX IF NOT EXISTS worktree_waits_state ON worktree_waits(state, owner, created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS worktree_waits_link ON worktree_waits(link_id) WHERE link_id IS NOT NULL")


def _exists(c):
    return H._has_table(c, 'worktree_waits')      # remembered per connection once seen (no read a task)


def _computer(c, bot):
    return c.execute("SELECT r.id,r.readiness FROM assignments a JOIN runners r ON r.id=a.runner_id "
                     "WHERE a.bot=? AND r.revoked_at IS NULL", (bot,)).fetchone()


def computer_waits(c, bot):
    """The bot's computer prepares a reserved worktree on its own; an older one only makes worktrees inside a turn,
    so for it the refusal stays as it was and nothing waits."""
    row = _computer(c, bot)
    return bool(row and readiness_document(row['readiness']).get('worktree_waits'))


def _current_task(c, who, task):
    """Only the task the asking turn is working on: a wait wakes that task's session, which a turn on another
    task cannot end."""
    if who.role != 'bot' or who.actor != task['owner_id'] or not who.attempt_id:
        return False
    from .task_privacy import attempt_tasks
    return task['id'] in attempt_tasks(c, who.attempt_id)


def record(c, who, task, repo, problem):
    """The wait for a refused worktree, or None when this refusal makes none: only `worktree_limit`, only for the
    owner bot's own turn on this very task, only on a computer that prepares worktrees by itself. A repeated request
    returns the open wait it already has."""
    if problem.code != 'worktree_limit' or not repo or not _exists(c) or not _current_task(c, who, task):
        return None
    if not computer_waits(c, H.actor_id(task['owner_id'])):
        return None
    row = c.execute(f"SELECT * FROM worktree_waits WHERE task_id=? AND repo=? COLLATE NOCASE AND state IN {OPEN}",
                    (task['id'], repo)).fetchone()
    if row:
        return dict(row)
    generation = c.execute("SELECT count(*) FROM worktree_waits WHERE task_id=? AND repo=? COLLATE NOCASE",
                           (task['id'], repo)).fetchone()[0] + 1
    now = H.now()
    wait = {'id': uuid.uuid4().hex, 'task_id': task['id'], 'owner': task['owner_id'], 'repo': repo,
            'generation': generation, 'attempt_id': who.attempt_id, 'state': 'waiting', 'link_id': None,
            'reason': f'This bot has {problem.extra.get("limit", "its most")} worktrees', 'created_at': now,
            'updated_at': now, 'finished_at': None}
    c.execute("INSERT INTO worktree_waits VALUES(:id,:task_id,:owner,:repo,:generation,:attempt_id,:state,:link_id,"
              ":reason,:created_at,:updated_at,:finished_at)", wait)
    H.event(c, who.actor, 'worktree.wait', task['id'], {'wait': wait['id'], 'repo': repo, 'generation': generation})
    return wait


def refusal(problem, wait):
    """What the bot reads: no room now, Tico wakes this task when its worktree is ready, end the turn."""
    detail = (f"No room for another worktree now: {problem.detail} Tico has recorded that this task needs a worktree "
              f"for {wait['repo']} and will wake this task, in this same session, once one is ready and set up. "
              "End your turn now: do not wait, poll, retry or remove worktrees. Your finished worktrees free "
              "themselves once you are idle.")
    return {'detail': detail, 'worktrees': problem.extra.get('worktrees', []),
            'wait': {'id': wait['id'], 'repo': wait['repo'], 'state': wait['state'], 'since': wait['created_at'],
                     'generation': wait['generation']}}


def _finish(c, wait, state, reason):
    """Close an open wait; False when it was already closed, so nothing is said twice."""
    now = H.now()
    done = c.execute("UPDATE worktree_waits SET state=?,reason=?,updated_at=?,finished_at=? WHERE id=? AND state IN ('waiting','reserved')",
              (state, reason[:500], now, now, wait['id'])).rowcount
    if done:
        H.event(c, H.KEEPER, 'worktree.wait.' + state, wait['task_id'], {'wait': wait['id'], 'repo': wait['repo'],
                                                                         'reason': reason[:500]})
    return bool(done)


def cancel_for_attempt(c, attempt_id, reason):
    """A person stopped the turn that asked: its waits end, and nothing wakes the task for them."""
    if not attempt_id or not _exists(c):
        return
    for row in c.execute(f"SELECT * FROM worktree_waits WHERE attempt_id=? AND state IN {OPEN}", (attempt_id,)).fetchall():
        _finish(c, dict(row), 'cancelled', reason)


def _ended(c, wait, task):
    """Why a wait no longer stands, or ''."""
    if not task or task['status'] in CLOSED_TASK:
        return 'The task is finished'
    if task['owner_id'] != wait['owner']:
        return 'The task has another owner now'
    bot = H.bot(c, H.actor_id(wait['owner']))
    if not bot or bot.get('state') != 'active':
        return 'The bot is not active'
    # Checked again before serving and before preparing: a task blocked since it asked gets no tree (and no wake).
    from .worktrees import start_refusal
    return start_refusal(c, task)


def _wake(c, wait, task, body):
    H._wake(c, task, wait['owner'], body, refs={'worktree_wait': wait['id'], 'generation': wait['generation']},
            trigger='other', source=wait['id'])


def serve(c, owners=None, default_org=''):
    """Serve the open waits (of `owners`, else every bot's), oldest first per bot. Answers the woken wait ids."""
    if not _exists(c):
        return []
    sql = f"SELECT * FROM worktree_waits WHERE state IN {OPEN}"
    args = ()
    if owners is not None:
        owners = sorted(set(owners))
        if not owners:
            return []
        sql += f" AND owner IN ({','.join('?' * len(owners))})"
        args = tuple(owners)
    rows = [dict(r) for r in c.execute(sql + " ORDER BY owner, created_at, id", args)]
    full, woken = set(), []
    for wait in rows:
        # One wait that breaks must not fail the heartbeat it runs in: that would also stop the worktree cleanup
        # that frees places. Its writes are undone, it ends with the reason, and the others are served.
        c.execute('SAVEPOINT worktree_wait')
        try:
            if _serve_one(c, wait, full, default_org):
                woken.append(wait['id'])
        except Exception as exc:
            c.execute('ROLLBACK TO worktree_wait')
            log.exception('worktree wait %s failed', wait['id'])
            try:
                _broken(c, wait, f'Tico could not prepare it ({type(exc).__name__})')
            except Exception:
                # Not even ending it worked: it stays open for the next pass, and the heartbeat goes on.
                c.execute('ROLLBACK TO worktree_wait')
                log.exception('worktree wait %s could not be ended', wait['id'])
        c.execute('RELEASE worktree_wait')
    return woken


def _broken(c, wait, reason):
    """End a wait that failed unexpectedly: the task hears once, quietly, so a lasting fault wakes nothing again."""
    if _finish(c, wait, 'cancelled', reason):
        H._wake(c, H.task(c, wait['task_id']), wait['owner'],
                f"Tico stopped holding a place for this task's {wait['repo']} worktree: {reason}. Run "
                f"`hub task worktree add {wait['repo']}` again when you work on this task.",
                refs={'worktree_wait': wait['id'], 'quiet': True}, trigger='other', source=wait['id'])


def _serve_one(c, wait, full, default_org):
    """Serve one open wait; True when it woke its task."""
    from . import worktrees as W
    task = H.task(c, wait['task_id'])
    ended = _ended(c, wait, task)
    if ended:
        _finish(c, wait, 'cancelled', ended)
        link = c.execute("SELECT * FROM task_links WHERE id=? AND kind='worktree' AND state='pending'",
                         (wait['link_id'] or '',)).fetchone()
        if link and json.loads(link['detail'] or '{}').get('checkout_state') == 'queued':
            from .worktrees import cancel_pending
            cancel_pending(c, dict(link), json.loads(link['detail'] or '{}'), ended)
        return False
    if wait['state'] == 'waiting':
        # The oldest wait that does not fit holds the bot's younger ones back: a freed place goes to it first.
        if wait['owner'] in full:
            return False
        bot = H.actor_id(wait['owner'])
        computer = _computer(c, bot)
        if not computer_waits(c, bot):
            # Moved to a computer that cannot prepare a worktree alone: the task is told once and asks again.
            if _finish(c, wait, 'cancelled', "The bot's computer cannot prepare worktrees by itself"):
                _wake(c, wait, task, f"Tico stopped holding a place for this task's {wait['repo']} worktree: this "
                                     "bot's computer cannot prepare one by itself. Run `hub task worktree add "
                                     f"{wait['repo']}` again when you work on this task.")
            return False
        who = SimpleNamespace(actor=wait['owner'], role='bot', runner_id=computer['id'], attempt_id='')
        try:
            link = W.provision(c, who, task, wait['repo'], default_org=default_org, brief=True)
        except Problem as problem:
            if problem.code == 'worktree_limit':
                full.add(wait['owner'])
                return False
            # Permission, computer or path: the wait ends and the task hears why the next time it runs.
            if _finish(c, wait, 'cancelled', problem.detail):
                H._wake(c, task, wait['owner'], f"Tico stopped holding a place for this task's {wait['repo']} "
                                                f"worktree: {problem.detail}", refs={'worktree_wait': wait['id'], 'quiet': True},
                        trigger='other', source=wait['id'])
            return False
        row = c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        detail = json.loads(row['detail'] or '{}')
        detail['wait_id'] = wait['id']
        detail.pop('error', None)
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
        c.execute("UPDATE worktree_waits SET state='reserved',link_id=?,updated_at=? WHERE id=?",
                  (link['link_id'], H.now(), wait['id']))
        H.event(c, H.KEEPER, 'worktree.wait.reserved', wait['task_id'],
                {'wait': wait['id'], 'link': link['link_id'], 'path': link['path']})
        wait.update(state='reserved', link_id=link['link_id'])
    link = c.execute("SELECT * FROM task_links WHERE id=? AND kind='worktree'", (wait['link_id'],)).fetchone()
    if not link or link['state'] == 'removed':
        # The reserved worktree went away before it was ready: wait for a place again.
        c.execute("UPDATE worktree_waits SET state='waiting',link_id=NULL,updated_at=? WHERE id=?", (H.now(), wait['id']))
        return False
    detail = json.loads(link['detail'] or '{}')
    ready = (link['state'] == 'present' and not detail.get('setup_pending')
             and detail.get('checkout_state') in ('ready', 'legacy_present'))
    if ready:
        if not _finish(c, wait, 'woken', 'Ready at ' + link['path']):
            return False
        _wake(c, wait, task, f"Your worktree for {wait['repo']} is ready at {link['path']} (branch "
                             f"{link['branch']}), checked out and set up. Tico held this task's place while the "
                             "bot was at its worktree limit; carry on with this task there.")
        return True
    if detail.get('checkout_state') == 'setup_failed' or detail.get('error'):
        reason = str(detail.get('error') or 'Setup failed')
        retry = ('hub task worktree setup ' if detail.get('checkout_state') in ('checkout_ready', 'setup_failed')
                 else 'hub task worktree add ') + wait['repo']
        if not _finish(c, wait, 'failed', reason):
            return False
        _wake(c, wait, task, f"Tico reserved this task's {wait['repo']} worktree at {link['path']}, but preparing "
                             f"it failed: {reason}. The place stays this task's; run `{retry}` to try again.")
        return True
    return False


def preparing(c, link_id):
    """The open, reserved wait a worktree was made for: its computer checks it out and sets it up unasked."""
    if not link_id or not _exists(c):
        return None
    row = c.execute("SELECT * FROM worktree_waits WHERE link_id=? AND state='reserved'", (link_id,)).fetchone()
    return dict(row) if row else None


def for_task(c, task_id):
    """A task's open waits, for its page: what it waits for, since when and why."""
    if not _exists(c):
        return []
    return [{'id': r['id'], 'repo': r['repo'], 'state': r['state'], 'since': r['created_at'], 'reason': r['reason']}
            for r in c.execute(f"SELECT * FROM worktree_waits WHERE task_id=? AND state IN {OPEN} ORDER BY created_at",
                               (task_id,))]


def waiting(c):
    """Every open wait with its bot, for Health. Oldest first."""
    if not _exists(c):
        return []
    return [dict(r) for r in c.execute(f"SELECT w.*,t.title,t.private FROM worktree_waits w JOIN tasks t ON t.id=w.task_id "
                                       f"WHERE w.state IN {OPEN} ORDER BY w.created_at")]


def tick(store, default_org='', force=False):
    """The scheduler's sweep, the fallback for a lost event: nothing to do unless something waits."""
    if not force and time.time() - _last['at'] < SWEEP_EVERY:
        return []
    _last['at'] = time.time()
    with store.read() as c:
        if not _exists(c) or not c.execute(f"SELECT 1 FROM worktree_waits WHERE state IN {OPEN} LIMIT 1").fetchone():
            return []
    with store.transaction() as c:
        return serve(c, default_org=default_org)
