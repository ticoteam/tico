"""One deadline poll for a test that waits on state the product exposes no event for.

Wait on an Event, a Barrier or a Condition when the product has one. When it does not, poll here: the short pause
between checks is this helper's alone, and the wait fails naming what never happened instead of passing late.
"""

import time


def wait_for(check, what, seconds=10.0):
    """Polls until check() is truthy, and returns its value. Fails naming `what` when it never is."""
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("never happened: " + what)
        time.sleep(0.01)
