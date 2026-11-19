# pyright: strict
"""The answers of the Pipelines routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

STAGE_SIDE = {
    "type": "boolean",
    "description": "A side stage is a work stage that tasks enter and leave by hand. It follows a main work stage.",
}
STAGE_PLACE = obj({"id": "s", "name": "s", "side": "b"})

SCHEMAS.update(
    {
        "PipelineSettingsInput": obj(
            {
                "settings_version": {"type": ["integer", "null"]},
                "stages": items(obj({"side": STAGE_SIDE}, required=[])),
            },
            required=[],
        ),
        "PipelineSettingsStage": obj(
            {
                "id": "s",
                "name": "s",
                "purpose": "s",
                "owner": "n",
                "side": STAGE_SIDE,
                "by_hand": "b",
                "requests_move_on": "b",
                "task_limit": {"type": ["integer", "null"]},
                "expected_minutes": {"type": ["integer", "null"]},
                "expected_bots_only": "b",
                "on_enter": "o",
                "requirements": items("o"),
            }
        ),
        "PipelineSettings": obj(
            {
                "id": "s",
                "name": "s",
                "settings_version": "i",
                "stages": items(ref("PipelineSettingsStage")),
                "done": {"oneOf": [STAGE_PLACE, {"type": "null"}]},
                "closed": {"oneOf": [STAGE_PLACE, {"type": "null"}]},
            }
        ),
        "PipelineSettingsResult": obj({"pipeline": ref("PipelineSettings")}),
        "PipelineSettingsList": obj({"pipelines": items(ref("PipelineSettings"))}),
        "PipelineLog": obj(
            {
                "log": items(obj({"at": "s", "text": "s", "task_id": "n", "by": "n", "why": "s"})),
                "learned": items(obj({"at": "s", "text": "s"})),
            }
        ),
        "FactoryMap": obj(
            {
                "factories": items(obj({"id": "s", "name": "s", "goal": "s", "manager_id": "n", "done": "i"})),
                "pipelines": items(
                    obj(
                        {
                            "id": "s",
                            "name": "s",
                            "factory_id": "n",
                            "open": "i",
                            "files_into": items({"type": "string"}),
                            "mark": {"enum": ["cant_run", "needs_person", None]},
                        }
                    )
                ),
            }
        ),
        "CompanyFloor": obj(
            {
                "factories": items(
                    obj(
                        {
                            "id": "n",
                            "name": "s",
                            "manager_id": "n",
                            "pipelines": items(
                                obj(
                                    {
                                        "id": "s",
                                        "name": "s",
                                        "manager_id": "n",
                                        "in": obj({"day": "i", "week": "i"}),
                                        "out": obj({"day": "i", "week": "i"}),
                                        "moved_into": items({"type": "string"}),
                                        "stages": items(
                                            obj(
                                                {
                                                    "id": "s",
                                                    "name": "s",
                                                    "count": "i",
                                                    "needs_you": "i",
                                                    "owners": items(
                                                        obj({"actor": "s", "count": "i", "needs_you": "i"})
                                                    ),
                                                    "more": obj({"owners": "i", "count": "i"}),
                                                    "into": "n",
                                                }
                                            )
                                        ),
                                    }
                                )
                            ),
                        }
                    )
                )
            }
        ),
        "FactoryCreated": obj({"factory": obj({"id": "s", "name": "s", "goal": "s"}, required=["id", "name"])}),
        "ReleaseSchedule": obj(
            {
                "on": "b",
                "times": items({"type": "string"}),
                "bot": "s",
                "writer": "s",
                "zone": "s",
                "release_manager": "n",
                "technical_writer": "n",
                "next": "n",
                "last": {"type": ["object", "null"]},
                "pending": {"type": "integer", "description": "Open tasks you may read waiting for a release"},
                "can_change": "b",
                "can_release": "b",
            }
        ),
        "ReleaseNowResult": obj(
            {
                "sent": "b",
                "reason": {"enum": ["no_release_manager", "already_open", None]},
                "task": {
                    "type": ["string", "null"],
                    "description": "The task filed, or the open one when you may read it",
                },
                "tasks": {"type": "integer", "description": "Open tasks you may read waiting for a release"},
            }
        ),
    }
)
