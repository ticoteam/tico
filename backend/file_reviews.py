"""File reviews: whether a person approved an exact file version (`hub file approved`, docs/files.md).

A bot asks a person for an OK on a task and shows the file (`hub task ask <id> --to <person> "line" --show
file:<id>@<version>`, or `--show file:<path in its checkout>`). The person's OK on that ask approves the version it
shows, and a send back asks for changes on it (`_asked`): a `file:<id>@<n>` ref names its version, and a bare
`file:<id>` or `file:<path>` ref names the version that was newest when the person answered. A bot may review another
bot's file too (`hub file review`), recorded in the file's activity log (action `reviewed`). By default only a
person's verdict counts (`--by human`); `--by bot:SLUG` or `--by any` reads those. The newest verdict on that version
decides, and a version never inherits another's: a new version is a new review.
"""

import re

from .store import H, Problem
from . import task_privacy as privacy

BY = re.compile(r"human|any|bot:[a-z0-9][a-z0-9_-]{0,63}")
VERDICTS = ("approve", "changes")


def review(c, auth, who, fid=None, version=None, path=None, digest=None, task=None, verdict=None, note=""):
    """A verdict given directly (`hub file review`): a bot reviewing another bot's file, on a task it reads, when it can
    open the file; a person may too. Recorded as a `reviewed` line like an answer's, with no question."""
    from .files import file_visible

    if verdict not in VERDICTS:
        raise Problem("validation", "A review approves or asks for changes", 422)
    task_id = auth.task(c, who, auth.resolve_task(c, who, task))["id"]
    H._writer(c, who.actor, task_id=task_id)
    mine = H.actor_id(who.actor) if H.is_bot(who.actor) else None
    if fid:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not row or row["archived"] or not file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "File not found", 404)
        number = version or row["current_version"]
        v = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?", (fid, number)).fetchone()
        if not v:
            raise Problem("not_found", f"File {fid} has no version {number}", 404)
    elif path and digest:
        # The same bytes another bot published from the same path: the newest such version this reviewer can open.
        row = v = None
        for found in c.execute(
            "SELECT v.* FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE "
            "v.repo_path=? AND v.digest=? AND NOT f.archived ORDER BY (f.bot_id=?), v.created_at DESC "
            "LIMIT 20",
            (path, digest, mine or ""),
        ).fetchall():
            candidate = c.execute("SELECT * FROM bot_files WHERE id=?", (found["file_id"],)).fetchone()
            if file_visible(c, auth, who, candidate, {}):
                row, v = candidate, found
                break
        if not row:
            raise Problem("not_found", "No published file you can open has these bytes at this path", 404)
    else:
        raise Problem("validation", "Give a file id, or a path and its digest", 422)
    H._writer(c, who.actor, task_id=row["task_id"])
    if mine and row["bot_id"] == mine:
        raise Problem("forbidden", "A bot never reviews its own file: ask a person or another bot", 403)
    entry = {"file": row["id"], "version": v["version"], "digest": v["source_digest"] or v["digest"]}
    c.execute(
        "INSERT INTO bot_file_activity(file_id,actor,action,attempt_id,task_id,version,digest,created_at,"
        "detail) VALUES(?,?,'reviewed',?,?,?,?,?,?)",
        (
            entry["file"],
            who.actor,
            getattr(who, "attempt_id", "") or None,
            task_id,
            entry["version"],
            entry["digest"],
            H.now(),
            H._dump({"verdict": verdict, "question": None, "task": task_id, "note": note or ""}),
        ),
    )
    return _verdict(c, who, [(row["id"], v["version"], entry["digest"])], by=who.actor)


def approved(c, auth, who, fid=None, version=None, path=None, digest=None, by="human"):
    """Whether this exact version is approved (`hub file approved`): {approved, by, at, task, question, file, version,
    digest, verdict}. By file id (current version without one), or by a path in the bot's checkout and the digest of
    its bytes now, which finds the bot's versions with those exact bytes. Only verdicts on tasks the caller reads
    count, from whom `by` names (a person by default, `bot:SLUG`, or `any`); the newest such verdict decides."""
    from .files import file_visible

    if not BY.fullmatch(by or ""):
        raise Problem("validation", "--by is human, any or bot:SLUG", 422)
    if by.startswith("bot:"):
        by = H.bot_actor((H.resolve_bot(c, by) or {}).get("id", by[4:]))
    if fid:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not row or not file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "File not found", 404)
        number = version or row["current_version"]
        v = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?", (fid, number)).fetchone()
        if not v:
            raise Problem("not_found", f"File {fid} has no version {number}", 404)
        found = [(row, v)]
    elif path and digest:
        if who.role != "bot":
            raise Problem("validation", "A path is a bot's own file: give a file id", 422)
        found = []
        for v in c.execute(
            "SELECT * FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE f.bot_id=? "
            "AND v.repo_path=? AND v.digest=? ORDER BY v.created_at DESC LIMIT 20",
            (H.actor_id(who.actor), path, digest),
        ).fetchall():
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (v["file_id"],)).fetchone()
            if file_visible(c, auth, who, row, {}):
                found.append((row, v))
        if not found:
            return {
                "approved": False,
                "by": None,
                "at": None,
                "task": None,
                "question": None,
                "file": None,
                "version": None,
                "digest": digest,
                "verdict": None,
            }
    else:
        raise Problem("validation", "Give a file id, or a path and its digest", 422)
    return _verdict(c, who, [(row["id"], v["version"], v["source_digest"] or v["digest"]) for row, v in found], by=by)


