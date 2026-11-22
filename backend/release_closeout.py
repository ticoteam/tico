# pyright: strict
"""Release close-out for a task filed for a Jira issue (its primary link, backend/item_sources/__init__.py): once its
merges are proven in an actual release tag, Tico closes the loop outside, each effect exactly once.

**When.** A forward or done move out of a visit whose stage asks In a release (task_effects.moved passes the visit
left), with every merged PR in a saved release (item_sources/github_release.py `released_in` for that visit, the In a
release reading of `github_release_commits`; no GitHub call here): the Release stage's own exit.

**What.** Two independent Jira writes through the outbound gate (backend/outbound_jira.py): a transition of the issue to
DONE_STATUS and one comment naming the release (tag and link). Each has an occurrence key from (task, the newest
release tag holding its merges, destination and operation), so a replay, a second move or a resume adds nothing, and
one failing or unknown never repeats the other; a close-out whose newest tag is older than one already announced for
the task (a PR removed) queues nothing, and a release of newer work closes out again. Only a task with exactly one
primary Jira link closes out (more after a merge: `closeout.skipped`, and its owner is asked once). No Slack reply
yet: it comes back when Slack sends move into the outbound gate, where org rules and privacy apply to every send.

Both writes carry `closeout: true`, the issue's Ready and the Done target in their frozen payload: the sender reads
the issue right before the write and, while it is withdrawn, gone or closed in another Done status, waits an hour and
tries again (not counted as a retry), so the write resumes by itself once the issue is back.

**Withdrawn or closed outside.** Jira's last definitive standing of the primary issue decides, nothing else, kept on
the link as `detail.standing` and changed only by a read Jira answered (a timeout or other read error changes
nothing): `active` in an In Progress category status or in its Ready status (the status it was filed in, saved at
intake as `detail.ready`, POST /api/v2/tasks `primary_status`); `withdrawn` in another To Do status; `closed` in a
Done category status; `gone` when Jira answers 404. A link with no saved Ready (filed without `primary_status`) takes
every To Do status as active: it never pauses for a withdrawal. Anything but active at close-out freezes it: nothing
is queued, `closeout.frozen` is logged, and the visit is kept as `detail.pending`, so the Jira pass keeps reading the
issue of the Done task (as it does while a close-out Jira write is unfinished). The pass (item_sources/jira.py
`refresh`) calls `watch` after each answered read: a non-active standing asks the owner (backend/owner_asks.py, a
people-only OK on the task, of someone who can read it), once per standing, a new standing replacing the ask; that is
how the owner hears of it, and no answer unfreezes anything. The owner unfreezes by putting the issue back in Jira:
active again takes the ask back and runs a pending close-out. Secondary Jira links are informational.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
LOG = logging.getLogger(__name__)
DONE_STATUS = "Done"  # Jira's default Done status; the transition goes to the status of this name
KEY_PREFIX = "closeout:"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # an unreadable time sorts first
LINES = {
    "withdrawn": "{key} left Ready in Jira ({status}): keep working on it, or close this task?",
    "closed": "{key} was closed in Jira ({status}): keep working on it, or close this task to settle it?",
    "gone": "Jira no longer shows {key}: keep working on it, or close this task?",
}
SEVERAL = "This task is filed for {n} Jira issues, so Tico closes none of them out: close each in Jira yourself?"


def _h() -> Any:
    from . import hubdb

    return hubdb


def primaries(conn: "sqlite3.Connection", task_id: str) -> list[Row]:
    """The task's primary Jira link rows, oldest first: one, or more after a merge of tasks filed for Jira issues."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM task_links WHERE task_id=? AND is_primary=1 AND source='jira' AND item_type='issue' "
            "ORDER BY created_at, rowid",
            (task_id,),
        ).fetchall()
    ]


def primary(conn: "sqlite3.Connection", task_id: str) -> Row | None:
    """The task's (first) primary Jira link row, or None."""
    found = primaries(conn, task_id)
    return found[0] if found else None


