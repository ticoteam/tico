"""Drop one task on another on the Tasks board (docs/tasks.md, "Merge and make a subtask"): make it a subtask, or merge.

`set_parent(A, B)` puts A under B after it was filed: A keeps its owner, stage and pipeline, and B waits on it as on any
task it filed (the engine's `links` row on B's open visit, beside the `parent` relation). History on both names who did
it.

`merge(A, into=B)` keeps B and ends A as its duplicate, in one write (the route's transaction: if any part is refused,
nothing changes). B gets A's description under "From #A", A's files and pages (linked, not copied), labels, related
links, followers and requesters; A's open subtasks move under B before A closes, so the close cascade finds none. A's
comments stay on A and its open asks end with it: B's note counts the comments and lists each question. A is Closed
with "Archiving this one as a duplicate of #B; its details were added there." and linked as a duplicate of B. With
`owner`, B is first given to that owner with the usual hand-over checks (the caller passes them in: `reassign`). A bot
owner of B is woken once with the note; a person is not. Nothing here calls a model.

`checks(A, B)` says, for each choice, the one line why it cannot be done (or None), and what a merge would move: the
board's dialog reads it before anyone confirms. People only, the route says; both tasks must be ones the person can
change.
"""

from typing import Any

from . import task_engine as E
from . import task_relations as TR

SUBTASK, MERGE = "subtask", "merge"


def _h() -> Any:
    from . import hubdb

    return hubdb


def _calls() -> Any:
    from . import task_calls

    return task_calls


def _num(row) -> str:
    return f"#{row['number']}" if row.get("number") is not None else str(row["id"])[:8]


# ----------------------------------------------------------------------------- what stands in the way
def _height(conn, task_id) -> int:
    """How many levels sit under a task: 0 for one with no subtasks."""
    found = conn.execute(
        "WITH RECURSIVE tree(id, level) AS (SELECT to_task_id, 1 FROM links WHERE from_task_id=? "
        "UNION SELECT l.to_task_id, tree.level+1 FROM links l JOIN tree ON l.from_task_id=tree.id WHERE tree.level<50) "
        "SELECT max(level) FROM tree",
        (task_id,),
    ).fetchone()
    return int(found[0] or 0) if found else 0


def _open_children(conn, task_id) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT t.id FROM links l JOIN tasks t ON t.id=l.to_task_id WHERE l.from_task_id=? AND t.status='open' "
            "ORDER BY l.created_at, l.id",
            (task_id,),
        )
    ]


def _open_pr(conn, task_id) -> bool:
    """A pull request linked to the task that is still open (or a draft): its work would be lost with the duplicate."""
    from .item_sources import github as GS

    if conn.execute(
        "SELECT 1 FROM task_links WHERE task_id=? AND kind='pr' AND state IN ('open','draft') LIMIT 1", (task_id,)
    ).fetchone():
        return True
    return any(link["metadata"] and link["metadata"].get("state") == "open" for link in GS.prs_of(conn, task_id))


def why_not(conn, a, b, choice) -> str | None:
    """One line why `a` cannot be dropped on `b` as `choice`, or None when it can."""
    H = _h()
    if a["id"] == b["id"]:
        return "A task cannot be dropped on itself."
    if a.get("status") != "open" or b.get("status") != "open":
        return f"{_num(a if a.get('status') != 'open' else b)} has ended."
    if bool(H.task_private(conn, a)) != bool(H.task_private(conn, b)):
        return "One is private and the other is not."
    if b["id"] == TR.parent_of(conn, a["id"]) and choice == SUBTASK:
        return f"{_num(a)} is already a subtask of {_num(b)}."
    if TR.is_descendant(conn, a["id"], b["id"]):
        return f"{_num(b)} is under {_num(a)}: that would make a loop."
    if choice == SUBTASK:
        if E.depth(conn, b["id"]) + 1 + _height(conn, a["id"]) > E.MAX_DEPTH:
            return f"Subtasks nest at most {E.MAX_DEPTH} deep."
        return None
    if a.get("pipeline_id") != b.get("pipeline_id"):
        return "Both tasks must be in the same pipeline to merge."
    if _open_pr(conn, a["id"]):
        return f"{_num(a)} has an open pull request."
    moved = [_height(conn, c) + 1 for c in _open_children(conn, a["id"])]
    if moved and E.depth(conn, b["id"]) + max(moved) > E.MAX_DEPTH:
        return f"{_num(a)}'s subtasks would nest more than {E.MAX_DEPTH} deep under {_num(b)}."
    return None


def _may_change(conn, actor, row, mover) -> bool:
    return bool(_calls().can_work(conn, actor, row, mover))


