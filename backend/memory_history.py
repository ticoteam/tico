"""A bot's learnings history: the commits that changed its memory/ and knowledge/ (docs/learnings.md).

Git is the record. The server holds no checkout, so the computer running the bot reports each commit
that touched those folders: subject, author, time, files, the diff of those folders, whether it reached
the shared repository, and the run that made it when that is known. This table is a view of those
commits, never a second copy of what the bot knows; a commit reported twice is the same row.

A run is named by the bot's own `Tico-Run:` commit trailer, or by the runner when the commit was made
during a run it executed. Either way the run must belong to the bot or its family (the original and
its branches share one repository); any other name is dropped rather than shown. Who may follow a
run's source is decided when it is read: the task or conversation it came from must still be
readable by the reader, else the update shows without its source.
"""

import json
import re
from datetime import timezone

from fastapi import Request
from pydantic import Field

from . import models as M
from . import task_privacy as privacy
from .shared_bots import copies, declared, source_of
from .store import H, Problem
from .views import human_only

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_memory_updates(
 bot TEXT NOT NULL, sha TEXT NOT NULL, subject TEXT NOT NULL, author TEXT NOT NULL, committed TEXT NOT NULL,
 files_json TEXT NOT NULL, diff TEXT NOT NULL DEFAULT '', truncated INTEGER NOT NULL DEFAULT 0,
 attempt_id TEXT NOT NULL DEFAULT '', shared INTEGER NOT NULL DEFAULT 0, runner_id TEXT NOT NULL,
 reported TEXT NOT NULL, PRIMARY KEY(bot, sha));
CREATE INDEX IF NOT EXISTS bot_memory_updates_by_time ON bot_memory_updates(bot, committed);
CREATE TABLE IF NOT EXISTS bot_memory_documents(
 bot TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(bot, path));
CREATE TABLE IF NOT EXISTS bot_memory_seen(
 actor TEXT NOT NULL, bot TEXT NOT NULL, seen TEXT NOT NULL, PRIMARY KEY(actor, bot));
"""

FOLDERS = ("memory/", "knowledge/")
DOCUMENTS = ("memory/learnings.md", "memory/decisions.md")
MAX_DIFF = 20_000
PAGE = 20
NEW_DAYS = 3
SHA = r"^[0-9a-f]{40}$"


class MemoryCommit(M.Contract):
    sha: str = Field(pattern=SHA)
    subject: str = Field(max_length=300)
    author: str = Field(default="", max_length=200)
    committed: str = Field(max_length=40)
    files: list[str] = Field(min_length=1, max_length=200)
    diff: str = Field(default="", max_length=MAX_DIFF)
    truncated: bool = False
    shared: bool = False
    attempt: str = Field(default="", max_length=100)


class MemoryReport(M.Contract):
    model_config = M.ConfigDict(extra="forbid", str_strip_whitespace=False)
    commits: list[MemoryCommit] = Field(default_factory=list, max_length=50)
    documents: dict[str, str] = Field(default_factory=dict, max_length=len(DOCUMENTS))


def family(c, bot):
    """The bots that write the same repository as `bot`: its original and every branch of that original."""
    root = source_of(declared(c, bot)) or bot
    return {root, *copies(c, root)}


def _run_of(c, bot, attempt):
    """The attempt id when it is a run of this bot's family, else ""."""
    if not attempt:
        return ""
    row = c.execute("SELECT bot FROM attempts WHERE id=?", (attempt,)).fetchone()
    return attempt if row and row["bot"] in family(c, bot) else ""


def record(c, who, bot, report):
    """Store what a computer reported for a bot assigned to it. Returns how many commits were new or changed."""
    if not c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?", (bot, who.runner_id)).fetchone():
        raise Problem("forbidden", "That bot is not assigned to this computer", 403)
    now, changed = H.now(), 0
    for commit in report.commits:
        files = [f for f in commit.files if f.startswith(FOLDERS) and ".." not in f.split("/")]
        at = H.parse_ts(commit.committed)
        if not files or not at or at.tzinfo is None:
            continue
        committed = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        attempt = _run_of(c, bot, commit.attempt)
        before = c.execute("SELECT attempt_id,shared FROM bot_memory_updates WHERE bot=? AND sha=?",
                           (bot, commit.sha)).fetchone()
        if before and (before["attempt_id"] or not attempt) and (before["shared"] or not commit.shared):
            continue
        # A commit's content never changes; only what is known about it grows (its run, reaching the repository).
        c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed,files_json,diff,truncated,"
                  "attempt_id,shared,runner_id,reported) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(bot,sha) DO UPDATE SET "
                  "attempt_id=CASE WHEN bot_memory_updates.attempt_id='' THEN excluded.attempt_id ELSE bot_memory_updates.attempt_id END,"
                  "shared=max(bot_memory_updates.shared,excluded.shared),"
                  # The diff arrives once the commit is in the shared repository (runner/memory_history.py).
                  "diff=CASE WHEN bot_memory_updates.diff='' THEN excluded.diff ELSE bot_memory_updates.diff END,"
                  "truncated=CASE WHEN bot_memory_updates.diff='' THEN excluded.truncated ELSE bot_memory_updates.truncated END",
                  (bot, commit.sha, commit.subject, commit.author, committed, json.dumps(files),
                   commit.diff, int(commit.truncated), attempt, int(commit.shared), who.runner_id, now))
        changed += 1
    for path, content in report.documents.items():
        if path in DOCUMENTS and len(content) <= 100_000:
            c.execute("INSERT INTO bot_memory_documents(bot,path,content,updated) VALUES(?,?,?,?) "
                      "ON CONFLICT(bot,path) DO UPDATE SET content=excluded.content,updated=excluded.updated "
                      "WHERE bot_memory_documents.content<>excluded.content", (bot, path, content, now))
    return changed


