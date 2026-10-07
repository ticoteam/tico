"""Files: what a bot creates, revises or delivers, listed on its page (docs/files.md).

A file is one row per (bot, scope, canonical identity). Edits add a version (bytes are immutable,
in the private blob store) and an entry in an append-only activity log; the row's last activity
orders the list. A file inherits the visibility of the place it came from: its task, its
conversation, or, once an owner promotes it, everyone who can see the bot. Every read below asks
that question, so a private chat never leaks a name, a count, a version or a byte.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
from urllib.parse import quote, urlsplit

from fastapi import Request
from pydantic import Field

from . import models as M
from .auth import Identity
from .blobs import register
from .store import H, Problem, encode, repo_url
from . import task_privacy as privacy
from clients import bot_files as BF

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_files(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL REFERENCES bots(slug), scope TEXT NOT NULL,
 identity TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, mime TEXT NOT NULL DEFAULT '',
 locator TEXT NOT NULL, url TEXT, provider TEXT, current_version INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'ready', task_id TEXT, conversation_id TEXT,
 first_activity_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 UNIQUE(bot, scope, identity));
CREATE INDEX IF NOT EXISTS bot_files_recent ON bot_files(bot, archived, last_activity_at, id);
CREATE TABLE IF NOT EXISTS bot_file_versions(
 file_id TEXT NOT NULL REFERENCES bot_files(id), version INTEGER NOT NULL,
 blob_id TEXT NOT NULL REFERENCES blobs(id), digest TEXT NOT NULL, source_digest TEXT,
 size INTEGER NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL, commit_sha TEXT, repo_path TEXT,
 attempt_id TEXT, actor TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(file_id, version));
CREATE TABLE IF NOT EXISTS bot_file_activity(
 id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL REFERENCES bot_files(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, attempt_id TEXT, task_id TEXT, version INTEGER,
 digest TEXT, created TEXT NOT NULL, detail_json TEXT);
CREATE INDEX IF NOT EXISTS bot_file_activity_file ON bot_file_activity(file_id, id);
CREATE TRIGGER IF NOT EXISTS bot_file_activity_append_only BEFORE UPDATE ON bot_file_activity
 BEGIN SELECT RAISE(ABORT, 'bot_file_activity is append-only'); END;
CREATE TRIGGER IF NOT EXISTS bot_file_activity_no_delete BEFORE DELETE ON bot_file_activity
 BEGIN SELECT RAISE(ABORT, 'bot_file_activity is append-only'); END;
CREATE TRIGGER IF NOT EXISTS bot_file_versions_immutable BEFORE UPDATE ON bot_file_versions
 BEGIN SELECT RAISE(ABORT, 'file versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bot_file_versions_no_delete BEFORE DELETE ON bot_file_versions
 BEGIN SELECT RAISE(ABORT, 'file versions are immutable'); END;
"""

ACTIONS = ("created", "modified", "published", "promoted", "archived", "link_updated")
LIST_MAX = 100


def new_id():
    return "file-" + secrets.token_hex(12)


def is_file_id(value):
    return isinstance(value, str) and len(value) == 29 and value.startswith("file-")


def series(bot, relative):
    """The stable identity of a published path: the same report edited is the same file."""
    return "blob-series:" + hashlib.sha256((bot + "\0" + relative).encode()).hexdigest()[:20]


class FileEdit(M.Contract):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    task: str | None = Field(default=None, max_length=100)
    archived: bool | None = None
    promote: bool | None = None


class LinkBody(M.Contract):
    url: str | None = Field(default=None, max_length=2000)
    file: str | None = Field(default=None, max_length=100)
    title: str = Field(default="", max_length=300)
    task: str = Field(default="", max_length=100)
    scope: str = Field(default="", max_length=10)
    bot: str = Field(default="", max_length=100)


def refused(exc):
    return Problem("file_refused", str(exc), 422)


