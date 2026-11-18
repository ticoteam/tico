# pyright: strict
"""The answers of the Alerts routes (backend/openapi_v2.py gathers them)."""

from ._base import SCHEMAS, items, obj, scalar

SCHEMAS.update(
    {
        "PersonalAlerts": obj(
            {
                "phone": "n",
                "status": "s",
                "verified_at": "n",
                "call_consent": "b",
                "preferences": obj(
                    {
                        "legal_mail": "b",
                        "urgent_calls": "b",
                        "timezone": "s",
                        "quiet_start": "s",
                        "quiet_end": "s",
                        "emergency_exception": "b",
                        "monthly_cap": "i",
                    }
                ),
                "monthly_spend": "i",
                "company_monthly_cap": "i",
                "company_monthly_spend": "i",
                "price_usd": "i",
                "paid_by": "s",
                "recent": items(obj({"id": "s", "kind": "s", "state": "s", "error": "s", "created_at": "s"})),
            }
        ),
        "AlertPhoneCode": obj({"status": "s", "expires_in": "i"}),
        "AlertTest": obj(
            {"id": "s", "status": "s"}, required=["id", "status"], price_usd=scalar("i"), paid_by=scalar("s")
        ),
        "AlertCompanyCap": obj({"monthly_cap": "i", "monthly_spend": "i", "price_usd": "i"}),
    }
)
