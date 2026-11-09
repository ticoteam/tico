"""A starter bot's setup status, under its new and old names.

`needs_setup` is what Tico writes; `needs_onboarding` is what older data, runners and callers still say. Every
reader goes through here so both are accepted for one release. Migration 45 rewrites the stored rows.
"""

NEEDS_SETUP = "needs_setup"
OLD_NEEDS_ONBOARDING = "needs_onboarding"
PARKED = (NEEDS_SETUP, OLD_NEEDS_ONBOARDING)
PARKED_SQL = "('" + "','".join(PARKED) + "')"  # for `IN {PARKED_SQL}` in a statement


def is_parked(state):
    """True for a bot that still needs its setup, whichever name the value carries."""
    return state in PARKED


def migrate(c):
    """Rewrite stored `needs_onboarding` to `needs_setup` (bot_config.onboarding_state). Idempotent."""
    c.execute("UPDATE bot_config SET onboarding_state=? WHERE onboarding_state=?", (NEEDS_SETUP, OLD_NEEDS_ONBOARDING))
