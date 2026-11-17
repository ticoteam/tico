# pyright: strict
"""The answers of the Team chart routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj, scalar

SCHEMAS.update(
    {
        "Person": obj(
            {"id": "s", "name": "s", "email": "s", "title": "s", "team": "s", "reports_to": "n", "org_parent": "s"},
            required=["id", "name", "org_parent"],
        ),
        "Access": obj({"see": "b", "read": "b", "write": "b"}, required=["see", "read", "write"]),
        "OrgBot": obj(
            {
                "id": "s",
                "display_name": "s",
                "description": "s",
                "team": "s",
                "reports_to": "n",
                "org_parent": "s",
                "owners": items({"type": "string"}),
                "status": "s",
                "access": ref("Access"),
                "onboarding_state": "s",
                "stage": {
                    "enum": ["onboarding", "active"],
                    "description": "Onboarding (learns only: no tasks, routines or sends) until a person above it marks onboarding complete, then active",
                },
            },
            required=["id", "display_name", "org_parent", "status", "access"],
        ),
        "Seat": obj(
            {
                "id": "s",
                "kind": {"enum": ["seat"]},
                "stage": {"enum": ["recruiting"]},
                "title": "s",
                "description": "s",
                "reports_to": "s",
                "team": "s",
                "created_at": "s",
                "org_parent": "s",
            },
            required=["id", "kind", "stage", "title", "description", "reports_to"],
        ),
        "SeatList": obj({"seats": items(ref("Seat"))}, required=["seats"]),
        "SeatClosed": obj({"id": "s", "closed": "b"}, required=["id", "closed"]),
        "SeatDetail": obj(
            {
                "id": "s",
                "kind": {"enum": ["seat"]},
                "stage": {"enum": ["recruiting"]},
                "title": "s",
                "description": "s",
                "reports_to": "s",
                "team": "s",
                "created_at": "s",
                "description_by": {"enum": ["chart", "person"]},
                "can_edit": "b",
                "reports_now": obj({"name": "s", "until": "s"}, required=["name", "until"]),
            },
            required=["id", "kind", "stage", "title", "description", "reports_to", "can_edit"],
        ),
        "SeatDrafted": obj(
            {"description": "s", "source": {"enum": ["catalog", "model", "none"]}}, required=["description", "source"]
        ),
        "SeatNeed": obj(
            {"tool": "s", "label": "s", "required": "b", "state": {"enum": ["connected", "on_hire", "not_set"]}},
            required=["tool", "label", "required", "state"],
        ),
        "SeatCandidate": obj(
            {
                "template": "s",
                "name": "s",
                "icon": "s",
                "department": "s",
                "department_id": "s",
                "line": "s",
                "summary": "s",
                "tags": items({"type": "string"}),
                "owns": items({"type": "string"}),
                "never": items({"type": "string"}),
                "onboarding": items({"type": "string"}),
                "prerequisites": items(ref("SeatNeed")),
                "match": {"enum": ["strong", "good", ""]},
                "source": {"enum": ["template"]},
                "starter": "b",
                "model": "s",
                "model_name": "s",
                "effort": "s",
            },
            required=[
                "template",
                "name",
                "icon",
                "department",
                "line",
                "tags",
                "owns",
                "prerequisites",
                "match",
                "source",
                "model",
                "model_name",
            ],
        ),
        "SeatCandidates": obj(
            {
                "seat": ref("SeatDetail"),
                "candidates": items(ref("SeatCandidate")),
                "models": items(obj({"id": "s", "name": "s", "provider": "s"}, required=["id", "name"])),
            },
            required=["seat", "candidates", "models"],
        ),
        "SeatHired": obj(
            {
                "slug": "s",
                "name": "s",
                "seat": "s",
                "stage": {"enum": ["onboarding"]},
                "reports_to": "s",
                "template": "s",
                "model": "s",
                "setup_task_id": "n",
                "note": "n",
            },
            required=["slug", "seat", "stage", "reports_to", "template"],
        ),
        "Audience": obj(
            {
                "everyone": "b",
                "people": items({"type": "string"}),
                "teams": items({"type": "string"}),
                "bots": items({"type": "string"}),
            },
            required=["everyone", "people", "teams", "bots"],
        ),
        "TeammateAccessView": obj(
            {
                "target": "s",
                "see": ref("Audience"),
                "read": ref("Audience"),
                "write": ref("Audience"),
                "default": {"type": "object"},
                "revision": "i",
                "you": obj({"see": "b", "read": "b", "write": "b", "manage": "b"}),
                "teams": items({"type": "object"}),
            },
            required=["target", "see", "read", "write", "revision"],
        ),
        "Group": obj(
            {
                "id": "s",
                "name": "s",
                "parent": "s",
                "people": items({"type": "string"}),
                "bots": items({"type": "string"}),
                "bot_slugs": items({"type": "string"}),
                "order": "i",
            }
        ),
        "GroupList": items(ref("Group")),
        "ReportOrderView": obj(
            {
                "manager": "s",
                "kind": "s",
                "name": "s",
                "can_order": "b",
                "reports": items(
                    obj({"id": "s", "kind": "s", "name": "s", "saved": "b"}, required=["id", "kind", "name", "saved"])
                ),
            },
            required=["manager", "kind", "name", "can_order", "reports"],
        ),
        "Org": obj(
            {
                "people": items(ref("Person")),
                "bots": items(ref("OrgBot")),
                "seats": items(ref("Seat")),
                "org_groups": "a",
                "teams": "o",
            }
        ),
        "OrgModelSeat": obj({"team": "s", "template": "s", "title": "s"}, required=["team", "template"]),
        "OrgModel": obj(
            {"model": "s", "name": "s", "about": "s", "seats": items(ref("OrgModelSeat"))},
            required=["model", "name", "about", "seats"],
        ),
        "OrgModels": obj({"models": items(ref("OrgModel"))}, required=["models"]),
        "SeatBatch": obj({"seats": items(ref("Seat"))}, required=["seats"]),
        # Cloud agents (backend/cloud_agents.py): a seat asks Tico's team to connect an agent it does not connect yet.
        "CloudAgent": obj(
            {"id": "s", "name": "s", "maker": "s", "site": "s", "line": "s", "supported": "b", "connect_id": "n"},
            required=["id", "name", "maker", "site", "line", "supported", "connect_id"],
        ),
        "CloudAgentList": obj({"agents": items(ref("CloudAgent"))}, required=["agents"]),
        "CloudAgentAdded": obj(
            {"bot": "s", "harness": "s", "model": "s", "seat": "s"}, required=["bot", "harness", "model", "seat"]
        ),
        "SeatAgent": obj(
            {
                "seat": ref("Seat"),
                "agent": ref("CloudAgent"),
                "steps": items({"enum": ["requested", "building", "ready"]}),
                "tasks": items(obj({"id": "s", "title": "s", "status": "s"}, required=["id", "title", "status"])),
            },
            required=["seat", "agent", "steps", "tasks"],
        ),
        "SeatCanceled": obj(
            {"id": "s", "closed": "b", "request_state": "s"}, required=["id", "closed", "request_state"]
        ),
    }
)

SCHEMAS["Seat"]["properties"].update({name: scalar("s") for name in ("agent", "agent_name", "request_state")})

SCHEMAS["Seat"]["required"] = sorted(set(SCHEMAS["Seat"]["required"]) | {"agent", "agent_name", "request_state"})
