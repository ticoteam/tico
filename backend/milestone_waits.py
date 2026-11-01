"""A task waits for a milestone, not only for another task to be done (Dev 256).

Oct 19: Dev 248's real prerequisite was "PR #943 merged". #943 merged at 12:42Z, but its ticket stayed In QA (rightly),
so the blocked_by link held and nothing woke 248 until a lead run at 13:04Z. The v0.3.64 checklist waited on the whole
v0.3.63 checklist, which stays open for its 48-hour QA watch; v0.3.63 was published at 13:41Z and v0.3.64 was restarted
by hand at 14:38Z. Now a task can wait for:

- `pr_merged`: a pull request merges (`pr:<url>`, or `pr:<task>` for the one pull request linked on that task);
- `release_published`: a release tag is published (`release:v0.3.64`, or `release:<owner>/<repo>@v0.3.64`).

The wait is stored on the waiting task (`task_milestone_waits`) and shown on it ("waits for PR #943 to merge"). The
upstream task is never touched: it keeps its own step. When the milestone happens the waiting task's owner gets ONE wake,
through the one wake path (H._wake, refs `milestone`, an AWAITED kind in backend/wake_rules.py so a parked task still
wakes). The key is the waiting task and the milestone: `woke_at` is set in the same write that sends the wake, so the
webhook, a replayed webhook and the minute sweep can all see it and only the first sends anything.

What happened is recorded too (`milestones`: the GitHub webhook writes a merged pull request and a published release),
and a merged pull request link on any task counts, so a wait made after the merge, or a merge recorded before its wake
was written, is settled by `reconcile`: at create, and on the scheduler's minute pass, bounded and once per distinct
milestone. A merge or release whose webhook never arrived at all is asked of GitHub by `recover` (the background
GitHub pass, outside any write), which records it through `happened`.
A `task:<id>` wait is the existing blocked_by relation (task_relations.add_blocker); nothing here duplicates it.

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import logging
import re
import threading
import time
from urllib.parse import quote

from .batch_work import isolated

KINDS = ("pr_merged", "release_published")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_milestone_waits(
  task_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('pr_merged','release_published')), target TEXT NOT NULL,
  label TEXT NOT NULL, url TEXT, created_by TEXT, created_at TEXT NOT NULL, woke_at TEXT,
  PRIMARY KEY(task_id, kind, target))""",
    "CREATE INDEX IF NOT EXISTS task_milestone_waits_open ON task_milestone_waits(kind, target) WHERE woke_at IS NULL",
    """CREATE TABLE IF NOT EXISTS milestones(
  kind TEXT NOT NULL, target TEXT NOT NULL, at TEXT NOT NULL, source TEXT, PRIMARY KEY(kind, target))""",
)
PR_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$", re.IGNORECASE)
TAG = re.compile(r"^v?\d+(\.\d+){1,3}([-+][\w.-]+)?$")
WAKE_REF = "milestone"


def _H():
    from . import hubdb
    return hubdb


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    # What the owner checks when the milestone comes, in the wake (`note`; the task_snooze.clean_note rule).
    _H().add_column(c, "task_milestone_waits", "note", "TEXT")


def _present(c):
    return _H()._has_table(c, "task_milestone_waits")


# ------------------------------------------------------------------------------------------------ parsing
def pr_target(url):
    """("owner/repo#n" lowercased, "PR #n", clean url) for a pull request URL, or None."""
    clean = str(url or "").split("?")[0].split("#")[0].strip().rstrip("/")
    match = PR_URL.match(clean)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}#{int(match.group(3))}".lower(), f"PR #{int(match.group(3))}", clean


def release_target(value, release_repo=""):
    """("owner/repo@vX.Y.Z", "release vX.Y.Z") for `vX.Y.Z` or `owner/repo@vX.Y.Z`, or None."""
    value = str(value or "").strip()
    repo, _, tag = value.rpartition("@")
    repo = (repo or release_repo or "").strip().lower()
    tag = tag.strip()
    if not TAG.match(tag) or not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        return None
    tag = tag if tag.startswith("v") else "v" + tag
    return f"{repo}@{tag}", f"release {tag}"


def sentence(kind, label):
    return f"{label} to merge" if kind == "pr_merged" else f"{label} to be published"


