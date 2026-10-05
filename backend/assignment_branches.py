"""Temporary, task-scoped instances of a persistent shared role.

These are separate bot actors for execution/session isolation. Their Git work stays in a local
runner tree; this module does not publish, merge, or delete that tree.
"""

import hashlib
import json
import re

from fastapi import Request

from . import hubdb
from .shared_bots import declared, source_of, check_runner, FOLLOWED
from .store import H, Problem, encode

CAPABILITY = "assignment_instances_v1"
CLEANUP_CAPABILITY = "assignment_cleanup_v1"
ACTIVE_PHASES = ("preparing", "working", "paused", "interrupted", "verifying")
CAPACITY_LIMIT = 3


def _slug(source, key, generation):
    digest = hashlib.sha256(f"{source}\0{key}\0{generation}".encode()).hexdigest()[:18]
    prefix = re.sub(r"[^a-z0-9-]+", "-", source.lower()).strip("-")[:24].strip("-") or "role"
    return f"{prefix}-work-{digest}-g{generation}"


def _assignment(row):
    if not row:
        return None
    value = dict(row)
    value["checkpoint"] = json.loads(value.pop("checkpoint_json") or "{}")
    value["receipts"] = json.loads(value.pop("receipts_json") or "{}")
    return value


def _role(c, source):
    config = declared(c, source)
    row = c.execute("SELECT * FROM bot_config WHERE bot=?", (source,)).fetchone()
    bot = H.bot(c, source)
    if not row or not bot or bot["state"] == "archived" or source_of(config):
        raise Problem("not_found", "Persistent source role not found", 404)
    if bot["state"] != "active":
        raise Problem("assignment_source", "The persistent source role must be active before it can allocate work", 409)
    if not config.get("shared"):
        raise Problem("branches_disabled", "Enable Allow branches on this role before creating temporary assignments", 409)
    return row, bot, config


def _require_manager(c, auth, who, source):
    auth.domain(who)
    if who.role in ("owner", "human"):
        auth.require_write(c, who, source)
        if not auth.bot_manager(c, who, source):
            raise Problem("forbidden", "Only a person who manages the source role can manage assignments", 403)
        return
    source_config = declared(c, source)
    parent = str(source_config.get("reports_to") or "")
    if (who.role != "bot" or parent != who.actor
            or not source_config.get("assignment_allocator_enabled")):
        raise Problem("forbidden", "Only the source role's explicitly enabled direct parent may allocate its tasks", 403)
    auth.require_write(c, who, source)


def _task(c, auth, who, source, task_id):
    task_id = auth.resolve_task(c, who, task_id)
    row = auth.task(c, who, task_id)
    if row["owner"] != "bot:" + source:
        raise Problem("task_owner", "The linked delivery task must already be owned by the persistent source role", 409)
    if row["requester"] != who.actor:
        raise Problem("task_authority", "Allocate only a task you requested; task ownership and request rights remain enforced", 403)
    if row["status"] not in ("open", "doing"):
        raise Problem("task_phase", "Only an open or active delivery task can start a temporary assignment", 409)
    return row


def _runner(c, source):
    row = c.execute("SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id "
                     "WHERE a.bot=? AND r.revoked_at IS NULL", (source,)).fetchone()
    if not row or not row["last_seen"] or row["last_seen"] <= H.shift(H.now(), seconds=-120):
        raise Problem("source_runner", "The source role must be online on its existing computer before allocating an assignment", 409)
    capabilities = set(H._json(row["capabilities_json"], []) or [])
    if CAPABILITY not in capabilities:
        raise Problem("runner_capability", "The source computer is running an older runner without isolated assignment support", 409)
    return row


def _event(c, row, actor, action, detail):
    c.execute("INSERT INTO assignment_branch_events VALUES(?,?,?,?,?,?)",
              (H.new_id(), row["id"], actor, action, encode(detail), H.now()))


def _load(c, ident):
    return c.execute("SELECT * FROM assignment_branches WHERE id=? OR bot=?", (ident, ident)).fetchone()


