# pyright: strict
"""The answers of the Health routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

# The Heartbeat (backend/heartbeat.py): one short beat of ordered steps, every 10 s by default.
INT_N = {"type": ["integer", "null"]}


SCHEMAS.update(
    {
        "Storage": obj(
            {
                "mode": {"type": "string", "enum": ["local", "s3"]},
                "bucket": "s",
                "region": "s",
                "files": "i",
                "bytes": "i",
                "copy": obj({"done": "i", "total": "i", "failed": "i"}),
            }
        ),
        "TurnCounts": obj(
            {
                "running": "i",
                "starting": "i",
                "waiting": "i",
                "queued": "i",
                "start_seconds": "i",
                "waits": items(obj({"reason": "s", "count": "i", "oldest": "s"})),
            }
        ),
        "Health": obj(
            {"audience": "s", "checks": "a", "attention": "i", "checked": "s"},
            computers=items(obj({"id": "s", "turn_counts": ref("TurnCounts"), "turn_summary": "s"}, required=["id"])),
            stalled_runs=items(
                obj(
                    {
                        "run": "s",
                        "bot": "s",
                        "stalled": "b",
                        "stalled_at": "s",
                        "last_event": {"type": ["string", "null"]},
                        "last_event_at": {"type": ["string", "null"]},
                        "stall_minutes": "i",
                    }
                )
            ),
            required=["audience", "checks", "attention", "checked"],
            storage={
                **ref("Storage"),
                "description": "Read-only file storage usage and copy progress; Team owners only",
            },
            stuck={
                "type": "array",
                "items": {"type": "object"},
                "description": "Bots with queued work that is not starting, or left in setup for over a day (the caller may "
                "read them): bot, name, queued, oldest, computer, reason (not_ready, computer_offline, no_computer, "
                "slow, limit, updating, restarting, setup, or a reason the claim holds the work: draining, maintaining, "
                "lane_held, uncertain, scoped, bot_limit, cooling_off, run_hold, budget, backoff, "
                "readiness_unknown, "
                "sign_in_rejected, not_isolated, "
                "isolation_unknown, profile, config, check_model, unreadable), why, drain_progress (counts of running "
                "and handed-back turns on update holds), since (setup only: when the "
                "bot was added), and fix (label plus href/tab, login, move or finish; null unless the caller "
                "administers bots, or for setup may manage the bot: finish is POST /api/v2/bots/{bot}/onboarded)",
            },
            on_fallback={
                "type": "array",
                "items": {"type": "object"},
                "description": "Bots whose latest run in the last day ran on something other than what they are set to "
                "(the caller may read them): bot, harness (what ran it), model (the model that did the work, or null), "
                "waiting_for_sign_in (the runtime it is set to, when the Gateway ran it because that runtime is not signed "
                "in on its computer, else null), set_to {runtime, model}, line (one plain sentence), changed_at (when the "
                "unbroken run of such turns began)",
            },
            limited={
                "type": "array",
                "items": {"type": "object"},
                "description": "Bots a usage limit holds now (the caller may read them): bot, since",
            },
        ),
        "HeartbeatStep": obj(
            {
                "key": "s",
                "name": "s",
                "kind": {"enum": ["due", "recovery", "poll"]},
                "kind_name": "s",
                "every_s": INT_N,
                "every": "s",
                "switch": "b",
                "on": "b",
                "can_change": "b",
                "last_run_at": "n",
                "last_ms": INT_N,
                "last_result": "n",
                "last_did_at": "n",
                "last_did": "n",
                "last_error": "n",
                "last_error_at": "n",
                "failing": "b",
                "runs_24h": "i",
                "did_24h": "i",
                "failed_24h": "i",
            }
        ),
        # A beat whose failures repeat the newest row's is folded into it: `repeats` beats, the last at `last_repeated_at`.
        "HeartbeatBeat": obj(
            {
                "id": "s",
                "started_at": "s",
                "total_ms": "i",
                "did": "i",
                "failed": "i",
                "repeats": "i",
                "last_repeated_at": "n",
                "steps": items(
                    obj({"key": "s", "name": "s", "text": "n", "ms": "i", "error": "n"}, required=["key", "ms"])
                ),
            }
        ),
        "HeartbeatView": obj(
            {
                "interval_s": "i",
                "interval_min": "i",
                "interval_max": "i",
                "last_beat_at": "n",
                "beats_24h": "i",
                "quiet_beats": "i",
                "quiet_since_at": "n",
                "kinds": items(obj({"key": "s", "name": "s"})),
                "steps": items(ref("HeartbeatStep")),
                "beats": items(ref("HeartbeatBeat")),
                "next": "n",
                "can_change": "b",
            }
        ),
        "SystemRoutineSaved": obj(
            {
                "job": {
                    "anyOf": [ref("HeartbeatStep"), obj({"key": "s", "name": "s"})],
                    "description": "The saved routine's row: a HeartbeatStep for ask_deadlines, "
                    "else its GET /system-routines row",
                }
            }
        ),
    }
)
