"""Company skills at run time: what a bot's runner stages, the bundles it fetches, its staging receipt, and usage.

- `resolved_installs` is the one reading of a target's installs (latest or pinned), for the runner and for other modules.
- The runner asks `GET attempts/{aid}/skills` at a Claude turn's start, fetches each bundle once by sha
  (`GET skills/bundles/{sha256}`), stages them and reports what it staged (`POST attempts/{aid}/skills/receipt`).
- Usage keeps the skill, who, where and when only, never prompt text or tool arguments: a `Skill` tool call the runner
  reports in the attempt's events (`record_runner_usage`), and a person's `/name` to a bot in a thread
  (`record_thread_usage`). One row per (session, tool id); a repeat is ignored.

Spec: emp-tico-open-source reports/2026-11-05-company-skills/spec.md §4 Bots and §5. Tables:
backend/migrations/20261106061559_company_skills.sql.
"""
import hashlib
import json
import re

from fastapi import Request
from pydantic import Field

from . import hubdb as H
from .models import Contract
from .store import Problem

NAME = r"^[a-z0-9][a-z0-9._-]{0,79}$"
NAME_RE = re.compile(NAME)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# `/name` at the start of a person's message, followed by nothing, a space or a line end.
TYPED = re.compile(r"/([a-z0-9][a-z0-9._-]{0,79})(?=\s|$)")
TOOL_ID_MAX = 200
# Who may read an approved bundle: people, bots and the computers that run them. Not service keys.
BUNDLE_READERS = ("owner", "human", "bot", "runner")


class StagedSkill(Contract):
    name: str = Field(pattern=NAME, max_length=80)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SkillReceipt(Contract):
    """What a runner staged for one attempt (runner-rs/src/company_skills.rs)."""
    staged: list[StagedSkill] = Field(default_factory=list, max_length=200)


def bundle_sha(files):
    """The canonical bundle sha (the contract's): files sorted by path, each {"content", "path"}, compact JSON with
    sorted keys and raw UTF-8. backend/skills.py `bundle_sha` is the same algorithm."""
    ordered = sorted(({"content": f["content"], "path": f["path"]} for f in files), key=lambda f: f["path"])
    text = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def resolved_installs(c, target_type, target_id):
    """[{name, version, sha256, description}] installed for one bot or person, by name: the latest approved version, or
    the pinned one. A deleted skill, one with no approved version, or a pin to another skill's version is left out."""
    rows = c.execute(
        "SELECT s.name, v.number, v.sha256, v.description FROM skill_installs i "
        "JOIN skills s ON s.id=i.skill_id AND s.deleted_at IS NULL "
        "JOIN skill_versions v ON v.skill_id=s.id AND v.id = CASE WHEN i.version_policy='pinned' "
        "THEN i.pinned_version_id ELSE s.latest_version_id END "
        "WHERE i.target_type=? AND i.target_id=? ORDER BY s.name", (target_type, target_id)).fetchall()
    return [{"name": r[0], "version": r[1], "sha256": r[2], "description": r[3] or ""} for r in rows]


def attempt_bot(c, execution, who, aid):
    """The attempt's row, for its runner or for the bot's own run of it; anyone else is refused (403)."""
    if who.role == "runner":
        return execution.attempt(c, who, aid, active=False)
    if who.role == "bot" and who.attempt_id and who.attempt_id == aid:
        row = c.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
        if row and "bot:" + row["bot_id"] == who.actor:
            return row
    raise Problem("forbidden", "This execution attempt is not yours", 403)


def bundle(c, who, sha):
    """{name, version, sha256, files} of an approved version, checked against its sha before it is served."""
    if who.role not in BUNDLE_READERS:
        raise Problem("forbidden", "Skill bundles are for people, bots and their computers", 403)
    if not SHA_RE.fullmatch(sha or ""):
        raise Problem("not_found", "Skill bundle not found", 404)
    row = c.execute("SELECT s.name, v.number, v.sha256, v.files FROM skill_versions v "
                    "JOIN skills s ON s.id=v.skill_id AND s.deleted_at IS NULL WHERE v.sha256=? "
                    "ORDER BY v.approved_at LIMIT 1", (sha,)).fetchone()
    if not row:
        raise Problem("not_found", "Skill bundle not found", 404)
    files = json.loads(row[3])
    if bundle_sha(files) != sha:
        raise Problem("bundle_mismatch", "This skill version's files do not match its sha", 500)
    return {"name": row[0], "version": row[1], "sha256": sha, "files": files}


def keep_receipt(c, row, body):
    """Upserts what the runner staged for the attempt's bot, one row per skill. A name or sha that is no approved
    version of a directory skill is ignored."""
    now, kept = H.now(), 0
    for item in body.staged:
        skill = c.execute("SELECT s.id FROM skills s JOIN skill_versions v ON v.skill_id=s.id "
                          "WHERE s.name=? AND s.deleted_at IS NULL AND v.sha256=?", (item.name, item.sha256)).fetchone()
        if not skill:
            continue
        c.execute("INSERT INTO skill_receipts(target_type,target_id,skill_id,sha256,source,staged_at) "
                  "VALUES('Bot',?,?,?,'runner',?) ON CONFLICT(target_type,target_id,skill_id) DO UPDATE SET "
                  "sha256=excluded.sha256,source=excluded.source,staged_at=excluded.staged_at",
                  (row["bot_id"], skill[0], item.sha256, now))
        kept += 1
    return {"kept": kept}