def _rows(conn, actor, a_id, b_id, mover):
    H = _h()
    rows = []
    for tid in (a_id, b_id):
        row = H.task(conn, tid) if tid else None
        if not row or not H.task_private_readable(conn, actor, row):
            H.refuse(conn, actor, "not-found", "No such task")
        if not _may_change(conn, actor, row, mover):
            H.refuse(conn, actor, "identity", f"You cannot change {_num(row)}: both tasks must be ones you may change.")
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- make it a subtask
def _link_under(conn, actor, child, parent, ts):
    """Put `child` under `parent`: the engine's link on the parent's open visit and the `parent` relation. Returns the
    old parent's id, or None."""
    H = _h()
    old = TR.parent_of(conn, child["id"])
    above = E.task(conn, parent["id"]) or {}
    conn.execute("DELETE FROM links WHERE to_task_id=?", (child["id"],))
    conn.execute("DELETE FROM task_relations WHERE from_task=? AND kind='parent'", (child["id"],))
    conn.execute(
        "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
        (H.new_id(), parent["id"], child["id"], (above.get("visit") or {}).get("id"), "sub_task", ts),
    )
    TR._insert(conn, actor, child["id"], parent["id"], "parent", ts)
    H._task_event(conn, child["id"], actor, "parent_id", old, parent["id"], "")
    H._task_event(conn, parent["id"], actor, "subtask", None, child["id"], "")
    if old:
        H._task_event(conn, old, actor, "subtask", child["id"], None, "")
    for tid in {child["id"], parent["id"], old} - {None}:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (ts, tid))
    E._drop_stale(conn, parent["id"])  # the parent waits on it now: a queued wake for its owner goes
    return old


def set_parent(conn, actor, task_id, parent_id, *, mover=None):
    """A person puts a task under another after it was filed. Both keep their owners, stages and pipelines; the parent
    waits on it as on any subtask. Returns the task."""
    H = _h()
    a, b = _rows(conn, actor, task_id, parent_id, mover)
    reason = why_not(conn, a, b, SUBTASK)
    if reason:
        H.refuse(conn, actor, "task_cannot_drop", reason)
    old = _link_under(conn, actor, a, b, H.now())
    H.event(conn, actor, "task.parent", a["id"], {"parent": b["id"], "was": old})
    if old:
        E.changed(conn, old, actor)
    return H.task(conn, a["id"])


# ----------------------------------------------------------------------------- merge: what moves
def _comments(conn, task_id) -> int:
    return sum(1 for m in _h().task_comments(conn, task_id) if m.get("kind") == "say")


def _asks(conn, task_id) -> list[dict]:
    """The open asks and OKs on the task's open visit: [{line, kind, person}]."""
    return [
        {"line": row["line"], "kind": row["kind"], "person": row["person_id"]}
        for row in conn.execute(
            "SELECT r.line, r.kind, r.person_id FROM requirements r JOIN visits v ON v.id=r.visit_id "
            "AND v.left_at IS NULL WHERE v.task_id=? AND r.withdrawn_at IS NULL AND r.kind IN ('ok','answer') "
            "ORDER BY r.position, r.created_at",
            (task_id,),
        )
    ]


def _files(conn, task_id) -> list[dict]:
    """The task's files and the pages attached to it: [{id, name, version, page}]. Linked from the kept task, never
    copied."""
    out, seen = [], set()
    for row in conn.execute(
        "SELECT f.id, f.mime, f.current_version, v.name FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
        "AND v.version=f.current_version WHERE f.task_id=? AND NOT f.archived ORDER BY f.first_activity_at, f.id",
        (task_id,),
    ):
        seen.add(row["id"])
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "version": row["current_version"],
                "page": "html" in str(row["mime"] or ""),
            }
        )
    for row in conn.execute(
        "SELECT p.file_id, max(p.version) AS version FROM requirement_pages p JOIN visits v ON v.id=p.visit_id "
        "WHERE v.task_id=? AND p.replaced_at IS NULL GROUP BY p.file_id ORDER BY min(p.created_at)",
        (task_id,),
    ):
        if row["file_id"] in seen:
            continue
        seen.add(row["file_id"])
        name = conn.execute(
            "SELECT name FROM bot_file_versions WHERE file_id=? AND version=?", (row["file_id"], row["version"])
        ).fetchone()
        out.append({"id": row["file_id"], "name": name[0] if name else "Page", "version": row["version"], "page": True})
    return out


def _followers(conn, a, b) -> list[str]:
    from . import task_followers as F

    have = {f["actor"] for f in F.listed(conn, b)}
    return [
        f["actor"]
        for f in F.listed(conn, a)
        if f["actor"] not in have and _h().task_private_readable(conn, f["actor"], b)
    ]


