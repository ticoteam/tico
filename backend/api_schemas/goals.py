# pyright: strict
"""The answers of the Goals routes (backend/openapi_v2.py gathers them)."""

from .. import needs_shape
from ..api_contract import ref
from ._base import SCHEMAS, items, merge, obj

SCHEMAS.update(
    {
        # Goals and KPIs
        "Goal": obj(
            {
                "id": "s",
                "title": "s",
                "owner_id": "s",
                "parent_id": "n",
                "body": "s",
                "status": {
                    "type": ["string", "null"],
                    "description": "red, yellow, green, gray (no data), done, dropped; null while it has no colour yet",
                },
                "status_note": "s",
                "status_by_id": "n",
                "status_at": "n",
                "status_source": {
                    "type": ["string", "null"],
                    "description": "auto: worked out by the Strategy Owner; person: set by hand and kept until handed back",
                },
                "suggest_status": {
                    "type": ["string", "null"],
                    "description": "On a colour set by hand: the colour the Strategy Owner would choose, when it differs",
                },
                "suggest_note": "n",
                "suggest_at": "n",
                "rank": {"type": ["integer", "null"]},
                "created_at": "s",
                "created_by_id": "s",
                "updated_at": "s",
            },
            required=[
                "id",
                "title",
                "owner_id",
                "parent_id",
                "body",
                "status",
                "status_note",
                "status_by_id",
                "status_at",
                "status_source",
                "rank",
                "created_at",
                "created_by_id",
                "updated_at",
            ],
            owner_name={"type": "string"},
            status_by_name={"type": "string"},
            why={
                "type": "string",
                "description": "One line tying it to the company's vision; empty: not tied to the vision",
            },
            why_by_id={"type": ["string", "null"]},
            why_at={"type": ["string", "null"]},
        ),
        "GoalCheckinView": obj(
            {
                "id": "s",
                "goal_id": "s",
                "kpi_id": "n",
                "created_at": "s",
                "author": "s",
                "sender_id": "s",
                "body": "s",
                "signal": {"type": ["string", "null"], "enum": ["on_track", "at_risk", "off_track", None]},
            },
            required=["id", "goal_id", "created_at", "author", "sender_id", "body", "signal"],
            author_name={"type": "string"},
            sender_name={"type": "string"},
        ),
        "GoalProposalView": obj(
            {
                "id": "s",
                "kind": {"enum": ["goal_wording", "goal_kpi", "kpi_definition", "kpi_target", "flag", "goal_why"]},
                "goal_id": "n",
                "kpi_id": "n",
                "payload": "o",
                "reason": "s",
                "proposed_by": "s",
                "proposed_at": "s",
                "status": {"enum": ["pending", "confirmed", "rejected"]},
                "decided_by": "n",
                "decided_at": "n",
                "decision_note": "s",
                "result": {"type": ["object", "null"]},
            },
            required=["id", "kind", "goal_id", "kpi_id", "payload", "reason", "proposed_by", "proposed_at", "status"],
            goal_title={"type": ["string", "null"]},
            kpi_name={"type": ["string", "null"]},
            card={
                "type": "object",
                "description": "What its card says beside the payload: goal_title, kpi_name; "
                "an Add a KPI suggestion's measure {name, definition, source_note, unit, direction, existing} "
                "and target_label; a new target's now_label and target_label; a new definition's or wording's "
                "changes [{label, now, proposed}]",
            },
            may_decide={"type": "boolean", "description": "Whether the caller may confirm or reject it"},
            may_reason={
                "type": "boolean",
                "description": "Whether the caller may still add the reason it "
                "was turned down: they turned it down, and it has none",
            },
        ),
        "GoalView": obj(
            {},
            required=["kpis", "children", "chain", "tasks", "events", "checkins", "proposals"],
            kpis=items(ref("Kpi")),
            children=items(ref("Goal")),
            chain=items(ref("Goal")),
            tasks=items({"type": "object"}),
            events=items({"type": "object"}),
            checkins=items(ref("GoalCheckinView")),
            proposals=items(ref("GoalProposalView")),
        ),
        "GoalResult": obj({"goal": {}}),
        "CompanyResult": obj(
            {
                "company": obj(
                    {"vision": "s", "description": "s", "updated_at": "n", "updated_by": "n", "updated_by_name": "s"}
                ),
                "may_edit": {"type": "boolean", "description": "The owner on their own click, never an admin or a bot"},
            }
        ),
        "CompanyHistory": obj(
            {
                "events": items(
                    obj(
                        {
                            "id": "s",
                            "created_at": "s",
                            "actor": "s",
                            "actor_name": "s",
                            "field": {"enum": ["vision", "description"]},
                            "old": "s",
                            "new": "s",
                        }
                    )
                )
            }
        ),
        "GoalList": obj(
            {"goals": items(ref("Goal"))},
            required=["goals"],
            owner={"type": "string"},
            chain=items(ref("Goal")),
            reports=items(ref("Goal")),
            company=items(ref("Goal")),
        ),
        "GoalTreeRow": obj(
            {},
            required=["kpis", "open_tasks"],
            kpis=items(ref("Kpi")),
            open_tasks={"type": "integer"},
            checkin={"oneOf": [ref("GoalCheckinView"), {"type": "null"}], "description": "The latest check-in"},
        ),
        "GoalTree": obj(
            {
                "goals": {},
                "owners": {
                    "type": "object",
                    "description": "{actor: {kind: company|person|bot, id, name}} for every owner",
                },
                "unaligned": {"type": "object", "description": "{actor: open tasks that serve no goal}"},
                "other_kpis": items(ref("Kpi")),
                "proposals": items(ref("GoalProposalView")),
            },
            required=["goals", "owners", "unaligned", "other_kpis", "proposals"],
        ),
        "StrategyBotView": obj(
            {
                "name": "s",
                "regular_checks": {
                    "type": "boolean",
                    "description": "Whether the KPI pass and the automatic goal colours run",
                },
                "level": {"type": "integer", "enum": [1, 2, 3]},
                "level_chosen": {
                    "type": "boolean",
                    "description": "Whether the owner chose the level; if not, it is the default",
                },
                "levels": items(
                    obj(
                        {"level": "i", "name": "s", "sentence": "s", "ready": "b"},
                        required=["level", "name", "sentence", "ready"],
                    )
                ),
                "level_changed_by": "s",
                "level_changed_by_name": "s",
                "level_changed_at": "s",
                "level_history": items(
                    obj(
                        {"level": "i", "was": "i", "by": "s", "by_name": "s", "at": "s"},
                        required=["level", "was", "by", "at"],
                    )
                ),
                "asked_today": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "{person actor: items the Strategy Owner put in their Needs you today (UTC)}",
                },
                "level3_offer": ref("StrategyBotLevel3Offer"),
            },
            required=["name", "regular_checks", "level", "levels"],
        ),
        "StrategyBotLogEntry": obj(
            {
                "id": "s",
                "created_at": "s",
                "kind": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "cost",
                        "publish",
                        "send",
                        "bots",
                        "target",
                        "vision",
                        "instructions",
                        "person_task",
                    ],
                },
                "reason": "s",
                "goal_id": "s",
                "state": {"type": "string", "enum": ["done", "proposed", "undone"]},
                "proposal_task": {"type": "string", "description": "The owner's Needs you item, when filed"},
                "note": "s",
                "done": items({"type": "object", "additionalProperties": True}),
                "left": items({"type": "object", "additionalProperties": True}),
                "undoable": "b",
                "undone_at": "s",
                "undone_by": "s",
                "undo": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "What Undo restored and what it kept: {restored, kept, text}",
                },
            },
            required=["id", "created_at", "kind", "state", "done", "left", "undoable"],
        ),
        "StrategyBotLog": obj(
            {"items": items(ref("StrategyBotLogEntry")), "next_before": "s", "can_undo": "b"},
            required=["items", "can_undo"],
        ),
        "StrategyBotLevel3Offer": obj(
            {"streak": "i", "needed": "i", "due": "b", "last_offered_at": "s"}, required=["streak", "needed", "due"]
        ),
        "StrategyBotLevel3OfferResult": obj({"level3_offer": ref("StrategyBotLevel3Offer")}),
        "StrategyBotMergeResult": obj(
            {
                "task": {
                    "type": "object",
                    "description": "The merged item: the Strategy Owner's task, waiting on the person",
                },
                "originals": items(
                    obj(
                        {"task_id": "s", "title": "s", "asker": "s", "asker_name": "s", "state": "s", "note": "s"},
                        required=["task_id", "asker", "state"],
                    )
                ),
            }
        ),
        "GoalsNeedsYou": obj(
            {
                "actor": "s",
                "items": items(
                    {
                        "type": "object",
                        "required": ["id", "kind", "title", "body", "subject", "created_at", "actions"],
                        "additionalProperties": True,
                        "properties": {
                            "id": {"type": "string", "description": "<kind>:<what it is about>"},
                            "title": {"type": "string"},
                            "body": {"type": ["string", "null"]},
                            "subject": {
                                "oneOf": [
                                    obj({"type": {"enum": ["proposal", "kpi", "strategy"]}, "id": "s"}),
                                    {"type": "null"},
                                ],
                                "description": "The one thing it is about; null for an ask standing for several things",
                            },
                            "created_at": {
                                "type": ["number", "null"],
                                "description": "When its task was filed; null while it is not filed yet",
                            },
                            "actions": items({"enum": list(needs_shape.ACTIONS)})
                            | {
                                "description": "The writes that settle it: "
                                + "; ".join(f"`{k}` {v}" for k, v in needs_shape.ACTIONS.items())
                            },
                            "kind": {"enum": ["vision", "kpi_red", "recovery", "why", "proposal", "kpi_stale"]},
                            "goal_id": {"type": "string"},
                            "options": {
                                "type": "array",
                                "description": "kind recovery: each option in plain words, "
                                "{label, pick, actions: [{text, note}]}",
                                "items": {"type": "object"},
                            },
                            "goals": {
                                "type": "array",
                                "description": "kind why: the goals whose proposed Why lines wait on you",
                                "items": {"type": "object"},
                            },
                            "goal_title": {"type": ["string", "null"]},
                            "kpi_id": {"type": "string"},
                            "kpi_name": {"type": ["string", "null"]},
                            "reason": {"type": "string"},
                            "kpis": {
                                "type": "array",
                                "description": "kind kpi_stale: each of the person's measures whose reading is out of "
                                "date, in the one ask: {kpi_id, kpi_name, freshness, reason}",
                                "items": {"type": "object"},
                            },
                            "proposal": {"type": "object"},
                            "task": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "status": {"type": "string"},
                                },
                            },
                        },
                    }
                ),
            },
            required=["actor", "items"],
        ),
        "GoalRefreshView": obj({"changed": "a", "suggested": "a", "checked": "i"}),
        "GoalCheckins": obj({"goal_id": "s", "checkins": items(ref("GoalCheckinView"))}),
        "GoalCheckinResult": obj({"checkin": ref("GoalCheckinView")}),
        "GoalKpiResult": obj({"kpi": ref("Kpi")}),
        "GoalProposalList": obj({"proposals": items(ref("GoalProposalView"))}),
        "GoalProposalResult": obj({"proposal": ref("GoalProposalView")}),
    }
)

SCHEMAS["GoalResult"]["properties"]["goal"] = merge(SCHEMAS["Goal"], SCHEMAS["GoalView"])

SCHEMAS["GoalTree"]["properties"]["goals"] = items(merge(SCHEMAS["Goal"], SCHEMAS["GoalTreeRow"]))
