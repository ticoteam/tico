#!/usr/bin/env python3
"""Tico's own release windows (docs/releasing.md#release-windows). The canary (Tico Team) takes each green candidate
automatically. Stable (TIDY) takes a canary release only after someone approves its release notes.

    python3 scripts/release_train.py guard              should the release job run for this event (read-only)
    python3 scripts/release_train.py window             act: promote approved releases (a window opens, or the backstop
                                                        runs), then ship a green candidate or validate main
    python3 scripts/release_train.py promote            canary -> stable, when the canary's release is approved
    python3 scripts/release_train.py hotfix --reason "..." [--sha SHA] [--promote-stable true|false]
                                                [--override-hold true|false]
    python3 scripts/release_train.py status             the window, what canary and stable run, the approvals, the next step
    python3 scripts/release_train.py notes BASE HEAD    the release notes for HEAD since BASE (read-only)
    python3 scripts/release_train.py shots-into-approval VERSION FILE
                                                        put the pictures (release-shots' markdown FILE, or the failure
                                                        of its run) in the approval issue's section (idempotent)

The workflows (.github/workflows/train.yml, hotfix.yml) call it. Environment:

    GITHUB_TOKEN, GITHUB_REPOSITORY        the Actions token and repository
    GITHUB_EVENT_NAME                      schedule, push, workflow_run or workflow_dispatch
    SCHEDULE                               schedule only: the cron that fired (a window cron, or the backstop)
    RUN_EVENT, RUN_CONCLUSION, RUN_TITLE,  workflow_run only: the Release validation run that finished
    RUN_URL
    CANARY_URL, CANARY_UPDATE_KEY          the canary install (Tico Team) and its update key (docs/service-keys.md)
    STABLE_URL, STABLE_UPDATE_KEY          the stable install and its update key
    RELEASE_HOLD                           any value holds promotion to stable (a hotfix too, unless override_hold)
    RELEASE_TRAINS                         `on` lets schedules, pushes and finished runs act; a manual run always acts
    GITHUB_ACTOR                           hotfix only: who overrode a hold

Approval: each candidate that reaches the canary gets an open issue, "Release vX.Y.Z: awaiting approval" (label
release-approval), whose body is its release notes. A person with write access approves it by adding the `approved` label
or commenting `/approve` (a bot or app never counts); the `hold` label keeps it from stable. Promotion takes the canary's release to stable at a
window's open and at the backstop, only when it is approved, not held and healthy, and then closes its issue. A newer
release on the canary closes the older issues, with a link to the new one.

A release is published as not latest and marked latest (the GitHub release and the images' `latest` tags) only once the
canary runs it healthy, so new installs, update checks and desktop apps never get a release the canary has not run. The
image tags move with `docker buildx imagetools` (the workflow logs in to ghcr.io first).

Windows open at 07:30, 11:00, 14:00 and 20:30 Los Angeles time on weekdays, and at 07:30 and 15:00 on weekends. A window stays
open until a vX.Y.Z release is published after it. Nothing is stored: every run works the state out from
GitHub. Installs are read and updated only through the routes an update key reaches (GET and POST
/api/v2/system/update, POST /api/v2/system/update/check; backend/service_keys.py), never /healthz, which a proxy may not
route. The HTTP helper is the Release Manager's (templates/catalog/release-notes/software/rollout.py). Standard library
only.
"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ROLLOUT_PATH = ROOT / "templates/catalog/release-notes/software/rollout.py"
NOTES_PATH = ROOT / "scripts/release_notes.py"
LOS_ANGELES = ZoneInfo("America/Los_Angeles")
# Window starts in Los Angeles wall-clock time: every hour on the hour, 07:00 to 21:00, every day (15 a day).
WINDOWS = tuple((hour, 0) for hour in range(7, 22))
# GitHub's crons are UTC only: every UTC hour a window can start at, under PDT (UTC-7) or PST (UTC-8): 14:00 to 04:00
# UTC under PDT and 15:00 to 05:00 under PST, so 14 to 23 and 0 to 5 (train.yml lists the same ones).
CRONS = ["0 %d * * *" % hour for hour in (*range(14, 24), *range(0, 6))]
# A backstop for a missed event (a workflow_run that does not fire, say): it takes a step in an open window like a push
# does, never opens one, and promotes an approved canary release. Minutes 5, 25 and 45: never a window's minute (00 or 30).
BACKSTOP_CRON = "5-59/20 * * * *"
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TITLE = re.compile(r"^Validate ([0-9a-f]{40}) as (v\S+) from (v\S+)(?: \(incremental on ([0-9a-f]{40})\))?$")
UNFINISHED = {"queued", "pending", "waiting", "requested", "in_progress"}
# A candidate that did not pass: refused, or cancelled. Main's head is not validated again until the next push (or a
# manual run), and the run is reported once.
FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
ISSUE_LABEL = "release-train"
BLOCKER_LABEL = "release-blocker"
# ci.yml's ceiling: 10 full validations a UTC day, manual ones included. A run it stops is a hold, not a failure.
CEILING_HOLD = "Full runs: ceiling reached (10/day) · next after 00:00 UTC"
APPROVAL_LABEL = "release-approval"
APPROVED = "approved"
HOLD = "hold"
WRITE_LEVELS = ("admin", "maintain", "write")  # the repository roles that may approve
APPROVAL_TITLE = re.compile(r"^Release (v\d+\.\d+\.\d+): awaiting approval$")
APPROVE_FOOTER = (
    "---\n"
    "Approve: a person with write access adds the `approved` label or comments `/approve`. Hold: add the `hold` label.\n"
    "Stable (TIDY) takes an approved release at the next window or backstop run (every 20 minutes), unless it is held or a"
    " health check fails."
)
SHOTS_HEADING = "## What it looks like"
SHOTS_START, SHOTS_END = "<!-- release-shots:start -->", "<!-- release-shots:end -->"
SHOTS_PENDING = (
    "The before and after pictures are on their way; the release shots job adds them here when they are made."
)
SHOTS_SECTION = re.compile(re.escape(SHOTS_START) + r".*?" + re.escape(SHOTS_END), re.DOTALL)
RELEASE_WAIT = 90 * 60
VALIDATION_WAIT = 150 * 60
DISPATCH_WAIT = 120
UPDATE_WAIT = 30 * 60  # the updater pulls, snapshots, restarts and checks health, or rolls back
UPDATE_PATH = "/api/v2/system/update"
POLL = 20
REPUBLISH_AFTER = timedelta(minutes=30)  # a tag still unpublished this long after its Release run started
RELEASE_RUNS = 2  # the first Release run on a tag, and one more
IMAGES = ("tico", "tico-runner", "tico-updater")
REGISTRY = "ghcr.io/ticoteam"
SKEW = timedelta(seconds=60)  # GitHub's timestamps and this clock can differ a little


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass looks its module up in sys.modules while the module executes (Python 3.12).
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


INCREMENT_PATH = ROOT / "scripts/release_increment.py"
RED_JOB = re.compile(r"(python|browser)-\d+")  # the jobs whose logs name the tests that failed (release_increment)

rollout = _load("tico_rollout", ROLLOUT_PATH)
release_notes = _load("tico_release_notes", NOTES_PATH)
increment = _load("tico_release_increment", INCREMENT_PATH)


def sleep(seconds):
    """Every wait goes through here (tests replace it)."""
    time.sleep(seconds)


class Fail(Exception):
    """A step failed: it is reported on the release-train issue."""


# ---- versions, titles and the clock ----


def parse_version(tag):
    """(major, minor, patch) for a stable vX.Y.Z tag; None for anything else (prereleases included)."""
    match = STABLE_TAG.match(str(tag or ""))
    return tuple(int(part) for part in match.groups()) if match else None


def newest_stable(tags):
    stable = [tag for tag in tags if parse_version(tag)]
    return max(stable, key=parse_version) if stable else None


def next_patch(tag):
    major, minor, patch = parse_version(tag)
    return "v%d.%d.%d" % (major, minor, patch + 1)


def title(sha, version, previous, anchor=None):
    """Release validation's run-name; release.yml finds a run by its prefix. An incremental validation names its anchor."""
    if anchor:
        return "Validate %s as %s from %s (incremental on %s)" % (sha, version, previous, anchor)
    return "Validate %s as %s from %s" % (sha, version, previous)


