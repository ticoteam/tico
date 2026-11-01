"""Helper services restart on new code.

A Mac runs three helpers beside the runner (connectors, close-calls, importers), each a long-lived loop under its own launchd
job. A release moves the shared checkout and restarts the runner's job only, so a helper would go on with the old release in
memory while it lazily imports new modules and runs new scripts from the switched checkout: two versions in one process.
Each helper therefore records the checkout's revision at start and looks again at least once a minute; when it has changed,
the loop exits with status 0 between jobs and the supervisor (launchd KeepAlive) starts it on the new code. Any new revision
counts, not only changes under runner_support/: a helper loads more than that.
"""
import os
import re
import subprocess
import time
from pathlib import Path

from .outage import log

ROOT = Path(__file__).resolve().parents[1]
CHECK_EVERY_S = 60


def checkout_head(root=None, run=subprocess.run):
    try:
        done = run(["git", "-C", str(root or ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                   timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    head = done.stdout.strip()
    return head if not done.returncode and re.fullmatch(r"[0-9a-f]{40}", head) else ""


def supervised():
    """Only a supervisor that starts the runner again may let it exit to restart: launchd sets
    XPC_SERVICE_NAME to the job's label, the systemd user unit `scripts/tico install` writes on Linux sets
    TICO_SYSTEMD_UNIT (and Restart=always), and TICO_SUPERVISED=1 covers any other supervisor (the Docker
    runner, runit)."""
    env = os.environ
    return bool(env.get("XPC_SERVICE_NAME", "").startswith("team.tico")
                or env.get("TICO_SYSTEMD_UNIT", "").startswith("tico-")
                or env.get("TICO_SUPERVISED") == "1")


process_supervised = supervised      # CodeWatch's `supervised` argument shadows the name


class CodeWatch:
    def __init__(self, name, every=CHECK_EVERY_S, clock=time.monotonic, head=None, supervised=None, root=None, say=log):
        self.name, self.every, self.clock, self.say = name, every, clock, say
        self.root = root or ROOT
        self.head = head or (lambda: checkout_head(self.root))
        self.supervised = process_supervised if supervised is None else supervised
        self.started = self.head()
        self.checked = clock()

    def stale(self, force=False):
        """True when this process should exit so the supervisor starts it on the new code. Looks at most once
        per `every` seconds; an unknown revision (no git, mid-checkout) never counts. Logs the reason."""
        if not self.started or not self.supervised():
            return False
        if not force and self.clock() - self.checked < self.every:
            return False
        self.checked = self.clock()
        head = self.head()
        if not head or head == self.started:
            return False
        self.say(f"{self.name}: the checkout moved from {self.started[:7]} to {head[:7]}; "
                 "exiting so the supervisor restarts it on the new code")
        return True

    def wait(self, stop, seconds):
        """Sleep up to `seconds` on the `stop` event, looking at the revision every `every` seconds. True when the
        revision changed (exit now); False when the time is up or `stop` was set."""
        deadline = self.clock() + seconds
        while not stop.is_set():
            left = deadline - self.clock()
            if left <= 0:
                return False
            stop.wait(min(left, self.every))
            if not stop.is_set() and self.stale(force=True):
                return True
        return False