def _requesters(conn, a, b) -> list[dict]:
    """A's requesters that B does not have yet: its own requester first, then the rows of task_requesters."""
    have = {b.get("requester_id")} | {
        row[0]
        for row in conn.execute("SELECT actor FROM task_requesters WHERE task_id=? AND actor IS NOT NULL", (b["id"],))
    }
    out = []
    if a.get("requester_id") and a["requester_id"] not in have:
        out.append({"actor": a["requester_id"], "source": "manual"})
        have.add(a["requester_id"])
    for row in conn.execute("SELECT * FROM task_requesters WHERE task_id=? ORDER BY created_at", (a["id"],)):
        if row["actor"] and row["actor"] in have:
            continue
        have.add(row["actor"])
        out.append(dict(row))
    return out


# Relations that move to the kept task, as (kind, A's end): a parent moves with the subtasks, an order stays.
_MOVES = {("related", "both"), ("follow_up", "out"), ("follow_up", "in"), ("duplicate_of", "in")}


def _relations(conn, a, b) -> list[tuple[str, str, str]]:
    """(from, to, kind) rows to add for B, in place of A's."""
    out = []
    for row in conn.execute(
        "SELECT from_task, to_task, kind FROM task_relations WHERE (from_task=? OR to_task=?) AND kind<>'parent'",
        (a["id"], a["id"]),
    ):
        end = "both" if row["kind"] == "related" else "out" if row["from_task"] == a["id"] else "in"
        other = row["to_task"] if row["from_task"] == a["id"] else row["from_task"]
        if (row["kind"], end) not in _MOVES or other == b["id"]:
            continue
        pair = (b["id"], other) if end in ("out", "both") else (other, b["id"])
        if not TR._exists(conn, *pair, row["kind"]) and not (
            row["kind"] != "related" and TR._exists(conn, pair[1], pair[0], row["kind"])
        ):
            out.append((*pair, row["kind"]))
    return out


def _links(conn, a, b) -> list[dict]:
    have = {row[0] for row in conn.execute("SELECT url FROM task_links WHERE task_id=?", (b["id"],))}
    return [
        dict(row)
        for row in conn.execute(
            "SELECT url, title, kind FROM task_links WHERE task_id=? AND kind NOT IN ('pr','session','worktree') "
            "ORDER BY created_at",
            (a["id"],),
        )
        if row["url"] not in have
    ]


def _labels(a, b) -> list[str]:
    H = _h()
    kept = list(H.task_labels(b))
    return kept + [k for k in H.task_labels(a) if k not in kept]


def moves(conn, a, b) -> dict:
    """What a merge of `a` into `b` would move, counted from the tasks as they are."""
    H = _h()
    for row in (a, b):
        H.hydrate_task_tags(conn, [row])
    return {
        "comments": _comments(conn, a["id"]),
        "asks": [ask["line"] for ask in _asks(conn, a["id"])],
        "subtasks": len(_open_children(conn, a["id"])),
        "files": len(_files(conn, a["id"])),
        "followers": len(_followers(conn, a, b)),
        "requesters": len(_requesters(conn, a, b)),
        "labels": len(_labels(a, b)) - len(H.task_labels(b)),
        "links": len(_relations(conn, a, b)) + len(_links(conn, a, b)),
        "description": bool(str(a.get("body") or "").strip()),
        "due": a.get("due_at"),
    }


def checks(conn, actor, task_id, into_id, *, mover=None) -> dict:
    """For the board's dialog, before anyone confirms: both tasks, why each choice cannot be done (None when it can)
    and what a merge would move."""
    H = _h()
    a, b = _rows(conn, actor, task_id, into_id, mover)

    def end(row):
        owner = row.get("owner_id")
        return {
            "id": row["id"],
            "number": row.get("number"),
            "title": row.get("title") or "",
            "owner_id": owner,
            "owner": H.actor_name(conn, owner) if owner else "",
        }

    return {
        "task": end(a),
        "into": end(b),
        "same_owner": a.get("owner_id") == b.get("owner_id"),
        "subtask": why_not(conn, a, b, SUBTASK),
        "merge": why_not(conn, a, b, MERGE),
        "moves": moves(conn, a, b),
    }


# ----------------------------------------------------------------------------- merge: the write
def _plural(n, word) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def duplicate_note(b) -> str:
    return f"Archiving this one as a duplicate of {_num(b)}; its details were added there."


def _from_section(a, files) -> str:
    """What B's description gains: A's under "From #A", with its title, and links to A's files and pages."""
    lines = [f"## From {_num(a)}: {a.get('title') or ''}".rstrip()]
    body = str(a.get("body") or "").strip()
    if body:
        lines += ["", body]
    if files:
        lines += ["", "Files and pages:"]
        lines += [
            f"- {f['name']}: page:{f['id']}@{f['version']}"
            if f["page"]
            else f"- [{f['name']}](/api/v2/files/{f['id']})"
            for f in files
        ]
    return "\n".join(lines)


