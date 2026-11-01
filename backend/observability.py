"""Opt-in telemetry with a small, explicit wire contract and no ambient SDK state."""

import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path

from .config import DEFAULT_COMPANY_NAME, ROOT

ENVIRONMENTS = frozenset({"development", "staging", "production", "test"})
POSTHOG_HOSTS = frozenset({"https://us.i.posthog.com", "https://eu.i.posthog.com"})
SOURCE_FILES = frozenset(p.relative_to(ROOT).as_posix() for p in (ROOT / "backend").glob("*.py"))


def public_dsn(value):
    """Only public Sentry ingestion DSNs; never expose legacy password credentials."""
    if isinstance(value, str) and re.fullmatch(
        r"https://[a-fA-F0-9]+@(?:[a-z0-9-]+\.)*ingest(?:\.[a-z]{2})?\.sentry\.io/[0-9]+", value
    ):
        return value
    return ""


def release(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value) else ""


def browser_config(settings, actor):
    if (settings.observability_environment not in ENVIRONMENTS
            or len(settings.observability_id_secret) < 32):
        return {}
    host = settings.posthog_host.rstrip("/")
    key = settings.posthog_key
    if host not in POSTHOG_HOSTS or not re.fullmatch(r"phc_[A-Za-z0-9_-]+", key):
        key, host = "", ""
    dsn = public_dsn(settings.sentry_dsn)
    if not key and not dsn:
        return {}
    distinct_id = "tico:" + hmac.new(settings.observability_id_secret.encode(), actor.encode(),
                                     hashlib.sha256).hexdigest()
    return {"app": "tico", "environment": settings.observability_environment,
            "distinct_id": distinct_id, "posthog_key": key, "posthog_host": host,
            "sentry_dsn": dsn, "release": release(settings.release_id)}