def _skill_id(c, name):
    found = c.execute("SELECT id FROM skills WHERE name=? AND deleted_at IS NULL", (name,)).fetchone()
    return found[0] if found else None


def _staged_sha(c, bot, skill_id):
    found = c.execute("SELECT sha256 FROM skill_receipts WHERE target_type='Bot' AND target_id=? AND skill_id=?",
                      (bot, skill_id)).fetchone()
    return found[0] if found else None


def skill_calls(events):
    """(name, tool id) of each `Skill` call starting in a batch, where the runner named a valid skill."""
    calls = []
    for event in events:
        payload = event.payload if event.kind == "tool" else None
        if not isinstance(payload, dict) or payload.get("tool") != "Skill" or payload.get("status") != "started":
            continue
        name, tool_id = payload.get("skill"), payload.get("item_id")
        if (isinstance(name, str) and NAME_RE.fullmatch(name) and isinstance(tool_id, str)
                and 0 < len(tool_id) <= TOOL_ID_MAX):
            calls.append((name, tool_id))
    return calls


def record_runner_usage(c, aid, events):
    """A usage row per `Skill` call in an attempt's events whose name is a directory skill: the bot, trigger model,
    session = the attempt, the task the attempt works on, and the sha its runner staged. Others (a repository's own
    skill, a plugin's) are not counted. Returns the rows written."""
    calls = skill_calls(events)
    if not calls:
        return 0
    row = c.execute("SELECT a.bot_id, j.message_id FROM attempts a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                    (aid,)).fetchone()
    if not row:
        return 0
    bot, task_id = row[0], None
    msg = H.message(c, row[1], include_deleted=True) if row[1] else None
    if msg:
        from .execution import work_task_id
        task_id = work_task_id(msg, H.conversation(c, msg["conversation_id"]))
    written, now = 0, H.now()
    for name, tool_id in calls:
        skill_id = _skill_id(c, name)
        if not skill_id:
            continue
        written += c.execute(
            "INSERT OR IGNORE INTO skill_usages(skill_id,sha256,actor_type,actor_id,trigger,source,session_key,"
            "tool_use_id,task_id,used_at,created_at) VALUES(?,?,'Bot',?,'model','runner',?,?,?,?,?)",
            (skill_id, _staged_sha(c, bot, skill_id), bot, aid, tool_id, task_id, now, now)).rowcount
    return written


def typed_name(body):
    """The skill a message asks for when it starts with `/name`, else None."""
    found = TYPED.match(str(body or "").lstrip())
    return found[1] if found else None


def record_thread_usage(c, message, conversation, refs=None):
    """A person's message to a bot that starts with `/name`, where name is a directory skill installed for that bot: a
    use by the bot, asked for by the person (trigger person, source thread, session = the thread, tool id = the message).
    """
    sender, recipient = str(message.get("sender_id") or ""), str(message.get("recipient_id") or "")
    if not H.is_human(sender) or not H.is_bot(recipient):
        return 0
    name = typed_name(message.get("body"))
    if not name:
        return 0
    bot = H.actor_id(recipient)
    found = c.execute("SELECT s.id FROM skills s JOIN skill_installs i ON i.skill_id=s.id AND i.target_type='Bot' "
                      "AND i.target_id=? WHERE s.name=? AND s.deleted_at IS NULL", (bot, name)).fetchone()
    if not found:
        return 0
    task_id = H.message_task_id({"refs": refs or {}}, conversation)
    now = H.now()
    return c.execute(
        "INSERT OR IGNORE INTO skill_usages(skill_id,sha256,actor_type,actor_id,requested_by_id,trigger,source,"
        "session_key,tool_use_id,task_id,thread_id,used_at,created_at) "
        "VALUES(?,?,'Bot',?,?,'person','thread',?,?,?,?,?,?)",
        (found[0], _staged_sha(c, bot, found[0]), bot, H.actor_id(sender), conversation["id"], message["id"], task_id,
         conversation["id"], now, now)).rowcount


def install(app, store, execution, mutate):
    @app.get("/api/v2/attempts/{aid}/skills")
    def attempt_skills(request: Request, aid: str):
        """The company skills installed for this attempt's bot, for its runner to stage."""
        with store.read() as c:
            row = attempt_bot(c, execution, request.state.identity, aid)
            return {"skills": resolved_installs(c, "Bot", row["bot_id"])}

    @app.get("/api/v2/skills/bundles/{sha256}")
    def skill_bundle(request: Request, sha256: str):
        """An approved skill version's files, by its bundle sha."""
        with store.read() as c:
            return bundle(c, request.state.identity, sha256)

    @app.post("/api/v2/attempts/{aid}/skills/receipt")
    def attempt_skills_receipt(request: Request, aid: str, body: SkillReceipt):
        """What the runner staged for this attempt's bot: the verified half of each install."""
        return mutate(request, body, lambda c: keep_receipt(c, attempt_bot(c, execution, request.state.identity, aid),
                                                            body))