def _detail(link: Row) -> Row:
    try:
        value = json.loads(str(link.get("detail") or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _save_detail(conn: "sqlite3.Connection", link: Row, detail: Row) -> None:
    conn.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link["id"]))
    link["detail"] = json.dumps(detail)


def _fields(conn: "sqlite3.Connection", link: Row) -> Row:
    found = conn.execute(
        "SELECT fields FROM item_metadata WHERE source='jira' AND item_type='issue' AND item_key=? AND connection_id=?",
        (link["item_key"], link["connection_id"]),
    ).fetchone()
    try:
        fields: object = json.loads(found[0] or "{}") if found else None
    except ValueError:
        fields = None
    return cast("Row", fields) if isinstance(fields, dict) else {}


def judged(fields: Row, ready: object) -> str:
    """The standing a read of the issue shows: closed (Done category), withdrawn (a To Do status other than `ready`,
    the saved Ready; never with none saved) or active."""
    category = fields.get("category")
    if category == "done":
        return "closed"
    if category == "new" and isinstance(ready, str) and ready and fields.get("status") != ready:
        return "withdrawn"
    return "active"


def standing(link: Row) -> str:
    """The issue's last definitive standing (see the module); active until Jira answered a read."""
    return str(_detail(link).get("standing") or "active")


def remember_ready(conn: "sqlite3.Connection", task_id: str, status: str) -> None:
    """Intake (POST /api/v2/tasks `primary_status`): the status the issue was filed in is its Ready."""
    link = primary(conn, task_id)
    if link and status.strip():
        _save_detail(conn, link, {**_detail(link), "ready": status.strip()})


