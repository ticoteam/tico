"""Bounded calls for a server-side decision caller (ask ingest, rule checks): a pool of slots and a circuit breaker.

Each caller keeps its own pool (`Calls`, one per store), so one caller's calls never take another's slots. At most
`size` calls run at once in this process; with every slot in use the call is refused at once (`busy`), never waiting
for one. After BREAKER_FAILURES timeouts or errors in a row no call is made for BREAKER_SECONDS (`breaker`), then one
call probes. A call holds its slot until its thread ends, not until its caller stops waiting, so abandoned calls still
count against the bound. Failures that say nothing of the provider's health (NEUTRAL) never trip the breaker.
"""

import logging
import threading
import time

LOG = logging.getLogger("tico.call_slots")
BREAKER_FAILURES, BREAKER_SECONDS = 5, 60
NEUTRAL = ("spend_cap", "invalid")    # failures that say nothing of the provider's health: they never trip the breaker
REFUSED = ("busy", "breaker")         # refusals at admission: no call was made


class Calls:
    """One caller's calls in flight and its circuit breaker: process-local, under one lock. `label` names the caller
    in the breaker's log line."""

    def __init__(self, size, label):
        self.size, self.label = size, label
        self.slots = threading.BoundedSemaphore(size)
        self.lock = threading.Lock()
        self.failures, self.until, self.probing = 0, 0.0, False

    def admit(self):
        """(None, probe) when a call may start, its slot taken; else (`breaker` or `busy`, False), at once. The first
        call after the cool-off is the probe, one at a time."""
        probe = False
        with self.lock:
            if self.failures >= BREAKER_FAILURES:
                if self.probing or time.monotonic() < self.until:
                    return "breaker", False
                self.probing = probe = True
        if not self.slots.acquire(blocking=False):
            if probe:
                with self.lock:
                    self.probing = False
            return "busy", False
        return None, probe

    def done(self, ok, probe):
        """A call's end as its caller saw it: True answered, False timed out or failed, None neither (a spend cap)."""
        with self.lock:
            if probe:
                self.probing = False
            if ok:
                self.failures = 0
            elif ok is False:
                self.failures += 1
                if self.failures >= BREAKER_FAILURES:
                    if self.failures == BREAKER_FAILURES or probe:
                        LOG.warning("%s pause for %ss after %s failed calls in a row", self.label, BREAKER_SECONDS,
                                    self.failures)
                    self.until = time.monotonic() + BREAKER_SECONDS
