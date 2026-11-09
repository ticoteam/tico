"""The review page of a Dev ticket: what changed, both reviews' verdicts, the builder's notes, the checks, and the last
review's screenshots. It is written when the ticket's pull request merges, published as a page of the builder's on the
ticket (pinned there by the publish), and its link goes in the pull request body as `Review page: <url>`, the line the
release notes and the approval issue read. A missing page never holds approval: the line says why it is missing."""

# pyright: strict
from __future__ import annotations

import base64
import html
import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import quote

from . import hubdb, step_flow, task_relations
from .auth import Identity

# These modules are untyped: each function is partially unknown, and a cast cannot clear that on the attribute (the
# same as backend/stranded_resume.py). Calls through these aliases return Any, which the strict check accepts.
H: Any = hubdb
F: Any = step_flow
TR: Any = task_relations

log = logging.getLogger(__name__)

LINE = "Review page: "
LINE_RE = re.compile(r"^Review page:[^\n]*$", re.M)
TRIES = 3  # failed attempts before the pull request body says why there is no page
EVERY_S = 60  # how often the pass looks (backend/move_retry.py keeps the same rhythm)
PER_PASS = 20  # pages a pass builds or edits; the rest are due on a later pass
PICTURES_CAP = 6 * 1024 * 1024  # raw picture bytes; base64 adds a third, and the page cap is 10 MB
SHOTS_ASK = (
    "This is the last review before merge. If the diff touches ui/, take before and after screenshots of the changed "
    "screens (the pr-reviewer playbook, Screens) and attach manifest.json and the pictures to this check. If it does "
    "not, say so: the page then says no pictures."
)

Row = dict[str, object]


class Unbuildable(RuntimeError):
    """A ticket no page can be built for. Its pull request line says why, and no pass tries it again."""


# ------------------------------------------------------------------------------------------------ plain values
def _row(value: object) -> Row:
    return cast(Row, value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _later(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _query(c: Any, sql: str, args: tuple[object, ...] = ()) -> list[Row]:
    return [cast(Row, dict(r)) for r in c.execute(sql, args).fetchall()]


def _task(c: Any, task_id: str) -> Row | None:
    found: object = H.task(c, task_id)
    return None if found is None else _row(found)


# ------------------------------------------------------------------------------------------------ the PR line
def with_review_line(body: str, value: str) -> str:
    """The pull request body with its `Review page:` line set to `value`: replaced where it is, else added at the end."""
    line = LINE + value
    if LINE_RE.search(body or ""):
        return LINE_RE.sub(line, body, count=1)
    body = (body or "").rstrip()
    return (body + "\n\n" + line + "\n") if body else line + "\n"


def asks_shots(c: Any, step: Mapping[str, object]) -> bool:
    """Whether a check filed for `step` is the last review of its board: the one whose pictures the page shows."""
    if not step or F.check_kind(step) != "review" or not step.get("type_id"):
        return False
    reviews = F.checks(c, str(step["type_id"]), "review")
    return bool(reviews) and reviews[-1]["id"] == step["id"]


# ------------------------------------------------------------------------------------------------ queue
def queue(c: Any, task: Row | None, link_id: str) -> bool:
    """A ticket's pull request merged. Only a ticket whose type has a merge step (the Dev board) gets a page; any other
    pull request keeps no line, and no line means it is exempt."""
    if task is None or not F.merge_step(c, _text(task, "type_id")):
        return False
    rows = _query(c, "SELECT detail FROM task_links WHERE id=?", (link_id,))
    if not rows:
        return False
    detail = _row(H._json(rows[0].get("detail"), {}))
    if "review_page" in detail:
        return False
    detail["review_page"] = {"state": "pending", "tries": 0, "since": H.now(), "retry_at": ""}
    c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link_id))
    return True


# ------------------------------------------------------------------------------------------------ the pass
_lock = threading.Lock()
_next = {"at": 0.0}


