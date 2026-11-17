# pyright: strict
"""The answers of the KPIs routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import NUM_N, SCHEMAS, items, merge, obj

# The Growth page's numbers (backend/growth_numbers.py): the one read it draws from.
NUM_N_INT = {"type": ["integer", "null"]}


SCHEMAS.update(
    {
        "KpiLink": obj(
            {
                "id": "s",
                "goal_id": "s",
                "kind": {"enum": ["none", "improve", "maintain"]},
                "baseline": NUM_N,
                "baseline_at": "n",
                "target": NUM_N,
                "deadline": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                "min": NUM_N,
                "max": NUM_N,
            },
            required=["goal_id", "kind"],
        ),
        "KpiReadingView": obj(
            {
                "id": "s",
                "value": "f",
                "period_start": "s",
                "period_end": "s",
                "collected_at": "s",
                "quality": {"enum": ["measured", "estimate", "partial"]},
                "evidence": "s",
                "note": "s",
                "source": "s",
                "actor_id": {
                    "type": ["string", "null"],
                    "description": "The person who recorded it; null for a bot's automatic reading",
                },
                "definition_version": "i",
                "supersedes": "n",
            },
            required=["id", "value", "period_end", "quality", "definition_version"],
            kpi_id={"type": "string"},
            measured_at={"type": "string"},
            created_at={"type": "string"},
            superseded_by={"type": ["string", "null"], "description": "The reading that corrected this one, if any"},
            actor_name={"type": "string"},
        ),
        "Kpi": obj(
            {
                "id": {"type": "string", "description": "A UUID, or auto:<bot>:<metric> for a bot's automatic KPI"},
                "slug": "n",
                "name": "s",
                "definition": "s",
                "unit": "s",
                "direction": {"enum": ["up", "down", "range"]},
                "cadence": {"enum": ["daily", "weekly", "monthly"]},
                "owner_id": "s",
                "source_note": "s",
                "definition_version": "i",
                "created_at": "n",
                "created_by_id": "n",
                "updated_at": "n",
                "archived_at": {"type": ["string", "null"]},
                "archived_by_id": {"type": ["string", "null"]},
                "auto": "b",
                "latest": {"oneOf": [ref("KpiReadingView"), {"type": "null"}]},
                "readings": "i",
                "freshness": {
                    "enum": ["fresh", "stale", "missing", "archived"],
                    "description": "stale: one period missed; missing: never read or two missed. archived: a retained goal link, not counting. Never zero",
                },
                "spark": {"type": "array", "items": {"type": "number"}, "description": "The last values, oldest first"},
                "status": {
                    "enum": ["green", "yellow", "red", "gray", "none", None],
                    "description": "Against the target on the link (or a range); gray is stale or missing data; none is fresh with no target; null is an archived goal link with no colour",
                },
                "reason": "s",
            },
            required=[
                "id",
                "name",
                "unit",
                "direction",
                "cadence",
                "owner_id",
                "definition_version",
                "auto",
                "latest",
                "readings",
                "freshness",
                "spark",
                "status",
                "reason",
            ],
            link=ref("KpiLink"),
            target_label={"type": "string", "description": "'→ 65% by Dec 31' or 'range 40–60'"},
            expected={
                "type": ["number", "null"],
                "description": "Where the straight line from baseline to target is now",
            },
            owner_name={"type": "string"},
        ),
        "KpiList": obj({"kpis": items(ref("Kpi"))}, required=["kpis"]),
        "KpiDetail": obj(
            {
                "kpi": ref("Kpi"),
                "links": {},
                "readings": items(ref("KpiReadingView")),
                "definitions": items(
                    {
                        "type": "object",
                        "description": "version, ts, actor, name, definition, unit, direction, cadence, source_note; newest first",
                    }
                ),
                "checkins": items(ref("GoalCheckinView")),
                "proposals": items(ref("GoalProposalView")),
                "may_edit": "b",
                "may_log": "b",
            },
            required=["kpi", "links", "readings", "definitions", "checkins", "proposals", "may_edit", "may_log"],
        ),
        "KpiReadings": obj(
            {"kpi": {"type": "object", "description": "The KPI record"}, "readings": items(ref("KpiReadingView"))}
        ),
        "KpiReadingResult": obj({"reading": ref("KpiReadingView")}),
        "BotKpis": obj({"bot": "s", "kpis": items(ref("Kpi"))}),
    }
)

SCHEMAS["KpiDetail"]["properties"]["links"] = items(
    merge(
        SCHEMAS["KpiLink"],
        obj(
            {
                "goal_title": "s",
                "goal_owner": "s",
                "goal_status": "n",
                "target_label": "s",
                "status": "s",
                "reason": "s",
                "expected": NUM_N,
            },
            required=["goal_title", "status"],
        ),
    )
)

SCHEMAS.update(
    {
        "GrowthMonth": obj(
            {
                "month": "s",
                "spend": NUM_N,
                "bot_cost": NUM_N,
                "new_customers": NUM_N_INT,
                "cac": NUM_N,
                "kept_d30": NUM_N,
                "kept_d90": NUM_N,
                "kept_d180": NUM_N,
                "expected_months": NUM_N,
                "ltv": NUM_N,
                "ltv_cac": NUM_N,
                "payback_months": NUM_N,
                "quality": "n",
                "revenue_month": NUM_N,
                "margin_pct": NUM_N,
            }
        ),
        "GrowthChannel": obj(
            {"slug": "s", "name": "s", "stage": "n", "owner_ref": "n", "series": items(ref("GrowthMonth"))}
        ),
        "GrowthKpi": obj(
            {
                "id": "s",
                "name": "s",
                "unit": "s",
                "latest": NUM_N,
                "latest_at": "n",
                "target": NUM_N,
                "goal_id": "n",
                "parent_kpi_id": "n",
                "growth_role": "n",
                "stage": "n",
                "loop_name": "n",
                "loop_step": NUM_N_INT,
                "series": items(obj({"at": "n", "value": {"type": "number"}})),
            }
        ),
        "GrowthMarker": obj(
            {
                "task_id": "s",
                "title": "s",
                "kind": {"enum": "started finished".split()},
                "outcome": {"enum": ["won", "lost", "inconclusive", "kept", "dropped", None]},
                "result": {"enum": ["failed", "worked-once", "watching", None]},
                "at": "s",
                "channels": items({"type": "string"}),
                "goal_id": "n",
            }
        ),
        "GrowthNumbers": obj(
            {
                "months": items({"type": "string"}),
                "series": items(ref("GrowthMonth")),
                "channels": items(ref("GrowthChannel")),
                "kpis": obj(
                    {
                        "north_star": {"oneOf": [ref("GrowthKpi"), {"type": "null"}]},
                        "inputs": items(ref("GrowthKpi")),
                        "guardrails": items(ref("GrowthKpi")),
                        "other": items(ref("GrowthKpi")),
                    }
                ),
                "markers": items(ref("GrowthMarker")),
                "may_edit": {"type": "boolean"},
            }
        ),
    }
)