def _visible(c, auth, who, row):
    auth.require_read(c, who, row["source_bot"])
    try:
        task = auth.task(c, who, row["task_id"])
    except Problem:
        task = None
    cleanup = c.execute("SELECT state,requested,updated,detail,completed FROM assignment_branch_cleanup WHERE assignment_id=?",
                         (row["id"],)).fetchone()
    return {**_assignment(row), "task": ({"id": task["id"], "title": task["title"],
                                          "status": task["status"], "owner": task["owner"]} if task else None),
            "cleanup": dict(cleanup) if cleanup else None}


def _cleanup_busy(c, bot):
    attempt = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running','uncertain') LIMIT 1",
                        (bot,)).fetchone()
    job = c.execute("SELECT 1 FROM jobs WHERE bot=? AND state IN ('queued','leased','running','uncertain') LIMIT 1",
                    (bot,)).fetchone()
    return bool(attempt or job)


def request_cleanup(c, who, ident, body, auth):
    row = _load(c, ident)
    if not row:
        raise Problem("not_found", "Temporary assignment not found", 404)
    _require_manager(c, auth, who, row["source_bot"])
    if row["revision"] != body.expected_revision:
        raise Problem("version_conflict", "Assignment changed; refresh before requesting cleanup", 409)
    if row["phase"] not in ("archived", "cancelled"):
        raise Problem("assignment_cleanup_phase", "Archive or cancel the assignment before requesting separate cleanup", 409)
    if _cleanup_busy(c, row["bot"]):
        raise Problem("assignment_cleanup_busy", "An assignment still has a lease or pending reply; retain its evidence", 409)
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (row["runner_id"],)).fetchone()
    if not runner or CLEANUP_CAPABILITY not in set(H._json(runner["capabilities_json"], []) or []):
        raise Problem("assignment_cleanup_runner", "The original runner is unavailable or lacks guarded assignment cleanup support", 409)
    existing = c.execute("SELECT * FROM assignment_branch_cleanup WHERE assignment_id=?", (row["id"],)).fetchone()
    if existing and existing["state"] == "complete":
        return {"assignment_id": row["id"], **dict(existing)}
    now = H.now()
    if existing and existing["state"] == "requested":
        cleanup = dict(existing)
    else:
        c.execute("INSERT INTO assignment_branch_cleanup(assignment_id,runner_id,state,requested_by,requested,updated,detail,completed,attempt) "
                  "VALUES(?,?,'requested',?,?,?,'',NULL,1) ON CONFLICT(assignment_id) DO UPDATE SET "
                  "runner_id=excluded.runner_id,state='requested',requested_by=excluded.requested_by,"
                  "requested=excluded.requested,updated=excluded.updated,detail='',completed=NULL,"
                  "attempt=assignment_branch_cleanup.attempt+1",
                  (row["id"], row["runner_id"], who.actor, now, now))
        cleanup = dict(c.execute("SELECT * FROM assignment_branch_cleanup WHERE assignment_id=?", (row["id"],)).fetchone())
        _event(c, row, who.actor, "cleanup_requested", {"runner_id": row["runner_id"],
                                                          "archive_phase": row["phase"]})
        H.event(c, who.actor, "bot.assignment_branch_cleanup_requested", row["id"],
                {"runner_id": row["runner_id"], "phase": row["phase"]})
    return {"assignment_id": row["id"], **cleanup}


