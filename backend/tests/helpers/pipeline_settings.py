"""A pipeline record shared by the pipeline settings tests (test_pipeline_settings.py, test_board_owner.py): synthetic
rows only, in the fictional company of backend/tests/helpers/engine.py, where Ben is an Admin and Cara a member."""

from typing import Any

BEN, CARA = "human:ben", "human:cara"


def ops(**fields: Any) -> dict[str, Any]:
    """A two-stage pipeline record with every field set, as a caller sends it."""
    record = {
        "name": "Operations",
        "goal": "Keep the office running",
        "prefix": "OP",
        "manager_id": "bot:manager",
        "owner_id": CARA,
        "private": False,
        "factory_id": None,
        "preset": None,
        "stages": [
            {
                "name": "Intake",
                "purpose": "Sort what came in",
                "owner": "bot:builder",
                "side": False,
                "by_hand": False,
                "requests_move_on": False,
                "task_limit": None,
                "on_enter": {},
                "requirements": [
                    {
                        "kind": "owner_done",
                        "source": None,
                        "condition": None,
                        "settings": {},
                        "can_push_past": True,
                        "who_oks": "none",
                        "person_id": None,
                        "line": "Say it is sorted",
                    }
                ],
            },
            {
                "name": "Sign off",
                "purpose": "",
                "owner": "assignee",
                "side": False,
                "by_hand": False,
                "requests_move_on": False,
                "task_limit": 2,
                "on_enter": {},
                "requirements": [
                    {
                        "kind": "ok",
                        "source": None,
                        "condition": None,
                        "settings": {},
                        "can_push_past": True,
                        "who_oks": "people_only",
                        "person_id": BEN,
                        "line": "Ben OKs it",
                    }
                ],
            },
        ],
    }
    record.update(fields)
    return record
