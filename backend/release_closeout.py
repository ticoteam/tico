# pyright: strict
"""Release close-out for a task filed for a Jira issue (its primary link, backend/item_sources/__init__.py): once its
merges are proven in an actual release tag, Tico closes the loop outside, each effect exactly once.

**When.** A forward or done move out of a visit whose stage asks In a release (task_effects.moved passes the visit
left), with every merged PR in a saved release (item_sources/github_release.py `released_in` for that visit, the In a
release reading of `github_release_commits`; no GitHub call here): the Release stage's own exit.

**What.** Three independent effects, each with an occurrence key from (task, the release tags, destination and
operation), so a replay, a second move or a resume adds nothing, and one failing or unknown never repeats the others:

- Jira transition of the issue to DONE_STATUS (outbound gate, backend/outbound_jira.py);
- one Jira comment naming the release (tag and link);
- a reply in the task's originating Slack thread (its oldest link to a Slack message), posted as the task's bot
  through the Slack post route's own write (slack_bots.queue_post, after the route's post permission, onboarding and
  channel list gates), kept once by the `closeout-slack:` registry mark written in the same transaction. No Slack
  link, no reply.

**Withdrawn or closed outside.** Jira's saved standing of the primary issue decides, nothing else: `active` in an In
Progress category status or in its Ready status (the status of the link's first saved read when that was a To Do
one, kept as `detail.ready`; "" when the first read was already further on, so any later To Do status is a
withdrawal); `withdrawn` in another To Do status; `closed` in a Done category status; `gone` when Jira answers 404.
Anything but active at close-out freezes it: nothing is queued, `closeout.frozen` is logged, and the visit is kept as
`detail.pending`, so the Jira pass keeps reading the issue of the Done task. The pass (item_sources/jira.py `refresh`)
calls `watch` after each read: a non-active standing asks the owner (backend/owner_asks.py, a people-only OK on the
task), once per standing, a new standing replacing the ask; that is how the owner hears of it, and no answer
unfreezes anything. The owner unfreezes by putting the issue back in Jira: active again takes the ask back and runs a
pending close-out. Secondary Jira links are informational.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, quote

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger(__name__)
DONE_STATUS = "Done"  # Jira's default Done status; the transition goes to the status of this name
KEY_PREFIX = "closeout:"
SLACK_MARK = "closeout-slack:"
# A Slack message link: https://<workspace>.slack.com/archives/<channel>/p<ts without its dot>[?thread_ts=<ts>&...]
SLACK_LINK = re.compile(r"^https://[a-z0-9-]+\.slack\.com/archives/([CG][A-Z0-9]{6,})/p(\d{10})(\d{6})(?:\?(\S*))?$")
TS = re.compile(r"^\d{6,12}\.\d{1,6}$")
GONE = "Jira has no such issue"  # item_sources/jira.py `_refused(404)`
LINES = {
    "withdrawn": "{key} left Ready in Jira ({status}): keep working on it, or close this task?",
    "closed": "{key} was closed in Jira ({status}): keep working on it, or close this task to settle it?",
    "gone": "Jira no longer shows {key}: keep working on it, or close this task?",
}


def _h() -> Any:
    from . import hubdb

    return hubdb


def primary(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """The task's primary Jira link row, or None."""
    found = conn.execute(
        "SELECT * FROM task_links WHERE task_id=? AND is_primary=1 AND source='jira' AND item_type='issue' LIMIT 1",
        (task_id,),
    ).fetchone()
    return dict(found) if found else None