def tick(service: Any, *, pages: Any, blobs: Any, store: Any = None, paced: bool = False) -> list[str]:
    """One pass over the merged pull requests whose page is pending and due, oldest first, PER_PASS at most. `paced` is
    the heartbeat's: it runs the pass at most every EVERY_S (as backend/move_retry.py does), and a caller that runs passes
    itself, such as the factory sim, runs each one as it comes. Each one's failure is its own: it is due again later, and
    after TRIES failures the pull request body says why, and no pass tries it again."""
    if paced:
        with _lock:
            if time.monotonic() < _next["at"]:
                return []
            _next["at"] = time.monotonic() + EVERY_S
    store = store or service.store
    with store.read() as c:
        links = _query(
            c,
            "SELECT * FROM task_links WHERE kind='pr' AND state IN ('merged','shipped') "
            "AND json_extract(detail,'$.review_page.state')='pending' "
            "AND coalesce(json_extract(detail,'$.review_page.retry_at'),'')<=? "
            "ORDER BY coalesce(json_extract(detail,'$.review_page.since'),''),id LIMIT ?",
            (H.now(), PER_PASS),
        )
    done: list[str] = []
    for link in links:
        detail = _row(H._json(link.get("detail"), {}))
        state = _row(detail.get("review_page"))
        link_id = _text(link, "id")
        try:
            _advance(service, pages, blobs, store, link, state)
            done.append(link_id)
        except Unbuildable as exc:
            log.warning("review page for pull request %s: %s", link_id, exc)
            _give_up(service, store, link, state, str(exc))
        except Exception:
            log.exception("review page failed for pull request %s", link_id)
            _failed(service, store, link, state)
    return done


def _advance(service: Any, pages: Any, blobs: Any, store: Any, link: Row, state: Row) -> None:
    link_id = _text(link, "id")
    pr = _pull(service, link)
    body = _text(pr, "body")
    head = _text(_row(pr.get("head")), "sha")
    file_id = _text(state, "file_id")
    if not file_id:
        with store.read() as c:
            task = _task(c, _text(link, "task_id"))
            if task is None:
                raise RuntimeError("the ticket of this pull request is gone")
            builder = _builder(c, task)
            facts = _facts(c, blobs, task, link, pr)
        result: object = pages.publish(None, builder, _fields(task), render(facts))
        file_id = _page_id(result)
        if not file_id:
            raise RuntimeError("the page was not published")
        # Saved before the edit, so a failed edit keeps the page and retries the edit alone, never a second publish.
        _save(store, link_id, {**state, "file_id": file_id})
    url = f"{store.settings.public_url}/#/page/{quote(file_id, safe='')}"
    _patch(service, link, with_review_line(body, url))
    _save(store, link_id, {"state": "done", "file_id": file_id, "head": head, "at": H.now()})


def _page_id(result: object) -> str:
    return _text(_row(_row(result).get("page")), "file_id")


def _saved(store: Any, link_id: str) -> Row:
    """The review page state as saved now. A failure after a save (the page's file_id, saved before its edit) must not
    write back the state the pass started from."""
    with store.read() as c:
        rows = _query(c, "SELECT detail FROM task_links WHERE id=?", (link_id,))
    return _row(_row(H._json(rows[0].get("detail"), {})).get("review_page")) if rows else {}


def _failed(service: Any, store: Any, link: Row, state: Row) -> None:
    link_id = _text(link, "id")
    state = _saved(store, link_id) or state
    tries = _int(state.get("tries")) + 1
    if tries < TRIES:
        _save(store, link_id, {**state, "tries": tries, "retry_at": _later(60 * 2**tries)})
        return
    reason = "could not update the pull request" if _text(state, "file_id") else "could not build it"
    _give_up(service, store, link, {**state, "tries": tries}, reason)


