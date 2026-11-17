# pyright: strict
"""The answers of the Session routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, obj

SCHEMAS.update(
    {
        "Me": obj(
            {
                "actor": "s",
                "role": "s",
                "email": "s",
                "slug": {"type": "string", "description": "A bot caller: its slug (a label; the actor holds its id)."},
            },
            required=["actor", "role", "email"],
        ),
        "Token": obj({"access_token": "s", "token_type": "s", "expires_in": "i", "idle_timeout": "i", "person": "s"}),
        "Revoked": obj({"revoked": "b"}),
    }
)
