"""The `importers` job: runs the meeting importers the owner assigned to this computer.

The hub says which importers are enabled here (Settings > Meeting importers). Each one keeps its
own schedule, failure backoff and status heartbeat, so one broken credential never stalls another.
"""

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from clients.tico import Client

from ..outage import Outage, describe, log
from ..state import State
from . import REGISTRY, importer_class
from .base import CODES, ProviderError

RETRY_SECONDS = 60
GRANULARITY = 30


class ImporterService:
    def __init__(self, config, directory=None, *, client=None, state=None, classes=None, now=None,
                 backfill_days=None, only=None, transport=None):
        self.config = config
        self.client = client or Client(config["url"], config["token"], timeout=60, retries=1)
        self.state = state if state is not None else (State(directory) if directory else None)
        self.classes = classes or importer_class
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.backfill_days, self.only, self.transport = backfill_days, only, transport
        self.stop = threading.Event()
        self.instances, self.due, self.failures = {}, {}, {}

    def importer(self, source):
        if source not in self.instances:
            self.instances[source] = self.classes(source)(
                self.config, self.state, self.client, transport=self.transport, now=self.now,
                backfill_days=self.backfill_days)
        return self.instances[source]

    def assigned(self):
        if self.only:
            return [self.only] if self.only in REGISTRY else []
        rows = (self.client.get("runners/importers") or {}).get("importers") or []
        return [r["source"] for r in rows if isinstance(r, dict) and r.get("source") in REGISTRY]

    def report(self, source, /, **body):
        try:
            self.client.post("imports/sources/" + source + "/status", body)
        except Exception:
            pass                        # the next pass reports again; a failed report is not a failed sync

    def sync(self, source):
        """One importer, one pass. Returns how many meetings changed; never raises a secret."""
        importer = self.importer(source)
        try:
            imported = importer.tick(self.stop)
        except ProviderError as exc:
            self.failures[source] = self.failures.get(source, 0) + 1
            log("Tico " + importer.name + ": " + exc.code + ", " + str(exc))
            self.report(source, state="error", error_code=exc.code, message=str(exc)[:200])
            return None
        except Exception as exc:        # a bug or a hub outage: name the type, never the text
            self.failures[source] = self.failures.get(source, 0) + 1
            log("Tico " + importer.name + ": sync_error, " + describe(exc))
            self.report(source, state="error", error_code="sync_error", message=CODES["sync_error"])
            return None
        self.failures[source] = 0
        if imported:
            log("Tico " + importer.name + ": imported " + str(imported) + " meeting" + ("" if imported == 1 else "s"))
        self.report(source, state="ok", imported=imported)
        return imported

    def tick(self):
        """Every assigned importer that is due (all of them in a one-shot backfill)."""
        done = {}
        for source in self.assigned():
            if self.stop.is_set():
                break
            if not self.backfill_days and self.due.get(source, 0) > time.monotonic():
                continue
            imported = self.sync(source)
            done[source] = imported
            failures = self.failures.get(source, 0)
            delay = self.importer(source).interval if not failures else min(
                self.importer(source).interval, RETRY_SECONDS * 2 ** min(failures - 1, 3))
            self.due[source] = time.monotonic() + delay
        return done

    def run(self):
        from ..freshness import CodeWatch
        cloud = Outage("Tico importers", "cannot reach the Tico server", "still cannot reach the Tico server",
                       "Tico server reachable again")
        watch = CodeWatch("Tico importers")
        while not self.stop.is_set():
            try:
                self.tick()
                cloud.recovered()
            except Exception as exc:
                cloud.failed(exc)
            if watch.wait(self.stop, GRANULARITY):   # the checkout moved: the supervisor starts it on the new code
                return


def doctor(config):
    """Which importers have their credentials on this computer. No provider call is made."""
    out = {}
    for source in REGISTRY:
        importer = importer_class(source)(config, None, None)
        try:
            importer.ready()
            out[source] = "present, not remotely verified"
        except ProviderError as exc:
            out[source] = "not set up: " + str(exc)
    return out