def runner_cleanups(c, who, execution):
    runner = execution.runner(c, who)
    capabilities = set(H._json(runner["capabilities_json"], []) or [])
    if CLEANUP_CAPABILITY not in capabilities:
        return {"cleanups": []}
    rows = c.execute("SELECT cl.assignment_id,cl.runner_id,cl.requested,cl.attempt,ab.source_bot,ab.bot,ab.task_id,"
                     "ab.generation,ab.revision,bc.config_json FROM assignment_branch_cleanup cl "
                     "JOIN assignment_branches ab ON ab.id=cl.assignment_id "
                     "JOIN bot_config bc ON bc.bot=ab.bot WHERE cl.runner_id=? AND cl.state='requested' "
                     "AND ab.phase IN ('archived','cancelled') ORDER BY cl.requested,cl.assignment_id LIMIT 3",
                     (who.runner_id,)).fetchall()
    output = []
    for row in rows:
        if _cleanup_busy(c, row["bot"]):
            continue
        config = H._json(row["config_json"], {}) or {}
        proof_config = {key: config[key] for key in (
            "assignment_branch", "assignment_id", "assignment_task_id", "shared_from", "generation", "repo", "repo_url"
        ) if key in config}
        output.append({"id": row["assignment_id"], "assignment_id": row["assignment_id"],
                       "runner_id": row["runner_id"], "requested": row["requested"],
                       "attempt": row["attempt"],
                       "source_bot": row["source_bot"], "bot": row["bot"], "task_id": row["task_id"],
                       "generation": row["generation"], "revision": row["revision"], "config": proof_config})
    return {"cleanups": output}


def runner_cleanup_result(c, who, ident, body, execution):
    runner = execution.runner(c, who)
    row = c.execute("SELECT cl.*,ab.source_bot,ab.bot,ab.phase FROM assignment_branch_cleanup cl "
                     "JOIN assignment_branches ab ON ab.id=cl.assignment_id WHERE cl.assignment_id=?",
                     (ident,)).fetchone()
    if not row or row["runner_id"] != who.runner_id or row["phase"] not in ("archived", "cancelled"):
        raise Problem("not_found", "Cleanup request not found for this runner", 404)
    if CLEANUP_CAPABILITY not in set(H._json(runner["capabilities_json"], []) or []):
        raise Problem("runner_capability", "This runner did not advertise guarded assignment cleanup support", 409)
    if body.attempt != row["attempt"]:
        raise Problem("assignment_cleanup_attempt", "This cleanup reply belongs to an older request; use the current cleanup attempt", 409)
    if row["state"] == "complete":
        return {"assignment_id": ident, "state": "complete", "detail": row["detail"]}
    if row["state"] != "requested":
        raise Problem("assignment_cleanup_state", "This cleanup request is no longer pending; request it again after resolving the blocker", 409)
    if body.result == "complete":
        if _cleanup_busy(c, row["bot"]):
            raise Problem("assignment_cleanup_busy", "A lease or pending reply appeared; retain the remaining evidence", 409)
        state, detail, completed = "complete", "Local assignment trees and registration were removed after runner verification", H.now()
    else:
        if not body.detail.strip():
            raise Problem("assignment_cleanup_detail", "A blocked cleanup must explain what evidence was retained", 422)
        state, detail, completed = "blocked", body.detail.strip()[:2000], None
    now = H.now()
    c.execute("UPDATE assignment_branch_cleanup SET state=?,updated=?,detail=?,completed=? WHERE assignment_id=? AND state='requested'",
              (state, now, detail, completed, ident))
    _event(c, {"id": ident}, who.actor, "cleanup_" + state, {"detail": detail, "runner_id": who.runner_id})
    H.event(c, who.actor, "bot.assignment_branch_cleanup_" + state, ident,
            {"runner_id": who.runner_id, "detail": detail})
    return {"assignment_id": ident, "state": state, "detail": detail, "completed": completed}