def parse_title(text):
    match = TITLE.match(str(text or ""))
    return match.group(1, 2, 3) if match else None


def parse_anchor(text):
    """The anchor commit an incremental validation builds on, or None for a full one (or a title that is not one)."""
    match = TITLE.match(str(text or ""))
    return match.group(4) if match else None


def as_tag(release):
    plain = rollout.plain(release)
    return "v" + plain if plain else ""


def parse_time(text):
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cron_time(cron):
    """(hour, minute), in UTC, of a cron such as "30 14 * * *"."""
    minute, hour = cron.split()[:2]
    return int(hour), int(minute)


def window_starts_on(day):
    """The window starts on a Los Angeles calendar date, as UTC datetimes: the day type comes from that date."""
    return [
        datetime(day.year, day.month, day.day, hour, minute, tzinfo=LOS_ANGELES).astimezone(timezone.utc)
        for hour, minute in WINDOWS
    ]


def window_start(now):
    """The latest window start at or before now (UTC), by the Los Angeles date. Yesterday's first window is always at
    or before now, so there is always one."""
    now = now.astimezone(timezone.utc)
    today = now.astimezone(LOS_ANGELES).date()
    return max(start for day in (today - timedelta(days=1), today) for start in window_starts_on(day) if start <= now)


def next_window(now):
    """The first window start after now (UTC). It is never more than a day away."""
    now = now.astimezone(timezone.utc)
    today = now.astimezone(LOS_ANGELES).date()
    return min(start for day in (today, today + timedelta(days=1)) for start in window_starts_on(day) if start > now)


def window_cron_opens(cron, now):
    """Whether a window cron that fired at now opens a window: its own UTC time must be the latest window start at or
    before now. So a late cron still opens the window that is current, however late, and a superseded window opens
    nothing. The off-season twin of a cron (15:30 UTC is 08:30 PDT) is never a window start, so it opens nothing and
    does what a push does."""
    now = now.astimezone(timezone.utc) + SKEW
    hour, minute = cron_time(cron)
    fired = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if fired > now:
        fired -= timedelta(days=1)
    return window_start(now) == fired


def window_opens(env, now):
    """Whether this run opens a window: a manual run, or a window cron that is the current window's start."""
    event = env.get("GITHUB_EVENT_NAME", "")
    schedule = env.get("SCHEDULE", "")
    return event == "workflow_dispatch" or (
        event == "schedule" and schedule in CRONS and window_cron_opens(schedule, now)
    )


def is_backstop(env):
    return env.get("GITHUB_EVENT_NAME") == "schedule" and env.get("SCHEDULE") == BACKSTOP_CRON


def hhmm(when):
    """A window time as the Los Angeles clock shows it, such as 07:30 PST."""
    return when.astimezone(LOS_ANGELES).strftime("%H:%M %Z")


def reason_ok(reason):
    """A hotfix says why in a sentence: at least 15 characters and 3 words."""
    text = " ".join(str(reason or "").split())
    return len(text) >= 15 and len(text.split()) >= 3


# ---- GitHub ----


def validation_record(run):
    """A Release validation run as the incremental decisions read it (release_increment.anchor_of and red_since)."""
    parsed = parse_title(run.get("display_title"))
    run_id = run.get("id") if run.get("id") is not None else run.get("html_url")
    return {
        "id": run_id,
        "sha": parsed[0] if parsed else "",
        "previous": parsed[2] if parsed else "",
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "finished": parse_time(run.get("updated_at")),
        "url": run.get("html_url") or "",
        "incremental": parse_anchor(run.get("display_title")) is not None,
    }


def ceiling_stops(gh, runs, now):
    """The day's validation runs that ci.yml's ceiling stopped: failed runs created on this UTC day whose ceiling job said
    so. The count only grows until 00:00 UTC, so one stopped today means the day's ceiling is reached."""
    day = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return [
        run
        for run in runs
        if (run.get("created_at") or "").startswith(day)
        and run.get("conclusion") == "failure"
        and gh.ceiling_stopped(run)
    ]