def _give_up(service: Any, store: Any, link: Row, state: Row, reason: str) -> None:
    """The last word on a pull request's page: its line says why there is none, and the state is failed, which no pass
    picks up again. The line is written best effort; if its edit fails too, the pull request simply has no line, and a
    missing page never holds approval."""
    link_id = _text(link, "id")
    try:
        pr = _pull(service, link)
        _patch(service, link, with_review_line(_text(pr, "body"), f"none ({reason})"))
    except Exception:
        log.exception("review page line for pull request %s could not be written", link_id)
    _save(store, link_id, {**state, "state": "failed", "reason": reason, "at": H.now()})


def _save(store: Any, link_id: str, state: Row) -> None:
    with store.transaction() as c:
        rows = _query(c, "SELECT detail FROM task_links WHERE id=?", (link_id,))
        detail = _row(H._json(rows[0].get("detail"), {})) if rows else {}
        detail["review_page"] = state
        c.execute("UPDATE task_links SET detail=? WHERE id=?", (json.dumps(detail), link_id))


def _token(service: Any, repo: str, perms: dict[str, str]) -> str:
    tokens: Any = service.mint([repo], perms)
    return str(tokens[0])


def _pull(service: Any, link: Row) -> Row:
    repo = _text(link, "repo")
    response: Any = service._call(
        "GET",
        f"/repos/{quote(repo, safe='/')}/pulls/{_text(link, 'number')}",
        headers={"Authorization": "Bearer " + _token(service, repo, {"pull_requests": "read"})},
    )
    if response.status_code != 200:
        raise RuntimeError(f"GitHub read of the pull request answered {response.status_code}")
    return _row(response.json())


def _patch(service: Any, link: Row, body: str) -> None:
    repo = _text(link, "repo")
    response: Any = service._call(
        "PATCH",
        f"/repos/{quote(repo, safe='/')}/pulls/{_text(link, 'number')}",
        headers={"Authorization": "Bearer " + _token(service, repo, {"pull_requests": "write", "contents": "read"})},
        json={"body": body},
    )
    if response.status_code != 200:
        raise RuntimeError(f"GitHub edit of the pull request answered {response.status_code}")


def _builder(c: Any, task: Row) -> Identity:
    """The page is published as the ticket's builder, a bot. A ticket owned by a person, or by a bot that is not in the
    registry, gets no page: nothing is written under an id that is not a bot."""
    owner = _text(task, "owner_id")
    bot_id = H.actor_id(owner)
    if not owner.startswith("bot:") or not bot_id or H.bot(c, bot_id) is None:
        raise Unbuildable("ticket has no bot owner")
    return Identity("bot:" + bot_id, "bot")


def _fields(task: Row) -> dict[str, str]:
    task_id = _text(task, "id")
    slug = re.sub(r"[^A-Za-z0-9_-]", "", task_id)
    title = _text(task, "title") or "Ticket"
    return {"name": f"review-{slug}.html", "title": ("Review: " + title)[:120], "task": task_id}


# ------------------------------------------------------------------------------------------------ the facts
@dataclass(frozen=True)
class Review:
    name: str
    verdict: str
    head: str
    by: str
    filed: bool


@dataclass(frozen=True)
class Shot:
    page: str
    width: str
    theme: str
    pictures: dict[str, bytes]  # "before" and "after" -> PNG bytes, the ones the attachments carry


@dataclass(frozen=True)
class Shots:
    manifest: bool  # whether the last review attached a manifest at all
    pages: list[str]  # the pages its selection drew (empty: no screen changed)
    shots: list[Shot]
    dropped: int  # pictures left out to keep the page small
    head: str


@dataclass(frozen=True)
class Facts:
    title: str
    owner: str
    pr_title: str
    pr_url: str
    pr_number: int
    head: str
    spec: list[str]
    checks: str
    reviews: list[Review]
    notes: list[str]
    r2_head: str
    shots: Shots | None = field(default=None)


