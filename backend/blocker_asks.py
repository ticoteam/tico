"""One known blocker, one ask (Dev 553).

A bot that files a task to DevOps or to a person about a blocker may name it: the repository, the HTTP status and
the endpoint that refused (`blocker` on task create, `hub task create --blocker ticoteam/tico 403 workflow_dispatch`).
Two blockers are the same when all three match; nothing looser, so an old ask never hides a different problem.
When the same blocker already has an open ask, the new task is not a second ask: it stays the filing bot's own task,
blocked by the open ask, quiet until that ask is done, when every task it blocks wakes (hubdb._unblock). The bot is
told so in the reply and may unlink it, which hands the task to the owner it named, as its own ask.

DevOps, when only a person can fix a blocker, files that person ask with the same blocker: its own open tasks for it
then wait on the person ask instead of going back to the checker that filed them.

Only company-visible tasks match: a private task's blocker never joins, nor is joined by, another audience's work.
"""
import re

from . import hubdb as H

REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def ensure(c):
    H.add_column(c, "tasks", "blocker_key", "TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS tasks_blocker_key ON tasks(blocker_key) WHERE blocker_key IS NOT NULL")


def key(blocker):
    """The match key of a {repo, status, endpoint} blocker: repository case-folded, endpoint identity preserved
    after trimming surrounding whitespace. ValueError when a part is missing or malformed."""
    blocker = blocker or {}
    repo = str(blocker.get("repo") or "").strip().lower()
    status = blocker.get("status")
    endpoint = str(blocker.get("endpoint") or "").strip()
    if not REPO.match(repo):
        raise ValueError("blocker repo is owner/name, e.g. ticoteam/tico")
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise ValueError("blocker status is the HTTP status the service answered, e.g. 403")
    if not endpoint.strip("/") or len(endpoint) > 200:
        raise ValueError("blocker endpoint names what refused, e.g. workflow_dispatch (at most 200 characters)")
    return f"{repo} {status} {endpoint}"


def _devops(c):
    from .blockers import devops
    slug = devops(c)
    return H.bot_actor(slug) if slug else None


def asks_about(c, owner):
    """Whether a task for `owner` is an ask about a blocker: it goes to a person or to the DevOps bot."""
    return H.is_human(owner) or bool(owner) and owner == _devops(c)


def open_ask(c, blocker_key, owner, parent_id=None):
    """The open ask a new task for `owner` about `blocker_key` joins, or None. A person ask joins only another person
    ask: DevOps filing the one person ask for a blocker is the escalation, not a repeat of its own task. A person ask
    is preferred, then the oldest. Never the task it is filed under, nor one above that."""
    if not blocker_key or not asks_about(c, owner):
        return None
    above = {parent_id, *H.TR.ancestors(c, parent_id)} if parent_id else set()
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    devops = _devops(c)
    for row in H._rows(c.execute(f"SELECT id,owner FROM tasks WHERE blocker_key=? AND status IN ({marks}) "
                                 "AND coalesce(private,0)=0 ORDER BY created_at,id", (blocker_key, *H.ACTIVE_STATUSES))):
        if row["id"] in above:
            continue
        if H.is_human(row["owner"]):
            return H.task(c, row["id"])
    if H.is_human(owner) or not devops:
        return None
    for row in H._rows(c.execute(f"SELECT id FROM tasks WHERE blocker_key=? AND owner=? AND status IN ({marks}) "
                                 "AND coalesce(private,0)=0 ORDER BY created_at,id", (blocker_key, devops, *H.ACTIVE_STATUSES))):
        if row["id"] not in above:
            return H.task(c, row["id"])
    return None


def _loops(c, task_id, blocker_id):
    """Whether `blocker_id` blocking `task_id` would close a loop or invert a parent (TR._check_blocker's refusals)."""
    if H.TR.is_descendant(c, blocker_id, task_id) or H.TR.is_descendant(c, task_id, blocker_id):
        return True
    return bool(c.execute("WITH RECURSIVE ahead(id) AS (SELECT to_task FROM task_relations WHERE from_task=? "
                          "AND kind='blocks' UNION SELECT r.to_task FROM task_relations r JOIN ahead "
                          "ON r.from_task=ahead.id AND r.kind='blocks') SELECT 1 FROM ahead WHERE id=?",
                          (task_id, blocker_id)).fetchone())


def joined_text(c, ask, blocker_key, task_id):
    owner = ask["owner"]
    who = H.actor_name(c, owner)
    return (f"Same blocker as open task {ask['id']} ({ask['title'][:120]}), already asked of {who}: {blocker_key}. "
            "This task is yours, blocked by that ask instead of a second ask, and wakes when it is done. "
            f"To ask separately, unlink it: hub task relate {task_id} {ask['id']} --kind blocked_by --remove")


def record(c, actor, row, blocker_key, ask=None, owner=None):
    """Store the new task's blocker and join it to `ask`, the open ask open_ask found (then `owner` is the owner it was
    filed for). Returns the words for the filing bot, or None."""
    c.execute("UPDATE tasks SET blocker_key=? WHERE id=?", (blocker_key, row["id"]))
    if ask:
        H.TR.add_blocker(c, actor, row["id"], ask["id"])
        text = joined_text(c, ask, blocker_key, row["id"])
        c.execute("UPDATE tasks SET note=? WHERE id=?", (text[:2000], row["id"]))
        H.event(c, actor, "blocker.joined", row["id"], {"ask": ask["id"], "owner": owner})
        return text
    devops = _devops(c)
    if row.get("private") or not (H.is_human(row["owner"]) and devops and actor == devops):
        return None
    # DevOps's own tasks for this blocker wait on the one person ask, rather than going back to their checkers.
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    waiting = []
    for other in H._rows(c.execute(f"SELECT id FROM tasks WHERE blocker_key=? AND owner=? AND id<>? AND status IN ({marks}) "
                                   "AND coalesce(private,0)=0 ORDER BY created_at,id",
                                   (blocker_key, devops, row["id"], *H.ACTIVE_STATUSES))):
        if not _loops(c, other["id"], row["id"]):
            H.TR.add_blocker(c, actor, other["id"], row["id"])
            waiting.append(other["id"])
    if waiting:
        H.event(c, actor, "blocker.escalated", row["id"], {"waiting": waiting})
        return f"Your open tasks for this blocker now wait on it: {', '.join(waiting)}."
    return None


def unjoined(c, actor, task_id, blocker_id):
    """The owner unlinked a joined task from its open ask: it becomes its own ask, for the owner it was filed for.
    Tico's own unlinking (the ask finished, hubdb._unblock) leaves it with the bot that filed it."""
    if actor == H.KEEPER:
        return
    row = H.task(c, task_id)
    if not row or row["status"] not in H.ACTIVE_STATUSES or actor != row["owner"]:
        return
    found = H._one(c, "SELECT detail FROM events WHERE action='blocker.joined' AND target=? "
                      "ORDER BY rowid DESC LIMIT 1", (task_id,))
    detail = H._json(found["detail"], {}) if found else {}
    if not detail or detail.get("ask") != blocker_id or not detail.get("owner") or detail["owner"] == row["owner"]:
        return
    if H._one(c, "SELECT 1 FROM events WHERE action='blocker.unjoined' AND target=? LIMIT 1", (task_id,)):
        return
    H.event(c, actor, "blocker.unjoined", task_id, {"ask": blocker_id, "owner": detail["owner"]})
    H.task_update(c, actor, task_id, owner=detail["owner"], note="Unlinked from the shared ask: asked separately.")