def describe(c, row):
    """The waiting task's own line: 'waits for PR #943 to merge'."""
    return "waits for " + sentence(row["kind"], row["label"])


# ------------------------------------------------------------------------------------------------ writes
def parse(c, actor, spec, release_repo="", resolve_task=None):
    """(kind, target, label, url) for `pr:<url>`, `pr:<task>` (its one pull request link) or `release:<tag>`. Adding
    and removing read a spec the same way, so a wait added through a task reference comes off through it too.
    `resolve_task` resolves and access-checks a task reference; without it only a URL is read."""
    H = _H()
    kind_word, _, value = str(spec or "").partition(":")
    kind_word, value = kind_word.strip().lower(), value.strip()
    if kind_word == "pr":
        parsed = pr_target(value)
        if not parsed and resolve_task:
            other = resolve_task(value)
            prs = H._rows(c.execute("SELECT url FROM task_links WHERE task_id=? AND kind='pr' ORDER BY created_at",
                                    (other,)))
            if len(prs) != 1:
                H.refuse(c, actor, "milestone", f"{value} has {len(prs)} pull request links; name the one to wait for "
                                                "with pr:<url>")
            parsed = pr_target(prs[0]["url"])
        if not parsed:
            H.refuse(c, actor, "milestone", "pr: takes a pull request URL (https://github.com/<owner>/<repo>/pull/<n>) "
                                            "or a task with one pull request link")
        return ("pr_merged", *parsed)
    if kind_word == "release":
        parsed = release_target(value, release_repo)
        if not parsed:
            H.refuse(c, actor, "milestone", "release: takes a version tag like v0.3.64 (major.minor at least), or <owner>/<repo>@v0.3.64 when this "
                                            "server has no release repository")
        return ("release_published", *parsed, None)
    H.refuse(c, actor, "milestone", "wait on pr:<url|task>, release:<tag> or task:<id>")


def add(c, actor, task_row, spec, release_repo="", resolve_task=None, note=None):
    """Store one milestone wait on `task_row` from `spec` (`pr:<url>`, `pr:<task>`, `release:<tag>`). Returns the
    wait as `waits` lists it, with `added` False when it was already there. Settles it at once when the milestone has
    already happened. `note` goes in the owner's wake when it fires; a repeat with a note replaces the stored one."""
    from .task_snooze import clean_note
    H = _H()
    ensure(c)
    kind, target, label, url = parse(c, actor, spec, release_repo, resolve_task)
    note = clean_note(c, actor, note)
    added = c.execute("INSERT OR IGNORE INTO task_milestone_waits(task_id,kind,target,label,url,created_by,created_at,note) "
                      "VALUES(?,?,?,?,?,?,?,?)", (task_row["id"], kind, target, label, url, actor, H.now(), note)).rowcount == 1
    if not added and note:
        # A changed note is a change to the task: its history shows who changed it, and clients see the new version.
        was = c.execute("SELECT note FROM task_milestone_waits WHERE task_id=? AND kind=? AND target=? AND woke_at IS NULL",
                        (task_row["id"], kind, target)).fetchone()
        if was and was[0] != note:
            c.execute("UPDATE task_milestone_waits SET note=? WHERE task_id=? AND kind=? AND target=? AND woke_at IS NULL",
                      (note, task_row["id"], kind, target))
            H._task_event(c, task_row["id"], actor, "milestone_wait_note", was[0], note, "")
            c.execute("UPDATE tasks SET version=version+1, updated_at=? WHERE id=?", (H.now(), task_row["id"]))
            H.event(c, actor, "task.milestone_wait_note", task_row["id"], {"kind": kind, "target": target})
    if added:
        H._task_event(c, task_row["id"], actor, "milestone_wait", None, f"{kind}:{target}", "")
        c.execute("UPDATE tasks SET version=version+1, updated_at=? WHERE id=?", (H.now(), task_row["id"]))
        H.event(c, actor, "task.milestone_wait", task_row["id"], {"kind": kind, "target": target})
    reconcile(c, task_id=task_row["id"])
    if pending(c, task_row["id"]):
        from .task_blocked import hold
        hold(c, task_row["id"], "Waiting for " + sentence(kind, label))
    row = c.execute("SELECT * FROM task_milestone_waits WHERE task_id=? AND kind=? AND target=?",
                    (task_row["id"], kind, target)).fetchone()
    return {**view(c, dict(row)), "added": added}


