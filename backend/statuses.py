"""A starter bot's setup status: `needs_setup` until it says its setup is done (backend/onboarding.py)."""

NEEDS_SETUP = "needs_setup"
PARKED = (NEEDS_SETUP,)
PARKED_SQL = "('" + "','".join(PARKED) + "')"  # for `IN {PARKED_SQL}` in a statement


def is_parked(state):
    """True for a bot that still needs its setup."""
    return state in PARKED
