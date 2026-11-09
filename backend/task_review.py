"""Structured questions: the checks a question task's question meets (check_ask), the answers to one (answer_task),
and file version notes. Old ask messages on comments and file versions are history (step 6c)."""

from datetime import timezone
import html
import re

from .store import H, Problem
from . import task_privacy as privacy


def readable_text(value):
    text = " ".join(str(value).splitlines())
    text = re.sub(r"([\\`*_{}\[\]()!#+|~-])", r"\\\1", text)
    text = html.escape(text, quote=False).replace("@", "&#64;")
    text = re.sub(r"(?i)\b(https?|ftp):", r"\1&#58;", text)
    return re.sub(r"(?i)\bwww\.", "www&#46;", text)


def comment_rights(c, auth, who, task_id):
    """Whether the caller may write on the task: every comment, answer, review question, edit and file goes through here."""
    row = auth.task(c, who, task_id)
    from . import lifecycle

    lifecycle.refuse_send(c, who, None, what="task comments")
    if who.role == "bot" and not H.task_participant(c, who.actor, row):
        delegated = c.execute(
            "SELECT 1 FROM task_delegations WHERE task_id=? AND delegate=? AND expires_at>?",
            (task_id, who.actor, H.now()),
        ).fetchone()
        if (
            not delegated
            and not H.task_ancestor_party(c, who.actor, row)
            and not H.type_bot_reads(c, who.actor, row)
            and not H.task_manager_acts(c, who.actor, row)
        ):
            raise Problem(
                "forbidden",
                "Only task parties, their managers, delegates, ancestor parties or bots allowed by the task type may comment",
                403,
            )
    origin = None if H.task_private(c, row) else H.task_origin(c, row)
    others = [
        a for a in dict.fromkeys([row["owner_id"], row["requester_id"], origin]) if a and a not in (who.actor, H.KEEPER)
    ]
    target = next((a for a in others if H.is_bot(a)), None)
    if target and not H.task_represents(c, who.actor, row["owner_id"]):
        auth.require_write(c, who, H.actor_id(target))
    if target:
        auth.require_bot_contact(c, who, target, task_id=task_id, kind="comment")
    return row


def file_version(c, task_id, fid, number, *, auth=None, who=None):
    if who is not None:
        from .files import file_visible, page_blob_visible

        file = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if (file and not file_visible(c, auth, who, file, {})) or (
            not file and not page_blob_visible(c, auth, who, fid)
        ):
            raise Problem("not_found", "File version not found on this task", 404)
    row = c.execute(
        "SELECT v.* FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
        "WHERE f.task_id=? AND v.file_id=? AND v.version=?",
        (task_id, fid, number),
    ).fetchone()
    if row:
        return dict(row)
    # Legacy attachments are v1 without rewriting old data.
    if number == 1:
        row = c.execute(
            "SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id WHERE a.task_id=? AND b.id=?", (task_id, fid)
        ).fetchone()
        if row:
            return {**dict(row), "actor": row["owner"], "mime": row["content_type"], "version": 1}
    raise Problem("not_found", "File version not found on this task", 404)


def comment_file(c, auth, files, who, task_id, fid, number):
    """The (file id, version) a comment attaches. A person may also attach a chat file they can open (`<id>@1`):
    the task gets its own copy of the same bytes through the task-file service, so the task's readers open it
    through the task, and the chat file's own access and lifetime stay as they were."""
    try:
        return fid, file_version(c, task_id, fid, number, auth=auth, who=who)
    except Problem:
        # A person's own chat upload is also a versioned file in that chat (backend/visuals.py); any other file's
        # bytes are shared by that file's id, never by its blob's.
        if (
            number != 1
            or who.role not in ("owner", "human")
            or c.execute(
                "SELECT 1 FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id WHERE v.blob_id=? "
                "AND f.identity NOT LIKE 'chat-file:%'",
                (fid,),
            ).fetchone()
        ):
            raise
    from .blobs import register
    from .media import blob_for

    try:
        source = blob_for(c, auth, who, fid)
    except Problem:
        raise Problem("not_found", "File version not found on this task", 404) from None
    # Sharing the same file again points at the copy already on the task.
    same = c.execute(
        "SELECT v.file_id,v.version FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
        "WHERE f.task_id=? AND f.archived=0 AND v.digest=? AND v.name=? ORDER BY v.version DESC LIMIT 1",
        (task_id, source["digest"], source["name"]),
    ).fetchone()
    if same:
        return same["file_id"], file_version(c, task_id, same["file_id"], same["version"], auth=auth, who=who)
    item = register(c, who, source["digest"], source["size"], source["name"], source["content_type"])
    copy = files.publish_task_deliverable(c, who, task_id, item, source["digest"])
    c.execute("INSERT INTO task_assets VALUES(?,?)", (task_id, item["id"]))
    current = c.execute("SELECT current_version FROM bot_files WHERE id=?", (copy,)).fetchone()[0]
    file_added(c, who, task_id, item, current)
    return copy, file_version(c, task_id, copy, current)