def _counts(actor, by):
    """Whether a verdict from `actor` is one `by` asks for: human, any (a person or a bot), or that one actor."""
    if by == "human":
        return H.is_human(actor)
    if by == "any":
        return H.is_human(actor) or H.is_bot(actor)
    return actor == by


def _asked(c, versions):
    """The verdicts OKs and send backs give the versions their asks show: one dict per answer and version, like an
    activity row's (created_at, actor, task_id, file_id, version, digest, verdict, question). An answer taken back
    gives none."""
    out = []
    for fid, number, digest in versions:
        meta = c.execute(
            "SELECT f.bot_id, v.repo_path FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
            "WHERE f.id=? AND v.version=?",
            (fid, number),
        ).fetchone()
        refs = [f"file:{fid}@{number}", f"file:{fid}@v{number}", f"file:{fid}"]
        if meta and meta["repo_path"]:
            refs.append("file:" + meta["repo_path"])
        marks = ",".join("?" for _ in refs)
        for r in c.execute(
            "SELECT DISTINCT r.id AS requirement_id, r.added_by_id, vi.task_id, a.by_id, a.answer, a.created_at, "
            "j.value AS ref FROM requirements r JOIN visits vi ON vi.id=r.visit_id "
            "JOIN answers a ON a.requirement_id=r.id AND a.visit_id=r.visit_id "
            f"JOIN json_each(CASE WHEN json_valid(r.shows) THEN r.shows ELSE '[]' END) j "
            f"WHERE r.kind='ok' AND a.undone_at IS NULL AND a.answer IN ('ok','no') AND j.value IN ({marks})",
            refs,
        ).fetchall():
            if r["ref"] not in refs[:2]:
                # A ref without a version names the newest one when the person answered; a path is the asking bot's.
                if r["ref"] != f"file:{fid}" and (not meta or H.bot_actor(meta["bot_id"]) != r["added_by_id"]):
                    continue
                newest = c.execute(
                    "SELECT v.file_id, v.version FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
                    "WHERE v.created_at<=? AND (v.file_id=? OR (? AND f.bot_id=? AND v.repo_path=?)) "
                    "ORDER BY v.created_at DESC, v.version DESC LIMIT 1",
                    (
                        r["created_at"],
                        fid,
                        int(r["ref"] != f"file:{fid}"),
                        meta["bot_id"] if meta else "",
                        meta["repo_path"] if meta else "",
                    ),
                ).fetchone()
                if not newest or (newest["file_id"], newest["version"]) != (fid, number):
                    continue
            out.append(
                {
                    "created_at": r["created_at"],
                    "actor": r["by_id"],
                    "task_id": r["task_id"],
                    "file_id": fid,
                    "version": number,
                    "digest": digest,
                    "verdict": "approve" if r["answer"] == "ok" else "changes",
                    "question": r["requirement_id"],
                }
            )
    return out


def _verdict(c, who, versions, by="human"):
    """The newest verdict on any of `versions` (file id, version, digest; the newest version first) from whom `by`
    names, on a task the caller reads: an OK or a send back on an ask that shows it, or a bot's direct review. A later
    verdict on a newer version with the same bytes wins over an earlier approval. With none counted, the first version
    is not approved."""
    digests = {(fid, number): digest for fid, number, digest in versions}
    marks = ", ".join("(?, ?)" for _ in versions)
    args = [part for fid, number, _ in versions for part in (fid, number)]
    rows = [
        {
            "created_at": r["created_at"],
            "actor": r["actor"],
            "task_id": r["task_id"],
            "file_id": r["file_id"],
            "version": r["version"],
            "digest": r["digest"],
            "verdict": (H._json(r["detail"], {}) or {}).get("verdict"),
            "question": (H._json(r["detail"], {}) or {}).get("question"),
        }
        for r in c.execute(
            "SELECT * FROM bot_file_activity WHERE action='reviewed' AND (file_id, version) IN (VALUES "
            f"{marks}) ORDER BY id DESC",
            args,
        ).fetchall()
    ]
    rows = sorted(rows + _asked(c, versions), key=lambda r: r["created_at"] or "", reverse=True)
    for r in rows:
        task = H.task(c, r["task_id"]) if r["task_id"] else None
        if not _counts(r["actor"], by) or not task or not privacy.task_readable(c, who, task):
            continue
        digest = digests[(r["file_id"], r["version"])]
        return {
            "approved": r["verdict"] == "approve" and r["digest"] == digest,
            "by": r["actor"],
            "at": r["created_at"],
            "task": r["task_id"],
            "question": r["question"],
            "file": r["file_id"],
            "version": r["version"],
            "digest": digest,
            "verdict": r["verdict"],
        }
    fid, number, digest = versions[0]
    return {
        "approved": False,
        "by": None,
        "at": None,
        "task": None,
        "question": None,
        "file": fid,
        "version": number,
        "digest": digest,
        "verdict": None,
    }