def merge(conn, actor, task_id, into_id, *, owner=None, reassign=None, mover=None) -> dict:
    """Merge task `task_id` (A) into `into_id` (B), in the caller's one write. `owner`, when given, is A's or B's owner:
    B is given to them first with `reassign(conn, b_id, owner)`, the route's usual hand-over checks; a refusal there
    leaves everything as it was. Returns {task: B, closed: A, note}."""
    H = _h()
    a, b = _rows(conn, actor, task_id, into_id, mover)
    reason = why_not(conn, a, b, MERGE)
    if reason:
        H.refuse(conn, actor, "task_cannot_drop", reason)
    if owner and owner not in (a.get("owner_id"), b.get("owner_id")):
        H.refuse(conn, actor, "kind", "The merged task is kept by one of the two owners")
    if owner and owner != b.get("owner_id"):
        if reassign is None:
            H.refuse(conn, actor, "kind", "A new owner needs the hand-over checks")
        reassign(conn, b["id"], owner)
        b = H.task(conn, b["id"])
    for row in (a, b):
        H.hydrate_task_tags(conn, [row])
    ts = H.now()
    files, asks, comments = _files(conn, a["id"]), _asks(conn, a["id"]), _comments(conn, a["id"])
    followers, requesters = _followers(conn, a, b), _requesters(conn, a, b)
    relations, links, labels = _relations(conn, a, b), _links(conn, a, b), _labels(a, b)
    added_labels = labels[len(H.task_labels(b)) :]
    # B: A's description and files under "From #A", and A's labels.
    body = "\n\n".join(part for part in (str(b.get("body") or "").rstrip(), _from_section(a, files)) if part)
    H.task_update(conn, actor, b["id"], body=body, labels=labels if added_labels else None, mover=mover)
    from . import roadmaps
    from . import task_followers as F

    tid = F.thread_id(conn, b)
    for who in followers if tid else []:
        F._upsert(conn, tid, who, F.CHOSEN)
    for req in requesters:
        values = {k: req.get(k) or "" for k in ("name", "email", "phone", "other", "organization", "note")}
        roadmaps.add_requester(
            conn,
            b["id"],
            {**values, "source": req.get("source") or "manual", "important": int(bool(req.get("important")))},
            actor=req.get("actor"),
        )
    for from_id, to_id, kind in relations:
        TR._insert(conn, actor, from_id, to_id, kind, ts)
        TR._history(conn, actor, from_id, to_id, kind, True, f"from {_num(a)}", at=ts)
    for link in links:
        H.task_link(conn, actor, b["id"], link["url"], link.get("title"), kind=link["kind"], mover=mover)
    # A's open subtasks go under B before A closes, so the close finds none to close.
    moved = []
    for child in _open_children(conn, a["id"]):
        _link_under(conn, actor, H.task(conn, child), b, ts)
        moved.append(child)
    TR.relate(conn, actor, a["id"], b["id"], "duplicate_of", mover=mover)
    note = duplicate_note(b)
    H.task_close(conn, actor, a["id"], note, mover=mover)
    H.task_comment(conn, actor, a["id"], note, wake=False)
    # The note on B: what came in, the comments left on A and each question that ended with it.
    came = [f'its description under "From {_num(a)}"'] if str(a.get("body") or "").strip() else []
    came += [f"{len(files)} of its files and pages, linked"] if files else []
    came += [_plural(len(added_labels), "label")] if added_labels else []
    came += [_plural(len(followers), "follower")] if followers else []
    came += [_plural(len(requesters), "requester")] if requesters else []
    came += [_plural(len(relations) + len(links), "related link")] if relations or links else []
    came += [_plural(len(moved), "open subtask") + " moved here"] if moved else []
    lines = [f"Merged {_num(a)} \"{a.get('title') or ''}\" into this task as its duplicate."]
    if came:
        lines.append("Added: " + "; ".join(came) + ".")
    if comments:
        lines.append(f"{_plural(comments, 'comment')} on {_num(a)} stay there.")
    if asks:
        lines.append(f"Open questions on {_num(a)} ended with it; ask again if one still matters:")
        lines += [
            f"- {ask['line']}" + (f" ({H.actor_name(conn, ask['person'])})" if ask.get("person") else "")
            for ask in asks
        ]
    if a.get("due_at"):
        lines.append(f"{_num(a)} was due {str(a['due_at'])[:10]}.")
    text = "\n".join(lines)
    H.task_comment(conn, actor, b["id"], text, wake=False)
    E.tell_owner(conn, b["id"], text, f"merged:{a['id']}")
    H.event(conn, actor, "task.merge", a["id"], {"into": b["id"], "subtasks": moved, "owner": owner})
    return {"task": H.task(conn, b["id"]), "closed": H.task(conn, a["id"]), "note": text}