def set_policy(c, who, source, body, auth):
    row, bot, config = _role(c, source)
    if who.role not in ("owner", "human"):
        raise Problem("forbidden", "Only a human manager can enable assignment allocation", 403)
    if not auth.bot_manager(c, who, source):
        raise Problem("forbidden", "Only a person who manages this role can configure assignment allocation", 403)
    if row["revision"] != body.expected_revision:
        raise Problem("version_conflict", "The role changed; refresh before saving", 409)
    parent = str(config.get("reports_to") or "")
    if body.enabled and not parent.startswith("bot:"):
        raise Problem("assignment_allocator", "This role has no direct parent bot to authorize as allocator; a human manager may still allocate its tasks", 409)
    config["assignment_allocator_enabled"] = bool(body.enabled and parent.startswith("bot:"))
    updated = row["revision"] + 1
    c.execute("UPDATE bot_config SET config_json=?,revision=?,definition_updated=?,definition_updated_by=? WHERE bot=?",
              (encode(config), updated, H.now(), who.actor, source))
    H.event(c, who.actor, "bot.assignment_allocator_policy", source,
            {"enabled": config["assignment_allocator_enabled"], "allocator": parent if parent.startswith("bot:") else ""})
    return {"source": source, "enabled": config["assignment_allocator_enabled"],
            "allocator": parent if config["assignment_allocator_enabled"] else "", "revision": updated}


def create(c, who, source, body, auth, settings_admin, execution):
    source_row, source_bot, source_config = _role(c, source)
    _require_manager(c, auth, who, source)
    task_id = auth.resolve_task(c, who, body.task_id)
    prior = c.execute("SELECT * FROM assignment_branches WHERE source_bot=? AND assignment_key=? AND generation=?",
                      (source, body.assignment_key, body.generation)).fetchone()
    if prior:
        if prior["allocator"] != who.actor or prior["task_id"] != task_id:
            raise Problem("assignment_conflict", "That assignment generation is already bound to another task or allocator", 409)
        return _visible(c, auth, who, prior)
    task = _task(c, auth, who, source, task_id)
    runner = _runner(c, source)
    request_hash = hashlib.sha256(encode({"source": source, "task": task["id"], "runner": runner["id"]}).encode()).hexdigest()
    previous = c.execute("SELECT * FROM assignment_branches WHERE source_bot=? AND assignment_key=? "
                         "ORDER BY generation DESC LIMIT 1", (source, body.assignment_key)).fetchone()
    expected_generation = (previous["generation"] + 1) if previous else 1
    if body.generation != expected_generation:
        raise Problem("assignment_generation", f"Use generation {expected_generation}; generations are never reused", 409)
    if previous and previous["phase"] not in ("archived", "cancelled"):
        raise Problem("assignment_active", "Retire the prior generation before reopening this assignment", 409)
    if previous and previous["task_id"] != task["id"]:
        raise Problem("assignment_identity", "A retired assignment key remains bound to its original task", 409)
    count = c.execute("SELECT count(*) FROM assignment_branches WHERE phase NOT IN ('waiting_review','waiting_release','archived','cancelled')").fetchone()[0]
    if count >= CAPACITY_LIMIT:
        raise Problem("assignment_capacity", "All three temporary implementation slots are active; move one to review or release", 409)
    slug = _slug(source, body.assignment_key, body.generation)
    if c.execute("SELECT 1 FROM bots WHERE slug=?", (slug,)).fetchone():
        raise Problem("assignment_identity", "The generated actor already exists; its identity will not be reused", 409)

    ident, now = H.new_id(), H.now()
    c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created) VALUES(?,?,?,?,?,'','keeper','active',?)",
              (slug, body.display_name, source_bot["runtime"], source_bot["model"], source_bot["effort"], now))
    original_repo = source_row["repo"] or ("emp-" + source)
    config = {"name": slug, "display_name": body.display_name,
              "description": f"Temporary assignment for {task['title']}", "reports_to": "bot:" + source,
              "status": "active", "repo": original_repo,
              "repo_url": source_config.get("repo_url", ""), "host": "keeper", "tasks": "hub",
              "thread_mode": "task", "shared_from": source, "assignment_branch": True,
              "assignment_id": ident, "assignment_key": body.assignment_key, "generation": body.generation,
              "assignment_task_id": task["id"], "model_managed_by": "cloud",
              "repo_access_mode": "chosen"}
    # Follow the persistent role's execution behavior, while forcing task-only session state.
    config.update({key: source_config[key] for key in FOLLOWED if key in source_config and key != "session"})
    # The actor accepts messages only in its single linked delivery task. Do not inherit an
    # open source-role inbox and accidentally turn a scoped assignment into another intake path.
    config["bot_contact"] = "tasks"
    team = settings_admin._team(c, slug, config)
    source_access = c.execute("SELECT access_json,bot_owners_json,owner_ids_json,created_by FROM bot_config WHERE bot=?", (source,)).fetchone()
    access_json = source_access["access_json"] if source_access else None
    owners_json = source_access["bot_owners_json"] if source_access else None
    owner_ids_json = source_access["owner_ids_json"] if source_access else None
    created_by = source_access["created_by"] if source_access and source_access["created_by"] else who.actor
    c.execute("INSERT INTO bot_config(bot,config_json,team,operator,owner_ids_json,description,reports_to,repo,thread_mode,"
              "definition_updated,definition_updated_by,access_json,bot_owners_json,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (slug, encode(config), team, source_row["operator"], owner_ids_json, config["description"],
               config["reports_to"], original_repo, "task", now, who.actor, access_json, owners_json, created_by))
    c.execute("INSERT INTO assignment_branches(id,source_bot,assignment_key,generation,bot,task_id,allocator,runner_id,"
              "display_name,phase,revision,request_hash,created,updated) VALUES(?,?,?,?,?,?,?,?,?,'preparing',1,?,?,?)",
              (ident, source, body.assignment_key, body.generation, slug, task["id"], who.actor, runner["id"],
               body.display_name, request_hash, now, now))
    check_runner(c, slug, runner["id"], source)
    c.execute("INSERT INTO assignments VALUES(?,?,1,?,?)", (slug, runner["id"], now, who.actor))
    H.event(c, who.actor, "bot.assignment_branch_created", ident,
            {"source": source, "task": task["id"], "generation": body.generation, "runner": runner["id"]})
    _event(c, {"id": ident}, who.actor, "preparing", {"task": task["id"], "runner": runner["id"]})
    # Existing task rights are authoritative: task_update rejects an allocator who is not the
    # requester/owner or otherwise entitled to move this exact task.
    hubdb.task_update(c, who.actor, task["id"], owner="bot:" + slug,
                      note=f"Allocated to temporary assignment {body.display_name} ({ident})")
    assigned_task = H.task(c, task["id"])
    hubdb._wake(c, assigned_task, "bot:" + slug,
                f"Temporary assignment {body.display_name} is active for this task. Work only on the linked task; keep its code and notes in this assignment's local branch.",
                refs={"assignment_branch": ident})
    current = _load(c, ident)
    return _visible(c, auth, who, current)