def closed_out(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether the close-out queued anything for the task (its Jira writes), by the occurrence key's unique index."""
    return (
        conn.execute(
            "SELECT 1 FROM outbound_actions WHERE occurrence_key >= ? AND occurrence_key < ? LIMIT 1",
            (f"{KEY_PREFIX}{task_id}:", f"{KEY_PREFIX}{task_id};"),
        ).fetchone()
        is not None
    )


def unfinished(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Whether a close-out Jira write of the task has not ended yet (the Jira pass keeps reading its issue)."""
    return (
        conn.execute(
            "SELECT 1 FROM outbound_actions WHERE occurrence_key >= ? AND occurrence_key < ? "
            "AND state NOT IN ('done', 'failed', 'canceled') LIMIT 1",
            (f"{KEY_PREFIX}{task_id}:", f"{KEY_PREFIX}{task_id};"),
        ).fetchone()
        is not None
    )


def _newest(conn: "sqlite3.Connection", releases: list[tuple[str, str]]) -> tuple[str, str, str]:
    """(repo, tag, published at) of the newest of the releases holding the task's merges (published last)."""
    rows = conn.execute(
        "SELECT r.repo, r.tag, r.published_at FROM github_releases r JOIN json_each(?) j "
        "ON r.repo = json_extract(j.value, '$[0]') AND r.tag = json_extract(j.value, '$[1]')",
        (json.dumps(releases),),
    ).fetchall()
    if not rows:
        return (*releases[-1], "")
    # Compared as times, not text: "…00Z" sorts after "…00.500000Z" as text.
    found = max(rows, key=lambda r: (_h().parse_ts(str(r[2] or "")) or _EPOCH, str(r[1])))
    return (str(found[0]), str(found[1]), str(found[2] or ""))


def _announced(conn: "sqlite3.Connection", task_id: str) -> str:
    """When the newest release a close-out of the task already announced was published ('' for none): Tico's own rows
    only (outbound_actions reserves the prefix for it)."""
    stamps = [
        str(r[0])
        for r in conn.execute(
            "SELECT json_extract(metadata, '$.published_at') FROM outbound_actions "
            "WHERE occurrence_key >= ? AND occurrence_key < ? AND actor=?",
            (f"{KEY_PREFIX}{task_id}:", f"{KEY_PREFIX}{task_id};", _h().TICO_ACTOR),
        )
        if r[0]
    ]
    return max(stamps, key=lambda t: _h().parse_ts(t) or _EPOCH, default="")


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
    links = primaries(conn, task_id)
    if not links:
        return 0
    return _guarded(conn, task_id, lambda: close_out(conn, task_id, visit_id, links))


def close_out(conn: "sqlite3.Connection", task_id: str, visit_id: str, links: list[Row] | None = None) -> int:
    """Queue the close-out's two Jira writes once (see the module), when the visit left asked In a release and the
    task's merges are in actual release tags. Frozen (the issue not active in Jira) it queues nothing and keeps the
    visit as pending. Each write in its own savepoint: one refused (logged as `closeout.skipped`) leaves the other.
    Returns how many were new."""
    from . import outbound_actions as OA
    from .item_sources import github_release as GR

    H = _h()
    links = primaries(conn, task_id) if links is None else links
    releases = GR.released_in(conn, task_id, visit_id=visit_id) if links and visit_id else []
    if not links or not releases:
        return 0
    if len(links) > 1:
        _skipped(conn, task_id, "close-out", f"The task has {len(links)} primary Jira links")
        _ask_once(conn, task_id, links[0], "several", SEVERAL.format(n=len(links)))
        return 0
    link = links[0]
    detail = _detail(link)
    if standing(link) != "active":
        _save_detail(conn, link, {**detail, "pending": visit_id})
        H.event(conn, H.TICO_ACTOR, "closeout.frozen", task_id, {"issue": link["item_key"]})
        return 0
    if detail.pop("pending", None):
        _save_detail(conn, link, detail)
    repo, tag, published = _newest(conn, releases)
    announced, at = H.parse_ts(_announced(conn, task_id)), H.parse_ts(published)
    if announced is not None and at is not None and announced >= at:
        return 0  # announced already, or a newer one: this one (a replay, a PR removed since) says nothing new
    named = f"{repo}@{tag}"
    ref = named if len(named) <= 200 else hashlib.sha256(named.encode()).hexdigest()[:16]
    base = f"{KEY_PREFIX}{task_id}:{ref}"
    text = "\n".join(f"Released in {t}: https://github.com/{r}/releases/tag/{quote(t, safe='')}" for r, t in releases)
    record = {"releases": [f"{r}@{t}" for r, t in releases], "task": task_id, "published_at": published}
    pause = {"closeout": True, "done": DONE_STATUS, **({"ready": detail["ready"]} if detail.get("ready") else {})}
    made = 0
    for operation, payload in (("transition", {"to": DONE_STATUS, **pause}), ("comment", {"body": text, **pause})):
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
    return made


def _skipped(conn: "sqlite3.Connection", task_id: str, what: str, why: object) -> None:
    H = _h()
    detail = str(getattr(why, "detail", "") or why)[:300]
    H.event(conn, H.TICO_ACTOR, "closeout.skipped", task_id, {"action": what, "why": detail})


def _ask_once(conn: "sqlite3.Connection", task_id: str, link: Row, why: str, line: str) -> None:
    """Ask the task's owner `line` once for `why` (kept on the link as `detail.told` once asked)."""
    from . import owner_asks

    H = _h()
    detail = _detail(link)
    told: list[str] = list(detail.get("told") or [])
    if why in told:
        return
    task: Row | None = H.task(conn, task_id)
    if owner_asks.ask(conn, task_id, owner_asks.people_for(conn, task), line, title=line, body=str(link["url"])):
        _save_detail(conn, link, {**detail, "told": [*told, why]})


# ----------------------------------------------------------------------------- withdrawn or closed outside
def watch(conn: "sqlite3.Connection", key: str, connection_id: str, *, gone: bool = False) -> None:
    """After a read of a primary issue that Jira answered (`gone` for its 404): keep its standing, ask the owner once
    per non-active standing, and once it is active again take the ask back and run a pending close-out."""
    _guarded(conn, key, lambda: _watch(conn, key, connection_id, gone))


def _watch(conn: "sqlite3.Connection", key: str, connection_id: str, gone: bool) -> None:
    from .item_sources import primary_task

    H = _h()
    task_id = primary_task(conn, ("jira", "issue", key, connection_id))
    task: Row | None = H.task(conn, task_id) if task_id else None
    if not task_id:
        return
    same = (key, connection_id)
    link = next((x for x in primaries(conn, task_id) if (x["item_key"], x["connection_id"]) == same), None)
    if not task or not link:
        return
    task_id, detail = str(task["id"]), _detail(link)
    if task.get("status") != "open" and not detail.get("pending") and not unfinished(conn, task_id):
        return
    fields = _fields(conn, link)
    state = "gone" if gone else judged(fields, detail.get("ready"))
    if detail.get("standing", "active") != state:
        detail["standing"] = state
        _save_detail(conn, link, detail)
    if state == "active":
        _withdraw_outside(conn, task_id, link, detail, f"{key} is back in Jira")
        if detail.get("pending"):
            close_out(conn, task_id, str(detail["pending"]))
        return
    ours = str(fields.get("status") or "").casefold() == DONE_STATUS.casefold() and closed_out(conn, task_id)
    if state == "closed" and ours:  # Tico's own close-out closed it (any other Done status asks): a question left over
        _withdraw_outside(conn, task_id, link, detail, f"{key} is Done in Jira", advance=False)
        return
    line = LINES[state].format(key=key, status=fields.get("status") or "")
    if _ask_outside(conn, task, link, detail, state, line, "Asked again: Jira changed"):
        H.event(conn, H.TICO_ACTOR, "closeout.held", task_id, {"issue": key, "why": state, "asked": True})


def _withdraw_outside(
    conn: "sqlite3.Connection", task_id: str, link: Row, detail: Row, why: str, *, advance: bool = True
) -> None:
    from . import owner_asks

    asked = str(detail.get("ask") or "")
    if asked:
        detail.pop("ask", None)
        detail.pop("asked", None)
        _save_detail(conn, link, detail)
        owner_asks.withdraw(conn, asked, why, task_id, advance=advance)


def _ask_outside(
    conn: "sqlite3.Connection", task: Row, link: Row, detail: Row, state: str, line: str, why: str
) -> bool:
    """Ask the owner once per outside standing. If nobody can be asked, the next read can try again."""
    from . import owner_asks

    if detail.get("asked") == state:
        return False
    task_id = str(task["id"])
    found = owner_asks.ask(conn, task_id, owner_asks.people_for(conn, task), line, title=line, body=str(link["url"]))
    _withdraw_outside(conn, task_id, link, detail, why, advance=False)
    if not found:
        return False
    detail.update(ask=found[0], asked=state)
    _save_detail(conn, link, detail)
    return True


def watch_close(conn: "sqlite3.Connection", item: tuple[str, str, str, str], status: str) -> None:
    """Only a primary Close opportunity can ask its task's owner to settle, once per standing."""
    _guarded(conn, item[2], lambda: _watch_close(conn, item, status))


def unlink_close(conn: "sqlite3.Connection", link: Row) -> None:
    """Take back a Close link's ask when its task no longer tracks that opportunity."""
    from . import owner_asks

    asked = str(_detail(link).get("ask") or "")
    owner_asks.withdraw(conn, asked, "The Close opportunity was unlinked", str(link["task_id"]), raise_on_failure=True)


def _watch_close(conn: "sqlite3.Connection", item: tuple[str, str, str, str], status: str) -> None:
    found = conn.execute(
        "SELECT l.* FROM task_links l JOIN tasks t ON t.id=l.task_id AND t.status='open' "
        "WHERE l.is_primary=1 AND l.source=? AND l.item_type=? AND l.item_key=? AND l.connection_id=?",
        item,
    ).fetchone()
    if found is None:
        return
    link: Row = dict(found)
    task_id, detail = str(link["task_id"]), _detail(link)
    key = item[2]
    if status == "active":
        _withdraw_outside(conn, task_id, link, detail, f"{key} is active again in Close")
    else:
        task: Row = _h().task(conn, task_id)
        standing = "is no longer visible" if status == "gone" else f"was {status}"
        line = f"{key} {standing} in Close: keep working on it, or close this task to settle it?"
        _ask_outside(conn, task, link, detail, status, line, "Asked again: Close changed")