def remove(c, actor, task_row, spec, release_repo="", resolve_task=None):
    """Take a wait off, read from the same spec that added it. Returns its label."""
    H = _H()
    ensure(c)
    kind, target, label, _ = parse(c, actor, spec, release_repo, resolve_task)
    if not c.execute("DELETE FROM task_milestone_waits WHERE task_id=? AND kind=? AND target=?",
                     (task_row["id"], kind, target)).rowcount:
        H.refuse(c, actor, "not-found", f"{task_row['id']} does not wait on {spec}")
    H._task_event(c, task_row["id"], actor, "milestone_wait", f"{kind}:{target}", None, "")
    c.execute("UPDATE tasks SET version=version+1, updated_at=? WHERE id=?", (H.now(), task_row["id"]))
    from .task_blocked import reconcile as reconcile_blocked
    reconcile_blocked(c, task_row["id"])
    return sentence(kind, label)


# ------------------------------------------------------------------------------------------------ reads
def view(c, row):
    return {"kind": row["kind"], "target": row["target"], "label": row["label"], "url": row.get("url"),
            "sentence": describe(c, row), "created_at": row["created_at"], "woke_at": row.get("woke_at"), "note": row.get("note"),
            "met": bool(row.get("woke_at")) or happened_already(c, row["kind"], row["target"])}


def waits(c, task_id):
    """The task's milestone waits, oldest first, each with its sentence and whether it is met."""
    if not _present(c):
        return []
    return [view(c, r) for r in _H()._rows(c.execute(
        "SELECT * FROM task_milestone_waits WHERE task_id=? ORDER BY created_at, target", (task_id,)))]


PENDING_SQL = ("SELECT kind,label FROM task_milestone_waits WHERE task_id=? AND woke_at IS NULL "
               "ORDER BY created_at LIMIT 1")


def pending(c, task_id):
    """The first milestone this task still waits for, as a phrase ('PR #943 to merge'), or None."""
    if not _present(c):
        return None
    from .read_cache import one
    row = one(c, PENDING_SQL, (task_id,))
    return sentence(row["kind"], row["label"]) if row else None


def happened_already(c, kind, target):
    """Whether the milestone is on record: a webhook wrote it, or (a pull request) a link to it is merged or shipped."""
    if c.execute("SELECT 1 FROM milestones WHERE kind=? AND target=?", (kind, target)).fetchone():
        return True
    if kind == "pr_merged":
        repo, _, number = target.rpartition("#")
        return bool(c.execute("SELECT 1 FROM task_links WHERE kind='pr' AND lower(repo)=? AND number=? "
                              "AND state IN ('merged','shipped') LIMIT 1", (repo, int(number))).fetchone())
    return False


# ------------------------------------------------------------------------------------------------ wakes
def _fire(c, wait, why):
    """One wake for this wait, ever: claim it (woke_at) in the same write that sends it. Returns the task id or None."""
    H = _H()
    row = H.task(c, wait["task_id"])
    if not row or row["status"] not in (*H.ACTIVE_STATUSES, "blocked"):
        return None
    if c.execute("UPDATE task_milestone_waits SET woke_at=? WHERE task_id=? AND kind=? AND target=? AND woke_at IS NULL",
                 (H.now(), wait["task_id"], wait["kind"], wait["target"])).rowcount != 1:
        return None
    from .task_blocked import reconcile as reconcile_blocked
    reconcile_blocked(c, row["id"])
    row = H.task(c, row["id"])
    H._task_event(c, row["id"], H.KEEPER, "milestone_met", None, f"{wait['kind']}:{wait['target']}", why)
    if row["status"] == "waiting" and not H.waiting_for(c, row):
        # Resume the retained working step without the generic assignment wake: the milestone tells it once.
        step = H._one(c, "SELECT status FROM task_steps WHERE id=?", (row.get("step_id"),))
        retained = row.get("step_id") if step and step["status"] in H.ACTIVE_STATUSES and step["status"] != "waiting" else None
        H._set_status(c, H.KEEPER, row, "open", step=retained, note=why, skip_step_wake=(row["owner"],))
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (row["id"],))
        row = H.task(c, row["id"])
    note = wait.get("note")
    H._wake(c, row, row["owner"], f"Milestone: {row['title']}\n{why}" + (f"\nYour note: {note}" if note else ""),
            refs={WAKE_REF: f"{wait['kind']}:{wait['target']}", **({"note": note} if note else {})},
            trigger="status_or_relation", source=f"{wait['kind']}:{wait['target']}")
    return row["id"]


