# pyright: strict
"""The answers of the Subscriptions routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "SubscriptionList": obj(
            {
                "profiles_by_computer": items(
                    obj(
                        {
                            "runner_id": "s",
                            "label": "s",
                            "profiles": items(obj({"name": "s", "id": "s", "display_name": "s", "runtimes": "o"})),
                        }
                    )
                )
            }
        ),
        "SubscriptionIdentity": obj({"id": "s", "display_name": "s"}),
        "SubscriptionWeeklyResult": obj(
            {
                "weekly": {
                    "anyOf": [
                        obj(
                            {
                                "used_percent": {"type": ["number", "null"]},
                                "resets_at": "n",
                                "reported_at": "s",
                                "source": {"enum": ["manual"]},
                            }
                        ),
                        {"type": "null"},
                    ]
                }
            }
        ),
        "SubscriptionRefreshResult": obj(
            {
                "id": "s",
                "profile": "s",
                "runtime": "s",
                "state": {"enum": ["requested", "succeeded", "unavailable", "failed", "expired"]},
                "requested_at": "s",
                "updated_at": "s",
                "expires_at": "s",
            }
        ),
        "BotSubscription": obj(
            {
                "profile": "n",
                "id": "s",
                "display_name": "s",
                "source": {"enum": ["computer"]},
                "computer": {"anyOf": [obj({"runner_id": "s", "label": "s"}), {"type": "null"}]},
                "runtime": "s",
                "harness": "s",
                "signed_in": {"type": ["boolean", "null"]},
                "problem": "s",
                "warning": "s",
            },
            required=[
                "profile",
                "id",
                "display_name",
                "source",
                "computer",
                "runtime",
                "harness",
                "signed_in",
                "problem",
            ],
        ),
    }
)
