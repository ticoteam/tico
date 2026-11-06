"""The company skills directory (Library › Skills): proposals, review and approval, and installs (docs/skills.md; spec
reports/2026-11-05-company-skills/spec.md in emp-tico-open-source).

A skill is an immutable, versioned bundle identified by its sha256 (clients/skills.py has the rules and the sha). Anyone who
is a person or a bot proposes a new skill or an update; an admin approves it. Approval is one transaction against the exact
bundle that was screened and the version it was based on: if either moved, it is refused until the bundle is screened again
(`not_screened`, `stale`). An install is explicit: a row per bot or person, latest or pinned, and the runner or the skills
helper reports the sha it actually staged (`skill_receipts`), so the page can show both what was asked and what is there.

Screening itself (track 3) fills `skill_screenings`; the runtime that stages and serves bundles (backend/skill_runtime.py)
reads `resolved_installs` from here. Events name ids and the skill's name only: never a file's contents.
"""
import json
import uuid

from fastapi import Query, Request

from clients import botcopy
from clients import skills as bundles
from . import brand
from . import hubdb as H
from . import models as M
from .store import Problem

PEOPLE_AND_BOTS = ("owner", "human", "bot")     # a person's own rights, or a bot's; never a runner, a service key or a device
REVIEWED = ("pending", "approved", "declined", "withdrawn")
DIFF_CAP = 90_000
LIST_CAP = 500

# The bundle rules and the sha are the clients' (clients/skills.py), so the command and the server cannot disagree.
bundle_sha = bundles.bundle_sha


def validate_bundle(name, files):
    """The bundle, checked: {files (sorted), description, sha256}. Refuses with the reason (422), never truncates."""
    problem = bundles.bundle_problem(name, files)
    if problem:
        raise Problem("skill_bundle", problem, 422)
    normalized = bundles.normalize(files)
    return {"files": normalized, "description": bundles.description_of(normalized), "sha256": bundles.bundle_sha(normalized)}


def _id(prefix):
    return prefix + uuid.uuid4().hex[:24]


def _row(c, sql, args=()):
    found = c.execute(sql, args).fetchone()
    return dict(found) if found else None


def _json(text, default):
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return default
    return default if value is None else value


def _actor(c, who):
    """(type, id) of whoever is acting: a bot by its row, a person by the id in their actor."""
    if who.role == "bot" and H.is_bot(who.actor):
        bot = H.resolve_bot(c, H.actor_id(who.actor))
        if bot:
            return "Bot", bot["id"]
    return "Human", H.actor_id(who.actor)


def _admin(auth, who):
    """The owner or an admin, on their own click (backend/brand.py): never a bot, never someone acting for them."""
    return brand.admin(auth, who)


def _skill(c, name):
    return _row(c, "SELECT * FROM skills WHERE name=?", (name,))


def _skill_or_404(c, name):
    skill = None if name in bundles.RESERVED else _skill(c, name)
    if not skill:
        raise Problem("not_found", f"No skill named {name}", 404)
    return skill


def _version(c, skill_id, number):
    return _row(c, "SELECT * FROM skill_versions WHERE skill_id=? AND number=?", (skill_id, number))


def _version_by_id(c, version_id):
    return _row(c, "SELECT * FROM skill_versions WHERE id=?", (version_id,)) if version_id else None


def _version_view(row, with_files=False):
    value = {"number": row["number"], "sha256": row["sha256"], "description": row["description"],
             "approved_at": row["approved_at"], "approved_by_id": row["approved_by_id"]}
    if with_files:
        value["files"] = _json(row["files"], [])
    return value


_PROPOSAL_SQL = ("SELECT p.*, bv.number AS base_number, v.number AS version_number FROM skill_proposals p "
                 "LEFT JOIN skill_versions bv ON bv.id = p.base_version_id LEFT JOIN skill_versions v ON v.id = p.version_id")


def _proposal(c, pid):
    row = _row(c, _PROPOSAL_SQL + " WHERE p.id=?", (pid,))
    if not row:
        raise Problem("not_found", "No such skill proposal", 404)
    return row


