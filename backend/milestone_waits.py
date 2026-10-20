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
and a merged pull request link on any task counts, so a wait made after the merge, or a merge that arrived while the
server was down or before the wake was written, is settled by `reconcile`: at create, and on the scheduler's minute pass.
A `task:<id>` wait is the existing blocked_by relation (task_relations.add_blocker); nothing here duplicates it.

This module imports nothing from hubdb at load, so hubdb can import it.
"""

import re

from .batch_work import isolated

KINDS = ("pr_merged", "release_published")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_milestone_waits(
  task_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('pr_merged','release_published')), target TEXT NOT NULL,
  label TEXT NOT NULL, url TEXT, created_by TEXT, created TEXT NOT NULL, woke_at TEXT,
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


def _present(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_milestone_waits'").fetchone())


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
def add(c, actor, task_row, spec, release_repo="", resolve_task=None):
    """Store one milestone wait on `task_row` from `spec` (`pr:<url>`, `pr:<task>`, `release:<tag>`). Returns the
    wait as `waits` lists it, with `added` False when it was already there. Settles it at once when the milestone has
    already happened."""
    H = _H()
    ensure(c)
    kind_word, _, value = str(spec or "").partition(":")
    kind_word, value = kind_word.strip().lower(), value.strip()
    url = None
    if kind_word == "pr":
        parsed = pr_target(value)
        if not parsed and resolve_task:
            other = resolve_task(value)
            prs = H._rows(c.execute("SELECT url FROM task_links WHERE task_id=? AND kind='pr' ORDER BY created",
                                    (other,)))
            if len(prs) != 1:
                H.refuse(c, actor, "milestone", f"{value} has {len(prs)} pull request links; name the one to wait for "
                                                "with pr:<url>")
            parsed = pr_target(prs[0]["url"])
        if not parsed:
            H.refuse(c, actor, "milestone", "pr: takes a pull request URL (https://github.com/<owner>/<repo>/pull/<n>) "
                                            "or a task with one pull request link")
        kind, (target, label, url) = "pr_merged", parsed
    elif kind_word == "release":
        parsed = release_target(value, release_repo)
        if not parsed:
            H.refuse(c, actor, "milestone", "release: takes a tag like v0.3.64, or <owner>/<repo>@v0.3.64 when this "
                                            "server has no release repository")
        kind, (target, label) = "release_published", parsed
    else:
        H.refuse(c, actor, "milestone", "wait on pr:<url|task>, release:<tag> or task:<id>")
    added = c.execute("INSERT OR IGNORE INTO task_milestone_waits(task_id,kind,target,label,url,created_by,created) "
                      "VALUES(?,?,?,?,?,?,?)", (task_row["id"], kind, target, label, url, actor, H.now())).rowcount == 1
    if added:
        H._task_event(c, task_row["id"], actor, "milestone_wait", None, f"{kind}:{target}", "")
        c.execute("UPDATE tasks SET version=version+1, updated=? WHERE id=?", (H.now(), task_row["id"]))
        H.event(c, actor, "task.milestone_wait", task_row["id"], {"kind": kind, "target": target})
    reconcile(c, task_id=task_row["id"])
    row = c.execute("SELECT * FROM task_milestone_waits WHERE task_id=? AND kind=? AND target=?",
                    (task_row["id"], kind, target)).fetchone()
    return {**view(c, dict(row)), "added": added}


def remove(c, actor, task_row, spec, release_repo=""):
    H = _H()
    ensure(c)
    kind_word, _, value = str(spec or "").partition(":")
    parsed = pr_target(value) if kind_word.strip().lower() == "pr" else release_target(value, release_repo)
    kind = "pr_merged" if kind_word.strip().lower() == "pr" else "release_published"
    if not parsed or not c.execute("DELETE FROM task_milestone_waits WHERE task_id=? AND kind=? AND target=?",
                                   (task_row["id"], kind, parsed[0])).rowcount:
        H.refuse(c, actor, "not-found", f"{task_row['id']} does not wait on {spec}")
    H._task_event(c, task_row["id"], actor, "milestone_wait", f"{kind}:{parsed[0]}", None, "")
    c.execute("UPDATE tasks SET version=version+1, updated=? WHERE id=?", (H.now(), task_row["id"]))
    return True


# ------------------------------------------------------------------------------------------------ reads
def view(c, row):
    return {"kind": row["kind"], "target": row["target"], "label": row["label"], "url": row.get("url"),
            "sentence": describe(c, row), "created": row["created"], "woke_at": row.get("woke_at"),
            "met": bool(row.get("woke_at")) or happened_already(c, row["kind"], row["target"])}


def waits(c, task_id):
    """The task's milestone waits, oldest first, each with its sentence and whether it is met."""
    if not _present(c):
        return []
    return [view(c, r) for r in _H()._rows(c.execute(
        "SELECT * FROM task_milestone_waits WHERE task_id=? ORDER BY created, target", (task_id,)))]


def pending(c, task_id):
    """The first milestone this task still waits for, as a phrase ('PR #943 to merge'), or None."""
    if not _present(c):
        return None
    row = c.execute("SELECT kind,label FROM task_milestone_waits WHERE task_id=? AND woke_at IS NULL "
                    "ORDER BY created LIMIT 1", (task_id,)).fetchone()
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
    H._task_event(c, row["id"], H.KEEPER, "milestone_met", None, f"{wait['kind']}:{wait['target']}", why)
    H._wake(c, row, row["owner"], f"Milestone: {row['title']}\n{why}",
            refs={WAKE_REF: f"{wait['kind']}:{wait['target']}"}, trigger="status_or_relation",
            source=f"{wait['kind']}:{wait['target']}")
    return row["id"]


def _why(wait):
    return (f"{wait['label']} merged" if wait["kind"] == "pr_merged" else f"{wait['label']} was published") + \
        (f": {wait['url']}" if wait.get("url") else ".") + " The task it came from keeps its own step."


def happened(c, kind, target, source=""):
    """A milestone happened (the GitHub webhook): record it, and wake each task waiting for it once. Returns their ids."""
    H = _H()
    ensure(c)
    c.execute("INSERT OR IGNORE INTO milestones(kind,target,at,source) VALUES(?,?,?,?)", (kind, target, H.now(), source))
    woken = []
    for wait in H._rows(c.execute("SELECT * FROM task_milestone_waits WHERE kind=? AND target=? AND woke_at IS NULL "
                                  "ORDER BY created, task_id", (kind, target))):
        if _fire(c, wait, _why(wait)):
            woken.append(wait["task_id"])
    return woken


def reconcile(c, failures=None, task_id=None):
    """Every open wait whose milestone is already on record gets its one wake: one made after the merge, or a merge
    or release that arrived while the server was down or before its wake was written. The scheduler's minute pass and
    `add`. Returns the woken task ids."""
    H = _H()
    if not _present(c):
        return []
    where, args = ("AND w.task_id=?", (task_id,)) if task_id else ("", ())
    marks = ",".join("?" * (len(H.ACTIVE_STATUSES) + 1))
    woken = []
    for wait in H._rows(c.execute(
            "SELECT w.* FROM task_milestone_waits w JOIN tasks t ON t.id=w.task_id WHERE w.woke_at IS NULL "
            f"AND t.status IN ({marks}) {where} ORDER BY w.created, w.task_id", (*H.ACTIVE_STATUSES, "blocked", *args))):
        with isolated(c, "milestone_wait", wait["task_id"], failures):
            if happened_already(c, wait["kind"], wait["target"]) and _fire(c, wait, _why(wait)):
                woken.append(wait["task_id"])
    return woken
