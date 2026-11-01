"""File reviews: a bot's question to a person about exact file versions (`hub task question --review`), and whether a
person approved a version (`hub file approved`, docs/files.md).

The question's ask keeps the versions under review (`review`: file, version, digest) and shows them on the card as
attachments, which each reader sees through the file's own visibility (visuals.decorate). An answer records a verdict
per version in its file's activity log (action `reviewed`), so the approval check reads one indexed log, never the
messages. A bot may review another bot's file too (`hub file review`), for a company where a bot moves a stage. By
default only a person's verdict counts (`--by human`); `--by bot:SLUG` or `--by any` reads those. The newest verdict
on that version decides, and a version never inherits another's: a new version is a new review.
"""

import re

from .store import H, Problem
from . import task_privacy as privacy

APPROVE, CHANGES = "Approve", "Request changes"
AMOUNT = re.compile(r"\$?[0-9]+(?:\.[0-9]{1,2})?")
BY = re.compile(r"human|any|bot:[a-z0-9][a-z0-9_-]{0,63}")
VERDICTS = ("approve", "changes")


def _label(title, taken, n):
    """A pick's label from a file's title: the name without its extension, words not path parts, at most 60
    characters, unique among the picks; "Option n" when nothing is left."""
    text = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", str(title or "").strip())
    text = " ".join(re.sub(r"[_/\\]+|(?<=\w)-(?=\w)", " ", text).split())[:60].strip() or f"Option {n}"
    label, i = text, 2
    while label in taken:
        label = f"{text[:55]} ({i})"
        i += 1
    taken.add(label)
    return label


def resolve(c, auth, blobs, who, person, items):
    """The versions under review as (review entries, attachments), checked: each a stored file the asking bot may
    read, at a version it has, that the person asked can open too. A bare file id means its current version."""
    from .files import file_visible
    from .visuals import kind_of, _head
    reader = auth.identity_for_actor(c, person)
    entries, attachments, seen = [], [], set()
    for item in items:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (item.file,)).fetchone()
        if not row or row["archived"] or row["locator"] != "tico_blob" or not file_visible(c, auth, who, row, {}):
            raise Problem("not_found", f"File {item.file} not found", 404)
        number = item.version or row["current_version"]
        v = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?", (row["id"], number)).fetchone()
        if not v:
            raise Problem("not_found", f"File {item.file} has no version {number}", 404)
        if (row["id"], number) in seen:
            raise Problem("validation", f"{row['id']}@{number} is under review twice", 422)
        seen.add((row["id"], number))
        if not file_visible(c, auth, reader, row, {}):
            raise Problem("privacy", f"{H.actor_id(person)} cannot open {row['id']}, so they could not review it: "
                          "publish it where they can read it", 403)
        mime = (v["mime"] or "").split(";", 1)[0].strip().lower()
        kind = kind_of(mime, v["size"], _head(blobs, v["digest"]) if blobs else b"")
        entries.append({"file": row["id"], "version": number, "digest": v["source_digest"] or v["digest"]})
        attachments.append({"id": row["id"], "file_id": row["id"], "version": number, "ref": f"{row['id']}@{number}",
                            "title": row["title"], "kind": kind, "name": v["name"], "size": v["size"],
                            "content_type": v["mime"], "url": f"/api/v2/files/{row['id']}?v={number}"})
    return entries, attachments


def same_versions(entries):
    """Whether an open question task with the same words asks about these same versions (hubdb.task_create's duplicate
    check): the same question about a new version is a new review."""
    mine = {(e["file"], e["version"]) for e in entries}

    def same(c, task_id):
        row = c.execute("SELECT m.refs FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                        "WHERE t.id=? AND json_extract(m.refs,'$.review') IS NOT NULL "
                        "ORDER BY m.created_at DESC, m.id DESC LIMIT 1", (task_id,)).fetchone()
        files = ((H._json(row["refs"], {}) or {}).get("review") or {}).get("files") or [] if row else []
        return {(f.get("file"), f.get("version")) for f in files} == mine
    return same