class GitHub:
    """The few REST calls the release jobs make, with the Actions token."""

    def __init__(self, repo, token, api="https://api.github.com"):
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, method, path, body=None, token=None):
        url = path if path.startswith("http") else "%s/repos/%s/%s" % (self.api, self.repo, path.lstrip("/"))
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "tico-release-train",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token or self.token:
            headers["Authorization"] = "Bearer " + (token or self.token)
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read()
                    return response.status, (json.loads(raw) if raw else {}), response.headers.get("Link", "")
            except urllib.error.HTTPError as exc:
                with exc:
                    raw = exc.read()
                    if exc.code >= 500 and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    try:
                        return exc.code, json.loads(raw or b"{}"), ""
                    except ValueError:
                        return exc.code, {"message": raw[:300].decode("utf-8", "replace")}, ""
            except (urllib.error.URLError, OSError) as exc:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise Fail("GitHub %s %s: %s" % (method, path.split("?")[0], exc)) from exc
        raise Fail("GitHub %s %s did not answer" % (method, path))

    def get(self, path):
        code, data, _ = self.call("GET", path)
        if code != 200:
            raise Fail("GitHub GET %s: %s %s" % (path.split("?")[0], code, data.get("message", "")))
        return data

    def pages(self, path, key=None, limit=10):
        """Up to `limit` pages of a list, following Link: next."""
        items, url = [], path
        for _ in range(limit):
            code, data, link = self.call("GET", url)
            if code != 200:
                raise Fail("GitHub GET %s: %s %s" % (path.split("?")[0], code, data.get("message", "")))
            items.extend(data[key] if key else data)
            found = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
            if not found:
                break
            url = found.group(1)
        return items

    def tags(self):
        """Every v* tag name."""
        refs = self.pages("git/matching-refs/tags/v?per_page=100")
        return {ref["ref"].removeprefix("refs/tags/") for ref in refs}

    def head(self, ref="main"):
        return self.get("commits/" + urllib.parse.quote(ref, safe=""))["sha"]

    def compare(self, base, head):
        """GitHub's status of head against base: ahead, behind, identical or diverged."""
        return self.get("compare/%s...%s" % (base, head))["status"]

    def contains(self, ref, sha):
        """Whether `ref` (main) has `sha` in its history."""
        return self.compare(sha, ref) in ("ahead", "identical")

    def validation_runs(self):
        """Release validation runs dispatched on main, newest first (only these count as candidates)."""
        return self.get("actions/workflows/ci.yml/runs?branch=main&event=workflow_dispatch&per_page=100")[
            "workflow_runs"
        ]

    def ceiling_stopped(self, run):
        """Whether ci.yml's ceiling job stopped this validation run: that job failed on the ceiling's message (an API
        error would fail it too, so the message is what counts)."""
        for entry in self.pages("actions/runs/%d/jobs?per_page=100" % run["id"], key="jobs"):
            if entry.get("name") == "ceiling" and entry.get("conclusion") == "failure":
                return "ceiling reached" in self.job_log(entry["id"])
        return False

    def recent_runs(self, workflow):
        """A workflow's newest runs on any branch: whether one is still queued or running."""
        return self.get("actions/workflows/%s/runs?per_page=30" % workflow)["workflow_runs"]

    def workflow_runs(self, workflow, branch):
        query = urllib.parse.urlencode({"branch": branch, "per_page": 30})
        return self.get("actions/workflows/%s/runs?%s" % (workflow, query))["workflow_runs"]

    def dispatch(self, workflow, ref, inputs):
        code, data, _ = self.call("POST", "actions/workflows/%s/dispatches" % workflow, {"ref": ref, "inputs": inputs})
        if code not in (200, 204):
            raise Fail("dispatching %s on %s: %s %s" % (workflow, ref, code, data.get("message", "")))

    def job_log(self, job_id):
        """A job's log text, its last 2 MB (gh follows GitHub's redirect to the log file); "" when it cannot be read."""
        result = subprocess.run(
            ["gh", "api", "repos/%s/actions/jobs/%d/logs" % (self.repo, job_id)],
            capture_output=True,
            check=False,
            text=True,
            env={**os.environ, "GH_TOKEN": self.token or ""},
            timeout=180,
        )
        return result.stdout[-2 * 1024**2 :] if result.returncode == 0 else ""

    def failed_tokens(self, run_id):
        """The tests a red validation's failed jobs name (release_increment.failed_tokens); None when a failure cannot be
        named, so any later commit could be the fix."""
        jobs = self.pages("actions/runs/%d/jobs?per_page=100" % run_id, key="jobs")
        pairs = []
        for entry in jobs:
            name = entry.get("name") or ""
            if entry.get("conclusion") in FAILED and name not in increment.IGNORED_JOBS:
                pairs.append((name, self.job_log(entry["id"]) if RED_JOB.fullmatch(name) else ""))
        return increment.failed_tokens(pairs)

    def tag_commit(self, name):
        """The commit refs/tags/<name> points at (through an annotated tag), or None when there is no such tag."""
        code, data, _ = self.call("GET", "git/ref/tags/" + urllib.parse.quote(name, safe=""))
        if code == 404:
            return None
        if code != 200:
            raise Fail("GitHub GET git/ref/tags/%s: %s %s" % (name, code, data.get("message", "")))
        target = data.get("object") or {}
        if target.get("type") == "tag":
            target = self.get("git/tags/" + target.get("sha", "")).get("object") or {}
        return target.get("sha")

    def create_tag(self, name, sha):
        """refs/tags/<name> at sha. An existing tag at that commit is fine; at another commit it fails."""
        code, data, _ = self.call("POST", "git/refs", {"ref": "refs/tags/" + name, "sha": sha})
        if code == 201:
            return True
        if code == 422:
            existing = self.tag_commit(name)
            if existing == sha:
                return False
            if existing:
                raise Fail("%s already exists at another commit (%s)" % (name, existing[:12]))
        raise Fail("creating tag %s: %s %s" % (name, code, data.get("message", "")))

    def release(self, tag):
        code, data, _ = self.call("GET", "releases/tags/" + urllib.parse.quote(tag, safe=""))
        if code == 404:
            return None
        if code != 200:
            raise Fail("GitHub GET releases/tags/%s: %s %s" % (tag, code, data.get("message", "")))
        return data

    def latest_tag(self):
        """The tag of GitHub's latest release, or "" when there is none."""
        code, data, _ = self.call("GET", "releases/latest")
        if code == 404:
            return ""
        if code != 200:
            raise Fail("GitHub GET releases/latest: %s %s" % (code, data.get("message", "")))
        return data.get("tag_name") or ""

    def make_latest(self, release):
        code, data, _ = self.call("PATCH", "releases/%s" % release["id"], {"make_latest": "true"})
        if code != 200:
            raise Fail("marking %s latest: %s %s" % (release.get("tag_name"), code, data.get("message", "")))

    def issues(self, label, state="open"):
        query = urllib.parse.urlencode({"labels": label, "state": state, "per_page": 20})
        return [issue for issue in self.get("issues?" + query) if "pull_request" not in issue]

    def ensure_label(self, name, color, description):
        self.call("POST", "labels", {"name": name, "color": color, "description": description})  # 422: exists

    def create_issue(self, title_, body, labels):
        code, data, _ = self.call("POST", "issues", {"title": title_, "body": body, "labels": labels})
        if code != 201:
            raise Fail("opening the %s issue: %s %s" % (labels[0], code, data.get("message", "")))
        return data

    def edit(self, number, body, state):
        code, data, _ = self.call("PATCH", "issues/%d" % number, {"body": body, "state": state})
        if code != 200:
            raise Fail("updating #%d: %s %s" % (number, code, data.get("message", "")))

    def last_text(self, issue):
        """The issue's last comment, or its body when it has none."""
        count = int(issue.get("comments") or 0)
        if not count:
            return issue.get("body") or ""
        last = self.get("issues/%d/comments?per_page=1&page=%d" % (issue["number"], count))
        return (last[-1].get("body") or "") if last else ""

    def comment(self, number, body):
        code, data, _ = self.call("POST", "issues/%d/comments" % number, {"body": body})
        if code != 201:
            raise Fail("commenting on #%d: %s %s" % (number, code, data.get("message", "")))

    def close(self, number):
        self.call("PATCH", "issues/%d" % number, {"state": "closed", "state_reason": "completed"})

    def permission(self, login):
        """The repository role of a login, as the write levels it counts for: whether it may approve a release."""
        code, data, _ = self.call("GET", "collaborators/%s/permission" % urllib.parse.quote(login, safe=""))
        if code != 200:
            return False
        return data.get("permission") in WRITE_LEVELS or data.get("role_name") in WRITE_LEVELS


# ---- the record: job summary and the log the issue quotes ----

LOG = []


def note(message):
    """One line for the log, the job summary and the release-train issue."""
    LOG.append(message)
    print(message, file=sys.stderr, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write("- " + message + "\n")


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            for key, value in values.items():
                handle.write("%s=%s\n" % (key, value))


def run_url():
    return "%s/%s/actions/runs/%s" % (
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )


# ---- installs ----


def install(role, env):
    """{"name", "url", "key_env"} for CANARY or STABLE, or None when its URL or update key is not set yet."""
    url = (env.get(role + "_URL") or "").strip()
    key_env = role + "_UPDATE_KEY"
    if not url or not (env.get(key_env) or "").strip():
        return None
    return {"name": role.lower(), "url": url, "key_env": key_env, "canary": False}


def missing(role):
    return "%s install not configured (environment `release`: variable %s_URL, secret %s_UPDATE_KEY)" % (
        role.lower(),
        role,
        role,
    )


def update_status(target):
    """GET /api/v2/system/update with the install's update key: the release it runs (`running`), the last update's
    `state` and its release `history`. It is the only read an update key may make (backend/service_keys.py), and an
    answer means the server is up behind its proxy; None when it does not answer 200."""
    try:
        code, status = rollout.call(target, "GET", UPDATE_PATH)
    except rollout.Stop as exc:
        raise Fail(str(exc.args[0])) from exc
    return status if code == 200 and isinstance(status, dict) else None


def running(target):
    status = update_status(target)
    return rollout.plain(status.get("running")) if status else ""


BUSY = ("pulling", "restarting", "uncertain")


def update_install(target, version, timeout=UPDATE_WAIT):
    """Update to the version through the install's updater and wait until it reports healthy on it (30 minutes); a
    rollback or failure fails the step. Only the update key's routes are used, never /healthz.

    A rollback or failure counts only once this update is seen under way (the updater answers the request with
    `pulling`, then restarts the server; or its state, target or message is no longer what it said before the request):
    an earlier update's outcome, even one to the same version, is never judged."""
    want, name = rollout.plain(version), target["name"]
    snapshot = update_status(target)
    before = rollout.plain((snapshot or {}).get("running"))
    if not before:
        raise Fail("%s: its update status cannot be read (GET %s with the update key)" % (name, UPDATE_PATH))
    if before == want:
        note("%s already runs v%s" % (name, want))
        return
    try:
        rollout.call(target, "POST", UPDATE_PATH + "/check")  # 429 within a minute of another check is fine
        code, started = rollout.call(target, "POST", UPDATE_PATH, {"version": want})
    except rollout.Stop as exc:
        raise Fail(str(exc.args[0])) from exc
    if code != 200:
        raise Fail("%s refused the update to v%s (%s): %s" % (name, want, code, json.dumps(started)[:300]))
    note("%s updating v%s -> v%s" % (name, before, want))
    started = isinstance(started, dict) and started.get("state") in BUSY and rollout.plain(started.get("to")) == want
    deadline, state = time.monotonic() + timeout, ""
    while time.monotonic() < deadline:
        sleep(POLL)
        status = update_status(target)
        if status is None:
            started = True  # the server is restarting
            continue
        state = status.get("state") or ""
        if (rollout.plain(status.get("to")) == want and state in BUSY) or any(
            status.get(field) != snapshot.get(field) for field in ("state", "to", "message")
        ):
            started = True  # not what the updater said before the request
        if started and rollout.plain(status.get("to")) == want and state in ("rolled_back", "failed"):
            raise Fail("%s: update to v%s %s (%s)" % (name, want, state, status.get("message") or ""))
        if (
            state == "healthy"
            and rollout.plain(status.get("to")) == want
            and rollout.plain(status.get("running")) == want
        ):
            note("%s updated v%s -> v%s, healthy" % (name, before, want))
            return
    raise Fail(
        "%s did not report v%s healthy within %d minutes (last state %s)"
        % (name, want, timeout // 60, state or "unknown")
    )


def run_command(argv, timeout=180):
    """(exit code, output) of a command (tests replace it)."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stderr or done.stdout or "").strip()


def mark_latest(gh, version):
    """Once the canary runs `version` healthy: point the images' `latest` tags at it and then (only when all three
    moved) make it the latest GitHub release, which is what new installs, update checks and desktop apps follow. Safe to
    run again. Returns False, and changes nothing, when a newer release is already latest (a tag pushed meanwhile)."""
    current = gh.latest_tag()
    if parse_version(current) and parse_version(current) > parse_version(version):
        note("Not marking %s latest: %s, a newer release, already is" % (version, current))
        return False
    for image in IMAGES:
        source, latest = "%s/%s:%s" % (REGISTRY, image, version), "%s/%s:latest" % (REGISTRY, image)
        for attempt in range(4):
            code, out = run_command(["docker", "buildx", "imagetools", "create", "-t", latest, source])
            if code == 0:
                break
            sleep(5 * (attempt + 1))
        else:
            raise Fail("pointing %s at %s: %s" % (latest, version, out[-300:]))
    found = gh.release(version)
    if not found or found.get("draft"):
        raise Fail("marking %s latest: no published release" % version)
    gh.make_latest(found)
    note("Marked %s latest (the GitHub release and the images' latest tags)" % version)
    return True


def lagging_latest(gh):
    """The newest vX.Y.Z when it is published but GitHub's latest release is an older one (a mark_latest that failed or
    did not finish), else None."""
    newest = newest_stable(gh.tags())
    found = gh.release(newest) if newest else None
    if not found or found.get("draft") or not found.get("published_at"):
        return None
    current = gh.latest_tag()
    if parse_version(current) and parse_version(current) >= parse_version(newest):
        return None
    return newest


def catch_up_latest(gh, env):
    """Mark the newest release latest if an earlier step published it, the canary runs it healthy, and the marking
    failed or did not finish."""
    version = lagging_latest(gh)
    if not version:
        return
    canary = install("CANARY", env)
    status = update_status(canary) if canary else None
    if (
        not status
        or rollout.plain(status.get("running")) != rollout.plain(version)
        or status.get("state") not in ("healthy", "idle")
    ):
        note(
            "%s is published but not latest; the canary does not run it healthy (%s), so it stays that way"
            % (version, "%s, %s" % (status.get("running"), status.get("state")) if status else "no status")
        )
        return
    note("%s is published and healthy on the canary but not latest: marking it again" % version)
    mark_latest(gh, version)


# ---- the window: what to do now ----


def event_run(env):
    """workflow_run only: the Release validation run that just finished, as {conclusion, title, url}."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_run" or env.get("RUN_EVENT") != "workflow_dispatch":
        return None
    return {
        "conclusion": env.get("RUN_CONCLUSION", ""),
        "title": env.get("RUN_TITLE", ""),
        "url": env.get("RUN_URL", ""),
    }


