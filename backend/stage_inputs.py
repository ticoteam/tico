# pyright: strict
"""What earlier stages handed on to the one a task is in (docs/pipelines-v3.md, "What a stage is handed").

A stage's bot works from what came before it: the Spec page, the pull request, each Review page, what the Builder said
it fixed. Those live on other visits (and, for the Spec, on the task that filed this one), so a stage's own
requirements never name them. `of` lists them, oldest first, for the bot's wake (task_wakes.body) and for the task's
read (task_calls.task_view, which `tico task show` prints under "From earlier stages"):

- `pr`: each PR linked to the task, and each PR of a task this one filed (with the release it went out in).
- `page`: the page that counted for each page requirement on an earlier visit, newest per requirement; and those of
  the task that filed this one, from before it was filed (the Spec page as it was OK'd, D10).
- `done`: what the owner said when it said an earlier stage's work was done (Fixes 1's "fixed ...; declined ...").
- `missing`: a page requirement of a stage before this one that has no page on any visit (it was pushed past, or the
  task was moved over it). The bot says so as a blocker instead of working without it.
- `signoff`: each OK or send back a person gave on a page or an OK of a task this one filed (QA's pre-release
  sign-off), with their notes and the page version, oldest first (Acme, Nov 11: the Product task never heard them). The
  task's own sign-offs are not here: the one that let it into its stage is said on the way in
  (stage_effects.notes_on_the_way_in).

Each entry has `line`, the words a bot reads, without any person's name (`by` names who, for the reader's own
rendering). A filed task the reader may not name (`shown`) hands on nothing. A read: it writes nothing.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
NOTE_CHARS = 600  # a done note longer than this is cut; the whole note is on the task's history

_PAGES = (
    "SELECT p.file_id, p.version, p.attached_by_id, p.created_at, p.requirement_id, v.id AS visit_id, "
    "v.stage_id, v.entered_at FROM requirement_pages p JOIN visits v ON v.id = p.visit_id "
    "WHERE v.task_id=? AND p.replaced_at IS NULL ORDER BY v.entered_at, p.created_at, p.id"
)
_DONE = (
    "SELECT a.notes, a.by_id, a.created_at, v.id AS visit_id, v.stage_id, v.entered_at FROM answers a "
    "JOIN visits v ON v.id = a.visit_id JOIN requirements r ON r.id = a.requirement_id "
    "WHERE v.task_id=? AND r.kind='owner_done' AND a.answer='done' AND a.undone_at IS NULL "
    "ORDER BY v.entered_at, a.created_at, a.id"
)
_STAGE_PAGES = "SELECT * FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL"
_FILER = "SELECT from_task_id, created_at FROM links WHERE to_task_id=?"
# The tasks this one filed that link a PR, in one statement however many it filed (a read of a task with 30 sub-tasks
# reads no more than one with 3: test_task_privacy_reads).
_FILED = (
    "SELECT l.to_task_id FROM links l WHERE l.from_task_id=? AND EXISTS (SELECT 1 FROM task_links tl "
    "WHERE tl.task_id = l.to_task_id AND tl.source='github' AND tl.item_type='pr') ORDER BY l.created_at, l.id"
)
# Every OK or send back of a stage's page or OK on the tasks this one filed (not an OK a bot added to ask someone: its
# words may be private, task_asks), one statement however many it filed, oldest first per task; with the page the
# answer was given on, when it was on a page.
_SIGNOFFS = (
    "SELECT l.to_task_id, t.title, a.answer, a.notes, a.by_id, a.page_version, a.created_at, v.stage_id, "
    "a.requirement_id, (SELECT p.file_id FROM requirement_pages p WHERE p.visit_id = a.visit_id "
    "AND p.requirement_id = a.requirement_id AND p.version = a.page_version ORDER BY p.created_at DESC LIMIT 1) "
    "AS file_id FROM links l JOIN tasks t ON t.id = l.to_task_id JOIN visits v ON v.task_id = l.to_task_id "
    "JOIN answers a ON a.visit_id = v.id JOIN requirements r ON r.id = a.requirement_id "
    "WHERE l.from_task_id=? AND a.answer IN ('ok', 'no') AND a.undone_at IS NULL AND r.kind IN ('page', 'ok') "
    "AND r.stage_id IS NOT NULL "
    "ORDER BY l.created_at, l.id, a.created_at, a.id"
)
# The PRs linked to a task and to the tasks it filed, in one statement however many it filed: (task, item key).
_PR_KEYS = (
    "SELECT tl.task_id, tl.item_key FROM task_links tl WHERE tl.source='github' AND tl.item_type='pr' "
    "AND (tl.task_id=? OR tl.task_id IN (SELECT l.to_task_id FROM links l WHERE l.from_task_id=?)) "
    "ORDER BY tl.created_at, tl.id"
)
# Whether a task's pipeline works on code: a stage of it reads PRs. Its tasks are in the repositories In use.
_CODE = (
    "SELECT 1 FROM tasks t JOIN stages s ON s.pipeline_id = t.pipeline_id JOIN requirements r ON r.stage_id = s.id "
    "WHERE t.id=? AND r.kind='item' AND r.source='github' AND r.withdrawn_at IS NULL LIMIT 1"
)
_IN_USE = "SELECT full_name FROM repositories WHERE enabled=1 AND bot_repo=0 ORDER BY full_name COLLATE NOCASE"
_RELEASES = (
    "SELECT r.tag FROM github_release_commits rc JOIN github_releases r ON r.id = rc.release_id "
    "WHERE rc.commit_sha=? AND rc.included=1 ORDER BY r.published_at, r.tag"
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _rows(conn: "sqlite3.Connection", sql: str, args: tuple[Any, ...]) -> list[Row]:
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, found, strict=True)) for found in cursor.fetchall()]


def _stage_name(conn: "sqlite3.Connection", stage_id: str) -> str:
    from . import task_engine

    return str((task_engine.stage(conn, stage_id) or {}).get("name") or stage_id)


def _requirement(conn: "sqlite3.Connection", requirement_id: str) -> Row:
    from . import task_requirements as R

    return R.get(conn, requirement_id) or {"id": requirement_id, "kind": "page", "line": "Page"}


def _pr_line(link: Row) -> str:
    meta: Row = link.get("metadata") or {}
    name = f"PR {link['repo']}#{link['number']} {link.get('url') or ''}".rstrip()
    state = meta.get("state")
    if state == "merged":
        sha = str(meta.get("merged_sha") or "")[:12]
        return f"{name}: merged" + (f" as {sha}" if sha else "")
    if state == "closed":
        return f"{name}: closed without merging"
    head = str(meta.get("head_sha") or "")[:12]
    return f"{name}: open" + (f", newest commit {head}" if head else "")


def _prs(conn: "sqlite3.Connection", task_id: str, filed: bool) -> list[Row]:
    from .item_sources import github as G

    out: list[Row] = []
    for link in G.prs_of(conn, task_id):
        line = _pr_line(link)
        meta: Row = link.get("metadata") or {}
        entry: Row = {"kind": "pr", "url": link.get("url"), "repo": link["repo"], "number": link["number"]}
        entry["open"] = meta.get("state") not in ("merged", "closed")
        if filed:
            entry["filed"] = True
            line += " (work this task filed)"
            merged = meta.get("merged_sha") if meta.get("state") == "merged" else None
            tags = [str(found["tag"]) for found in _rows(conn, _RELEASES, (merged,))] if merged else []
            if merged:
                entry["releases"] = tags
                line += f"; in release {tags[0]}" if tags else "; in no release yet"
        out.append({**entry, "line": line})
    return out


def _page(conn: "sqlite3.Connection", found: Row, where: str) -> Row:
    requirement = _requirement(conn, str(found["requirement_id"]))
    from . import task_requirements as R

    ref = f"page:{found['file_id']}@{int(found['version'])}"
    return {
        "kind": "page",
        "stage": _stage_name(conn, str(found["stage_id"])),
        "requirement_id": requirement["id"],
        "file_id": found["file_id"],
        "version": int(found["version"]),
        "ref": ref,
        "by": found.get("attached_by_id"),
        "line": f"{R.name_of(requirement)} from {where}: {ref}, read it with `tico file read {found['file_id']}`",
    }


def _newest(rows: list[Row]) -> list[Row]:
    """The newest row per requirement, kept in the order of the visits they came from."""
    newest: dict[str, Row] = {}
    for found in rows:
        newest.pop(str(found["requirement_id"]), None)
        newest[str(found["requirement_id"])] = found
    return list(newest.values())


def _note(text: object) -> str:
    """What a person or a bot said, on one line, cut at NOTE_CHARS (the whole of it is on the task's history)."""
    note = " ".join(str(text or "").split())
    return note[: NOTE_CHARS - 1].rstrip() + "…" if len(note) > NOTE_CHARS else note


def _signoffs(conn: "sqlite3.Connection", task_id: str, shown: Callable[[str], bool]) -> dict[str, list[Row]]:
    """A person's OKs and send backs on the tasks this one filed that the reader may name, by filed task."""
    from . import task_requirements as R

    H = _h()
    out: dict[str, list[Row]] = {}
    for found in _rows(conn, _SIGNOFFS, (task_id,)):
        filed = str(found["to_task_id"])
        if not H.is_human(found["by_id"]) or not shown(filed):
            continue
        name = R.name_of(_requirement(conn, str(found["requirement_id"])))
        stage = _stage_name(conn, str(found["stage_id"]))
        said = "OK'd" if found["answer"] == "ok" else "sent back"
        entry: Row = {
            "kind": "signoff",
            "stage": stage,
            "task_id": filed,
            "answer": found["answer"],
            "notes": found.get("notes") or "",
            "by": found["by_id"],
            "filed": True,
        }
        if found.get("file_id") and found.get("page_version") is not None:
            version = int(found["page_version"])
            entry.update(file_id=found["file_id"], version=version, ref=f"page:{found['file_id']}@{version}")
            said += f" at {entry['ref']}"
        note = _note(found.get("notes"))
        title = str(found.get("title") or "work this task filed")
        line = f"{name} from {stage} ({title}, work this task filed): {said}"
        out.setdefault(filed, []).append({**entry, "line": f"{line}: {note}" if note else f"{line}, with no notes"})
    return out


def _filer_pages(conn: "sqlite3.Connection", task_id: str, readable: Callable[[Row], bool]) -> list[Row]:
    """The pages of the task that filed this one, from visits that began before it was filed."""
    from . import task_engine

    link = _rows(conn, _FILER, (task_id,))
    if not link:
        return []
    filer = task_engine.task(conn, str(link[0]["from_task_id"]))
    if not filer or not readable(filer):
        return []
    rows = [r for r in _rows(conn, _PAGES, (filer["id"],)) if str(r["entered_at"]) <= str(link[0]["created_at"])]
    named: Row = filer.get("pipeline") or {}
    pipeline = str(named.get("name") or "the task that filed this one")
    return [_page(conn, found, f"{_stage_name(conn, str(found['stage_id']))} ({pipeline})") for found in _newest(rows)]


def _missing(conn: "sqlite3.Connection", task: Row, attached: set[str]) -> list[Row]:
    """Each page requirement of a work stage before the task's own that has no page on any visit of the task."""
    from . import task_engine
    from . import task_requirements as R

    work = [s for s in task_engine.stages(conn, task["pipeline_id"]) if s["kind"] == "work"]
    at = next((i for i, s in enumerate(work) if s["id"] == task.get("stage_id")), 0)
    out: list[Row] = []
    for stage in work[:at]:
        for requirement in _rows(conn, _STAGE_PAGES, (stage["id"],)):
            if requirement["id"] in attached:
                continue
            out.append(
                {
                    "kind": "missing",
                    "stage": stage["name"],
                    "requirement_id": requirement["id"],
                    "line": f"{R.name_of(requirement)} from {stage['name']}: none was attached",
                }
            )
    return out


def of(
    conn: "sqlite3.Connection",
    task: Row,
    shown: Callable[[str], bool] = lambda _task_id: True,
    readable: Callable[[Row], bool] = lambda _task: True,
) -> list[Row]:
    """What earlier stages handed on to `task` (task_engine.task()'s shape), oldest first. `shown(task_id)`: whether
    a task this one filed may be named (task_wakes.told, task_calls.hidden_filed); `readable(task)`: whether the
    task that filed this one may be read, else its pages are left out."""
    visit: Row = task.get("visit") or {}
    visit_id = visit.get("id")
    out: list[Row] = [*_filer_pages(conn, task["id"], readable), *_prs(conn, task["id"], filed=False)]
    pages = _rows(conn, _PAGES, (task["id"],))
    attached = {str(r["requirement_id"]) for r in pages}
    earlier: list[tuple[tuple[str, str], Row]] = []
    for found in _newest([r for r in pages if r["visit_id"] != visit_id]):
        at = (str(found["entered_at"]), str(found["created_at"]))
        earlier.append((at, _page(conn, found, _stage_name(conn, str(found["stage_id"])))))
    for found in _rows(conn, _DONE, (task["id"],)):
        if found["visit_id"] == visit_id:
            continue
        note = _note(found.get("notes"))
        stage = _stage_name(conn, str(found["stage_id"]))
        line = f"{stage} was said done: {note}" if note else f"{stage} was said done, with no note"
        at = (str(found["entered_at"]), str(found["created_at"]))
        earlier.append((at, {"kind": "done", "stage": stage, "by": found["by_id"], "line": line}))
    out += [entry for _at, entry in sorted(earlier, key=lambda pair: pair[0])]
    out += _missing(conn, task, attached)
    # The work this task filed: its PRs, then what a person said when they signed it off, per filed task.
    signed = _signoffs(conn, task["id"], shown)
    for (filed_id,) in conn.execute(_FILED, (task["id"],)).fetchall():
        if shown(str(filed_id)):
            out += _prs(conn, str(filed_id), filed=True)
            out += signed.pop(str(filed_id), [])
    for rest in signed.values():  # filed work that links no PR
        out += rest
    return out


def repositories(conn: "sqlite3.Connection", task_id: str, readable: Callable[[str], bool]) -> list[str]:
    """The GitHub repositories a task's work is in, `owner/name`: those of the PRs linked to it and to the tasks it
    filed that `readable(task_id)` allows. With none linked, on a pipeline whose stages read PRs (Engineering), every
    repository In use (docs/repositories.md), from the moment the task is filed: a Builder woken on a filed task once
    worked in its own checkout and asked a person which repository to use. Empty otherwise."""
    import sqlite3

    from .item_sources import github as G

    found: dict[str, str] = {}
    allowed: dict[str, bool] = {task_id: True}
    for linked, key in conn.execute(_PR_KEYS, (task_id, task_id)).fetchall():
        if allowed.setdefault(str(linked), readable(str(linked))):
            repo = G.split_key(str(key))[0]
            found.setdefault(repo.lower(), repo)
    if found:
        return list(found.values())
    if not conn.execute(_CODE, (task_id,)).fetchone():
        return []
    try:
        return [str(row[0]) for row in conn.execute(_IN_USE).fetchall()]
    except sqlite3.OperationalError:
        return []  # no table: GitHub was never set up here


def repository(conn: "sqlite3.Connection", task_id: str, readable: Callable[[str], bool]) -> str | None:
    """The one GitHub repository a task's work is in (`repositories`). The runner sets it as the turn's GH_REPO
    (runner-rs/src/env.rs), so a bare `gh pr checks 46` reads it from a copy with no git remote too, and the gh shim
    picks its token. None when there is none, or more than one: the wake names each PR by its link then."""
    found = repositories(conn, task_id, readable)
    return found[0] if len(found) == 1 else None


def repository_line(repos: list[str]) -> str:
    """The line a wake opens the work with: the repository and how to get a worktree of it, or "" with none."""
    if not repos:
        return ""
    if len(repos) == 1:
        return f"Repository: {repos[0]}. Work on it in a worktree: `tico task worktree add {repos[0]}`."
    return (
        f"Repositories: {', '.join(repos)}. Work in the one the spec names, else pick one: "
        "`tico task worktree add <owner/name>`."
    )


def lines(conn: "sqlite3.Connection", entries: list[Row]) -> list[str]:
    """The entries as a bot reads them in its wake: each line, with who attached a page, said done or signed off."""
    H = _h()
    out: list[str] = []
    for entry in entries:
        by = entry.get("by")
        out.append(f"- {entry['line']}" + (f" ({H.actor_name(conn, by)})" if by else ""))
    return out


# Said once under the handed-on PRs: a bot made from an older template learns it here, not only from its playbook.
READ_CHECKS = "Read a PR's checks with `gh pr checks <its link>`: the link names the repository wherever you run it."


def one_pr(entries: list[Row]) -> str:
    """The line that keeps a task to one PR, or "" when it has no open one of its own: whoever changes code on it pushes
    to that PR's branch, never a second PR beside it (#171: a Builder handed the task mid-turn opened its own)."""
    url = next((str(e["url"]) for e in entries if e["kind"] == "pr" and e.get("open") and not e.get("filed")), "")
    if not url:
        return ""
    return (
        f"This task already has an open PR, {url}: push any change to its branch (`gh pr checkout {url}`) and update "
        "it. Never open a second PR for this task."
    )


IF_MISSING = (
    "If something you need from an earlier stage is missing or wrong, do not say done: send the task back to the "
    'stage it comes from, `tico task move <id> "<stage>" -m "<what is missing>" --version N`.'
)