def staff_display_name(c, who):
    """Only admitted staff metadata; normalized ID-title names lack provenance."""
    if who.role not in ("human", "owner") or not who.actor.startswith("human:"):
        return ""
    pid = who.actor[len("human:"):]
    human = c.execute("SELECT email FROM humans WHERE id=?", (pid,)).fetchone()
    if not human:
        return ""

    def bounded(value):
        if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return ""
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return ""
        value = value.strip()
        return value if 0 < len(value) <= 254 else ""

    row = c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    if row:
        try:
            roster = json.loads(row[0])
        except (ValueError, TypeError):
            roster = {}
        people = roster.get("people", []) if isinstance(roster, dict) else []
        people = people if isinstance(people, list) else []
        matches = [p for p in people if isinstance(p, dict) and p.get("id") == pid]
        if len(matches) == 1:
            name = bounded(matches[0].get("name"))
            # Initial import stores P.load(roster), which fills missing names with
            # pid.title(). Even a real name equal to that fallback is ambiguous.
            if name and name.casefold() != pid.title().casefold():
                return name
    email = bounded(who.email)
    if (email and email == who.email and email.lower() == str(human["email"] or "").lower()
            and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
        return email
    return ""


# Labels that come from code, never from data: an exception class, a function, a route template, the call site's source.
NAME = re.compile(r"[A-Za-z_<][A-Za-z0-9_.<>]{0,99}")
SOURCE = re.compile(r"[a-z][a-z_]{0,31}")
ROUTE = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /[A-Za-z0-9_{}/.:-]{0,200}")
# A Rust runner panic's location: a file of the runner's own crate.
RUNNER_FILE = re.compile(r"runner-rs/src/[a-z0-9_]+(?:/[a-z0-9_]+)*\.rs")
RUNNER_PANICS = 5        # reports kept from one runner post
# The hub's own limits on what a runner token can send, whatever the runner does: one report per file and line an
# hour, at most RUNNER_HOURLY reports per runner an hour.
RUNNER_REPEAT = 3600
RUNNER_HOURLY = 20


def instance(value):
    """The install's name as a short lowercase slug (`acme-co`), so one Sentry project can tell installs apart."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64] if isinstance(value, str) else ""


def route(request):
    """The matched route's template (`POST /api/v2/tasks/{tid}/comments`), never the path with its values."""
    found = getattr(request.scope.get("route"), "path", None)
    return f"{request.method} {found}" if found else ""


def sanitize_event(event, hint=None):
    """Rebuild rather than redact: SDK-added context never crosses this boundary."""
    tags = event.get("tags", {})
    source = tags.get("source")
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        return None
    clean = {"level": "error", "platform": "python", "tags": {"app": "tico", "source": source}}
    if tags.get("status") in {str(n) for n in range(500, 600)}:
        clean["tags"]["status"] = tags["status"]
    if tags.get("instance") and instance(tags["instance"]) == tags["instance"]:
        clean["tags"]["instance"] = tags["instance"]
    if isinstance(tags.get("route"), str) and ROUTE.fullmatch(tags["route"]):
        clean["tags"]["route"] = tags["route"]
    if release(tags.get("runner")):
        clean["tags"]["runner"] = tags["runner"]
    if event.get("environment") in ENVIRONMENTS:
        clean["environment"] = event["environment"]
    if release(event.get("release")):
        clean["release"] = release(event["release"])
    frames = []
    kind = ""
    for value in event.get("exception", {}).get("values", []):
        if isinstance(value.get("type"), str) and NAME.fullmatch(value["type"]):
            kind = value["type"]
        for frame in value.get("stacktrace", {}).get("frames", []):
            name = frame.get("filename")
            if ((name in SOURCE_FILES or (isinstance(name, str) and RUNNER_FILE.fullmatch(name)))
                    and type(frame.get("lineno")) is int and 0 < frame["lineno"] < 1_000_000):
                kept = {"filename": name, "lineno": frame["lineno"], "in_app": True}
                if isinstance(frame.get("function"), str) and NAME.fullmatch(frame["function"]):
                    kept["function"] = frame["function"]
                frames.append(kept)
    label = {"scheduler": "SchedulerError", "runner": "RunnerPanic"}.get(source, "RequestError")
    clean["exception"] = {"values": [{"type": kind or label, "stacktrace": {"frames": frames[-50:]}}]}
    # Preserve only the SDK-generated hexadecimal event identifier.
    if isinstance(event.get("event_id"), str) and re.fullmatch(r"[a-f0-9]{32}", event["event_id"]):
        clean["event_id"] = event["event_id"]
    return clean


def no_breadcrumb(crumb, hint=None):
    return None


class Observability:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        self.release = ""
        self.instance = ""
        self.runner_sent = {}     # (runner, file, line) -> when, then (runner,) -> [when, ...]
        self.runner_lock = threading.Lock()

    def start(self):
        dsn = public_dsn(self.settings.sentry_server_dsn)
        if not dsn or self.settings.observability_environment not in ENVIRONMENTS:
            return
        from .releases import version
        self.release = release(self.settings.release_id) or release(version())
        # Unnamed, the team's name; an install that never set one has the stock name, so its opaque id instead.
        named = self.settings.company_name if self.settings.company_name != DEFAULT_COMPANY_NAME else ""
        self.instance = (instance(self.settings.instance_name) or instance(named)
                         or instance(self.settings.environment_id))
        try:
            from sentry_sdk import Client
            self.client = Client(
                dsn=dsn, environment=self.settings.observability_environment,
                release=self.release or None,
                default_integrations=False, auto_enabling_integrations=False,
                send_default_pii=False, include_local_variables=False, include_source_context=False,
                max_breadcrumbs=0, traces_sample_rate=0, profiles_sample_rate=0,
                auto_session_tracking=False, send_client_reports=False,
                enable_logs=False, enable_metrics=False, server_name="", before_send=sanitize_event,
                before_breadcrumb=no_breadcrumb,
            )
        except Exception:
            self.client = None

    def capture(self, source, exc=None, status=None, route="", frames=None, runner=""):
        """Report one error, built from code labels only: never the exception's message or a frame's values.
        `frames` (filename, lineno) stand in for `exc`'s traceback when the error happened elsewhere (a runner)."""
        if self.client is None:
            return None
        try:
            frames = list(frames or [])
            tb = exc.__traceback__ if exc is not None else None
            while tb:
                try:
                    filename = Path(tb.tb_frame.f_code.co_filename).resolve().relative_to(ROOT).as_posix()
                except ValueError:
                    filename = ""
                if filename in SOURCE_FILES:
                    frames.append({"filename": filename, "lineno": tb.tb_lineno,
                                   "function": tb.tb_frame.f_code.co_name})
                tb = tb.tb_next
            event = {"tags": {"source": source, "status": str(status), "instance": self.instance,
                              "route": route, "runner": runner},
                     "environment": self.settings.observability_environment,
                     "release": self.release,
                     "exception": {"values": [{"type": type(exc).__name__ if exc is not None else "",
                                               "stacktrace": {"frames": frames}}]}}
            # Sanitize before handing anything to the SDK, and again at its final hook.
            clean = sanitize_event(event)
            if clean:
                return self.client.capture_event(clean)
        except Exception:
            pass
        return None

    def capture_runner(self, runner, version, panics, now=None):
        """A runner's panics, each its file and line in the runner's own source (runner-rs/src/panics.rs)."""
        if self.client is None:
            return
        now = time.monotonic() if now is None else now
        for panic in panics[:RUNNER_PANICS]:
            if not self.runner_allows(runner, panic.file, panic.line, now):
                continue
            frames = [{"filename": "runner-rs/" + panic.file, "lineno": panic.line}] if panic.file else []
            self.capture("runner", frames=frames, runner=version)

    def runner_allows(self, runner, file, line, now):
        with self.runner_lock:
            sent = self.runner_sent
            if len(sent) > 10_000:   # bounded memory: forget what is past both windows
                for key in [k for k, v in sent.items() if not isinstance(v, list) and now - v >= RUNNER_REPEAT]:
                    del sent[key]
            if now - sent.get((runner, file, line), -RUNNER_REPEAT) < RUNNER_REPEAT:
                return False
            hour = [t for t in sent.get((runner,), []) if now - t < 3600]
            if len(hour) >= RUNNER_HOURLY:
                sent[(runner,)] = hour
                return False
            sent[(runner, file, line)] = now
            sent[(runner,)] = hour + [now]
            return True

    def close(self):
        client, self.client = self.client, None
        if client:
            try:
                client.close(timeout=2)
            except Exception:
                pass


class TestError(RuntimeError):
    """Raised on purpose by `python -m backend.observability test`; nothing is wrong."""


def reporting_settings(env=None):
    """The settings error reporting reads, from the environment of a shell beside the server. `docker compose exec`
    does not see the entrypoint's exports, so this needs neither TICO_DB nor TICO_ENVIRONMENT_ID (Settings.from_env
    refuses both missing): the install's id is read, read-only, from its database as the entrypoint reads it."""
    import sqlite3
    from contextlib import closing
    from .config import Settings
    from .replication import rehearsal_on
    env = os.environ if env is None else env
    db = Path(env.get("TICO_DB") or Path(env.get("TICO_DATA_DIR") or "/data") / "hub.sqlite")
    environment_id = env.get("TICO_ENVIRONMENT_ID", "").strip()
    if not environment_id and db.is_file():
        try:
            with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as c:
                row = c.execute("SELECT value FROM registry_metadata WHERE key='docker-environment-id'").fetchone()
            environment_id = json.loads(row[0]) if row else ""
        except (sqlite3.Error, ValueError, TypeError):
            environment_id = ""
    company = env.get("TICO_TEAM_NAME", "").strip() or env.get("TICO_COMPANY_NAME", "").strip()
    return Settings(db_path=db, environment_id=environment_id if isinstance(environment_id, str) else "",
                    company_name=company or DEFAULT_COMPANY_NAME,
                    release_id=env.get("TICO_RELEASE", ""), instance_name=env.get("TICO_INSTANCE_NAME", "").strip(),
                    observability_environment=env.get("TICO_OBSERVABILITY_ENVIRONMENT", ""),
                    sentry_server_dsn="" if rehearsal_on(env) else env.get("TICO_SENTRY_SERVER_DSN", ""))


def main(argv=None):
    """`python -m backend.observability test`: send one harmless error, to see that reports arrive and how they look."""
    import argparse
    parser = argparse.ArgumentParser(prog="python -m backend.observability")
    parser.add_argument("command", choices=["test"])
    parser.parse_args(argv)
    telemetry = Observability(reporting_settings())
    telemetry.start()
    if telemetry.client is None:
        print("Error reporting is off: set TICO_SENTRY_SERVER_DSN and TICO_OBSERVABILITY_ENVIRONMENT, then restart.")
        return 1
    try:
        raise TestError("a test error with nothing in it")
    except TestError as exc:
        event_id = telemetry.capture("test", exc)
    telemetry.close()
    if not event_id:
        print("The test error was not sent.")
        return 1
    print(f"Sent test error {event_id} (release {telemetry.release or 'none'}, instance {telemetry.instance or 'none'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