def documents(c, bot):
    return {row["path"]: row["content"] for row in
            c.execute("SELECT path,content FROM bot_memory_documents WHERE bot=?", (bot,))}


def source(c, auth, who, attempt):
    """Where a run came from, for this reader: a task, or a message in a conversation; None when unreadable."""
    if not attempt:
        return None
    row = c.execute("SELECT a.bot,m.id AS mid,m.conversation_id FROM attempts a JOIN jobs j ON j.id=a.job_id "
                    "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (attempt,)).fetchone()
    if not row or not privacy.attempt_readable(c, privacy.actor(who), attempt):
        return None
    message, conversation = H.message(c, row["mid"]), H.conversation(c, row["conversation_id"])
    if not privacy.message_readable(c, privacy.actor(who), message):
        return None
    task = H.message_task_id(message, conversation)
    if task:
        found = H.task(c, task)
        if not found or not privacy.task_readable(c, who, found):
            return None
        return {"bot": row["bot"], "task": task, "title": found["title"]}
    try:
        auth.conversation(c, who, row["conversation_id"])
    except Problem:
        return None
    return {"bot": row["bot"], "conversation": row["conversation_id"], "message": row["mid"],
            "excerpt": re.sub(r"\s+", " ", message["body"] or "").strip()[:120]}


def install_memory_history(app, store, auth, mutate):
    @app.post("/api/v2/bots/{bot}/memory/report")
    def report(request: Request, bot: str, body: MemoryReport):
        """The computer running a bot reports commits that changed its memory/ or knowledge/."""
        who = request.state.identity
        if who.role != "runner":
            raise Problem("forbidden", "Only the computer running a bot reports its memory history", 403)
        return mutate(request, body, lambda c: {"recorded": record(c, who, bot, body)})

    @app.get("/api/v2/bots/{bot}/memory")
    def history(request: Request, bot: str, before: str = "", limit: int = PAGE):
        """Newest first. `unseen` counts updates after the reader last opened the history."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            limit = max(1, min(limit, 50))
            rows = c.execute("SELECT * FROM bot_memory_updates WHERE bot=? AND (?='' OR committed<?) "
                             "ORDER BY committed DESC, sha LIMIT ?", (bot, before, before, limit + 1)).fetchall()
            row = c.execute("SELECT seen FROM bot_memory_seen WHERE actor=? AND bot=?", (who.actor, bot)).fetchone()
            # Before a first visit, only the last few days count as new, not years of history reported at once.
            seen, column = (row["seen"], "reported") if row else (H.shift(H.now(), days=-NEW_DAYS), "committed")
            unseen = c.execute(f"SELECT count(*) FROM bot_memory_updates WHERE bot=? AND {column}>?",
                               (bot, seen)).fetchone()[0]
            updates = [{"sha": r["sha"], "subject": r["subject"], "author": r["author"], "when": r["committed"],
                        "files": json.loads(r["files_json"]), "diff": r["diff"], "truncated": bool(r["truncated"]),
                        "shared": bool(r["shared"]), "new": r[column] > seen,
                        "source": source(c, auth, who, r["attempt_id"])} for r in rows[:limit]]
            return {"updates": updates, "unseen": unseen, "documents": documents(c, bot),
                    "next_before": rows[limit - 1]["committed"] if len(rows) > limit else None}

    @app.post("/api/v2/bots/{bot}/memory/seen")
    def seen(request: Request, bot: str, body: M.Empty):
        """The reader opened the history: later reports count as unseen."""
        who = request.state.identity
        def work(c):
            if not H.bot(c, bot):
                raise Problem("not_found", "Bot not found", 404)
            auth.require_read(c, who, bot)
            c.execute("INSERT INTO bot_memory_seen(actor,bot,seen) VALUES(?,?,?) "
                      "ON CONFLICT(actor,bot) DO UPDATE SET seen=excluded.seen", (who.actor, bot, H.now()))
            return {"ok": True}
        return mutate(request, body, work)