def unpublished(gh, now, tag, plan):
    """The newest tag has no published release: its Release workflow is running, or it failed. One that failed is run
    again once, 30 minutes after the first started (the window job ships it as if it were new: Release workflow, canary,
    latest); after two runs it is reported for a person (docs/releasing.md#a-release-that-did-not-publish)."""
    runs = gh.workflow_runs("release.yml", tag)
    busy = next((run for run in runs if run.get("status") != "completed"), None)
    if busy:
        plan["why"] = "%s is tagged and its Release workflow is running: %s" % (tag, busy.get("html_url"))
        return plan
    links = ", ".join(run.get("html_url") or "" for run in runs) or "no Release run"
    if len(runs) >= RELEASE_RUNS:
        plan["stuck"] = (
            "%s is tagged but its release did not publish after %d Release runs (%s); publish it by hand: "
            "docs/releasing.md#a-release-that-did-not-publish" % (tag, len(runs), links)
        )
        plan["why"] = plan["stuck"]
        return plan
    started = [when for when in (parse_time(run.get("created_at")) for run in runs) if when]
    if started and now - min(started) < REPUBLISH_AFTER:
        plan["why"] = "%s is tagged but not published (%s); its Release workflow runs again at %s" % (
            tag,
            links,
            hhmm(min(started) + REPUBLISH_AFTER),
        )
        return plan
    sha = gh.tag_commit(tag)
    if not sha:
        raise Fail("%s: the tag is gone" % tag)
    earlier = [name for name in gh.tags() if parse_version(name) and parse_version(name) < parse_version(tag)]
    plan.update(
        action="ship",
        version=tag,
        sha=sha,
        previous=newest_stable(earlier) or "",
        republish=True,
        why=("%s is tagged at %s but not published (%s): starting its Release workflow again" % (tag, sha[:12], links)),
    )
    return plan