def _proposal_view(row):
    return {"id": row["id"], "name": row["name"], "status": row["status"], "screen_status": row["screen_status"],
            "sha256": row["sha256"], "description": row["description"], "note": row["note"],
            "source": _json(row["source"], {}), "base_version": row["base_number"], "version": row["version_number"],
            "proposed_by": {"type": row["proposed_by_type"], "id": row["proposed_by_id"]},
            "task_id": row["task_id"], "decided_by_id": row["decided_by_id"], "decided_at": row["decided_at"],
            "decline_reason": row["decline_reason"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _mine(c, who, row):
    typ, ident = _actor(c, who)
    return row["proposed_by_type"] == typ and row["proposed_by_id"] == ident


def _latest_screening(c, pid):
    return _row(c, "SELECT * FROM skill_screenings WHERE proposal_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (pid,))


def _screening_view(row):
    return {"id": row["id"], "sha256": row["sha256"], "base_version_id": row["base_version_id"],
            "question_set": row["question_set"], "status": row["status"], "reason": row["reason"],
            "verdict": row["verdict"], "risk": row["risk"], "summary": row["summary"],
            "findings": _json(row["findings"], []), "patterns": _json(row["patterns"], []), "created_at": row["created_at"]}


def _diff(before, after):
    """The unified diff per file between two bundles, capped (botcopy.unified per file)."""
    old = {item["path"]: item["content"].encode("utf-8") for item in before}
    new = {item["path"]: item["content"].encode("utf-8") for item in after}
    parts = [botcopy.unified(rel, old.get(rel), new.get(rel), cut=20_000) for rel in sorted(set(old) | set(new)) if old.get(rel) != new.get(rel)]
    return "".join(parts)[:DIFF_CAP]


_INSTALL_SQL = (
    "SELECT i.id, i.skill_id, i.target_type, i.target_id, i.version_policy, i.created_at, i.updated_at, "
    "s.name AS skill_name, lv.number AS latest_number, lv.sha256 AS latest_sha256, "
    "pv.number AS pinned_number, pv.sha256 AS pinned_sha256, "
    "r.sha256 AS staged_sha256, r.staged_at AS staged_at, r.source AS staged_source, "
    "b.display_name AS bot_name, hu.name AS human_name "
    "FROM skill_installs i JOIN skills s ON s.id = i.skill_id "
    "LEFT JOIN skill_versions lv ON lv.id = s.latest_version_id "
    "LEFT JOIN skill_versions pv ON pv.id = i.pinned_version_id "
    "LEFT JOIN skill_receipts r ON r.target_type = i.target_type AND r.target_id = i.target_id AND r.skill_id = i.skill_id "
    "LEFT JOIN bots b ON i.target_type = 'Bot' AND b.id = i.target_id "
    "LEFT JOIN humans hu ON i.target_type = 'Human' AND hu.id = i.target_id")


def _installs(c, where="1=1", args=()):
    return [dict(row) for row in c.execute(_INSTALL_SQL + " WHERE " + where + " ORDER BY s.name, i.created_at", args).fetchall()]


def _install_view(row):
    pinned = row["version_policy"] == "pinned"
    number, sha = (row["pinned_number"], row["pinned_sha256"]) if pinned else (row["latest_number"], row["latest_sha256"])
    return {"id": row["id"], "name": row["skill_name"], "version_policy": row["version_policy"],
            "pinned_version": row["pinned_number"] if pinned else None,
            "target": {"type": row["target_type"], "id": row["target_id"], "name": row["bot_name"] or row["human_name"]},
            "desired": {"number": number, "sha256": sha} if sha else None,
            "staged": {"sha256": row["staged_sha256"], "staged_at": row["staged_at"], "source": row["staged_source"]}
            if row["staged_sha256"] else None,
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _may_see(c, auth, who, target_type, target_id):
    """Whether the caller may see an install for this target: a person's own, an admin's all, a bot's when they see the bot."""
    if target_type == "Human":
        return target_id == H.actor_id(who.actor) or _admin(auth, who)
    return _admin(auth, who) or bool(auth.bot_access(c, who, target_id)["see"])


def _target(c, auth, who, target_type, target_id):
    """The target's id, when the caller may install for it: a bot needs write on it; a person, that person or an admin."""
    if target_type == "Bot":
        bot = H.resolve_bot(c, target_id)
        if not bot:
            raise Problem("not_found", "No such bot", 404)
        auth.require_write(c, who, bot["id"])
        return bot["id"]
    human = H.human(c, target_id)
    if not human:
        raise Problem("not_found", "No such person", 404)
    if human["id"] != H.actor_id(who.actor) and not _admin(auth, who):
        raise Problem("forbidden", "Only that person or an admin installs a skill for them", 403)
    return human["id"]


def _pin(c, skill, policy, number):
    if policy == "latest":
        if number is not None:
            raise Problem("validation", "Name a pinned_version only with version_policy pinned", 422)
        return None
    if number is None:
        raise Problem("validation", "A pinned install names its pinned_version", 422)
    version = _version(c, skill["id"], number)
    if not version:
        raise Problem("not_found", f"{skill['name']} has no version {number}", 404)
    return version["id"]


def _install(c, auth, who, skill, target_type, target_id, policy, number):
    tid = _target(c, auth, who, target_type, target_id)
    pinned = _pin(c, skill, policy, number)
    typ, ident = _actor(c, who)
    now = H.now()
    row = _row(c, "SELECT * FROM skill_installs WHERE skill_id=? AND target_type=? AND target_id=?", (skill["id"], target_type, tid))
    if row:
        c.execute("UPDATE skill_installs SET version_policy=?, pinned_version_id=?, installed_by_type=?, installed_by_id=?, updated_at=? "
                  "WHERE id=?", (policy, pinned, typ, ident, now, row["id"]))
        install_id = row["id"]
    else:
        install_id = _id("ski_")
        c.execute("INSERT INTO skill_installs (id, skill_id, target_type, target_id, version_policy, pinned_version_id, installed_by_type, "
                  "installed_by_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (install_id, skill["id"], target_type, tid, policy, pinned, typ, ident, now, now))
    H.event(c, who.actor, "skill.installed", install_id, {"name": skill["name"], "target_type": target_type, "target_id": tid,
                                                         "version_policy": policy})
    return install_id


def _install_row(c, install_id):
    return _installs(c, "i.id=?", (install_id,))[0]


def propose(c, auth, who, body):
    """A new skill, or an update of one from `base_version` (or its latest). Replaces this proposer's pending proposal for the name."""
    files = [{"path": item.path, "content": item.content} for item in body.files]
    checked = validate_bundle(body.name, files)
    skill = _skill(c, body.name)
    base = None
    if body.base_version is not None:
        if not skill:
            raise Problem("validation", "A new skill has no version to base it on", 422)
        base = _version(c, skill["id"], body.base_version)
        if not base:
            raise Problem("not_found", f"{body.name} has no version {body.base_version}", 404)
    elif skill:
        base = _version_by_id(c, skill["latest_version_id"])
    if skill and c.execute("SELECT 1 FROM skill_versions WHERE skill_id=? AND sha256=?", (skill["id"], checked["sha256"])).fetchone():
        raise Problem("unchanged", f"{body.name} already has this exact version", 409)
    if body.task_id and not H.task(c, body.task_id):
        raise Problem("not_found", "No such task", 404)
    source = body.source.model_dump(exclude_none=True) if body.source else {}
    kind = "bot" if H.is_bot(who.actor) else "person"
    if source.get("url"):
        kind = "url"
    source = {"kind": kind, **source}
    typ, ident = _actor(c, who)
    now = H.now()
    c.execute("UPDATE skill_proposals SET status='withdrawn', updated_at=? WHERE name=? AND status='pending' AND proposed_by_type=? "
              "AND proposed_by_id=?", (now, body.name, typ, ident))
    pid = _id("skp_")
    c.execute("INSERT INTO skill_proposals (id, name, skill_id, base_version_id, sha256, files, description, source, note, status, "
              "proposed_by_type, proposed_by_id, task_id, screen_status, created_at, updated_at) "
              "VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,'pending',?,?)",
              (pid, body.name, skill["id"] if skill else None, base["id"] if base else None, checked["sha256"],
               json.dumps(checked["files"], ensure_ascii=False), checked["description"], json.dumps(source, ensure_ascii=False),
               body.note, typ, ident, body.task_id, now, now))
    H.event(c, who.actor, "skill.proposed", pid, {"name": body.name, "sha256": checked["sha256"], "update": bool(skill),
                                                 "base_version": base["number"] if base else None})
    return {"proposal": _proposal_view(_proposal(c, pid))}


def approve(c, auth, who, pid, acknowledged):
    """Admins only, one transaction: the screened bundle must be this proposal's exact bundle, based on the skill's current version."""
    if not _admin(auth, who):
        raise Problem("forbidden", "Only an owner or an admin approves a skill", 403)
    row = _proposal(c, pid)
    if row["status"] != "pending":
        raise Problem("not_pending", f"This proposal is already {row['status']}", 409)
    skill = _skill(c, row["name"])
    current = skill["latest_version_id"] if skill else None
    screen = _latest_screening(c, pid)
    if (row["screen_status"] != "screened" or not screen or screen["status"] != "screened" or screen["sha256"] != row["sha256"]
            or screen["base_version_id"] != row["base_version_id"]):
        raise Problem("not_screened", "This exact version has not passed screening yet", 409)
    if row["base_version_id"] != current or screen["base_version_id"] != current:
        raise Problem("stale", "The skill has a newer version since this was screened: screen it again", 409)
    if (screen["risk"] == "high" or screen["verdict"] == "unsafe") and not acknowledged:
        raise Problem("acknowledgement_required", "Screening rated this high risk or unsafe: say you have read it to approve", 422)
    if skill and c.execute("SELECT 1 FROM skill_versions WHERE skill_id=? AND sha256=?", (skill["id"], row["sha256"])).fetchone():
        raise Problem("unchanged", f"{row['name']} already has this exact version", 409)
    now, approver = H.now(), H.actor_id(who.actor)
    if skill:
        skill_id = skill["id"]
        number = c.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM skill_versions WHERE skill_id=?", (skill_id,)).fetchone()[0]
        c.execute("UPDATE skills SET description=?, updated_at=? WHERE id=?", (row["description"], now, skill_id))
    else:
        skill_id, number = _id("sk_"), 1
        c.execute("INSERT INTO skills (id, name, description, latest_version_id, created_by_type, created_by_id, deleted_at, created_at, "
                  "updated_at) VALUES (?,?,?,NULL,?,?,NULL,?,?)",
                  (skill_id, row["name"], row["description"], row["proposed_by_type"], row["proposed_by_id"], now, now))
    version_id = _id("skv_")
    c.execute("INSERT INTO skill_versions (id, skill_id, number, sha256, files, description, proposal_id, approved_by_id, approved_at, "
              "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (version_id, skill_id, number, row["sha256"], row["files"], row["description"], pid, approver, now, now))
    c.execute("UPDATE skills SET latest_version_id=?, updated_at=? WHERE id=?", (version_id, now, skill_id))
    c.execute("UPDATE skill_proposals SET status='approved', version_id=?, decided_by_id=?, decided_at=?, updated_at=? WHERE id=?",
              (version_id, approver, now, now, pid))
    H.event(c, who.actor, "skill.approved", pid, {"name": row["name"], "version": number, "sha256": row["sha256"]})
    return {"proposal": _proposal_view(_proposal(c, pid)),
            "version": {"number": number, "sha256": row["sha256"], "approved_at": now}}


def decline(c, auth, who, pid, reason):
    if not _admin(auth, who):
        raise Problem("forbidden", "Only an owner or an admin declines a skill", 403)
    row = _proposal(c, pid)
    if row["status"] != "pending":
        raise Problem("not_pending", f"This proposal is already {row['status']}", 409)
    now = H.now()
    c.execute("UPDATE skill_proposals SET status='declined', decline_reason=?, decided_by_id=?, decided_at=?, updated_at=? WHERE id=?",
              (reason, H.actor_id(who.actor), now, now, pid))
    H.event(c, who.actor, "skill.declined", pid, {"name": row["name"]})
    return {"proposal": _proposal_view(_proposal(c, pid))}


def withdraw(c, auth, who, pid):
    row = _proposal(c, pid)
    if not _mine(c, who, row):
        raise Problem("forbidden", "Only the one who proposed it withdraws it", 403)
    if row["status"] != "pending":
        raise Problem("not_pending", f"This proposal is already {row['status']}", 409)
    c.execute("UPDATE skill_proposals SET status='withdrawn', updated_at=? WHERE id=?", (H.now(), pid))
    return {"proposal": _proposal_view(_proposal(c, pid))}


def rescreen(c, auth, who, pid):
    row = _proposal(c, pid)
    if not (_admin(auth, who) or _mine(c, who, row)):
        raise Problem("forbidden", "Only an admin or the one who proposed it screens it again", 403)
    if row["status"] != "pending":
        raise Problem("not_pending", f"This proposal is already {row['status']}", 409)
    c.execute("UPDATE skill_proposals SET screen_status='pending', updated_at=? WHERE id=?", (H.now(), pid))
    return {"proposal": _proposal_view(_proposal(c, pid))}


def install(app, store, auth, mutate):
    def reader(request):
        who = request.state.identity
        auth.domain(who)
        if who.role not in PEOPLE_AND_BOTS:
            raise Problem("forbidden", "Skills are read by people and bots", 403)
        return who

    # Fixed paths first: `skills/proposals`, `skills/installs` must not be read as a skill's name.
    @app.post("/api/v2/skills/proposals", status_code=201)
    def skill_propose(request: Request, body: M.SkillPropose):
        """Propose a skill or an update: the whole folder as text files. A bot or a person may; an admin approves. The bundle is
        checked here (the same rules as `hub skill submit`), and screening follows before anyone can approve it."""
        who = reader(request)
        return mutate(request, body, lambda c: propose(c, auth, who, body))

    @app.get("/api/v2/skills/proposals")
    def skill_proposals(request: Request, status: str | None = Query(default=None)):
        """Admins see every proposal; anyone else sees their own. Newest first, at most 200."""
        who = reader(request)
        if status is not None and status not in REVIEWED:
            raise Problem("validation", "status is pending, approved, declined or withdrawn", 422)
        with store.read() as c:
            clauses, args = [], []
            if status:
                clauses.append("p.status=?")
                args.append(status)
            if not _admin(auth, who):
                typ, ident = _actor(c, who)
                clauses.append("p.proposed_by_type=? AND p.proposed_by_id=?")
                args.extend([typ, ident])
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = c.execute(_PROPOSAL_SQL + where + " ORDER BY p.created_at DESC LIMIT 200", args).fetchall()
            return {"proposals": [_proposal_view(dict(row)) for row in rows]}

    @app.get("/api/v2/skills/proposals/{pid}")
    def skill_proposal(request: Request, pid: str):
        """A proposal with its files, its diff against the version it was based on, whether the skill has moved on since (then
        `diff_latest` too), and its latest screening. Its proposer and admins only."""
        who = reader(request)
        with store.read() as c:
            row = _proposal(c, pid)
            if not (_admin(auth, who) or _mine(c, who, row)):
                raise Problem("forbidden", "Only an admin or the one who proposed it reads it", 403)
            proposal = dict(row)
            files = _json(row["files"], [])
            base = _version_by_id(c, row["base_version_id"])
            skill = _skill(c, row["name"])
            latest = _version_by_id(c, skill["latest_version_id"]) if skill else None
            stale = (skill["latest_version_id"] if skill else None) != row["base_version_id"]
            value = {"proposal": _proposal_view(proposal), "files": files,
                     "diff": _diff(_json(base["files"], []) if base else [], files),
                     "stale": stale, "screening": None}
            if stale:
                value["diff_latest"] = _diff(_json(latest["files"], []) if latest else [], files)
            screen = _latest_screening(c, pid)
            if screen:
                value["screening"] = _screening_view(screen)
            return value

    @app.post("/api/v2/skills/proposals/{pid}/approve")
    def skill_approve(request: Request, pid: str, body: M.SkillApprove):
        """An admin approves a screened proposal (409 not_screened or stale otherwise): the next version of the skill."""
        who = reader(request)
        return mutate(request, body, lambda c: approve(c, auth, who, pid, body.acknowledged))

    @app.post("/api/v2/skills/proposals/{pid}/decline")
    def skill_decline(request: Request, pid: str, body: M.SkillDecline):
        who = reader(request)
        return mutate(request, body, lambda c: decline(c, auth, who, pid, body.reason))

    @app.post("/api/v2/skills/proposals/{pid}/withdraw")
    def skill_withdraw(request: Request, pid: str, body: M.Empty):
        who = reader(request)
        return mutate(request, body, lambda c: withdraw(c, auth, who, pid))

    @app.post("/api/v2/skills/proposals/{pid}/rescreen")
    def skill_rescreen(request: Request, pid: str, body: M.Empty):
        """Screen the proposal again (an admin or its proposer): approval waits for the new screen."""
        who = reader(request)
        return mutate(request, body, lambda c: rescreen(c, auth, who, pid))

    @app.get("/api/v2/skills/installs")
    def skill_installs(request: Request, target_type: str = Query(...), target_id: str = Query(...)):
        """The installs of one bot or person, with what is wanted and what is staged there."""
        who = reader(request)
        if target_type not in ("Bot", "Human"):
            raise Problem("validation", "target_type is Bot or Human", 422)
        with store.read() as c:
            if target_type == "Bot":
                bot = H.resolve_bot(c, target_id)
                if not bot:
                    raise Problem("not_found", "No such bot", 404)
                target_id = bot["id"]
                auth.require_see(c, who, target_id)
            elif not _may_see(c, auth, who, "Human", target_id):
                raise Problem("forbidden", "Only that person or an admin sees their installs", 403)
            return [_install_view(row) for row in _installs(c, "i.target_type=? AND i.target_id=?", (target_type, target_id))]

    @app.delete("/api/v2/skills/installs/{iid}")
    def skill_uninstall(request: Request, iid: str):
        """Take a skill off a bot or person: the same rights as installing it."""
        who = reader(request)

        def work(c):
            row = _row(c, "SELECT * FROM skill_installs WHERE id=?", (iid,))
            if not row:
                raise Problem("not_found", "No such install", 404)
            _target(c, auth, who, row["target_type"], row["target_id"])
            c.execute("DELETE FROM skill_installs WHERE id=?", (iid,))
            H.event(c, who.actor, "skill.uninstalled", iid, {"target_type": row["target_type"], "target_id": row["target_id"]})
            return {"id": iid, "deleted": True}
        return mutate(request, M.Empty(), work)

    @app.get("/api/v2/skills")
    def skills_list(request: Request):
        """The directory: each skill with its latest approved version and how many installs it has."""
        reader(request)
        with store.read() as c:
            rows = c.execute(
                "SELECT s.id, s.name, s.description, v.number, v.sha256, v.approved_at, "
                "(SELECT COUNT(*) FROM skill_installs i WHERE i.skill_id = s.id) AS installs_count "
                "FROM skills s LEFT JOIN skill_versions v ON v.id = s.latest_version_id "
                "WHERE s.deleted_at IS NULL ORDER BY s.name LIMIT ?", (LIST_CAP,)).fetchall()
            return {"skills": [{"id": r["id"], "name": r["name"], "description": r["description"],
                                "latest": {"number": r["number"], "sha256": r["sha256"], "approved_at": r["approved_at"]}
                                if r["number"] is not None else None,
                                "installs_count": r["installs_count"]} for r in rows]}

    @app.get("/api/v2/skills/{name}")
    def skill_detail(request: Request, name: str):
        """One skill: its versions (no files) and the installs the caller may see."""
        who = reader(request)
        with store.read() as c:
            skill = _skill_or_404(c, name)
            versions = c.execute("SELECT * FROM skill_versions WHERE skill_id=? ORDER BY number DESC", (skill["id"],)).fetchall()
            latest = _version_by_id(c, skill["latest_version_id"])
            installs = [_install_view(row) for row in _installs(c, "i.skill_id=?", (skill["id"],))
                        if _may_see(c, auth, who, row["target_type"], row["target_id"])]
            return {"skill": {"id": skill["id"], "name": skill["name"], "description": skill["description"],
                              "latest": _version_view(latest) if latest else None},
                    "versions": [_version_view(row) for row in versions], "installs": installs}

    @app.get("/api/v2/skills/{name}/versions/{number}")
    def skill_version(request: Request, name: str, number: int):
        """One approved version, with its files."""
        reader(request)
        with store.read() as c:
            skill = _skill_or_404(c, name)
            version = _version(c, skill["id"], number)
            if not version:
                raise Problem("not_found", f"{name} has no version {number}", 404)
            return {"skill": name, "version": _version_view(version, with_files=True)}

    @app.post("/api/v2/skills/{name}/installs")
    def skill_install(request: Request, name: str, body: M.SkillInstall):
        """Install a skill for one bot or person, latest or pinned to a version. Explicit only: nothing reaches later joiners."""
        who = reader(request)

        def work(c):
            return _install_view(_install_row(c, _install(c, auth, who, _skill_or_404(c, name), body.target_type, body.target_id,
                                                          body.version_policy, body.pinned_version)))
        return mutate(request, body, work)

    @app.post("/api/v2/skills/{name}/installs/bulk")
    def skill_install_bulk(request: Request, name: str, body: M.SkillInstallBulk):
        """Install for several targets at once, all or nothing: each target is checked before any row is written."""
        who = reader(request)

        def work(c):
            skill = _skill_or_404(c, name)
            ids = [_install(c, auth, who, skill, t.target_type, t.target_id, body.version_policy, body.pinned_version)
                   for t in body.targets]
            return {"installs": [_install_view(_install_row(c, install_id)) for install_id in dict.fromkeys(ids)]}
        return mutate(request, body, work)