def file_added(c, who, task_id, item, number, poster=None):
    """What a file added to a task records, uploaded or shared from a chat: the task's own "file" activity line,
    the task.file audit event and the preview worker's row. The caller wakes the media worker once it commits."""
    from .files import page_references

    provenance = {"file": item["id"], "version": number}
    if pages := page_references(item["id"], c):
        provenance["page_files"] = sorted(pages)
    name, data = H.sourced_text(item["name"], [provenance], provenance)
    H._task_event(c, task_id, who.actor, "file", None, name, "" if number == 1 else f"version {number}", data=data)
    H.event(c, who.actor, "task.file", task_id, {**data, "name": name, "size": item["size"]})
    c.execute("INSERT OR IGNORE INTO blob_media(blob_id,poster_blob_id) VALUES(?,?)", (item["id"], poster))


def check_ask(c, task_id, ask, auth, who, lint=True):
    """The question a question task carries (media.question_write), as stored, after its checks: it goes to `ask.who`,
    else the task's requester. A bot's question to a person also keeps the asking rules (hubdb.lint_question), unless
    the caller checked them already (`lint` false)."""
    if not ask:
        return None
    value = ask.model_dump(exclude_none=True)
    value["who"] = ask.who
    for question in value["questions"]:
        # The card fields a question leaves out are not stored, so refs read as before for an ask without them.
        if not question.get("why"):
            question.pop("why", None)
        for option in question["options"]:
            if option.get("file"):
                fid, number = option["file"].rsplit("@", 1)
                option["source"] = {"file": fid, "version": int(number)}
            if not option.get("then"):
                option.pop("then", None)
            if not option.get("recommended"):
                option.pop("recommended", None)
    row = H.task(c, task_id)
    target = H.resolve_actor(c, ask.who) if ask.who else row["requester_id"]
    if H.task_private(c, row) and (H.is_human(target) or H.is_bot(target)):
        from .auth import actor_reads_task

        if not actor_reads_task(c, target, row):
            raise Problem("private", "Only someone who can read the task can receive its questions", 403)
    if lint and who.role == "bot":
        H.require_plain_question(c, who.actor, target, ask.questions)
    if ask.who:
        if not H.bot(c, H.actor_id(target)) and not H.human(c, H.actor_id(target)):
            raise Problem("validation", "Question recipient not found", 422)
        if not H.task_private_readable(c, target, H.task(c, task_id)):
            raise Problem("privacy", "Private questions stay with the requester and current owner", 403)
        if H.is_bot(target):
            row = H.task(c, task_id)
            if target not in (row["owner_id"], row["requester_id"], H.task_origin(c, row)):
                auth.require_write(c, who, H.actor_id(target))
            auth.require_bot_contact(c, who, target, task_id=task_id, kind="comment")
    for question in ask.questions:
        for option in question.options:
            if option.file:
                fid, number = option.file.rsplit("@", 1)
                file_version(c, task_id, fid, int(number), auth=auth, who=who)
    if ask.if_no_answer and ask.if_no_answer.by:
        by = H.parse_ts(ask.if_no_answer.by)
        if not by or by.tzinfo is None:
            raise Problem("date", "if_no_answer.by must be an ISO-8601 date/time with a timezone", 422)
        if by <= H.parse_ts(H.now()):
            raise Problem("date", "if_no_answer.by is already past: give the person time to answer", 422)
        from .ask_deadlines import EXPIRE_DAYS

        if by > H.parse_ts(H.shift(H.now(), days=EXPIRE_DAYS)):
            raise Problem(
                "date", f"if_no_answer.by is more than {EXPIRE_DAYS} days away, when an unanswered ask expires", 422
            )
        # Stored in UTC as every timestamp is, so it compares with them as text.
        value["if_no_answer"]["by"] = H.shift(by.astimezone(timezone.utc).isoformat())
    default_source(value)
    return value