def _facts(c: Any, blobs: Any, task: Row, link: Row, pr: Row) -> Facts:
    task_id = _text(task, "id")
    review_steps: list[Row] = [_row(s) for s in F.checks(c, _text(task, "type_id"), "review")]
    latest: dict[str, Row] = {}
    for child_id in TR.child_ids(c, task_id):
        child = _task(c, str(child_id))
        if child is None:
            continue
        step = _row(F.check_step(c, child, task))
        if step and F.check_kind(step) == "review":
            latest[_text(step, "id")] = child  # child ids are in filing order, so the last one is the latest check
    reviews: list[Review] = []
    for step in review_steps:
        child = latest.get(_text(step, "id"), {})
        reviews.append(
            Review(
                name=_text(step, "name"),
                verdict=_text(child, "verdict"),
                head=_text(child, "verdict_head"),
                by=H.actor_id(_text(child, "owner_id")),
                filed=bool(child),
            )
        )
    last = review_steps[-1] if review_steps else None
    r2 = latest.get(_text(last, "id")) if last else None
    head = _text(_row(pr.get("head")), "sha")
    return Facts(
        title=_text(task, "title"),
        owner=H.actor_id(_text(task, "owner_id")),
        pr_title=_text(pr, "title"),
        pr_url=_text(pr, "html_url"),
        pr_number=_int(pr.get("number")),
        head=head,
        spec=_spec(_text(pr, "body")),
        checks=_text(link, "checks"),
        reviews=reviews,
        notes=_notes(c, task),
        r2_head=_text(r2 or {}, "verdict_head"),
        shots=_shots(c, blobs, _text(r2 or {}, "id")) if r2 else None,
    )


def _spec(body: str) -> list[str]:
    """The lines under the pull request's `## Spec` heading, at most six."""
    lines: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().lower() == "spec"
            continue
        if inside and line.strip():
            lines.append(line.strip())
    return lines[:6]


def _notes(c: Any, task: Row) -> list[str]:
    owner = H.actor_id(_text(task, "owner_id"))
    rows = _query(
        c,
        "SELECT actor_id,new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='note' "
        "ORDER BY created_at,rowid",
        (_text(task, "id"),),
    )
    notes = [_text(r, "new") for r in rows if H.actor_id(_text(r, "actor_id")) == owner and _text(r, "new")]
    return notes[-10:]


def _shots(c: Any, blobs: Any, task_id: str) -> Shots:
    """The last review's attachments: its manifest, and the pictures the manifest names (before and after, per shot)."""
    rows = _query(
        c,
        "SELECT f.id,f.last_activity_at,v.name,v.digest FROM bot_files f "
        "JOIN bot_file_versions v ON v.file_id=f.id AND v.version=f.current_version "
        "WHERE f.task_id=? AND f.archived=0 ORDER BY f.last_activity_at,f.id",
        (task_id,),
    )
    by_name = {_text(r, "name"): _text(r, "digest") for r in rows}
    empty = Shots(manifest=False, pages=[], shots=[], dropped=0, head="")
    if "manifest.json" not in by_name:
        return empty
    try:
        manifest = _row(json.loads(blobs.get(by_name["manifest.json"])))
    except Exception:
        log.exception("review shots manifest unreadable for task %s", task_id)
        return empty
    selection = _row(manifest.get("selection"))
    budget, dropped = PICTURES_CAP, 0
    shots: list[Shot] = []
    for item in _items(manifest.get("shots")):
        shot = _row(item)
        pictures: dict[str, bytes] = {}
        for side in ("before", "after"):
            name = _text(shot, side)
            if not name or name not in by_name:
                continue
            data: bytes = blobs.get(by_name[name])
            if len(data) > budget:
                dropped += 1
                continue
            budget -= len(data)
            pictures[side] = data
        shots.append(
            Shot(page=_text(shot, "page"), width=_text(shot, "width"), theme=_text(shot, "theme"), pictures=pictures)
        )
    return Shots(
        manifest=True,
        pages=[str(p) for p in _items(selection.get("pages"))],
        shots=shots,
        dropped=dropped,
        head=_text(manifest, "head_sha"),
    )