def _why(wait):
    return (f"{wait['label']} merged" if wait["kind"] == "pr_merged" else f"{wait['label']} was published") + \
        (f": {wait['url']}" if wait.get("url") else ".") + " The task it came from keeps its own step."


def happened(c, kind, target, source="", release_repo=""):
    """A milestone happened (the GitHub webhook): record it, and wake each task waiting for it once. Returns their ids.
    The first time a release is recorded it also follows up the Dev tickets it carries (backend/release_flow.py)."""
    H = _H()
    ensure(c)
    new = c.execute("INSERT OR IGNORE INTO milestones(kind,target,at,source) VALUES(?,?,?,?)",
                    (kind, target, H.now(), source)).rowcount == 1
    if new and kind == "release_published":
        from . import release_flow
        with isolated(c, "release_published", target):
            release_flow.published(c, target, source if str(source).startswith("https://") else "", release_repo)
    woken = []
    for wait in H._rows(c.execute("SELECT * FROM task_milestone_waits WHERE kind=? AND target=? AND woke_at IS NULL "
                                  "ORDER BY created_at, task_id", (kind, target))):
        if _fire(c, wait, _why(wait)):
            woken.append(wait["task_id"])
    return woken


RECONCILE_TARGETS = 200     # distinct milestones the minute pass settles at most; the oldest waits go first


def _met(c, targets):
    """The (kind, target) pairs among `targets` that are on record: one query for the milestones seen, one for merged
    pull request links (those from before milestones were recorded), whatever the number of waits on each."""
    targets = list(targets)
    if not targets:
        return set()
    marks = ",".join("(?,?)" for _ in targets)
    met = {(r[0], r[1]) for r in c.execute(
        f"SELECT kind,target FROM milestones WHERE (kind,target) IN (VALUES {marks})",
        [v for pair in targets for v in pair])}
    prs = {}
    for kind, target in targets:
        if kind == "pr_merged" and (kind, target) not in met:
            repo, _, number = target.rpartition("#")
            prs.setdefault(int(number), set()).add(repo)
    if prs:
        numbers = ",".join("?" * len(prs))
        for repo, number in c.execute(f"SELECT DISTINCT lower(repo),number FROM task_links WHERE kind='pr' "
                                      f"AND number IN ({numbers}) AND state IN ('merged','shipped')", list(prs)):
            if repo in prs.get(number, ()):
                met.add(("pr_merged", f"{repo}#{number}"))
    return met


def _pending_targets(c, task_id=None, limit=RECONCILE_TARGETS):
    """Distinct milestones open waits on unfinished tasks are waiting for, the longest-waiting first."""
    H = _H()
    where, args = ("AND w.task_id=?", (task_id,)) if task_id else ("", ())
    marks = ",".join("?" * (len(H.ACTIVE_STATUSES) + 1))
    return [(r[0], r[1]) for r in c.execute(
        "SELECT w.kind,w.target FROM task_milestone_waits w JOIN tasks t ON t.id=w.task_id WHERE w.woke_at IS NULL "
        f"AND t.status IN ({marks}) {where} GROUP BY w.kind,w.target ORDER BY min(w.created_at) LIMIT ?",
        (*H.ACTIVE_STATUSES, "blocked", *args, limit))]