def options(entries, attachments, pick, given):
    """The question's options for a review: the bot's own when it gave some, else Approve and Request changes; in a
    pick, one per file (its entry keeps the label) and Request changes."""
    from . import models as M
    if pick:
        taken = set()
        for entry, a in zip(entries, attachments, strict=True):
            entry["label"] = _label(a["title"] or a["name"], taken, len(taken) + 1)
        return [*(M.QuestionChoice(label=e["label"], then="This one is approved") for e in entries),
                M.QuestionChoice(label=CHANGES, then="Your note on what to change goes back")]
    if given:
        return given
    return [M.QuestionChoice(label=APPROVE, then="This version is approved"),
            M.QuestionChoice(label=CHANGES, then="Your note on what to change goes back")]


def spend_payload(spend):
    return {"amount": spend["amount"], "account": spend["account"], "what": spend["what"]}


def check_spend(spend, labels):
    """The spend a pick approves, as stored on the ask: its option exists and the payload lints like any spend."""
    if spend is None:
        return None
    if spend.label not in labels:
        raise Problem("validation", f'--spend names "{spend.label}", which is not one of the options', 422)
    if not AMOUNT.fullmatch(spend.amount.strip()) or not float(spend.amount.strip().lstrip("$")) > 0:
        raise Problem("validation", "A spend amount is a number above zero, like 12.50", 422)
    value = {"label": spend.label, "amount": spend.amount.strip(), "account": spend.account.strip(),
             "what": spend.what.strip()}
    problems = H.lint_approval("spend", spend_payload(value))
    if problems:
        raise Problem("validation", "; ".join(problems), 422)
    return value


def verdicts(review, labels, other):
    """What one answer says of each version: approve, changes, passed (another file was picked) or other (a label
    that is neither). A note with no pick asks for changes."""
    files = review.get("files") or []
    if CHANGES in labels or not labels and other:
        said = {i: "changes" for i in range(len(files))}
    elif review.get("pick"):
        said = {i: "approve" if e.get("label") in labels else "passed" for i, e in enumerate(files)}
    else:
        said = {i: "approve" if APPROVE in labels else "other" for i in range(len(files))}
    return [{"file": e["file"], "version": e["version"], "digest": e["digest"], "verdict": said[i]}
            for i, e in enumerate(files)]


def require_readable(c, auth, who, reviewed):
    """The person answering can still open every version they are judging."""
    from .files import file_visible
    for entry in reviewed:
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (entry["file"],)).fetchone()
        if not row or not file_visible(c, auth, who, row, {}):
            raise Problem("privacy", "You can no longer open a file under review", 403)


def record(c, who, task_id, question_id, answer_id, reviewed):
    """Each verdict as a `reviewed` line in its file's activity log, which hub file approved reads."""
    at = H.now()
    for entry in reviewed:
        c.execute("INSERT INTO bot_file_activity(file_id,actor,action,attempt_id,task_id,version,digest,created_at,"
                  "detail) VALUES(?,?,'reviewed',NULL,?,?,?,?,?)",
                  (entry["file"], who.actor, task_id, entry["version"], entry["digest"], at,
                   H._dump({"verdict": entry["verdict"], "question": question_id, "answer": answer_id})))


def spend_approval(c, who, asked, task_id, spend, previous):
    """The spend the picked option carries, approved by the person who picked it: a `spend` approval the asking bot
    requested, decided by this answer, spent once by the bot (hub approval consume). One per question: a later
    answer that picks it again keeps the first."""
    for answer in previous:
        if answer.get("spend_approval"):
            return answer["spend_approval"]
    if not H.is_human(who.actor):
        raise Problem("forbidden", "Only a person approves spending", 403)
    made = H.approval_request(c, asked["from_actor"], "spend", spend_payload(spend), task_id=task_id, to=who.actor)
    # The request's own notice to the person is answered by this pick: it never sits unread in their inbox.
    c.execute("UPDATE messages SET read_at=? WHERE id=? AND read_at IS NULL", (H.now(), made["message_id"]))
    H.approval_decide(c, who.actor, made["id"], "approved", note=f'Picked "{spend["label"]}".')
    return made["id"]


