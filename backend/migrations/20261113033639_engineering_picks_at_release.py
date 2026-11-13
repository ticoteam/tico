"""Engineering at release (spec "A stage picks its owner from several", AC-12; backend/stage_pick.py).

In the Engineering pipeline (id or preset `engineering`), once:

- On deck and Build pick from the first two Builders (template `builder`): Take turns, Anyone, and the pick becomes
  the task's owner. Review 1 and Review 2 pick from the first two Reviewers (template `reviewer`): Take turns, New to
  the task, and the pick holds only the review. Bots are ordered as backend/pipeline_seats._bots orders them: those in a
  seat of the template first, by seat title, then any other bot from it, by slug.
- A stage is left alone when it has fewer than two such bots, or when its owner is already "One of several" (someone
  set it). Fixes 1 and Fixes 2 are never touched.
- Each open task with no owner in On deck or Build, where this run set the stage, is picked once: its open visit gets
  `pick_state` 'full', so the never-stuck pass gives it to a bot (task_engine.pick_again), oldest first. Every other
  task is untouched.
- The pipeline's settings version goes up by one when it is above 1, as a newer preset text does
  (pipeline_settings._follow): an untouched preset stays at 1, so a role bot that appears later is still filled in.
  The change is logged as a `pipeline.save` event.

Running it again changes nothing: every stage it sets is then "One of several".
"""

import json
import uuid
from datetime import datetime, timezone

BUILD_STAGES = ("engineering-on-deck", "engineering-build")
REVIEW_STAGES = ("engineering-review-1", "engineering-review-2")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _bots(conn, template):
    """pipeline_seats._bots: the template's bots in seat order, then any other bot made from it, by slug."""
    seated = []
    if "template" in _columns(conn, "seats"):
        seated = conn.execute(
            "SELECT s.filled_by FROM seats s JOIN bots b ON b.id = s.filled_by "
            "WHERE s.template=? AND s.filled_by <> '' AND COALESCE(b.state, 'active') <> 'archived' "
            "ORDER BY s.title, s.created_at, s.id",
            (template,),
        ).fetchall()
    rest = []
    if "template" in _columns(conn, "bot_settings"):
        rest = conn.execute(
            "SELECT b.id FROM bots b JOIN bot_settings s ON s.bot_id = b.id "
            "WHERE s.template=? AND COALESCE(b.state, 'active') <> 'archived' ORDER BY b.slug",
            (template,),
        ).fetchall()
    return list(dict.fromkeys(str(row[0]) for row in [*seated, *rest]))


def _pick(bots, prefer, owns_task):
    """`stages.pick` as stage_pick.stored writes it."""
    return json.dumps(
        {
            "bots": [f"bot:{bot}" for bot in bots[:2]],
            "how": "turns",
            "limit_each": None,
            "prefer": prefer,
            "owns_task": owns_task,
        },
        sort_keys=True,
    )


def up(conn):
    if "pick" not in _columns(conn, "stages") or "pick_state" not in _columns(conn, "visits"):
        return
    if "state" not in _columns(conn, "bots"):
        return
    found = conn.execute(
        "SELECT id, settings_version FROM pipelines WHERE retired_at IS NULL AND (id='engineering' "
        "OR preset='engineering') ORDER BY id='engineering' DESC LIMIT 1"
    ).fetchone()
    if not found:
        return
    pipeline_id, version = str(found[0]), int(found[1] or 1)
    builders, reviewers = _bots(conn, "builder"), _bots(conn, "reviewer")
    wanted = {}
    if len(builders) >= 2:
        wanted.update(dict.fromkeys(BUILD_STAGES, _pick(builders, "anyone", True)))
    if len(reviewers) >= 2:
        wanted.update(dict.fromkeys(REVIEW_STAGES, _pick(reviewers, "new", False)))
    ts = _now()
    changes = {}
    for stage_id, pick in wanted.items():
        row = conn.execute(
            "SELECT owner, pick FROM stages WHERE id=? AND pipeline_id=? AND kind='work' AND retired_at IS NULL",
            (stage_id, pipeline_id),
        ).fetchone()
        if not row or row[0] == "pick":
            continue
        conn.execute("UPDATE stages SET owner='pick', pick=?, updated_at=? WHERE id=?", (pick, ts, stage_id))
        changes[stage_id] = {"owner": [row[0], "pick"], "pick": [row[1], json.loads(pick)]}
    if not changes:
        return
    bump = 1 if version > 1 else 0
    conn.execute(
        "UPDATE pipelines SET settings_version = settings_version + ?, updated_at=? WHERE id=?",
        (bump, ts, pipeline_id),
    )
    picked = [s for s in BUILD_STAGES if s in changes]
    marked = 0
    if picked:
        marks = ",".join("?" * len(picked))
        marked = conn.execute(
            "UPDATE visits SET pick_state='full', pick_why=NULL WHERE left_at IS NULL AND pick_state IS NULL "
            f"AND stage_id IN ({marks}) AND task_id IN (SELECT id FROM tasks WHERE status='open' "
            f"AND (owner_id IS NULL OR owner_id='') AND stage_id IN ({marks}))",
            (*picked, *picked),
        ).rowcount
    if "detail" in _columns(conn, "events"):
        conn.execute(
            "INSERT INTO events (id, created_at, actor, action, target, detail) VALUES (?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                ts,
                "keeper",
                "pipeline.save",
                pipeline_id,
                json.dumps(
                    {
                        "version": version + bump,
                        "made": False,
                        "why": "Engineering at release: One of several",
                        "stages": changes,
                        "tasks_to_pick": marked,
                    }
                ),
            ),
        )