def plan_step(gh, now, finished=None, manual=False, checkout=None):
    """What the open window does next, worked out from GitHub alone (and, for an incremental validation, the checkout's
    diff). Returns {"action": "none" | "wait" | "ship" | "dispatch", "why", ...}: "red" is a candidate of this window that
    did not pass, "stuck" a tag whose release did not publish, and "held" a full validation that a cancelled or unnamed-red
    run in the last 24 hours holds back, all to report. A manual run validates main's head again even after a red or
    cancelled run. A dispatch is a full validation (mode "full") or an incremental one on the anchor (mode "incremental",
    with its plan): docs/releasing.md#incremental-validation. At most one full validation is dispatched automatically in
    24 hours, whatever its outcome."""
    tags = gh.tags()
    newest = newest_stable(tags)
    if not newest:
        raise Fail("no vX.Y.Z tag to release after")
    start = window_start(now)
    # previous: the release a candidate is compared with (the newest tag, unless the candidate is a tag being published again)
    plan = {
        "action": "none",
        "newest": newest,
        "previous": newest,
        "start": start,
        "red": None,
        "held": None,
        "stuck": None,
    }
    published = gh.release(newest)
    if not published or published.get("draft"):
        return unpublished(gh, now, newest, plan)
    published_at = parse_time(published.get("published_at"))
    if published_at is None:
        plan["why"] = "%s has no publication time; holding" % newest
        return plan
    if published_at >= start:
        plan["why"] = "window closed: %s was published after the %s window opened; the next opens at %s" % (
            newest,
            hhmm(start),
            hhmm(next_window(now)),
        )
        return plan
    version = next_patch(newest)
    plan["version"] = version
    runs = gh.validation_runs()
    # A run the ceiling stopped ran no suite: it is no red candidate, no anchor and no full run.
    stopped = ceiling_stops(gh, runs, now)
    stopped_urls = {run.get("html_url") for run in stopped}
    if finished and finished["conclusion"] in FAILED and finished["url"] not in stopped_urls:
        parsed = parse_title(finished["title"])
        if parsed and parsed[1:] == (version, newest):
            plan["red"] = {"sha": parsed[0], "conclusion": finished["conclusion"], "url": finished["url"]}
    head = gh.head("main")
    plan["sha"] = head
    if gh.compare(newest, head) != "ahead":
        plan["why"] = "window open since %s; main is at %s, nothing new" % (hhmm(start), newest)
        return plan
    head_failed = None
    records = []  # every release's validations, newest first: the anchor and the red validation are read from them
    for run in runs:
        parsed = parse_title(run.get("display_title"))
        if not parsed or run.get("html_url") in stopped_urls:
            continue
        records.append(validation_record(run))
        if parsed[1:] != (version, newest) or run.get("status") != "completed":
            continue  # an earlier release's validation can anchor or supersede a red one, but ships nothing here
        sha = parsed[0]
        if run.get("conclusion") == "success":
            finished_at = parse_time(run.get("updated_at"))
            # The first green candidate of this window ships: one that finished after it opened, or main's head.
            if (
                (sha == head or (finished_at and finished_at >= start))
                and gh.compare(newest, sha) == "ahead"
                and gh.contains("main", sha)
            ):
                plan.update(
                    action="ship",
                    sha=sha,
                    run=run,
                    why="%s at %s is green: %s" % (version, sha[:12], run.get("html_url")),
                )
                return plan
        elif sha == head and head_failed is None and run.get("conclusion") in FAILED:
            head_failed = run
    if head_failed and not manual and not plan["red"]:
        plan["red"] = {"sha": head, "conclusion": head_failed.get("conclusion"), "url": head_failed.get("html_url")}
    if stopped:
        # The ceiling holds full validations until 00:00 UTC; a real red above is still reported, a stop is only a hold.
        plan.update(action="wait", why="window open since %s; %s" % (hhmm(start), CEILING_HOLD))
        return plan
    busy = next((run for run in gh.recent_runs("ci.yml") if run.get("status") in UNFINISHED), None)
    if busy:
        plan.update(action="wait", why="window open since %s; waiting for %s" % (hhmm(start), busy.get("html_url")))
        return plan

    def main_has(sha):
        return gh.contains("main", sha)

    def named(record):
        return gh.failed_tokens(record["id"])

    anchor = increment.anchor_of(records, now, main_has, named)
    # The open reds: the anchor when it is red and no green validation since supersedes it, then the newest red since it.
    reds = [anchor] if increment.anchor_open(records, anchor, main_has) else []
    newer = increment.red_since(records, anchor, main_has)
    if newer is not None:
        reds.append({**newer, "failed": named(newer)})
    # The anchor may validate an earlier release: its product part (the upgrade journey) then starts from its previous.
    previous_changed = anchor is not None and anchor["previous"] != newest
    if not manual:
        # Each open red runs again only once a commit since it selects one of its failures (or names none: any commit).
        for red in reds:
            since = checkout.tokens_since(red["sha"], head) if checkout is not None and red["sha"] != head else None
            go, why = increment.red_rule(red, head, since)
            if not go:
                plan.update(action="wait", why="window open since %s; %s" % (hhmm(start), why))
                return plan
    # At most one full validation is dispatched automatically in 24 hours, whatever its outcome: a cancelled or
    # unnamed-red one waits for a person (a manual run still dispatches). The chain of incrementals still runs a full
    # validation once the newest full one is a day old.
    full_recent = increment.full_within_day(records, now)
    if anchor is None or (full_recent is None and not manual):
        if full_recent is not None and not manual:
            text = (
                "a full validation already ran in the last 24h (%s, %s): a person runs Release train by hand to "
                "validate again" % (full_recent["url"], full_recent["conclusion"])
            )
            plan.update(action="wait", held=text, why="window open since %s; %s" % (hhmm(start), text))
            return plan
        plan.update(
            action="dispatch",
            mode="full",
            anchor=None,
            carried=None,
            why="validate %s as %s from %s: a full validation (no full validation in the last 24 hours ran the suite to "
            "an answer, of any release)" % (head[:12], version, newest),
        )
        return plan
    carried = sorted(set().union(*(red["failed"] or set() for red in reds)))
    if checkout is not None:
        # The plan is made in Release validation from its own checkout; here it is only checked to be makeable.
        try:
            checkout.plan(anchor["sha"], head, set(carried), previous_changed=previous_changed)
        except Exception as exc:  # noqa: BLE001 - a plan that cannot be made waits: a second full validation is not an option
            note("Incremental plan failed: %s: %s" % (type(exc).__name__, exc))
            plan.update(
                action="wait",
                why="window open since %s; the incremental plan on %s cannot be made (%s: %s)"
                % (hhmm(start), anchor["sha"][:12], type(exc).__name__, exc),
            )
            return plan
    plan.update(
        action="dispatch",
        mode="incremental",
        anchor=anchor["sha"],
        anchor_previous=anchor["previous"],
        carried=carried,
        why="validate %s as %s from %s incrementally on %s%s%s"
        % (
            head[:12],
            version,
            newest,
            anchor["sha"][:12],
            "; carries %s" % increment.names(carried) if carried else "",
            ("; the product part runs: the previous release is %s, the anchor's was %s" % (newest, anchor["previous"]))
            if previous_changed
            else "",
        ),
    )
    return plan


def problems(plan):
    """The lines a plan reports on the release-train issue: the same text for the same problem, so it is posted once."""
    lines = []
    red = plan.get("red")
    if red:
        lines.append(
            "Candidate %s at %s ended %s: %s" % (plan["version"], red["sha"][:12], red["conclusion"], red["url"])
        )
    if plan.get("held"):
        lines.append(plan["held"])
    if plan.get("stuck"):
        lines.append(plan["stuck"])
    return lines


def hotfix_busy(gh):
    return next((run for run in gh.recent_runs("hotfix.yml") if run.get("status") in UNFINISHED), None)


def cmd_guard(gh, env, now):
    """Read-only, outside the release concurrency group: decides whether the release job runs at all, so a push that
    finds nothing to do never enters the `release` environment or queues behind (or displaces) a hotfix."""
    event = env.get("GITHUB_EVENT_NAME", "")
    schedule = env.get("SCHEDULE", "")
    opening = window_opens(env, now)
    go = False
    if event != "workflow_dispatch" and env.get("RELEASE_TRAINS", "").strip() != "on":
        note("Release windows are off (repository variable RELEASE_TRAINS is not `on`)")
    else:
        busy = hotfix_busy(gh)
        if busy:
            note("A hotfix is running (%s); it reports itself, and the next event acts after it" % busy.get("html_url"))
            opening = False
        elif opening:
            go = True
            note("Window step: %s" % ("window opens (%s)" % schedule if event == "schedule" else "manual run"))
        else:
            plan = plan_step(gh, now, event_run(env))
            lines = problems(plan)
            go = plan["action"] in ("ship", "dispatch") or bool(lines and not reported(gh, lines))
            note("Window step: %s" % plan["why"])
            # The guard cannot read the canary (no environment), so only the backstop checks a lagging latest and an
            # approved release waiting for stable: at most every 20 minutes. The window job decides on the canary.
            if not go and is_backstop(env):
                lagging = lagging_latest(gh)
                pending = None if lagging else pending_approval(gh)
                if lagging or pending:
                    go = True
                    note(
                        "Window step: %s"
                        % (
                            "%s is published but not latest" % lagging
                            if lagging
                            else "%s is approved for stable" % pending
                        )
                    )
    output(go=str(go).lower())
    return 0


# ---- publishing ----


def publish(gh, sha, version):
    """Tag the validated commit, start release.yml on the tag and wait for the GitHub release (not marked latest: that
    waits for the canary, mark_latest)."""
    since = datetime.now(timezone.utc)
    created = gh.create_tag(version, sha)
    note("Tagged %s at %s" % (version, sha[:12]) if created else "%s was already tagged at %s" % (version, sha[:12]))
    running = any(run.get("status") != "completed" for run in gh.workflow_runs("release.yml", version))
    if gh.release(version) is None and not running:
        # A tag made with the Actions token starts no workflow, so release.yml runs from the tag by dispatch: the same
        # ref, commit and validation lookup as a tag push. desktop=false keeps its build-only-if-changed rule, and
        # latest=false leaves the latest release and image tags where they are until the canary is healthy.
        gh.dispatch("release.yml", version, {"desktop": "false", "latest": "false"})
        note("Started the Release workflow on %s" % version)
    return wait_release(gh, version, since - SKEW)


