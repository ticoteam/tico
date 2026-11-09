"""Helpers that other tests share, moved out of test_company_file.py so they import no test file."""

from backend import company_file as C


def chart(seats=20):
    """A first chart cut to `seats` bots. Every company model is over the 8 KB parameter the installer packs a plan
    into today; carrying a whole model chart waits on the plan-bucket transport (hosted signup, Org chart models)."""
    draft = C.Turn(C.blank()).finish()
    draft["bots"] = draft["bots"][:seats]
    return draft