def list_for(c, who, source, auth):
    auth.require_read(c, who, source)
    rows = c.execute("SELECT * FROM assignment_branches WHERE source_bot=? ORDER BY created DESC", (source,)).fetchall()
    return {"source": source, "enabled": bool(declared(c, source).get("shared")),
            "allocator_enabled": bool(declared(c, source).get("assignment_allocator_enabled")),
            "capacity": CAPACITY_LIMIT,
            "active": c.execute("SELECT count(*) FROM assignment_branches WHERE phase NOT IN ('waiting_review','waiting_release','archived','cancelled')").fetchone()[0],
            "assignments": [_visible(c, auth, who, row) for row in rows]}


def update(c, who, ident, body, auth):
    row = _load(c, ident)
    if not row:
        raise Problem("not_found", "Temporary assignment not found", 404)
    _require_manager(c, auth, who, row["source_bot"])
    if row["revision"] != body.expected_revision:
        raise Problem("version_conflict", "Assignment changed; refresh before saving", 409)
    reviewed_learning = body.reviewed_learning_note.strip()
    if body.confirm_learning_review and not reviewed_learning:
        raise Problem("learning_note", "Give the reviewed reusable lesson before confirming it", 422)
    if reviewed_learning and not body.confirm_learning_review:
        raise Problem("learning_review", "A human manager must confirm the lesson is generalized and contains no task/customer details or credentials", 409)
    if reviewed_learning and who.role not in ("owner", "human"):
        raise Problem("learning_reviewer", "Only a human manager can approve a reusable lesson for the persistent role", 403)
    task = H.task(c, row["task_id"])
    if not task:
        raise Problem("task_missing", "The linked task no longer exists", 409)
    phase = body.phase or row["phase"]
    handoff_task = None
    if body.handoff_task_id.strip():
        if phase != "cancelled":
            raise Problem("assignment_handoff", "A handoff task can be recorded only when cancelling this assignment", 409)
        handoff_id = auth.resolve_task(c, who, body.handoff_task_id.strip())
        handoff_task = auth.task(c, who, handoff_id)
        if (handoff_task["id"] == task["id"] or handoff_task["owner"] != "bot:" + row["source_bot"]
                or handoff_task["requester"] != who.actor or handoff_task["status"] not in ("open", "doing")):
            raise Problem("assignment_handoff", "Handoff must name a separate open task already assigned to the source role and requested by you", 409)
    allowed = {
        "preparing": {"paused", "cancelled"},
        "working": {"waiting_review", "paused", "interrupted", "cancelled"},
        "waiting_review": {"working", "waiting_release", "cancelled"},
        "waiting_release": {"verifying", "cancelled"},
        "paused": {"working", "cancelled"},
        "interrupted": {"working", "cancelled"},
        "verifying": {"archived", "cancelled"},
    }
    if phase != row["phase"] and phase not in allowed.get(row["phase"], set()):
        raise Problem("assignment_transition", f"Cannot move an assignment from {row['phase']} to {phase}", 409)
    saved_checkpoint = json.loads(row["checkpoint_json"] or "{}")
    checkpoint = body.checkpoint if body.checkpoint else saved_checkpoint
    if phase in ("waiting_review", "waiting_release", "interrupted") and not checkpoint:
        raise Problem("checkpoint_required", f"Record a safe checkpoint before moving an assignment to {phase}", 409)
    if phase == "paused" and row["phase"] == "working" and not checkpoint:
        raise Problem("checkpoint_required", "Record a safe checkpoint before pausing active work", 409)
    if body.phase == "working" and row["phase"] == "interrupted" and not body.checkpoint:
        raise Problem("checkpoint_required", "Resume an interrupted assignment from an explicit checkpoint", 409)
    # A lost heartbeat or an expired lease is not proof that the old process stopped. Keep
    # unsettled attempts blocking release/archive until the normal attempt reconciliation settles them.
    running = c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running','uncertain') LIMIT 1",
                        (row["bot"],)).fetchone()
    if phase in ("paused", "interrupted", "waiting_review", "waiting_release", "verifying", "archived", "cancelled") and running:
        raise Problem("assignment_busy", "Wait for the current run to settle before changing this assignment phase", 409)
    if phase == "waiting_review" and task["status"] != "review":
        raise Problem("task_phase", "Move the linked task to review before releasing this execution slot", 409)
    if phase == "waiting_release" and task["status"] not in ("ready", "done"):
        raise Problem("task_phase", "The linked task must be ready or done before waiting for release", 409)
    if phase == "verifying" and task["status"] not in ("ready", "done", "closed"):
        raise Problem("task_phase", "The linked task must be ready, done or closed before verification", 409)
    if phase == "archived":
        receipts = {"deployed_version": body.deployed_version.strip(),
                    "acceptance": body.acceptance_receipt.strip(),
                    "learning": body.learning_receipt.strip(), "evidence": body.evidence_receipt.strip()}
        if task["status"] not in ("done", "closed") or not all(receipts.values()):
            raise Problem("assignment_receipts", "Archive requires a completed task, deployed version, acceptance, reviewed learning and preserved evidence receipts", 409)
        reviewed = c.execute("SELECT 1 FROM assignment_branch_events WHERE assignment_id=? "
                             "AND action='learning_reviewed' LIMIT 1", (row["id"],)).fetchone()
        if not reviewed:
            raise Problem("assignment_learning_receipt", "Archive requires a human-reviewed reusable lesson recorded in assignment history", 409)
        pending = c.execute("SELECT 1 FROM jobs WHERE bot=? AND state IN ('queued','leased','running','uncertain') LIMIT 1",
                            (row["bot"],)).fetchone()
        if pending:
            raise Problem("assignment_pending", "Resolve queued or uncertain replies before archiving", 409)
    if phase == "cancelled":
        if not body.note.strip():
            raise Problem("cancellation_note", "Cancellation requires a recorded reason", 422)
        unsettled = c.execute("SELECT 1 FROM jobs WHERE bot=? AND state IN ('leased','running','uncertain') LIMIT 1",
                              (row["bot"],)).fetchone()
        if unsettled:
            raise Problem("assignment_pending", "Resolve running or uncertain replies before cancelling", 409)
        if task["owner"] == "bot:" + row["bot"]:
            hubdb.task_update(c, who.actor, task["id"], owner="bot:" + row["source_bot"],
                              note="Temporary assignment cancelled: " + body.note[:1000])
            hubdb._wake(c, H.task(c, task["id"]), "bot:" + row["source_bot"],
                        ("The temporary assignment was cancelled and the delivery task returned to your queue."
                         + (f" Follow-up handoff: {handoff_task['title']} ({handoff_task['id']})." if handoff_task else "")),
                        refs={"assignment_branch": row["id"], "cancelled": True,
                              **({"handoff_task_id": handoff_task["id"]} if handoff_task else {})})
        c.execute("UPDATE jobs SET state='cancelled' WHERE bot=? AND state='queued'", (row["bot"],))
    learning_message_id = ""
    if reviewed_learning:
        digest = hashlib.sha256(reviewed_learning.encode("utf-8")).hexdigest()
        previous_note = None
        for event in c.execute("SELECT detail_json FROM assignment_branch_events WHERE assignment_id=? AND action='learning_reviewed'",
                               (row["id"],)).fetchall():
            try:
                detail = json.loads(event["detail_json"] or "{}")
            except (TypeError, ValueError):
                detail = {}
            if detail.get("digest") == digest:
                previous_note = detail.get("message_id")
                break
        if previous_note:
            learning_message_id = str(previous_note)
        else:
            message = hubdb.say(
                c, who.actor, "bot:" + row["source_bot"],
                "Human-reviewed reusable engineering lesson for the persistent role's learning trunk. "
                "Use only the generalized lesson below; do not open the assignment task or copy its code, "
                "transcript, customer details, attachments or credentials. Add a useful general lesson to "
                "the role's appropriate knowledge file, commit in the source learning checkout, and use "
                "the checkout's explicit upstream with normal shared sync. Never force-push or reset; if "
                "the trunk moved, preserve both commits and report the conflict.\n\nReviewed lesson:\n" + reviewed_learning,
                kind="notice", refs={"assignment_learning_reviewed": row["id"]})
            learning_message_id = message["id"]
            _event(c, row, who.actor, "learning_reviewed", {
                "message_id": learning_message_id, "digest": digest,
                "source_bot": row["source_bot"], "trunk": "source-role-explicit-upstream"})
            H.event(c, who.actor, "bot.assignment_learning_reviewed", row["id"], {
                "message_id": learning_message_id, "digest": digest, "source_bot": row["source_bot"]})
    receipts = json.loads(row["receipts_json"] or "{}")
    if body.phase == "archived":
        receipts.update({"deployed_version": body.deployed_version.strip(),
                         "acceptance": body.acceptance_receipt.strip(),
                         "learning": body.learning_receipt.strip(), "evidence": body.evidence_receipt.strip()})
    display = body.display_name or row["display_name"]
    archived_at = H.now() if phase in ("archived", "cancelled") else row["archived_at"]
    c.execute("UPDATE assignment_branches SET display_name=?,phase=?,revision=revision+1,checkpoint_json=?,receipts_json=?,updated=?,archived_at=? WHERE id=?",
              (display, phase, encode(checkpoint), encode(receipts), H.now(), archived_at, row["id"]))
    if phase in ("archived", "cancelled"):
        bot_config = declared(c, row["bot"])
        bot_config["status"] = "archived"
        c.execute("UPDATE bots SET state='archived',display_name=? WHERE slug=?", (display, row["bot"]))
        c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?", (encode(bot_config), row["bot"]))
    else:
        c.execute("UPDATE bots SET display_name=? WHERE slug=?", (display, row["bot"]))
        bot_config = declared(c, row["bot"])
        bot_config["display_name"] = display
        c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?", (encode(bot_config), row["bot"]))
    _event(c, row, who.actor, phase, {"from": row["phase"], "note": body.note, "checkpoint": checkpoint,
                                    "receipts": receipts, "display_name": display,
                                    **({"handoff_task_id": handoff_task["id"]} if handoff_task else {})})
    if phase == "working" and row["phase"] != "working":
        hubdb._wake(c, H.task(c, row["task_id"]), "bot:" + row["bot"],
                    f"Resume temporary assignment {display} from its recorded checkpoint. Work only on its linked task.",
                    refs={"assignment_branch": row["id"], "resume": True})
    H.event(c, who.actor, "bot.assignment_branch_updated", row["id"], {"phase": phase, "revision": row["revision"] + 1})
    result = _visible(c, auth, who, _load(c, row["id"]))
    if learning_message_id:
        result["learning_message_id"] = learning_message_id
    return result