def default_source(ask):
    """A fallback that copies an option's label keeps that option's source too."""
    default = ask.get("if_no_answer") or {}
    wanted = str(default.get("do") or "").strip().casefold()
    for question in ask.get("questions", []):
        for option in question.get("options", []):
            if wanted and str(option.get("label") or "").strip().casefold() == wanted and option.get("source"):
                default["source"] = option["source"]


def version_review(c, fid, number, *, actor):
    row = c.execute("SELECT * FROM task_file_reviews WHERE file_id=? AND version=?", (fid, number)).fetchone()
    asked = H.message(c, row["ask_message_id"]) if row and row["ask_message_id"] else None
    if asked and not privacy.message_readable(c, actor, asked):
        asked = None
    if asked:
        from .files import readable_page_refs
        from .teammate_access import _viewer_for

        for principal in actor if isinstance(actor, (tuple, list, set)) else [actor]:
            viewer = _viewer_for(c, principal)
            if viewer:
                asked = {
                    **asked,
                    "refs": readable_page_refs(c, None, viewer, asked["refs"], task_id=H.message_task_id(asked)),
                }
    comment_visible = row and (
        not row["comment_id"] or privacy.message_readable(c, actor, H.message(c, row["comment_id"]))
    )
    return {
        "note": row["note"] if comment_visible else None,
        "comment_id": row["comment_id"] if comment_visible else None,
        "ask": {**{k: asked["refs"].get(k) for k in ("questions", "who")}, "by": asked["sender_id"]} if asked else None,
        "answers": H.review_answers(c, asked["id"], actor=actor) if asked else [],
    }


def edit_version(c, auth, who, task_id, fid, number, body):
    """Whoever added a file version changes its note. A question about it is a question task (`hub task question`)."""
    auth.task(c, who, task_id)
    H._writer(c, who.actor, task_id=task_id)
    version = file_version(c, task_id, fid, number, auth=auth, who=who)
    if version["actor"] != who.actor:
        raise Problem("forbidden", "Only whoever added this version may edit its note", 403)
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task_id))
    c.execute("INSERT OR IGNORE INTO task_file_reviews(file_id,version) VALUES(?,?)", (fid, number))
    if "note" in body.model_fields_set:
        c.execute("UPDATE task_file_reviews SET note=? WHERE file_id=? AND version=?", (body.note, fid, number))
    return version_review(c, fid, number, actor=privacy.actor(who))


def checked_answers(questions, refs, body):
    """A structured answer checked against the question as stored (`questions` by id, the ask's `refs`): every question
    answered with its own labels, or the Other text where a question takes it. Returns (the answer with each question
    keyed, the unticked labels of several picks: the "not this one"s). A comment ask and a question task check alike."""
    if body.dismiss:
        if body.answers:
            raise Problem("validation", "Dismiss does not include answers", 422)
    else:
        if refs.get("asked_again_at") and set(questions) - set(body.answers):
            # A question the bot reworded in place (question_tasks.fold) is answered only by naming its current
            # question id, so a pick or an Other made on the earlier wording never settles the new one.
            raise Problem(
                "changed", "The question was reworded after you opened it: read the new wording and answer again", 409
            )
        if set(body.answers) - set(questions):
            raise Problem("validation", "Unknown question id", 422)
        missing = set(questions) - set(body.answers)
        if missing and (not body.other or any(not questions[qid].get("other", True) for qid in missing)):
            raise Problem("validation", "Answer every question", 422)
        # The page omits unselected questions when its shared Other text answers them.
        body = body.model_copy(update={"answers": {qid: body.answers.get(qid, []) for qid in questions}})
        for qid, question in questions.items():
            labels = body.answers[qid]
            allowed = {o["label"] for o in question["options"]}
            if (
                len(set(labels)) != len(labels)
                or set(labels) - allowed
                or (not question.get("multi") and len(labels) > 1)
            ):
                raise Problem("validation", "Choose valid labels for each question", 422)
            if not labels and not (body.other and question.get("other", True)):
                raise Problem("validation", "Choose an option or supply an allowed other answer", 422)
        # Other text is allowed when at least one question offers a free-text answer.
        if body.other and not any(q.get("other", True) for q in questions.values()):
            raise Problem("validation", "Other text is not allowed for this question", 422)
    # Several picks answered with some left unticked: the bot hears each of those as "not this one" (the card's
    # Send ticked), the note saying what instead.
    passed = (
        {}
        if body.dismiss
        else {
            qid: [o["label"] for o in q["options"] if o["label"] not in body.answers[qid]]
            for qid, q in questions.items()
            if q.get("multi") and body.answers[qid]
        }
    )
    return body, passed