def review(c, auth, who, fid=None, version=None, path=None, digest=None, task=None, verdict=None, note=""):
    """A verdict given directly (`hub file review`): a bot reviewing another bot's file, on a task it reads, when it can
    open the file; a person may too. Recorded as a `reviewed` line like an answer's, with no question."""
    from .files import file_visible
    if verdict not in VERDICTS:
        raise Problem("validation", "A review approves or asks for changes", 422)
    task_id = auth.task(c, who, auth.resolve_task(c, who, task))["id"]
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
        for found in c.execute("SELECT v.* FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE "
                               "v.repo_path=? AND v.digest=? AND NOT f.archived ORDER BY (f.bot=?), v.created_at DESC "
                               "LIMIT 20", (path, digest, mine or "")).fetchall():
            candidate = c.execute("SELECT * FROM bot_files WHERE id=?", (found["file_id"],)).fetchone()
            if file_visible(c, auth, who, candidate, {}):
                row, v = candidate, found
                break
        if not row:
            raise Problem("not_found", "No published file you can open has these bytes at this path", 404)
    else:
        raise Problem("validation", "Give a file id, or a path and its digest", 422)
    if mine and row["bot"] == mine:
        raise Problem("forbidden", "A bot never reviews its own file: ask a person or another bot", 403)
    entry = {"file": row["id"], "version": v["version"], "digest": v["source_digest"] or v["digest"]}
    c.execute("INSERT INTO bot_file_activity(file_id,actor,action,attempt_id,task_id,version,digest,created_at,"
              "detail) VALUES(?,?,'reviewed',?,?,?,?,?,?)",
              (entry["file"], who.actor, getattr(who, "attempt_id", "") or None, task_id, entry["version"], entry["digest"],
               H.now(), H._dump({"verdict": verdict, "question": None, "task": task_id, "note": note or ""})))
    return _verdict(c, who, [(row["id"], v["version"], entry["digest"])], by=who.actor)


def approved(c, auth, who, fid=None, version=None, path=None, digest=None, by="human"):
    """Whether this exact version is approved (`hub file approved`): {approved, by, at, task, question, file, version,
    digest, verdict}. By file id (current version without one), or by a path in the bot's checkout and the digest of
    its bytes now, which finds the bot's versions with those exact bytes. Only verdicts on tasks the caller reads
    count, from whom `by` names (a person by default, `bot:SLUG`, or `any`); the newest such verdict decides."""
    from .files import file_visible
    if not BY.fullmatch(by or ""):
        raise Problem("validation", "--by is human, any or bot:SLUG", 422)
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
        for v in c.execute("SELECT * FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE f.bot=? "
                           "AND v.repo_path=? AND v.digest=? ORDER BY v.created_at DESC LIMIT 20",
                           (H.actor_id(who.actor), path, digest)).fetchall():
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (v["file_id"],)).fetchone()
            if file_visible(c, auth, who, row, {}):
                found.append((row, v))
        if not found:
            return {"approved": False, "by": None, "at": None, "task": None, "question": None, "file": None,
                    "version": None, "digest": digest, "verdict": None}
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


def _verdict(c, who, versions, by="human"):
    """The newest verdict on any of `versions` (file id, version, digest; the newest version first) from whom `by`
    names, on a task the caller reads. A later verdict on a newer version with the same bytes wins over an earlier
    approval. With none counted, the first version is not approved."""
    digests = {(fid, number): digest for fid, number, digest in versions}
    marks = ", ".join("(?, ?)" for _ in versions)
    args = [part for fid, number, _ in versions for part in (fid, number)]
    rows = c.execute("SELECT * FROM bot_file_activity WHERE action='reviewed' AND (file_id, version) IN (VALUES "
                     f"{marks}) ORDER BY id DESC", args).fetchall()
    for r in rows:
        task = H.task(c, r["task_id"]) if r["task_id"] else None
        if not _counts(r["actor"], by) or not task or not privacy.task_readable(c, who, task):
            continue
        detail = H._json(r["detail"], {}) or {}
        verdict = detail.get("verdict")
        digest = digests[(r["file_id"], r["version"])]
        return {"approved": verdict == "approve" and r["digest"] == digest, "by": r["actor"], "at": r["created_at"],
                "task": r["task_id"], "question": detail.get("question"), "file": r["file_id"],
                "version": r["version"], "digest": digest, "verdict": verdict}
    fid, number, digest = versions[0]
    return {"approved": False, "by": None, "at": None, "task": None, "question": None, "file": fid, "version": number,
            "digest": digest, "verdict": None}