def reconcile(c, failures=None, task_id=None):
    """Every open wait whose milestone is already on record gets its one wake: one made after the merge, or a merge
    or release that arrived while the server was down or before its wake was written. Bounded: at most
    RECONCILE_TARGETS distinct milestones a pass, each looked up once whatever the number of waits on it. The
    scheduler's minute pass and `add`. Returns the woken task ids."""
    H = _H()
    if not _present(c):
        return []
    woken = []
    for kind, target in sorted(_met(c, _pending_targets(c, task_id))):
        with isolated(c, "milestone_wait", f"{kind}:{target}", failures):
            where, args = ("AND task_id=?", (task_id,)) if task_id else ("", ())
            for wait in H._rows(c.execute(f"SELECT * FROM task_milestone_waits WHERE kind=? AND target=? "
                                          f"AND woke_at IS NULL {where} ORDER BY created_at, task_id",
                                          (kind, target, *args))):
                if _fire(c, wait, _why(wait)):
                    woken.append(wait["task_id"])
    return woken


# ------------------------------------------------------------------------------------------------ GitHub
CHECK_EVERY = 180          # seconds between passes that ask GitHub
CHECK_TARGETS = 20         # milestones asked about a pass, least recently asked first
RECHECK = 600              # seconds before the same unmet milestone is asked about again


def recover(service):
    """A merge or release whose webhook never arrived (the server was down, the delivery failed, or the pull request
    is linked on no task) is asked of GitHub: distinct pending milestones in repositories the App reaches, at most
    CHECK_TARGETS a pass, least recently asked first, each again after RECHECK. GitHub is read outside any write; a
    milestone found is recorded through `happened`, which wakes each waiting task once. Runs in its own thread, like
    github.reconcile_worktree_prs."""
    with service.lock:
        now = time.monotonic()
        if getattr(service, "milestone_recover_running", False) or getattr(service, "milestone_recover_next", 0) > now:
            return
        service.milestone_recover_next = now + CHECK_EVERY
        cache = service.__dict__.setdefault("milestone_recover_cache", {})
        with service.store.read() as c:
            app = service.row(c)
            if not app or not _present(c):
                return
            org = str(app["org"] or "").lower()
            reachable = {r[0].lower() for r in c.execute("SELECT full_name FROM repositories WHERE reachable=1")}
            pending = _pending_targets(c)
            met = _met(c, pending)
            targets = [t for t in pending if t not in met]
        def repo_of(kind, target):
            return (target.rpartition("#")[0] if kind == "pr_merged" else target.partition("@")[0]).lower()
        due = sorted((t for t in targets if repo_of(*t) in reachable and repo_of(*t).split("/")[0] == org
                      and cache.get(t, {}).get("next", 0) <= now),
                     key=lambda t: cache.get(t, {}).get("last", 0))[:CHECK_TARGETS]
        if not due:
            return
        service.milestone_recover_running = True

    def run():
        tokens = {}
        try:
            for kind, target in due:
                try:
                    repo = repo_of(kind, target)
                    if repo not in tokens:
                        tokens[repo] = service.mint([repo], {"pull_requests": "read", "contents": "read"})[0]
                    auth = {"Authorization": "Bearer " + tokens[repo]}
                    if kind == "pr_merged":
                        response = service._call("GET", f"/repos/{quote(repo, safe='/')}/pulls/"
                                                        f"{int(target.rpartition('#')[2])}", headers=auth)
                        body = response.json() if response.status_code == 200 else {}
                        found = bool(body.get("merged")) and str(body.get("html_url") or "github")
                    else:
                        tag = target.partition("@")[2]
                        response = service._call("GET", f"/repos/{quote(repo, safe='/')}/releases/tags/"
                                                        f"{quote(tag, safe='')}", headers=auth)
                        body = response.json() if response.status_code == 200 else {}
                        found = bool(body.get("published_at")) and not body.get("draft") and \
                            str(body.get("html_url") or "github")
                    if found:
                        with service.store.transaction() as c:
                            happened(c, kind, target, found, release_repo=service.settings.release_repo)
                except Exception:
                    logging.getLogger("tico.milestone_waits").exception("Milestone check failed for %s", target)
                with service.lock:
                    if (kind, target) not in cache and len(cache) >= 512:
                        cache.pop(next(iter(cache)))
                    cache[(kind, target)] = {"next": time.monotonic() + RECHECK, "last": time.monotonic()}
        finally:
            with service.lock:
                service.milestone_recover_running = False
    threading.Thread(target=run, name="tico-milestone-recover", daemon=True).start()
