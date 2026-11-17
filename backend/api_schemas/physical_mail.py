# pyright: strict
"""The answers of the Physical mail routes (backend/openapi_v2.py gathers them)."""

from ..api_contract import ref
from ._base import SCHEMAS, items, obj

SCHEMAS.update(
    {
        "PostalMailGrant": obj(
            {
                "id": "s",
                "address_id": "s",
                "grantee_type": {"enum": ["person", "bot"]},
                "grantee_id": "s",
                "level": {"enum": ["read"]},
                "granted_by_id": "s",
                "created_at": "s",
                "updated_at": "s",
            }
        ),
        "PostalMailGrants": obj({"address_id": "s", "grants": items(ref("PostalMailGrant")), "next": "n"}),
        "PostalMailGrantResult": obj({"grant": ref("PostalMailGrant")}),
        "PostalMailRevoked": obj({"ok": "b"}),
        "PostalMailBots": obj(
            {
                "bots": items(
                    obj(
                        {
                            "id": "s",
                            "slug": "s",
                            "display_name": "s",
                            "state": "s",
                            "read_error": "n",
                            "routing_error": "n",
                        }
                    )
                ),
                "next": "n",
            }
        ),
        "PostalMailSettings": obj(
            {
                "settings": obj(
                    {"id": "i", "legal_bot_id": "n", "updated_by_id": "n", "created_at": "s", "updated_at": "s"}
                )
            }
        ),
    }
)