# ------------------------------------------------------------------------------------------------ the page
CSS = """
body{margin:0;background:#fff;color:#1a1a1a;font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;font-weight:600;margin:28px 0 8px}
.meta{color:#666;font-size:14px}
ul{margin:0;padding-left:18px}
li{margin:4px 0}
.note{color:#555;font-size:14px;margin:8px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
figure{margin:0 0 16px}
figcaption{font-size:13px;color:#555;margin-bottom:4px}
img{width:100%;height:auto;border:1px solid #ddd;border-radius:4px;display:block}
@media (max-width:600px){.pair{grid-template-columns:1fr}}
"""


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _short(sha: str) -> str:
    return sha[:7] if sha else ""


def render(f: Facts) -> bytes:
    """The page: one HTML file, its pictures inline as data: URIs (the only images a page may show)."""
    out = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_esc('Review: ' + f.title)}</title><style>{CSS}</style></head><body><main>",
        f"<h1>{_esc(f.title)}</h1>",
    ]
    meta = f"Builder {_esc(f.owner)}"
    if f.pr_number:
        meta += f' · <a href="{_esc(f.pr_url)}">#{f.pr_number} {_esc(f.pr_title)}</a>'
    out.append(f'<p class="meta">{meta}</p>')
    out.append("<h2>What changed</h2>")
    if f.spec:
        out.append("<ul>" + "".join(f"<li>{_esc(line)}</li>" for line in f.spec) + "</ul>")
    else:
        out.append('<p class="note">No spec given.</p>')
    out.append("<h2>Reviews</h2>")
    if f.reviews:
        rows: list[str] = []
        for r in f.reviews:
            if not r.filed:
                rows.append(f"<li>{_esc(r.name)}: not filed</li>")
                continue
            verdict = _esc(r.verdict) or "no verdict yet"
            head = f" at {_esc(_short(r.head))}" if r.head else ""
            by = f" by {_esc(r.by)}" if r.by else ""
            rows.append(f"<li>{_esc(r.name)}: {verdict}{head}{by}</li>")
        out.append("<ul>" + "".join(rows) + "</ul>")
    else:
        out.append('<p class="note">No review steps on this board.</p>')
    out.append("<h2>Builder notes</h2>")
    if f.notes:
        out.append("<ul>" + "".join(f"<li>{_esc(n)}</li>" for n in f.notes) + "</ul>")
    else:
        out.append('<p class="note">No notes.</p>')
    out.append("<h2>Tests</h2>")
    out.append(f'<p class="note">Checks: {_esc(f.checks or "none recorded")}</p>')
    out.append("<h2>Screens</h2>")
    out.append(_screens(f))
    out.append("</main></body></html>")
    return "".join(out).encode("utf-8")


def _screens(f: Facts) -> str:
    shots = f.shots
    if shots is None or not shots.manifest:
        return '<p class="note">No pictures.</p>'
    if not shots.pages and not shots.shots:
        return '<p class="note">No screens changed.</p>'
    parts: list[str] = []
    if f.r2_head and f.head and f.r2_head != f.head:
        parts.append(f'<p class="note">Pictures from {_esc(_short(f.r2_head))}, before the last fixes.</p>')
    parts.append('<div class="pair">')
    for shot in shots.shots:
        label = f"{shot.page} · {shot.width}px · {shot.theme}"
        for side in ("before", "after"):
            data = shot.pictures.get(side)
            if not data:
                continue
            src = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
            alt = _esc(f"{label} {side}")
            parts.append(
                f'<figure><figcaption>{_esc(label)} · {side}</figcaption><img alt="{alt}" src="{src}"></figure>'
            )
    parts.append("</div>")
    if shots.dropped:
        parts.append(f'<p class="note">{shots.dropped} pictures left out to keep the page small.</p>')
    return "".join(parts)