def wait_release(gh, version, since, timeout=RELEASE_WAIT):
    deadline = time.monotonic() + timeout
    while True:
        found = gh.release(version)
        if found and not found.get("draft"):
            note("Published %s: %s" % (version, found.get("html_url")))
            return found
        for run in gh.workflow_runs("release.yml", version):
            started = parse_time(run.get("created_at"))
            if run.get("status") == "completed" and run.get("conclusion") in FAILED and started and started >= since:
                raise Fail(
                    "the Release workflow for %s ended %s: %s" % (version, run.get("conclusion"), run.get("html_url"))
                )
        if time.monotonic() >= deadline:
            raise Fail("no GitHub release for %s after %d minutes" % (version, timeout // 60))
        sleep(POLL)


def dispatch_candidate(
    gh, sha, version, previous, timeout=DISPATCH_WAIT, anchor=None, carried=None, anchor_previous=""
):
    """Dispatch Release validation and wait until GitHub lists the run, so the next release job (which runs after
    this one, in the same concurrency group) sees it running and does not dispatch it again. An incremental one names
    the anchor it builds on, the failed tests it carries (`carried`) and the release the anchor validated against; it
    makes its own plan from its checkout, so the dispatch stays small. A full one carries no extra inputs."""
    inputs = {"candidate_sha": sha, "previous_release": previous, "version": version}
    if anchor is not None:
        inputs.update(
            anchor=anchor,
            carried=json.dumps(sorted(carried or [])),
            anchor_previous=anchor_previous or "",
        )
    want, since = (
        title(sha, version, previous, anchor),
        datetime.now(timezone.utc) - SKEW,
    )
    gh.dispatch("ci.yml", "main", inputs)
    note("Dispatched Release validation: " + want)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for run in gh.validation_runs():
            started = parse_time(run.get("created_at"))
            if run.get("display_title") == want and started and started >= since:
                note("Validation run: %s" % run.get("html_url"))
                return run
        sleep(POLL)
    note("The dispatched validation was not listed within %d s; the next event finds it" % timeout)
    return None


# ---- approval: stable takes a canary release only once someone approves its notes ----


def approval_title(version):
    return "Release %s: awaiting approval" % version


def find_approval(gh, version, state="open"):
    """The approval record of a version (open unless state says otherwise), or None."""
    want = approval_title(version)
    return next((issue for issue in gh.issues(APPROVAL_LABEL, state) if issue.get("title") == want), None)


def open_approvals(gh):
    """The open approval records, oldest version first, as (version, issue)."""
    found = []
    for issue in gh.issues(APPROVAL_LABEL):
        match = APPROVAL_TITLE.match(str(issue.get("title") or ""))
        if match:
            found.append((match.group(1), issue))
    return sorted(found, key=lambda item: parse_version(item[0]) or (0, 0, 0))


def approver_of(gh, account):
    """The login of an account that may approve: a person (a GitHub user, not a bot or an app) with write access.
    "" for anything else, so a bot or app with write access cannot approve a release."""
    login = str(account.get("login") or "")
    if account.get("type") != "User" or not login or login.endswith("[bot]"):
        return ""
    return login if gh.permission(login) else ""


def approval_state(gh, issue):
    """(who approved, held). The approver is the approver_of the account that added the `approved` label, or of the
    account that commented `/approve` (the newest such comment that counts); "" when neither did. `held` is the hold label."""
    names = {str(label.get("name") or "") for label in issue.get("labels") or []}
    number = issue["number"]
    approver = ""
    if APPROVED in names:
        for event in reversed(gh.pages("issues/%d/events?per_page=100" % number)):
            if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == APPROVED:
                approver = approver_of(gh, event.get("actor") or {})
                break
    if not approver:
        for comment in reversed(gh.pages("issues/%d/comments?per_page=100" % number)):
            lines = str(comment.get("body") or "").strip().splitlines()
            if lines and lines[0].strip().lower() == "/approve":
                approver = approver_of(gh, comment.get("user") or {})
                if approver:
                    break
    return approver, HOLD in names


def pending_approval(gh):
    """The oldest open approval record that is approved and not held, or None: stable may take it."""
    for version, issue in open_approvals(gh):
        approver, held = approval_state(gh, issue)
        if approver and not held:
            return version
    return None


def shots_block(section):
    """The pictures section between the markers with_shots replaces. `section` is markdown that starts with its heading."""
    return "\n".join([SHOTS_START, section.strip(), SHOTS_END])


def approval_body(version, sha, notes, validated):
    head = "Candidate %s at %s (validated: %s)" % (version, sha[:12], validated or "not recorded")
    pending = shots_block("%s\n\n%s" % (SHOTS_HEADING, SHOTS_PENDING))
    return "\n".join([head, "", pending, "", notes.rstrip(), "", APPROVE_FOOTER]) + "\n"


def with_shots(body, section):
    """The approval body with its pictures section: the section between the markers is replaced, or, in a body without
    them, goes under the candidate line. The same section twice gives the same body."""
    block = shots_block(section)
    if SHOTS_SECTION.search(body):
        return SHOTS_SECTION.sub(lambda _found: block, body, count=1)
    head, _, rest = body.partition("\n")
    return head + "\n\n" + block + "\n" + rest


def open_approval(gh, version, sha, notes, validated=""):
    """The approval record of a candidate the canary is about to run: created, or updated and reopened when an earlier
    attempt made it. Its labels exist first. Returns the issue."""
    gh.ensure_label(APPROVAL_LABEL, "0e8a16", "A release waits for someone to approve its notes")
    gh.ensure_label(APPROVED, "0e8a16", "Stable (TIDY) may take this release")
    gh.ensure_label(HOLD, "b60205", "Holds this release from stable (TIDY)")
    body = approval_body(version, sha, notes, validated)
    found = find_approval(gh, version, state="all")
    if found:
        # A republish or a retry keeps the pictures already on the record; only a new record starts with them pending.
        gh.edit(found["number"], kept_shots(body, found.get("body")), "open")
        return found
    return gh.create_issue(approval_title(version), body, [APPROVAL_LABEL])


def kept_shots(body, old):
    """`body` with the pictures section that `old` (an approval record's body) has, in place of its pending line; `body`
    as it is when `old` has no section."""
    kept = SHOTS_SECTION.search(old or "")
    if not kept:
        return body
    section = kept.group(0)
    return SHOTS_SECTION.sub(lambda _found: section, body, count=1)


def retire_approvals(gh, version, text, *, inclusive):
    """Closes the open approval records older than `version` (and `version`'s own, when inclusive), with a comment."""
    for old, issue in open_approvals(gh):
        older = parse_version(old) < parse_version(version)
        if older or (inclusive and old == version):
            gh.comment(issue["number"], text)
            gh.close(issue["number"])
            note("Closed the approval for %s: %s" % (old, " ".join(text.split())))


def drop_approval(gh, issue, reason):
    """Closes the approval record of a candidate the canary did not reach: there is nothing to approve."""
    gh.comment(issue["number"], "Not on the canary, so there is nothing to approve: %s" % " ".join(str(reason).split()))
    gh.close(issue["number"])


def release_base(env, previous):
    """What a candidate is compared with: the release stable (TIDY) runs now, which is what its approver reads the changes
    since and what its release shots compare. The previous tag when stable's release cannot be read or stable is not
    configured."""
    stable = install("STABLE", env)
    if not stable:
        return previous
    try:
        release = as_tag(running(stable))
    except Fail as exc:
        note("Base: stable's release cannot be read (%s); compared with %s" % (exc, previous))
        return previous
    return release if parse_version(release) else previous


def release_notes_for(gh, env, head, previous):
    """The notes for head since release_base, so an approver sees everything stable would take."""
    return release_notes.build(gh, release_base(env, previous), head)


# ---- the release shots: the pictures on a candidate's approval issue ----


def shots_section(markdown, url):
    """The section for the approval issue: the markdown release-shots wrote, or, when the shots left none, that the
    pictures could not be made, with the run that tried."""
    text = (markdown or "").strip()
    if not text:
        failed = (
            "The before and after pictures could not be made: the release shots job did not finish. Its run: " + url
        )
        return SHOTS_HEADING + "\n\n" + failed
    return text if text.startswith(SHOTS_HEADING) else SHOTS_HEADING + "\n\n" + text


def cmd_shots_into_approval(gh, version, path):
    """Puts a candidate's pictures in its approval issue: release-shots' markdown at path, or the failure with this run when
    there is none. The section replaces the one it put before, so running it again changes nothing."""
    try:
        markdown = Path(path).read_text()
    except OSError:
        markdown = ""
    found = find_approval(gh, version, state="all")
    if not found:
        raise Fail("no approval record for %s to put its pictures in" % version)
    body = found.get("body") or ""
    updated = with_shots(body, shots_section(markdown, run_url()))
    if updated == body:
        note("The pictures on #%s are already current" % found["number"])
        return 0
    gh.edit(found["number"], updated, found["state"])  # the issue keeps its state: a closed record stays closed
    note("Put the pictures on #%s (%s)" % (found["number"], "made" if markdown.strip() else "not made: see the run"))
    return 0


def approval_waits(approval, approver, held):
    """Why the canary's release may not go to stable yet, from its approval record (None when there is none)."""
    if approval is None:
        return ["no open approval for this release"]
    number = approval.get("number")
    if held:
        return ["#%s is on hold" % number]
    if not approver:
        return ["#%s is not approved yet" % number]
    return []


# ---- promote: canary -> stable ----


def promotion_gate(*, version, canary_status, approval_waits, hold, blockers):
    """Whether stable may take what the canary runs. Returns (waits, faults): waits hold the promotion quietly (no
    approval yet, the approval is on hold, RELEASE_HOLD, a release-blocker issue); faults hold it and are reported.
    Anything this cannot tell (no answer, an unknown state) is a fault, never a pass."""
    waits, faults = list(approval_waits), []
    if not canary_status:
        faults.append("the canary's update status cannot be read")
    else:
        state = canary_status.get("state") or ""
        if rollout.plain(canary_status.get("running")) != rollout.plain(version):
            faults.append(
                "the canary's update status says it runs %s, not %s"
                % (rollout.plain(canary_status.get("running")) or "nothing", rollout.plain(version))
            )
        elif state not in ("healthy", "idle"):
            faults.append(
                "the canary's last update is %s: %s" % (state or "unknown", canary_status.get("message") or "")
            )
    return waits + holds(hold, blockers), faults


def holds(hold, blockers):
    """What holds stable where it is: the RELEASE_HOLD variable and open release-blocker issues."""
    found = ["RELEASE_HOLD is set: %s" % hold.strip()] if (hold or "").strip() else []
    return found + ["release-blocker #%s is open: %s" % (issue.get("number"), issue.get("title")) for issue in blockers]


def promote(gh, env):
    """Canary -> stable, when the canary runs a stable release ahead of stable's, that release is approved and not held,
    and the canary runs it healthy. Stable's update closes the release's approval record, and so do older ones."""
    canary, stable = install("CANARY", env), install("STABLE", env)
    if not canary or not stable:
        note("Promote: skipped, " + missing("CANARY" if not canary else "STABLE"))
        return
    status = update_status(canary)
    canary_release = rollout.plain((status or {}).get("running"))
    if not canary_release:
        raise Fail("promote: holding, the canary's update status cannot be read")
    stable_release = running(stable)
    if not stable_release:
        raise Fail("promote: holding, stable's update status cannot be read")
    want, have = as_tag(canary_release), as_tag(stable_release)
    if want == have:
        note("Promote: stable already runs %s" % want)
        retire_approvals(gh, want, "Stable (TIDY) already runs %s." % want, inclusive=True)
        return
    if not parse_version(want) or not parse_version(have) or parse_version(want) <= parse_version(have):
        note("Promote: skipped, the canary's %s is not a stable release ahead of stable's %s" % (want, have))
        return
    approval = find_approval(gh, want)
    approver, held = approval_state(gh, approval) if approval else ("", False)
    waits, faults = promotion_gate(
        version=want,
        canary_status=status,
        approval_waits=approval_waits(approval, approver, held),
        hold=env.get("RELEASE_HOLD", ""),
        blockers=gh.issues(BLOCKER_LABEL),
    )
    if faults:
        raise Fail("promote %s: holding, %s" % (want, "; ".join(faults)))
    if waits:
        note("Promote: holding %s on the canary, %s" % (want, "; ".join(waits)))
        return
    note("Promote: stable %s -> %s (approved by %s)" % (have, want, approver))
    update_install(stable, want)
    retire_approvals(gh, want, "Promoted to stable (TIDY), approved by @%s." % approver, inclusive=True)


# ---- the release-train issue ----


def marker(key):
    """A hidden line naming the problem, so the same failure is not posted twice in a row."""
    return "<!-- release-train %s -->" % hashlib.sha256("\n".join(key).encode()).hexdigest()[:16]


def reported(gh, key):
    """Whether the open release-train issue's last word is already this problem."""
    found = gh.issues(ISSUE_LABEL)
    return bool(found) and marker(key) in gh.last_text(found[0])


def report(gh, ok, lines, *, key=None, title_="Release window failing", close_title=None, extra=""):
    """Failure: comment on the open release-train issue, or open one; not again when its last comment is the same
    problem (`key`, the failure lines; default `lines`). Success: close the open one. With close_title and nothing open,
    a success files a closed record (hotfixes, a republished release). `extra` is more text under the lines."""
    gh.ensure_label(ISSUE_LABEL, "d93f0b", "Release windows and hotfixes: open means a release step failed")
    gh.ensure_label(BLOCKER_LABEL, "b60205", "Holds promotion of the canary release to stable")
    body = "\n".join(["Run: " + run_url(), ""] + ["- " + line for line in lines])
    if extra:
        body += "\n\n" + extra
    found = gh.issues(ISSUE_LABEL)
    if not ok:
        mark = marker(lines if key is None else key)
        body += "\n\n" + mark
        if found and mark in gh.last_text(found[0]):
            print("#%d already reports this; not posted again" % found[0]["number"], file=sys.stderr)
        elif found:
            gh.comment(found[0]["number"], body)
        else:
            gh.create_issue(title_, body, [ISSUE_LABEL])
        return
    for issue in found:
        gh.comment(issue["number"], "Resolved: this run shipped with every step green.\n\n" + body)
        gh.close(issue["number"])
    if close_title and not found:
        issue = gh.create_issue(close_title, body, [ISSUE_LABEL])
        gh.close(issue["number"])


def cmd_window(gh, env, opening, now=None):
    """The release job: at a window's open, or at the backstop, promote the approved canary release first; then ship the
    first green candidate, or validate main's head. Reports a failure, or a completed window, on the release-train issue."""
    now = now or datetime.now(timezone.utc)
    failures, promoted, shipment = [], False, None

    def step(name, action):
        try:
            action()
        except Fail as exc:
            failures.append(str(exc))
            note("FAILED %s: %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001 - a crash still reaches the issue
            failures.append("%s crashed: %s: %s" % (name, type(exc).__name__, exc))
            note("FAILED %s: %s: %s" % (name, type(exc).__name__, exc))

    record = None
    step("latest", lambda: catch_up_latest(gh, env))
    if opening or is_backstop(env):
        step("promote", lambda: promote(gh, env))
        promoted = True
    plan = {}
    manual = env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    step(
        "plan",
        lambda: plan.update(plan_step(gh, now, event_run(env), manual=manual, checkout=increment.Checkout(ROOT))),
    )
    if plan:
        note("Window: " + plan["why"])
        for line in problems(plan):
            failures.append(line)
            note("REPORTED " + line)
        if plan["action"] == "ship":
            if not promoted:
                # Stable takes the canary's approved release before the canary moves on.
                step("promote", lambda: promote(gh, env))

            def ship():
                nonlocal shipment, record
                canary = install("CANARY", env)
                if not canary:
                    raise Fail("ship: %s; nothing tagged" % missing("CANARY"))
                base = release_base(env, plan["previous"])
                notes = release_notes.build(gh, base, plan["sha"])
                publish(gh, plan["sha"], plan["version"])
                # The approval record exists before the canary moves, so an approval of the candidate is never lost.
                approval = open_approval(
                    gh, plan["version"], plan["sha"], notes, plan.get("run", {}).get("html_url", "")
                )
                try:
                    update_install(canary, plan["version"])
                except Exception as exc:
                    drop_approval(gh, approval, str(exc))
                    raise
                # The canary runs it and its approval record is open: its release shots may run (train.yml). Written now,
                # not at the end of the step, so a crash after this point still leaves the shots their candidate.
                shipment = {"version": plan["version"], "sha": plan["sha"], "base": base}
                output(shipped="true", **shipment)
                retire_approvals(
                    gh,
                    plan["version"],
                    "Superseded by %s, which is on the canary: %s" % (plan["version"], approval.get("html_url")),
                    inclusive=False,
                )
                if not mark_latest(gh, plan["version"]):
                    record = "Release %s shipped, not marked latest (a newer release is)" % plan["version"]

            step("ship", ship)
        elif plan["action"] == "dispatch":
            step(
                "validate",
                lambda: dispatch_candidate(
                    gh,
                    plan["sha"],
                    plan["version"],
                    plan["newest"],
                    anchor=plan.get("anchor"),
                    carried=plan.get("carried"),
                    anchor_previous=plan.get("anchor_previous"),
                ),
            )
    if failures:
        report(gh, False, list(LOG), key=failures)
    elif shipment:
        report(
            gh,
            True,
            list(LOG),
            close_title=record or ("Release %s republished" % plan["version"] if plan.get("republish") else None),
        )
    # shipped, version, sha and base were written when the candidate shipped (train.yml's shots job reads them), so each
    # key is written once: GitHub keeps the last value of a key. Only the completion and a window that shipped nothing
    # are left to write here.
    values = {"reported": "true"}
    if shipment is None:
        values["shipped"] = "false"
    output(**values)
    return 1 if failures else 0


# ---- hotfix ----


def wait_validation(gh, want, since, timeout=VALIDATION_WAIT):
    deadline = time.monotonic() + timeout
    while True:
        for run in gh.validation_runs():
            started = parse_time(run.get("created_at"))
            if run.get("display_title") != want or not started or started < since:
                continue
            if run.get("status") == "completed":
                if run.get("conclusion") == "success":
                    note("Validation green: %s" % run.get("html_url"))
                    return run
                raise Fail("Release validation ended %s: %s" % (run.get("conclusion"), run.get("html_url")))
            break
        if time.monotonic() >= deadline:
            raise Fail("Release validation for %s did not finish in %d minutes" % (want, timeout // 60))
        sleep(POLL)


def hotfix(gh, env, sha, reason, promote_stable, override_hold=False, record=None):
    """Ships a hotfix to the canary, and to stable when promote_stable and nothing holds it (override_hold passes a hold).
    Its approval record is opened before the canary moves and closed once stable runs it; without stable, it waits for
    approval like any canary release. Its notes go to `record` (a list), for the hotfix's issue."""
    if not reason_ok(reason):
        raise Fail("a hotfix needs a reason of at least a short sentence (15 characters, 3 words)")
    note("Hotfix reason: " + " ".join(reason.split()))
    tags = gh.tags()
    newest = newest_stable(tags)
    if not newest:
        raise Fail("no vX.Y.Z tag to release after")
    sha = gh.head(sha.strip() or "main")
    if not gh.contains("main", sha):
        raise Fail("%s is not on main: land the fix on main first" % sha[:12])
    if gh.compare(newest, sha) != "ahead":
        raise Fail("%s does not come after %s" % (sha[:12], newest))
    version = next_patch(newest)
    want = title(sha, version, newest)
    since = datetime.now(timezone.utc)
    existing = next(
        (
            run
            for run in gh.validation_runs()
            if run.get("display_title") == want
            and (run.get("conclusion") == "success" or run.get("status") in UNFINISHED)
        ),
        None,
    )
    if existing and existing.get("conclusion") == "success":
        note("Validation already green: %s" % existing.get("html_url"))
        green = existing
    else:
        if existing:
            since = parse_time(existing.get("created_at")) or since
            note("Waiting for the running validation: %s" % existing.get("html_url"))
        else:
            gh.dispatch("ci.yml", "main", {"candidate_sha": sha, "previous_release": newest, "version": version})
            note("Dispatched Release validation: " + want)
        green = wait_validation(gh, want, since - SKEW)
    notes = release_notes_for(gh, env, sha, newest)
    if record is not None:
        record.append(notes)
    publish(gh, sha, version)
    canary = install("CANARY", env)
    if not canary:
        note("Canary: skipped, %s; stable not touched" % missing("CANARY"))
        mark_latest(gh, version)
        return version
    approval = open_approval(gh, version, sha, notes, green.get("html_url", ""))
    try:
        update_install(canary, version)
    except Exception as exc:
        drop_approval(gh, approval, str(exc))
        raise
    retire_approvals(
        gh,
        version,
        "Superseded by hotfix %s, which is on the canary: %s" % (version, approval.get("html_url")),
        inclusive=False,
    )
    mark_latest(gh, version)
    if not promote_stable:
        note("Stable: not promoted (promote_stable is off); the approval for %s decides" % version)
        return version
    stable = install("STABLE", env)
    if not stable:
        note("Stable: skipped, " + missing("STABLE"))
        return version
    held = holds(env.get("RELEASE_HOLD", ""), gh.issues(BLOCKER_LABEL))
    if held and not override_hold:
        note(
            "Stable: held, %s. The approval for %s waits; or run the hotfix again with override_hold"
            % ("; ".join(held), version)
        )
        return version
    if held:
        note(
            "Stable: HOLD OVERRIDDEN by %s (override_hold): %s"
            % (env.get("GITHUB_ACTOR") or "unknown", "; ".join(held))
        )
    update_install(stable, version)
    retire_approvals(gh, version, "Promoted to stable (TIDY) by hotfix %s." % version, inclusive=True)
    return version


def cmd_hotfix(gh, env, sha, reason, promote_stable, override_hold=False):
    version, failure, record = "", None, []
    try:
        version = hotfix(gh, env, sha, reason, promote_stable, override_hold, record=record)
    except Fail as exc:
        failure = str(exc)
        note("FAILED hotfix: %s" % exc)
    except Exception as exc:  # noqa: BLE001 - a crash still reaches the issue
        failure = "%s: %s" % (type(exc).__name__, exc)
        note("FAILED hotfix: %s" % failure)
    short = " ".join(str(reason or "").split())[:80]
    report(
        gh,
        failure is None,
        list(LOG),
        key=[run_url(), short, failure or ""],
        title_="Hotfix failing: " + short,
        close_title="Hotfix %s: %s" % (version, short),
        extra="\n".join(record),
    )
    output(reported="true")
    return 1 if failure else 0


# ---- status ----


def cmd_status(gh, env, now):
    plan = plan_step(gh, now)
    out = {
        "window_start": plan["start"].isoformat(),
        "next_window": next_window(now).isoformat(),
        "newest_tag": plan["newest"],
        "next": plan["action"],
        "why": plan["why"],
        "hold": env.get("RELEASE_HOLD", ""),
        "blockers": [issue["number"] for issue in gh.issues(BLOCKER_LABEL)],
        "approvals": [],
    }
    for version, issue in open_approvals(gh):
        approver, held = approval_state(gh, issue)
        out["approvals"].append({"version": version, "number": issue["number"], "approved_by": approver, "held": held})
    for role in ("CANARY", "STABLE"):
        target = install(role, env)
        status = update_status(target) if target else None
        out[role.lower()] = {
            "configured": bool(target),
            "running": (status or {}).get("running"),
            "state": (status or {}).get("state"),
        }
    print(json.dumps(out, indent=2))
    return 0


def cmd_notes(gh, base, head):
    """The release notes for head since base, on stdout (read-only)."""
    print(release_notes.build(gh, base, head))
    return 0


def main(argv=None, env=None, now=None):
    env = dict(os.environ if env is None else env)
    now = now or datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=["guard", "window", "promote", "hotfix", "status", "notes", "shots-into-approval"]
    )
    parser.add_argument("refs", nargs="*", help="notes: BASE HEAD; shots-into-approval: VERSION FILE")
    parser.add_argument("--sha", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--promote-stable", default="true")
    parser.add_argument("--override-hold", default="false")
    args = parser.parse_args(argv)
    gh = GitHub(env.get("GITHUB_REPOSITORY") or "ticoteam/tico", env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "")
    try:
        if args.command == "guard":
            return cmd_guard(gh, env, now)
        if args.command == "window":
            return cmd_window(gh, env, window_opens(env, now), now)
        if args.command == "hotfix":
            return cmd_hotfix(
                gh,
                env,
                args.sha,
                args.reason,
                args.promote_stable.strip().lower() == "true",
                args.override_hold.strip().lower() == "true",
            )
        if args.command == "status":
            return cmd_status(gh, env, now)
        if args.command == "notes":
            if len(args.refs) != 2:
                print("usage: release_train.py notes BASE HEAD", file=sys.stderr)
                return 2
            return cmd_notes(gh, args.refs[0], args.refs[1])
        if args.command == "shots-into-approval":
            if len(args.refs) != 2:
                print("usage: release_train.py shots-into-approval VERSION FILE", file=sys.stderr)
                return 2
            return cmd_shots_into_approval(gh, args.refs[0], args.refs[1])
        promote(gh, env)
        return 0
    except Fail as exc:
        note("FAILED %s: %s" % (args.command, exc))
        if args.command == "guard":
            output(go="false")
        return 1


if __name__ == "__main__":
    sys.exit(main())