def _detail(link: Row) -> Row:
    try:
        value = json.loads(str(link.get("detail") or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _save_detail(conn: "sqlite3.Connection", link: Row, detail: Row) -> None:
    conn.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
    link["detail"] = json.dumps(detail)


def _metadata(conn: "sqlite3.Connection", link: Row) -> Row | None:
    found = conn.execute(
        "SELECT fields, read_error FROM item_metadata WHERE source='jira' AND item_type='issue' AND item_key=? "
        "AND connection_id=?",
        (link["item_key"], link["connection_id"]),
    ).fetchone()
    if not found:
        return None
    try:
        fields = json.loads(found[0] or "{}")
    except ValueError:
        fields = {}
    kept = cast("Row", fields) if isinstance(fields, dict) else {}
    return {"fields": kept, "read_error": str(found[1] or "")}


def standing(conn: "sqlite3.Connection", link: Row) -> str:
    """active, withdrawn, closed or gone (see the module); active while unread."""
    found = _metadata(conn, link)
    if not found:
        return "active"
    if found["read_error"].startswith(GONE):
        return "gone"
    fields: Row = found["fields"]
    category, status = fields.get("category"), str(fields.get("status") or "")
    if category == "done":
        return "closed"
    detail = _detail(link)
    if category == "new" and "ready" in detail and status != detail["ready"]:
        return "withdrawn"
    return "active"


def closed_out(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether the close-out queued anything for the task (its Jira writes), by the occurrence key's unique index."""
    return (
        conn.execute(
            "SELECT 1 FROM outbound_actions WHERE occurrence_key >= ? AND occurrence_key < ? LIMIT 1",
            (f"{KEY_PREFIX}{task_id}:", f"{KEY_PREFIX}{task_id};"),
        ).fetchone()
        is not None
    )


# ----------------------------------------------------------------------------- the close-out
def _guarded(conn: "sqlite3.Connection", what: str, run: Callable[[], int | None]) -> int:
    """`run` in a savepoint: a fault here never fails the move or the Jira read that called it (logged, undone)."""
    conn.execute("SAVEPOINT closeout_guard")
    try:
        found = run() or 0
    except Exception:  # noqa: BLE001 - the caller's own write stands; the next move or read tries again
        conn.execute("ROLLBACK TO closeout_guard")
        LOG.warning("Release close-out (%s) failed", what, exc_info=True)
        found = 0
    conn.execute("RELEASE closeout_guard")
    return found


def moved(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> int:
    """task_effects.moved, on a forward or done move out of `visit_id`: close out when the task has a primary Jira
    link."""
    if not primary(conn, task_id):
        return 0
    return _guarded(conn, task_id, lambda: close_out(conn, task_id, visit_id))


def close_out(conn: "sqlite3.Connection", task_id: str, visit_id: str) -> int:
    """Queue the close-out's three effects once (see the module), when the visit left asked In a release and the
    task's merges are in actual release tags. Frozen (the issue not active in Jira) it queues nothing and keeps the
    visit as pending. Each effect in its own savepoint: one refused (logged as `closeout.skipped`) leaves the others.
    Returns how many were new."""
    from . import outbound_actions as OA
    from .item_sources import github_release as GR

    H = _h()
    link = primary(conn, task_id)
    releases = GR.released_in(conn, task_id, visit_id=visit_id) if link and visit_id else []
    if not link or not releases:
        return 0
    detail = _detail(link)
    if standing(conn, link) != "active":
        _save_detail(conn, link, {**detail, "pending": visit_id})
        H.event(conn, H.TICO_ACTOR, "closeout.frozen", task_id, {"issue": link["item_key"]})
        return 0
    if detail.pop("pending", None):
        _save_detail(conn, link, detail)
    named = "+".join(f"{repo}@{tag}" for repo, tag in releases)
    ref = named if len(named) <= 200 else hashlib.sha256(named.encode()).hexdigest()[:16]
    base = f"{KEY_PREFIX}{task_id}:{ref}"
    text = "\n".join(
        f"Released in {tag}: https://github.com/{repo}/releases/tag/{quote(tag, safe='')}" for repo, tag in releases
    )
    record = {"releases": [f"{repo}@{tag}" for repo, tag in releases], "task": task_id}
    made = 0
    for operation, payload in (("transition", {"to": DONE_STATUS}), ("comment", {"body": text})):
        conn.execute("SAVEPOINT closeout")
        try:
            _, new = OA.enqueue(
                conn,
                None,
                actor=H.TICO_ACTOR,
                channel="jira",
                operation=operation,
                destination=str(link["item_key"]),
                occurrence_key=f"{base}:jira:{operation}",
                payload=payload,
                metadata=record,
                connection_id=str(link["connection_id"] or ""),
                task_id=task_id,
            )
            made += int(new)
        except Exception as exc:  # noqa: BLE001 - this effect is refused (no write key, the policy): the others go
            conn.execute("ROLLBACK TO closeout")
            _skipped(conn, task_id, f"jira {operation}", exc)
        conn.execute("RELEASE closeout")
    return made + _slack_reply(conn, task_id, f"{SLACK_MARK}{task_id}:{ref}", text)


def _skipped(conn: "sqlite3.Connection", task_id: str, what: str, why: object) -> None:
    H = _h()
    detail = str(getattr(why, "detail", "") or why)[:300]
    H.event(conn, H.TICO_ACTOR, "closeout.skipped", task_id, {"action": what, "why": detail})


def slack_thread(conn: "sqlite3.Connection", task_id: str) -> tuple[str, str] | None:
    """(channel id, thread ts) of the task's originating Slack thread: its oldest link to a Slack message (the thread
    the message is in, `thread_ts`, else the message itself), or None."""
    for (url,) in conn.execute(
        "SELECT url FROM task_links WHERE task_id=? AND url LIKE 'https://%.slack.com/archives/%' "
        "ORDER BY created_at, rowid",
        (task_id,),
    ).fetchall():
        found = SLACK_LINK.match(str(url).strip())
        if not found:
            continue
        thread = (parse_qs(found.group(4) or "").get("thread_ts") or [""])[0]
        ts = thread if TS.match(thread) else f"{found.group(2)}.{found.group(3)}"
        return found.group(1), ts
    return None


def _slack_reply(conn: "sqlite3.Connection", task_id: str, mark: str, text: str) -> int:
    """The reply in the originating Slack thread, once (the registry mark), as the task's bot (its owner, else the bot
    that asked for it)."""
    from . import repositories as _repositories
    from . import slack_bots as SB

    H, repositories = _h(), cast("Any", _repositories)
    thread = slack_thread(conn, task_id)
    if thread is None or repositories.metadata(conn, mark):
        return 0
    task: Row = H.task(conn, task_id) or {}
    bot = next((str(a) for a in (task.get("owner_id"), task.get("requester_id")) if H.is_bot(a)), "")
    slug = str(H.actor_id(bot)) if bot else ""
    found: Row = (H.bot(conn, slug) if slug else None) or {}
    why = (
        "No bot owns the task to post as"
        if not bot
        else "The task is private"
        if H.task_private(conn, task)
        else "The bot is not active"
        if found.get("state") != "active"
        else ""
    )
    if why:
        _skipped(conn, task_id, "slack reply", why)
        return 0
    conn.execute("SAVEPOINT closeout_slack")
    try:
        SB.need(conn, slug, SB.slack_entry(conn, None, slug), "post")
        SB.not_onboarding(conn, slug)
        entry = SB.postable(conn, None, thread[0])
        posted = SB.queue_post(conn, slug, entry, thread[1], SB.check_text(text))
        repositories.save_metadata(conn, mark, {"at": H.now(), "message_id": posted["message_id"]})
    except Exception as exc:  # noqa: BLE001 - not posted (no Slack access, channel not listed): the Jira writes stand
        conn.execute("ROLLBACK TO closeout_slack")
        conn.execute("RELEASE closeout_slack")
        _skipped(conn, task_id, "slack reply", exc)
        return 0
    conn.execute("RELEASE closeout_slack")
    return 1


# ----------------------------------------------------------------------------- withdrawn or closed outside
def watch(conn: "sqlite3.Connection", key: str, connection_id: str) -> None:
    """After the Jira pass read a primary issue: learn its Ready status from the first read, ask the owner once per
    non-active standing, and once it is active again take the ask back and run a pending close-out."""
    _guarded(conn, key, lambda: _watch(conn, key, connection_id))


def _watch(conn: "sqlite3.Connection", key: str, connection_id: str) -> None:
    from . import owner_asks
    from .item_sources import primary_task

    H = _h()
    task_id = primary_task(conn, ("jira", "issue", key, connection_id))
    task: Row | None = H.task(conn, task_id) if task_id else None
    link = primary(conn, task_id) if task_id else None
    if not task or not link:
        return
    task_id, detail = str(task["id"]), _detail(link)
    if task.get("status") != "open" and not detail.get("pending"):
        return
    fields: Row = (_metadata(conn, link) or {}).get("fields") or {}
    if "ready" not in detail and fields.get("category"):
        detail["ready"] = str(fields.get("status") or "") if fields["category"] == "new" else ""
        _save_detail(conn, link, detail)
    state, asked = standing(conn, link), str(detail.get("ask") or "")
    if state == "active":
        if asked:
            detail.pop("ask")
            detail.pop("asked", None)
            _save_detail(conn, link, detail)
            owner_asks.withdraw(conn, asked, f"{key} is back in Jira", task_id)
        if detail.get("pending"):
            close_out(conn, task_id, str(detail["pending"]))
        return
    if detail.get("asked") == state or closed_out(conn, task_id):
        return  # asked about this standing already, or Tico's own close-out closed it
    line = LINES[state].format(key=key, status=fields.get("status") or "")
    found = owner_asks.ask(conn, task_id, owner_asks.people_for(conn, task), line, title=line, body=str(link["url"]))
    if asked:
        owner_asks.withdraw(conn, asked, "Asked again: Jira changed", task_id, advance=False)
    detail.update(ask=found[0] if found else "", asked=state)
    _save_detail(conn, link, detail)
    H.event(conn, H.TICO_ACTOR, "closeout.held", task_id, {"issue": key, "why": state, "asked": bool(found)})
