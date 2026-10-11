"""Side jobs on a runner that has no launchd: the runner supervises `importers` and `close-calls`.

A Mac runs them as separate launchd jobs (scripts/tico install). A container has one
process to start, so with TICO_SIDE_JOBS=1 `python -m runner run` also runs this supervisor. A job
runs only while it is wanted: the hub assigned meeting importers to this computer, the Close key is
in this computer's secrets, or (`connectors`, mail and calendar) the Google service-account key is
here or the hub names this computer's operator a processing operator. Nothing else needs turning on. Each job is its own child process, so a
crash in one never takes down the bot runner, and each restarts with backoff.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .outage import describe, log

ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 60
STABLE_SECONDS = 120          # a child that ran this long starts its backoff over
BACKOFF_START, BACKOFF_MAX = 10, 300
STOP_GRACE = 30


def importers_wanted(client, config):
    from clients.tico import APIError
    from .importers import REGISTRY
    try:
        rows = (client.get("runners/importers") or {}).get("importers") or []
    except APIError as exc:
        if exc.status == 403:               # this computer is not an import operator: not wanted
            return []
        raise
    return [r["source"] for r in rows if isinstance(r, dict) and r.get("source") in REGISTRY]


def close_wanted(client, config):
    from .close_calls import KEY_NAME, SECRET_FILE, load_key
    return ["close"] if load_key(Path(config["projects_dir"]) / SECRET_FILE, KEY_NAME) else []


def connectors_wanted(client, config):
    from clients.tico import APIError
    from . import mail_key
    from .connectors import mail_secret_path
    mail_key.protect(config)
    if mail_secret_path(config).is_file():
        return ["mail", "calendar"]
    try:
        rows = (client.get("runners/connectors") or {}).get("connectors") or []
    except APIError as exc:
        if exc.status == 404:               # a hub from before the assignment: only the key counts
            return []
        raise
    return [r for r in rows if isinstance(r, str)]


def report_connectors(client, sources):
    pass                                    # the missing heartbeat shows the connector as stale in Settings


def report_importers(client, sources):
    for source in sources:
        client.post("imports/sources/" + source + "/status", {
            "state": "error", "error_code": "sync_error",
            "message": "The importers job on this computer stopped; it is restarting."})


def report_close(client, sources):
    client.post("imports/sources/close/status", {"state": "error", "error_code": "sync_error"})


# name -> (what makes it wanted, how a crash shows up in Settings). `wanted` returns the sources
# it would serve; an empty list means stop the job.
JOBS = {"importers": (importers_wanted, report_importers), "close-calls": (close_wanted, report_close),
        "connectors": (connectors_wanted, report_connectors)}


class Child:
    def __init__(self, process, started):
        self.process, self.started = process, started


class SideJobs:
    def __init__(self, config, config_path, *, client=None, jobs=None, spawn=None, clock=time.monotonic,
                 poll=None):
        from clients.tico import Client
        self.config, self.config_path = config, Path(config_path)
        self.client = client or Client(config["url"], config["token"], timeout=30, retries=1)
        self.jobs = JOBS if jobs is None else jobs
        # TICO_SIDE_JOBS_POLL exists so the container smoke test does not wait a minute per check.
        self.spawn, self.clock = spawn or self.popen, clock
        self.poll = poll or int(os.environ.get("TICO_SIDE_JOBS_POLL") or POLL_SECONDS)
        self.children, self.failures, self.retry_at, self.sources = {}, {}, {}, {}
        self.stopping = threading.Event()
        self.thread = None

    def popen(self, name):
        env = {k: v for k, v in os.environ.items() if k != "TICO_SIDE_JOBS"}
        return subprocess.Popen([sys.executable, "-m", "runner", "--config", str(self.config_path), name],
                                cwd=ROOT, env=env, stdin=subprocess.DEVNULL)

    def wanted(self, name):
        """The sources this job would serve, or None when the hub cannot be asked (keep what runs)."""
        try:
            return self.jobs[name][0](self.client, self.config)
        except Exception as exc:
            log("Tico side jobs: cannot tell whether " + name + " is wanted, " + describe(exc))
            return None

    def halt(self, name):
        child = self.children.pop(name, None)
        if not child or child.process.poll() is not None:
            return
        child.process.terminate()
        try:
            child.process.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            child.process.kill()
            child.process.wait()

    def crashed(self, name, code):
        child = self.children.pop(name)
        ran = self.clock() - child.started
        if code == 0:                       # it asked to restart on new code
            self.failures[name], delay = 0, 5
        else:
            self.failures[name] = 0 if ran >= STABLE_SECONDS else self.failures.get(name, 0) + 1
            delay = min(BACKOFF_MAX, BACKOFF_START * 2 ** max(self.failures[name] - 1, 0))
            try:
                self.jobs[name][1](self.client, self.sources.get(name) or [])
            except Exception:
                pass                        # Settings shows the job as stale instead
        self.retry_at[name] = self.clock() + delay
        log("Tico side jobs: " + name + " exited (" + str(code) + "), restarting in " + str(delay) + "s")

    def tick(self):
        for name in self.jobs:
            child = self.children.get(name)
            if child and child.process.poll() is not None:
                self.crashed(name, child.process.poll())
                child = None
            sources = self.wanted(name)
            if sources is None:
                continue
            self.sources[name] = sources
            if not sources:
                if child:
                    log("Tico side jobs: " + name + " is no longer assigned here; stopping it")
                    self.halt(name)
                continue
            if not child and self.clock() >= self.retry_at.get(name, 0):
                self.children[name] = Child(self.spawn(name), self.clock())
                log("Tico side jobs: started " + name + " (" + ", ".join(sources) + ")")

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:        # the bot runner must never depend on this loop
                log("Tico side jobs: " + describe(exc))
            self.stopping.wait(self.poll)
        for name in list(self.children):
            self.halt(name)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="side-jobs", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(STOP_GRACE * 2 + 5)
