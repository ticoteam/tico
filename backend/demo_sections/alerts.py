"""Personal Alerts preferences in the sample company's Inboxes settings; no verification or send leaves the demo."""
from .. import personal_alerts


def seed(builder):
    def write(c):
        personal_alerts.save(c, "ana", {"phone": "+14155550123", "phone_version": "demo-alert-phone",
                                        "verified_at": builder.ago(days=3).isoformat(), "call_consent": 1,
                                        "urgent_needs": 1, "legal_mail": 1, "bot_stuck": 1,
                                        "timezone": "America/Los_Angeles", "monthly_cap": 10})
    builder.write(write)