def answer_words(name, questions, body, passed, subject=None):
    """What an answer says, for the bot that asked: "Ana answered "<question>": A." (`subject` names a file version
    under review), before any Other text."""
    if body.dismiss:
        return (
            f"{name} closed the question (declined) on {subject}."
            if subject
            else f"{name} closed the question (declined)."
        )
    if subject and len(questions) == 1 and list(body.answers.values()) == [["Approve"]]:
        return f"{name} approved {subject}."
    text = " ".join(
        f'{name} answered "{readable_text(q["question"])}": {", ".join(readable_text(label) for label in body.answers[qid]) or "Other"}.'
        + (f' Not this one: {", ".join(readable_text(label) for label in passed[qid])}.' if passed.get(qid) else "")
        for qid, q in questions.items()
    )
    return text + (f" File: {subject}." if subject else "")


def answer_task(c, auth, who, task_id, body, wake, warn=""):
    """A structured answer to a question task (backend/question_tasks.py): the answer is its close. `warn` (an org
    rule's Warn) opens the answer its readers get. A comment's or file version's ask is history (step 6c): it is not
    answered any more."""
    H._writer(c, who.actor, task_id=task_id)
    if not body.target.task:
        raise Problem(
            "asks_gone", "Asks are gone: a question is a question task now; answer it with target {task}", 422
        )
    if body.target.task != task_id:
        raise Problem("not_found", "Question not found on this task", 404)
    from . import question_tasks

    return question_tasks.answer(c, auth, who, task_id, body, warn)


# The newest visual per task. `bot_files_task` (backend/files.py) finds a task's files without scanning every file.
COVERS_SQL = (
    "WITH visuals AS ("
    "SELECT f.task_id,f.id,v.version,v.mime,v.created_at,v.rowid AS position,"
    "coalesce(v.width,m.width) AS width,coalesce(v.height,m.height) AS height,"
    "coalesce(v.thumb_blob_id,m.thumb_blob_id) AS thumb_blob_id,"
    "coalesce(v.poster_blob_id,m.poster_blob_id) AS poster_blob_id "
    "FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
    "LEFT JOIN blob_media m ON m.blob_id=v.blob_id "
    "WHERE f.task_id IN ({marks}) AND f.archived=0 AND f.locator='tico_blob' "
    "UNION ALL SELECT a.task_id,b.id,1,b.content_type,b.created_at,b.rowid,"
    "m.width,m.height,m.thumb_blob_id,m.poster_blob_id "
    "FROM task_assets a JOIN blobs b ON b.id=a.blob_id LEFT JOIN blob_media m ON m.blob_id=b.id "
    "WHERE a.task_id IN ({marks}) AND NOT EXISTS "
    "(SELECT 1 FROM bot_file_versions v WHERE v.blob_id=b.id)), "
    "ranked AS (SELECT *,ROW_NUMBER() OVER (PARTITION BY task_id "
    "ORDER BY created_at DESC,version DESC,position DESC) AS rank FROM visuals "
    "WHERE mime LIKE 'image/%' OR (mime LIKE 'video/%' AND poster_blob_id IS NOT NULL)) "
    "SELECT * FROM ranked WHERE rank=1"
)


def task_covers(c, task_ids):
    """Newest visual version per task, with one query for a bounded task page."""
    if not task_ids:
        return {}
    marks = ",".join("?" * len(task_ids))
    rows = c.execute(COVERS_SQL.format(marks=marks), (*task_ids, *task_ids))
    out = {}
    for row in rows:
        suffix = "/poster" if row["mime"].startswith("video/") else "/thumb" if row["thumb_blob_id"] else ""
        out[row["task_id"]] = {
            "url": f"/api/v2/files/{row['id']}{suffix}?v={row['version']}",
            "width": row["width"],
            "height": row["height"],
        }
    return out