class Files:
    def __init__(self, app, store, auth, blobs, mutate):
        self.app, self.store, self.auth, self.blobs, self.mutate = app, store, auth, blobs, mutate

    # ------------------------------------------------------------------ who and where
    def actor_of(self, who, bot):
        return "bot:" + bot if who.role == "runner" else who.actor

    def writer_bot(self, c, who, given=""):
        """The bot a write is for: the authenticated bot, or the bot a runner ran a turn for."""
        if who.role == "bot":
            return H.actor_id(who.actor)
        if who.role == "runner":
            if not given or not H.bot(c, given):
                raise Problem("not_found", "Bot not found", 404)
            return H.actor_id(given)
        raise Problem("forbidden", "Only a bot, or the computer running it, publishes files", 403)

    def turn_context(self, c, who, bot, attempt_id):
        """(conversation id, task id) the turn ran in; a runner may only name an attempt it ran."""
        if who.role == "bot":
            attempt_id = attempt_id if who.agent else who.attempt_id
        if not attempt_id:
            return None, None
        row = c.execute("SELECT a.runner_id,m.id AS mid,m.conversation_id FROM attempts a "
                        "JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                        "WHERE a.id=? AND a.bot=?", (attempt_id, bot)).fetchone()
        if not row or (who.role == "runner" and row["runner_id"] != who.runner_id):
            raise Problem("forbidden", "That turn is not this computer's", 403)
        message, conversation = H.message(c, row["mid"]), H.conversation(c, row["conversation_id"])
        if not privacy.attempt_readable(c, "bot:" + bot, attempt_id):
            raise Problem("privacy", "This bot can no longer read the task for this turn", 403)
        return row["conversation_id"], H.message_task_id(message, conversation)

    def target(self, c, who, bot, fields):
        """(scope, task id, conversation id, attempt id) for a write. Never chosen by a parameter alone:
        a task must be one this bot may see, and bot-wide is refused from inside a chat."""
        attempt = who.attempt_id if who.role == "bot" and not who.agent else str(fields.get("attempt") or "")
        conversation, turn_task = self.turn_context(c, who, bot, attempt)
        task = str(fields.get("task") or "").strip()
        wanted = str(fields.get("scope") or "").strip()
        if wanted not in ("", "task", "bot"):
            raise Problem("validation", "scope is task or bot", 422)
        if who.role == "runner":
            task, wanted = "", ""            # what the runner publishes stays where the turn ran
        if task:
            acting = who if who.role == "bot" else Identity("bot:" + bot, "bot", runner_id=who.runner_id, attempt_id=attempt)
            self.auth.task(c, acting, task)
        if wanted == "bot":
            if ((task or turn_task) and H.task_private(c, H.task(c, task or turn_task))
                    or attempt and any(H.task_private(c, H.task(c, tid)) for tid in privacy.attempt_tasks(c, attempt))):
                raise Problem("privacy", "Private task files stay on their task", 403)
            if conversation and not turn_task and not task:
                raise Problem("forbidden", "A bot cannot make a chat's file bot-wide; an owner promotes it", 403)
            return "bot", task or turn_task, None, attempt
        task = task or turn_task
        if task:
            return "task:" + task, task, conversation, attempt
        if conversation:
            return "conversation:" + conversation, None, conversation, attempt
        raise Problem("validation", "Say which task this is for (task) or publish it bot-wide (scope=bot)", 422)

    # ------------------------------------------------------------------ writes
    def find(self, c, bot, scope, identity):
        return c.execute("SELECT * FROM bot_files WHERE bot=? AND scope=? AND identity=?",
                         (bot, scope, identity)).fetchone()

    def activity(self, c, fid, actor, action, *, attempt="", task=None, version=None, digest=None, detail=None, now=None):
        c.execute("INSERT INTO bot_file_activity(file_id,actor,action,attempt_id,task_id,version,digest,created,detail_json) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (fid, actor, action, attempt or None, task, version, digest,
                                                now or H.now(), encode(detail or {})))
        if task:
            c.execute("UPDATE tasks SET updated=? WHERE id=?", (now or H.now(), task))

    def open_row(self, c, *, bot, scope, identity, title, kind, mime, locator, url=None, provider=None,
                 task, conversation, now):
        row = self.find(c, bot, scope, identity)
        if row:
            return row, False
        fid = new_id()
        c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,mime,locator,url,provider,current_version,state,"
                  "task_id,conversation_id,first_activity_at,last_activity_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,'ready',?,?,?,?)",
                  (fid, bot, scope, identity, title[:300], kind, mime, locator, url, provider, task, conversation, now, now))
        return c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone(), True

    def add_version(self, c, *, bot, actor, scope, task, conversation, attempt, identity, title, name, mime, blob_id,
                    digest, size, source_digest=None, commit="", repo_path="", explicit_title=False):
        """One publish of bytes already in the blob store: a new file, a new version, or a no-op touch."""
        now = H.now()
        row, created = self.open_row(c, bot=bot, scope=scope, identity=identity, title=title or name,
                                     kind=BF.kind_of(name), mime=mime, locator="tico_blob", task=task,
                                     conversation=conversation, now=now)
        last = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?",
                         (row["id"], row["current_version"])).fetchone()
        changed = created or last is None or (source_digest or digest) != (last["source_digest"] or last["digest"])
        version = row["current_version"]
        if changed:
            version += 1
            c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,digest,source_digest,size,name,mime,commit_sha,repo_path,attempt_id,actor,created,media_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')",
                      (row["id"], version, blob_id, digest, source_digest, size, name, mime, commit or None,
                       repo_path or None, attempt or None, actor, now))
        action = "created" if created else "modified" if changed else "published"
        self.activity(c, row["id"], actor, action, attempt=attempt, task=task, version=version,
                      digest=source_digest or digest, now=now)
        fields = {"current_version": version, "state": "ready", "mime": mime, "kind": BF.kind_of(name)}
        if changed:
            fields["archived"] = 0                      # new work brings a removed file back
        if explicit_title and title:
            fields["title"] = title[:300]
        if task and not row["task_id"]:
            fields["task_id"] = task
        fields["last_activity_at"] = now
        c.execute("UPDATE bot_files SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                  (*fields.values(), row["id"]))
        return c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone(), created, changed

    def publish_task_deliverable(self, c, who, task_id, item, digest):
        """Compatibility hook for attachment upload paths, including streaming uploads."""
        from .views import default_bot
        task = H.task(c, task_id)
        actors = (who.actor, task["owner"], task["requester"])
        bot = next((H.actor_id(a) for a in actors if H.is_bot(a)), None) or default_bot(c, self.store.settings)
        if who.role == "bot":
            try:
                BF.check_name(item["name"])
            except BF.Refused as exc:
                raise refused(exc) from exc
        fid, _ = self.attach_task(c, who, task_id, item, digest, bot)
        return fid

    def attach_task(self, c, who, task_id, item, digest, bot):
        """Same task and display name means one series, even across authors."""
        row = c.execute("SELECT f.* FROM bot_files f JOIN bot_file_versions v "
                        "ON v.file_id=f.id AND v.version=f.current_version "
                        "WHERE f.task_id=? AND f.archived=0 AND v.name=? "
                        "ORDER BY f.last_activity_at DESC LIMIT 1", (task_id, item["name"])).fetchone()
        created = row is None
        legacy = None
        if row is None:
            legacy = c.execute("SELECT b.* FROM blobs b JOIN task_assets a ON a.blob_id=b.id "
                               "WHERE a.task_id=? AND b.name=? AND b.id<>? AND NOT EXISTS "
                               "(SELECT 1 FROM bot_file_versions v WHERE v.blob_id=b.id) "
                               "ORDER BY b.created DESC LIMIT 1", (task_id, item["name"], item["id"])).fetchone()
            fid, stamp = (legacy["id"] if legacy else new_id()), H.now()
            c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,mime,locator,task_id,"
                      "first_activity_at,last_activity_at) VALUES(?,?,?,?,?,?,?,'tico_blob',?,?,?)",
                      (fid, bot, "task:" + task_id, "task-file:" + fid, item["name"], BF.kind_of(item["name"]),
                       item["content_type"], task_id, stamp, stamp))
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            if legacy:
                self.insert_task_version(c, fid, 1, legacy["id"], legacy["digest"], legacy["size"],
                                         legacy["name"], legacy["content_type"], legacy["owner"], legacy["created"], pending=False)
        number = row["current_version"] + 1 + (1 if legacy else 0)
        self.insert_task_version(c, row["id"], number, item["id"], digest, item["size"], item["name"],
                                 item["content_type"], who.actor, H.now())
        c.execute("UPDATE bot_files SET current_version=?,mime=?,last_activity_at=? WHERE id=?",
                  (number, item["content_type"], H.now(), row["id"]))
        self.activity(c, row["id"], who.actor, "created" if created else "modified", task=task_id,
                      version=number, digest=digest)
        return row["id"], number

    @staticmethod
    def insert_task_version(c, fid, number, blob, digest, size, name, mime, actor, created, *, pending=True):
        c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created,media_state) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (fid, number, blob, digest, size, name, mime, actor, created, "pending" if pending else "none"))
        if not pending:
            from .file_metadata import FIELDS
            media = c.execute("SELECT * FROM blob_media WHERE blob_id=?", (blob,)).fetchone()
            if media:
                c.execute("UPDATE bot_file_versions SET " + ",".join(key + "=?" for key in FIELDS)
                          + " WHERE file_id=? AND version=?", (*[media[key] for key in FIELDS], fid, number))

    def task_listing(self, c, who, task_id):
        from .task_review import version_review
        self.auth.task(c, who, task_id)
        out = []
        for row in c.execute("SELECT * FROM bot_files WHERE task_id=? AND locator='tico_blob' "
                             "ORDER BY last_activity_at DESC", (task_id,)):
            if not self.visible(c, who, row, {}):
                continue
            versions = []
            for v in c.execute("SELECT * FROM bot_file_versions WHERE file_id=? ORDER BY version DESC", (row["id"],)):
                fields = dict(v)
                versions.append({"n": v["version"], "size": v["size"], "mime": v["mime"], "sha256": v["digest"],
                                 "created": v["created"], "by": v["actor"],
                                 **version_review(c, row["id"], v["version"], actor=privacy.actor(who)),
                                 **{k: fields.get(k) for k in ("width", "height", "duration_ms", "media_state")},
                                 "poster_url": f"/api/v2/files/{row['id']}/poster?v={v['version']}" if fields.get("poster_blob_id") else None,
                                 "thumb_url": f"/api/v2/files/{row['id']}/thumb?v={v['version']}" if fields.get("thumb_blob_id") else None,
                                 "url": f"/api/v2/files/{row['id']}?v={v['version']}"})
            if versions:
                name = c.execute("SELECT name FROM bot_file_versions WHERE file_id=? AND version=?",
                                 (row["id"], row["current_version"])).fetchone()[0]
                out.append({"id": row["id"], "name": name, "mime": row["mime"],
                            "current_version": row["current_version"], "archived": bool(row["archived"]), "versions": versions})
        for b in c.execute("SELECT b.*,m.width,m.height,m.duration_ms,m.media_state,m.poster_blob_id,m.thumb_blob_id "
                           "FROM blobs b JOIN task_assets a ON a.blob_id=b.id LEFT JOIN blob_media m ON m.blob_id=b.id WHERE a.task_id=? "
                           "AND NOT EXISTS(SELECT 1 FROM bot_file_versions v WHERE v.blob_id=b.id)", (task_id,)):
            if not privacy.blob_readable(c, privacy.actor(who), b["id"]):
                continue
            out.append({"id": b["id"], "name": b["name"], "mime": b["content_type"], "current_version": 1,
                        "archived": False, "versions": [{"n": 1, "size": b["size"], "mime": b["content_type"],
                        "sha256": b["digest"], "created": b["created"], "by": b["owner"],
                        **version_review(c, b["id"], 1, actor=privacy.actor(who)),
                        **{k: b[k] for k in ("width", "height", "duration_ms", "media_state")},
                        "poster_url": f"/api/v2/files/{b['id']}/poster?v=1" if b["poster_blob_id"] else None,
                        "thumb_url": f"/api/v2/files/{b['id']}/thumb?v=1" if b["thumb_blob_id"] else None,
                        "url": f"/api/v2/files/{b['id']}?v=1"}]})
        return {"files": out}

    def read_input(self, content_type, raw, query):
        if content_type.startswith("application/json"):
            try:
                fields = json.loads(raw or b"{}")
            except ValueError as exc:
                raise Problem("validation", "The body is not valid JSON", 422) from exc
            if not isinstance(fields, dict):
                raise Problem("validation", "The body is a JSON object", 422)
            text, packed = fields.pop("text", None), fields.pop("content_base64", None)
            if text is not None:
                data = str(text).encode("utf-8")
            elif packed is not None:
                try:
                    data = base64.b64decode(str(packed), validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise Problem("validation", "content_base64 is not valid base64", 422) from exc
            else:
                data = b""
            return fields, data
        return dict(query), raw

    def publish(self, request, who, fields, data, *, imported=False):
        """Bytes into the blob store, then one idempotent transaction that lists them."""
        fields = {k: (v if isinstance(v, (str, int, bool)) else "") for k, v in fields.items()}
        failed = str(fields.get("sync") or "") == "failed"
        with self.store.read() as c:
            bot = self.writer_bot(c, who, str(fields.get("bot") or ""))
        source = str(fields.get("source") or "")
        relative = str(fields.get("path") or "").replace("\\", "/").strip("/")
        if relative and (".." in relative.split("/") or relative.startswith("/")):
            raise Problem("file_refused", "That path is outside the bot's checkout", 422)
        try:
            if imported:
                s3_bucket, s3_key = BF.parse_s3(source)
                name = str(fields.get("name") or s3_key.rsplit("/", 1)[-1])
                identity, relative = f"s3:{s3_bucket}/{s3_key}", ""
            else:
                name = str(fields.get("name") or relative.rsplit("/", 1)[-1])
                identity = series(bot, relative or name)
            name, mime = BF.check_name(name)
            if not failed:
                BF.check_size(len(data))
        except BF.Refused as exc:
            raise refused(exc) from exc
        digest, blob = None, None
        if not failed:
            digest = hashlib.sha256(data).hexdigest()
            blob = self.blobs.put(data, mime)
        body = {**{k: fields.get(k, "") for k in ("bot", "path", "title", "task", "scope", "attempt", "commit", "source")},
                "name": name, "digest": digest, "identity": identity, "etag": str(fields.get("etag") or ""),
                "sync": "failed" if failed else ""}

        def work(c):
            scope, task, conversation, attempt = self.target(c, who, bot, fields)
            actor = self.actor_of(who, bot)
            if failed:
                row, created = self.open_row(c, bot=bot, scope=scope, identity=identity,
                                             title=str(fields.get("title") or name), kind=BF.kind_of(name), mime=mime,
                                             locator="tico_blob", task=task, conversation=conversation, now=H.now())
                c.execute("UPDATE bot_files SET state='not_synced' WHERE id=?", (row["id"],))
                return {"file": self.brief(c, row), "created": created, "synced": False}
            item = register(c, Identity("bot:" + bot, "bot"), digest, len(data), name, mime)
            row, created, changed = self.add_version(
                c, bot=bot, actor=actor, scope=scope, task=task, conversation=conversation, attempt=attempt,
                identity=identity, title=str(fields.get("title") or ""), name=name, mime=mime, blob_id=item["id"],
                digest=digest, size=len(data), source_digest=str(fields.get("etag") or "") or None,
                commit=str(fields.get("commit") or "")[:64] if re.fullmatch(r"[0-9a-f]{7,64}", str(fields.get("commit") or "")) else "",
                repo_path=relative, explicit_title=bool(fields.get("title")))
            H.event(c, actor, "file.publish", row["id"], {"bot": bot, "version": row["current_version"], "size": len(data)})
            return {"file": self.brief(c, row), "created": created, "changed": changed, "synced": True}
        return self.write(request, who, body, work)

    def write(self, request, who, body, work):
        result = self.store.mutate(who, request.url.path, request.headers.get("idempotency-key"), body, work)
        if isinstance(result, dict) and "_refusal" in result:
            refusal = result["_refusal"]
            raise Problem(refusal["code"], refusal["detail"], refusal["status"])
        return result

    def link(self, request, body):
        who = request.state.identity
        if who.role == "runner":
            raise Problem("forbidden", "A computer publishes the files it copies, not links", 403)

        def work(c):
            managing = who.role in ("owner", "human")
            if managing:
                bot = H.actor_id(body.bot)
                if not H.bot(c, bot) or not self.auth.visible_bot(c, who, bot):
                    raise Problem("not_found", "Bot not found", 404)
                self.require_manager(who)
                scope, task, conversation, attempt = "bot", None, None, ""
                if body.task:
                    self.auth.task(c, who, body.task)
                    task = body.task
            else:
                bot = self.writer_bot(c, who, body.bot)
                scope, task, conversation, attempt = self.target(c, who, bot, {"task": body.task, "scope": body.scope})
            actor = self.actor_of(who, bot)
            now = H.now()
            if body.file and not body.url:
                row = c.execute("SELECT * FROM bot_files WHERE id=?", (body.file,)).fetchone()
                if not row or row["bot"] != bot or row["locator"] != "remote_link" or not self.visible(c, who, row, {}):
                    raise Problem("not_found", "That link is not one of yours", 404)
                scope, task, conversation = row["scope"], row["task_id"], row["conversation_id"]
                identity, url, kind, provider = row["identity"], row["url"], row["kind"], row["provider"]
            else:
                try:
                    url, identity, kind, provider, _ = BF.normalize_link(body.url)
                except BF.Refused as exc:
                    raise refused(exc) from exc
            row, created = self.open_row(c, bot=bot, scope=scope, identity=identity,
                                         title=body.title or url, kind=kind, mime="", locator="remote_link", url=url,
                                         provider=provider, task=task, conversation=conversation, now=now)
            fields = {"last_activity_at": now, "url": url, "state": "ready"}
            if body.title:
                fields["title"] = body.title[:300]
            if task and not row["task_id"]:
                fields["task_id"] = task
            if not created:
                fields["archived"] = 0
            c.execute("UPDATE bot_files SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), row["id"]))
            self.activity(c, row["id"], actor, "created" if created else "link_updated", attempt=attempt, task=task, now=now)
            H.event(c, actor, "file.link", row["id"], {"bot": bot})
            return {"file": self.brief(c, c.execute("SELECT * FROM bot_files WHERE id=?", (row["id"],)).fetchone()),
                    "created": created}
        result = self.write(request, who, {**body.model_dump(), "actor": who.actor}, work)
        return result

    def require_manager(self, who):
        if not (who.role in ("owner", "human") and self.auth.bot_admin(who)):
            raise Problem("forbidden", "Only the owner or a bot administrator manages a bot's files", 403)

    def edit(self, request, fid, body):
        who = request.state.identity

        def work(c):
            stored = c.execute("SELECT * FROM bot_files WHERE id=? AND identity LIKE 'task-file:%'", (fid,)).fetchone()
            if stored and body.model_dump(exclude_none=True) == {"archived": True}:
                task = self.auth.task(c, who, stored["task_id"])
                if (who.actor not in (task["owner"], task["requester"])
                        and not (who.role == "owner" or who.role == "human" and H.can_move(c, who.actor))):
                    raise Problem("forbidden", "Only a task participant or someone who can move it archives its attachments", 403)
                c.execute("UPDATE bot_files SET archived=1,last_activity_at=? WHERE id=?", (H.now(), fid))
                c.execute("DELETE FROM task_assets WHERE task_id=? AND blob_id IN "
                          "(SELECT blob_id FROM bot_file_versions WHERE file_id=?)", (stored["task_id"], fid))
                self.activity(c, fid, who.actor, "archived", task=stored["task_id"])
                H.event(c, who.actor, "task.file_archived", stored["task_id"], {"file": fid})
                return {"file": {"id": fid, "archived": True}}
            if not is_file_id(fid) and not stored:
                if body.model_dump(exclude_none=True) != {"archived": True}:
                    raise Problem("validation", "Task attachments support archive only", 422)
                linked = c.execute("SELECT task_id FROM task_assets WHERE blob_id=?", (fid,)).fetchall()
                if not linked:
                    raise Problem("not_found", "File not found", 404)
                for task in linked:
                    row = self.auth.task(c, who, task["task_id"])
                    if (who.actor not in (row["owner"], row["requester"])
                            and not (who.role == "owner" or who.role == "human" and H.can_move(c, who.actor))):
                        raise Problem("forbidden", "Only a task participant or someone who can move it archives its attachments", 403)
                c.execute("DELETE FROM task_assets WHERE blob_id=?", (fid,))
                for task in linked:
                    c.execute("UPDATE tasks SET updated=? WHERE id=?", (H.now(), task["task_id"]))
                    for published in c.execute("SELECT f.id FROM bot_files f JOIN bot_file_versions v "
                                               "ON v.file_id=f.id "
                                               "WHERE v.blob_id=? AND f.scope=? AND f.archived=0",
                                               (fid, "task:" + task["task_id"])).fetchall():
                        c.execute("UPDATE bot_files SET archived=1,last_activity_at=? WHERE id=?", (H.now(), published["id"]))
                        c.execute("DELETE FROM task_assets WHERE task_id=? AND blob_id IN "
                                  "(SELECT blob_id FROM bot_file_versions WHERE file_id=?)", (task["task_id"], published["id"]))
                        self.activity(c, published["id"], who.actor, "archived", task=task["task_id"])
                    H.event(c, who.actor, "task.file_archived", task["task_id"], {"file": fid})
                return {"file": {"id": fid, "archived": True}}
            row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
            if not row or not self.visible(c, who, row, {}):
                raise Problem("not_found", "File not found", 404)
            actor, now = who.actor, H.now()
            if who.role == "bot":
                if H.actor_id(who.actor) != row["bot"]:
                    raise Problem("not_found", "File not found", 404)
                if body.archived is not None or body.promote:
                    raise Problem("forbidden", "A bot cannot remove or promote files; an owner does", 403)
            else:
                self.require_manager(who)
            fields = {}
            if body.title is not None:
                fields["title"] = body.title
            if body.task is not None:
                if body.task:
                    self.auth.task(c, who, body.task)
                fields["task_id"] = body.task or None
            if body.promote:
                if row["task_id"] and H.task_private(c, H.task(c, row["task_id"])):
                    raise Problem("privacy", "Private task files stay on their task", 403)
                if row["scope"] == "bot":
                    raise Problem("conflict", "This file is already bot-wide", 409)
                if self.find(c, row["bot"], "bot", row["identity"]):
                    raise Problem("conflict", "A bot-wide file with the same identity already exists", 409)
                fields["scope"] = "bot"
            if body.archived:
                fields["archived"] = 1
            elif body.archived is False:
                raise Problem("validation", "A removed file comes back when the bot publishes it again", 422)
            if not fields:
                return {"file": self.brief(c, row)}
            fields["last_activity_at"] = now
            c.execute("UPDATE bot_files SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), fid))
            if body.promote:
                self.activity(c, fid, actor, "promoted", task=row["task_id"], now=now, detail={"from": row["scope"]})
            elif body.archived:
                self.activity(c, fid, actor, "archived", now=now)
            else:
                self.activity(c, fid, actor, "modified", version=row["current_version"] or None, now=now,
                              detail={"edited": sorted(k for k in fields if k != "last_activity_at")})
            H.event(c, actor, "file.edit", fid, {"fields": sorted(fields)})
            return {"file": self.brief(c, c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone())}
        return self.write(request, who, {"id": fid, **body.model_dump()}, work)

    # ------------------------------------------------------------------ reads
    def visible(self, c, who, row, cache):
        """Whether `who` may know this file exists: its scope's visibility, and never a runner's."""
        if who.role not in ("owner", "human", "bot"):
            return False
        if row["task_id"] and not privacy.task_readable(c, who, H.task(c, row["task_id"])):
            return False
        # `cache` lives for one read: a run's tasks are read once, however many files it made.
        for version in c.execute("SELECT blob_id,attempt_id FROM bot_file_versions WHERE file_id=?", (row["id"],)):
            if not privacy.blob_readable(c, privacy.actor(who), version["blob_id"], memo=cache):
                return False
            if version["attempt_id"] and not privacy.attempt_readable(c, privacy.actor(who), version["attempt_id"],
                                                                      memo=cache):
                return False
        if who.role == "bot" and H.actor_id(who.actor) != row["bot"] and not row["scope"].startswith("task:"):
            return False
        key = row["scope"]
        if key not in cache:
            kind, _, ident = key.partition(":")
            try:
                if kind == "task":
                    self.auth.task(c, who, ident)
                elif kind == "conversation":
                    self.auth.conversation(c, who, ident)
                elif not self.auth.bot_access(c, who, row["bot"])["read"]:
                    raise Problem("forbidden", "private", 403)
                cache[key] = True
            except Problem:
                cache[key] = False
        return cache[key]

    def bot_row(self, c, who, bot):
        """The bot's files page: a bot the caller may see. What is listed is only what they may read:
        its own files need Read, the files of a task or chat they are part of they always see."""
        if not H.bot(c, bot):
            raise Problem("not_found", "Bot not found", 404)
        self.auth.require_see(c, who, bot)

    def brief(self, c, row):
        return {"id": row["id"], "title": row["title"], "state": row["state"], "version": row["current_version"],
                "scope": row["scope"].partition(":")[0], "locator": row["locator"]}

    def task_info(self, c, who, task_id, cache):
        if not task_id:
            return None
        if task_id not in cache:
            try:
                task = self.auth.task(c, who, task_id)
                cache[task_id] = {"title": task["title"], "status": task["status"]}
            except Problem:
                cache[task_id] = None
        return cache[task_id]

    def view(self, c, who, row, caches):
        version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=?",
                            (row["id"], row["current_version"])).fetchone()
        last = c.execute("SELECT actor,action,created FROM bot_file_activity WHERE file_id=? ORDER BY id DESC LIMIT 1",
                         (row["id"],)).fetchone()
        link = row["locator"] == "remote_link"
        synced = row["state"] == "ready" and (link or version is not None)
        provider = row["provider"] or ""
        _, label = BF.provider_of(urlsplit(row["url"] or "").hostname or "") if link else ("", "")
        task = self.task_info(c, who, row["task_id"], caches["task"])
        value = {**({k: version[k] for k in ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "media_state")} if version else {}), "id": row["id"], "bot": row["bot"], "title": row["title"], "kind": row["kind"], "mime": row["mime"],
                 "locator": row["locator"], "scope": row["scope"].partition(":")[0], "version": row["current_version"],
                 "state": row["state"], "synced": synced, "size": version["size"] if version else None,
                 "name": version["name"] if version else None,
                 "open": ({"type": "external", "url": row["url"]} if link
                          else {"type": "tico", "url": "/api/v2/files/" + row["id"] +
                                ("" if is_file_id(row["id"]) else f"?v={row['current_version']}")} if version and synced else None),
                 "provider": provider, "provider_label": label if link else "",
                 "note": f"Link opens in {label} (requires access)" if link and provider != "web"
                         else f"Link opens on {label} (requires access)" if link else "",
                 "source": "s3" if row["identity"].startswith("s3:") else "",
                 "task_id": row["task_id"] if task else None, "task_title": task["title"] if task else None,
                 "working": bool(task and task["status"] == "doing"),
                 "github_url": self.github_url(c, row["bot"], version, caches),
                 "actor": last["actor"] if last else None, "action": last["action"] if last else None,
                 "first_activity_at": row["first_activity_at"], "last_activity_at": row["last_activity_at"],
                 "archived": bool(row["archived"])}
        return value

    def github_url(self, c, bot, version, caches):
        if not version or not version["commit_sha"] or not version["repo_path"]:
            return None
        if bot not in caches["repo"]:
            row = c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
            caches["repo"][bot] = repo_url(row["repo"] if row else "", self.store.settings.github_owner)
        base = caches["repo"][bot]
        if not base.startswith("https://github.com/"):
            return None
        return f"{base.removesuffix('.git')}/blob/{version['commit_sha']}/{quote(version['repo_path'])}"

    def listing(self, who, bot, limit, cursor):
        with self.store.read() as c:
            self.bot_row(c, who, bot)
            cache, caches = {}, {"task": {}, "repo": {}}
            rows = [r for r in c.execute("SELECT * FROM bot_files WHERE bot=? AND archived=0 "
                                         "AND (identity NOT LIKE 'task-file:%' OR EXISTS (SELECT 1 FROM bot_file_versions v "
                                         "WHERE v.file_id=bot_files.id AND v.actor='bot:' || bot_files.bot)) "
                                         "ORDER BY last_activity_at DESC, id DESC", (bot,))
                    if self.visible(c, who, r, cache)]
            start = 0
            if cursor:
                try:
                    stamp, _, ident = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
                except (ValueError, binascii.Error, UnicodeError) as exc:
                    raise Problem("validation", "That cursor is not valid", 422) from exc
                start = next((i for i, r in enumerate(rows) if (r["last_activity_at"], r["id"]) < (stamp, ident)), len(rows))
            page = rows[start:start + limit]
            more = start + limit < len(rows)
            nxt = (base64.urlsafe_b64encode(f"{page[-1]['last_activity_at']}|{page[-1]['id']}".encode()).decode()
                   if more and page else None)
            return {"bot": bot, "files": [self.view(c, who, r, caches) for r in page], "total": len(rows),
                    "next_cursor": nxt, "has_more": more,
                    "can_manage": who.role in ("owner", "human") and self.auth.bot_admin(who)}

    def visible_file(self, c, who, fid):
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (fid,)).fetchone()
        if not row or not self.visible(c, who, row, {}):
            raise Problem("not_found", "File not found", 404)
        return row

    def versions(self, who, fid):
        with self.store.read() as c:
            row = self.visible_file(c, who, fid)
            caches = {"repo": {}}
            out = []
            for v in c.execute("SELECT * FROM bot_file_versions WHERE file_id=? ORDER BY version DESC", (fid,)):
                out.append({"version": v["version"], "current": v["version"] == row["current_version"], "name": v["name"],
                            "size": v["size"], "mime": v["mime"], "digest": v["source_digest"] or v["digest"],
                            "actor": v["actor"], "created": v["created"], "url": f"/api/v2/files/{fid}/versions/{v['version']}",
                            "github_url": self.github_url(c, row["bot"], v, caches),
                            **{k: v[k] for k in ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "media_state")}})
            return {"file": fid, "versions": out}

    def activity_log(self, who, fid):
        with self.store.read() as c:
            self.visible_file(c, who, fid)
            rows = [{"id": r["id"], "actor": r["actor"], "action": r["action"], "task_id": r["task_id"],
                     "version": r["version"], "digest": r["digest"], "created": r["created"]}
                    for r in c.execute("SELECT * FROM bot_file_activity WHERE file_id=? ORDER BY id DESC LIMIT 500", (fid,))]
            rows = [r for r in rows if not r["task_id"] or privacy.task_readable(c, who, H.task(c, r["task_id"]))]
            return {"file": fid, "activity": rows}

    def serve(self, who, fid, version=None, meta=False, request=None, derivative=None):
        """The bytes of one version through Tico, never a storage address."""
        with self.store.read() as c:
            row = self.visible_file(c, who, fid)
            # Adopted legacy blob IDs keep their original bytes at an unversioned URL.
            number = (row["current_version"] if is_file_id(fid) else 1) if version is None else version
            v = c.execute("SELECT v.*,b.digest AS blob_digest,b.content_type FROM bot_file_versions v "
                          "JOIN blobs b ON b.id=v.blob_id WHERE v.file_id=? AND v.version=?", (fid, number)).fetchone()
            if not v or row["locator"] != "tico_blob":
                raise Problem("not_found", "This file has no stored copy to open" if not v else "File not found", 404)
            if meta:
                return {"id": fid, "name": v["name"], "size": v["size"], "content_type": v["mime"], **{k: v[k] for k in ("width", "height", "duration_ms", "poster_blob_id", "thumb_blob_id", "media_state")}}
            if derivative:
                bid = v[derivative + "_blob_id"]
                v = c.execute("SELECT * FROM blobs WHERE id=?", (bid,)).fetchone() if bid else None
                if not v:
                    raise Problem("not_found", "File preview not found", 404)
                blob = dict(v)
            else:
                blob = {"digest": v["blob_digest"], "size": v["size"], "name": v["name"], "content_type": v["mime"]}
        from .file_delivery import serve
        return serve(request, self.blobs, blob, version is not None)


