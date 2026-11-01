"""A blocker that is a permission or an outage goes to DevOps, not Head of Engineering (Dev 534 C).

GitHub refusing a reviewer's review (403), GitHub unreadable at a review move (sign-in or API down), a test runner
dispatch refused: none needs a decision, only someone who can fix access or the service. Tico files one task for it to
the company's DevOps bot, and to the type's manager (Head of Engineering on Dev tickets) only when there is no active
DevOps bot. One open task per kind and place: the same blocker again reuses it."""
import hashlib

from . import hubdb as H

# The DevOps bot template (templates/); a team that renamed the bot still built it from that template.
TEMPLATE = "devops"
SLUGS = ("devops-engineer", "devops")
GONE = ("paused", "planned", "archived", "quarantined")


def devops(c):
    """The slug of the active DevOps bot, or None."""
    built = [r[0] for r in c.execute("SELECT bot FROM bot_config WHERE json_extract(config,'$.template')=? "
                                     "ORDER BY bot", (TEMPLATE,))] if H._has_table(c, "bot_config") else []
    for slug in (*built, *SLUGS):
        row = H.bot(c, slug)
        if row and row.get("state") not in GONE:
            return slug
    return None


def ticket_of_pr(c, repository, number):
    """The open task a pull request is linked on, or None: whose type manager hears when there is no DevOps."""
    url = f"https://github.com/{repository}/pull/{int(number)}".lower()
    for (task_id,) in c.execute("SELECT task_id FROM task_links WHERE kind='pr' AND lower(url)=? ORDER BY created_at DESC",
                                (url,)):
        row = H.task(c, task_id)
        if row and row["status"] in H.ACTIVE_STATUSES:
            return row
    return None


def route(c, key, title, body, about=None):
    """File (or reuse) the task for a permission or outage blocker `key` and return it, or None when nobody can
    take it. `about` is the task the blocker stopped, whose type manager is the fallback.

    A blocker that stopped a private task is itself private, read by its owner (DevOps) and Tico: its title and body
    name the repository and pull request, which would tell the company about the private task. Its audit key is a
    digest, and it names the stopped task by neither id nor title, which DevOps cannot read anyway."""
    hidden = bool(about) and H.task_private(c, about)
    if hidden:
        key = "private:" + hashlib.sha256(key.encode()).hexdigest()[:24]
    previous = H._one(c, "SELECT detail FROM events WHERE action='blocker.routed' AND target=? "
                         "ORDER BY rowid DESC LIMIT 1", (key,))
    open_task = H.task(c, (H._json(previous["detail"], {}) or {}).get("task")) if previous else None
    if open_task and open_task["status"] in H.ACTIVE_STATUSES:
        return open_task
    slug = devops(c)
    owner = H.bot_actor(slug) if slug else None
    if not owner and about:
        manager = H.type_manager(c, about)
        owner = H.bot_actor(manager) if manager else None
    if not owner:
        return None
    stopped = ("" if not about else "\n\nIt stopped a private task." if hidden else
               f"\n\nIt stopped task {about['id']} ({about['title'][:120]}).")
    text = body + stopped + \
        "\n\nThis is a permission or outage blocker, not a decision: fix the access or the service, then mark this done."
    if not slug:
        text += " There is no active DevOps bot, so it came to the type's manager."
    made = H.task_create(c, H.KEEPER, title[:150], text, owner, deduplicate=False, lint=False,
                         private=True if hidden else None)
    H.event(c, H.KEEPER, "blocker.routed", key, {"task": made["id"], "owner": owner,
                                                 **({"about": about["id"]} if about and not hidden else {})})
    return made