def install(app, store, auth, mutate, settings_admin, execution):
    from .models import (AssignmentBranchCleanupRequest, AssignmentBranchCleanupResult,
                         AssignmentBranchCreate, AssignmentBranchPolicy, AssignmentBranchUpdate)

    @app.put("/api/v2/bots/{source}/assignment-branches/policy")
    def policy(request: Request, source: str, body: AssignmentBranchPolicy):
        who = request.state.identity
        return mutate(request, body, lambda c: set_policy(c, who, source, body, auth))

    @app.get("/api/v2/bots/{source}/assignment-branches")
    def list_assignments(request: Request, source: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return list_for(c, who, source, auth)

    @app.post("/api/v2/bots/{source}/assignment-branches")
    def create_assignment(request: Request, source: str, body: AssignmentBranchCreate):
        who = request.state.identity
        return mutate(request, body, lambda c: create(c, who, source, body, auth, settings_admin, execution))

    @app.patch("/api/v2/assignment-branches/{ident}")
    def update_assignment(request: Request, ident: str, body: AssignmentBranchUpdate):
        who = request.state.identity
        return mutate(request, body, lambda c: update(c, who, ident, body, auth))

    @app.post("/api/v2/assignment-branches/{ident}/cleanup")
    def request_assignment_cleanup(request: Request, ident: str, body: AssignmentBranchCleanupRequest):
        who = request.state.identity
        return mutate(request, body, lambda c: request_cleanup(c, who, ident, body, auth))

    @app.get("/api/v2/runners/assignment-cleanups")
    def assigned_cleanups(request: Request):
        who = request.state.identity
        with store.read() as c:
            return runner_cleanups(c, who, execution)

    @app.post("/api/v2/runners/assignment-cleanups/{ident}")
    def report_assignment_cleanup(request: Request, ident: str, body: AssignmentBranchCleanupResult):
        who = request.state.identity
        return mutate(request, body, lambda c: runner_cleanup_result(c, who, ident, body, execution))

    @app.get("/api/v2/assignment-branches/{ident}/events")
    def events(request: Request, ident: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            row = _load(c, ident)
            if not row:
                raise Problem("not_found", "Temporary assignment not found", 404)
            auth.require_read(c, who, row["source_bot"])
            return {"events": [dict(event) for event in c.execute(
                "SELECT actor,action,detail_json,created FROM assignment_branch_events WHERE assignment_id=? ORDER BY created,id",
                (row["id"],))]}