def install_files(app, store, auth, blobs, mutate):
    files = Files(app, store, auth, blobs, mutate)
    app.state.files = files
    upload_doc = {"requestBody": {"required": True, "content": {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
        "application/json": {"schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "text": {"type": "string"}, "content_base64": {"type": "string"},
            "title": {"type": "string"}, "task": {"type": "string"}, "scope": {"enum": ["task", "bot"]}}}}}}}

    @app.post("/api/v2/files/uploads", openapi_extra=upload_doc)
    async def upload(request: Request):
        """A bot (or the computer running its turn) publishes a file. Raw bytes with the fields in
        the query, or JSON with text or content_base64. Up to 25 MB raw, 2 MB as JSON."""
        fields, data = files.read_input(request.headers.get("content-type", ""), await request.body(), request.query_params)
        return await asyncio.to_thread(files.publish, request, request.state.identity, fields, data)

    @app.post("/api/v2/files/imports", openapi_extra=upload_doc)
    async def imported(request: Request):
        """Bytes the bot's computer copied from an S3 object (`hub file import`), with its `source`
        (s3://bucket/key) and `etag`. A changed etag is a new version."""
        fields, data = files.read_input(request.headers.get("content-type", ""), await request.body(), request.query_params)
        if not fields.get("source"):
            raise Problem("validation", "Say the s3:// source", 422)
        return await asyncio.to_thread(files.publish, request, request.state.identity, fields, data, imported=True)

    @app.post("/api/v2/files/links")
    def links(request: Request, body: LinkBody):
        """Register or touch an https document (Google, Notion, Figma, anything). Tico stores the
        address only: it never fetches the document or holds a provider token."""
        return files.link(request, body)

    @app.get("/api/v2/bots/{bot}/files")
    def bot_files(request: Request, bot: str, limit: int = 20, cursor: str = ""):
        return files.listing(request.state.identity, bot, max(1, min(limit, LIST_MAX)), cursor)

    @app.patch("/api/v2/files/{fid}")
    def edit(request: Request, fid: str, body: FileEdit):
        return files.edit(request, fid, body)

    @app.patch("/api/v2/files/{fid}/versions/{number}")
    def edit_version(request: Request, fid: str, number: int, body: M.FileVersionEdit):
        from .task_review import edit_version as change, version_review
        who = request.state.identity
        def current_task(c):
            row = c.execute("SELECT task_id FROM bot_files WHERE id=?", (fid,)).fetchone()
            if not row:
                row = c.execute("SELECT task_id FROM task_assets WHERE blob_id=?", (fid,)).fetchone()
            if not row or not row["task_id"]:
                raise Problem("not_found", "Task file not found", 404)
            auth.task(c, who, row["task_id"])
            return row["task_id"]
        mutate(request, body, lambda c: change(c, auth, who, current_task(c), fid, number, body), check=current_task)
        # A retry preserves the edit but reads current message provenance, including projected answers.
        with store.read() as c:
            current_task(c)
            return version_review(c, fid, number, actor=privacy.actor(who))

    @app.get("/api/v2/files/{fid}/activity")
    def activity(request: Request, fid: str):
        return files.activity_log(request.state.identity, fid)

    @app.get("/api/v2/files/{fid}/versions")
    def versions(request: Request, fid: str):
        return files.versions(request.state.identity, fid)

    @app.head("/api/v2/files/{fid}/versions/{number}", include_in_schema=False)
    @app.get("/api/v2/files/{fid}/versions/{number}")
    def version_bytes(request: Request, fid: str, number: int):
        return files.serve(request.state.identity, fid, number, request=request)
    return files
