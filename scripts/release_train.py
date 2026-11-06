#!/usr/bin/env python3
"""Tico's own release windows, canary then stable, and the hotfix lane (docs/releasing.md#release-windows).

    python3 scripts/release_train.py guard              should the release job run for this event (read-only)
    python3 scripts/release_train.py window [--open]    act: (--open: promote first) ship a green candidate or validate main
    python3 scripts/release_train.py promote            canary -> stable, when the bake gate passes
    python3 scripts/release_train.py hotfix --reason "..." [--sha SHA] [--promote-stable true|false]
                                                [--override-hold true|false]
    python3 scripts/release_train.py status             the window, what canary and stable run, the next step

The workflows (.github/workflows/train.yml, hotfix.yml) call it. Environment:

    GITHUB_TOKEN, GITHUB_REPOSITORY        the Actions token and repository
    GITHUB_EVENT_NAME                      schedule, push, workflow_run or workflow_dispatch
    SCHEDULE                               schedule only: the cron that fired (a window cron, or the backstop)
    RUN_EVENT, RUN_CONCLUSION, RUN_TITLE,  workflow_run only: the Release validation run that finished
    RUN_URL
    CANARY_URL, CANARY_UPDATE_KEY          the canary install (Tico Team) and its update key (docs/service-keys.md)
    STABLE_URL, STABLE_UPDATE_KEY          the stable install and its update key
    BAKE_HOURS                             how long the canary runs a release before stable gets it (default 4)
    RELEASE_HOLD                           any value holds promotion to stable (a hotfix too, unless override_hold)
    RELEASE_TRAINS                         `on` lets schedules, pushes and finished runs act; a manual run always acts
    GITHUB_ACTOR                           hotfix only: who overrode a hold

A release is published as not latest and marked latest (the GitHub release and the images' `latest` tags) only once the
canary runs it healthy, so new installs, update checks and desktop apps never get a release the canary has not run. The
image tags move with `docker buildx imagetools` (the workflow logs in to ghcr.io first).

A window opens at 14:30 and 22:00 UTC (07:30 and 15:00 in Los Angeles during daylight time, an hour earlier in winter)
and stays open until a vX.Y.Z release is published after it. Nothing is stored: every run works the state out from
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

ROOT = Path(__file__).resolve().parents[1]
ROLLOUT_PATH = ROOT / "templates/catalog/release-notes/software/rollout.py"
# Window starts in UTC, the same times as train.yml's crons. GitHub's schedule fires late, sometimes by many minutes;
# that only delays the promotion, because a push or a finished run inside the window acts on its own.
WINDOWS = ((14, 30), (22, 0))
CRONS = ["30 14 * * *", "0 22 * * *"]
# A backstop for a missed event (a workflow_run that does not fire, say): it takes a step in an open window like a push
# does, and never opens one, so it never promotes. Minutes 5, 25 and 45: never a window's minute (00 or 30).
BACKSTOP_CRON = "5-59/20 * * * *"
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TITLE = re.compile(r"^Validate ([0-9a-f]{40}) as (v\S+) from (v\S+)$")
UNFINISHED = {"queued", "pending", "waiting", "requested", "in_progress"}
# A candidate that did not pass: refused, or cancelled. Main's head is not validated again until the next push (or a
# manual run), and the run is reported once.
FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
ISSUE_LABEL = "release-train"
BLOCKER_LABEL = "release-blocker"
RELEASE_WAIT = 90 * 60
VALIDATION_WAIT = 150 * 60
DISPATCH_WAIT = 120
UPDATE_WAIT = 30 * 60      # the updater pulls, snapshots, restarts and checks health, or rolls back
UPDATE_PATH = "/api/v2/system/update"
POLL = 20
REPUBLISH_AFTER = timedelta(minutes=30)   # a tag still unpublished this long after its Release run started
RELEASE_RUNS = 2                          # the first Release run on a tag, and one more
IMAGES = ("tico", "tico-runner", "tico-updater")
REGISTRY = "ghcr.io/ticoteam"
SKEW = timedelta(seconds=60)   # GitHub's timestamps and this clock can differ a little


def _load_rollout():
    spec = importlib.util.spec_from_file_location("tico_rollout", ROLLOUT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rollout = _load_rollout()


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


def title(sha, version, previous):
    """Release validation's run-name; release.yml finds a run by it."""
    return "Validate %s as %s from %s" % (sha, version, previous)


def parse_title(text):
    match = TITLE.match(str(text or ""))
    return match.groups() if match else None


def as_tag(release):
    plain = rollout.plain(release)
    return "v" + plain if plain else ""


def parse_time(text):
    try:
        parsed = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def window_start(now):
    """The latest window start at or before now (UTC)."""
    now = now.astimezone(timezone.utc)
    starts = [datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
              for day in (now - timedelta(days=1), now) for hour, minute in WINDOWS]
    return max(start for start in starts if start <= now)


def next_window(now):
    return min(start for start in (window_start(now + timedelta(hours=hours)) for hours in range(1, 25)) if start > now)


def hhmm(when):
    return when.astimezone(timezone.utc).strftime("%H:%M UTC")


def reason_ok(reason):
    """A hotfix says why in a sentence: at least 15 characters and 3 words."""
    text = " ".join(str(reason or "").split())
    return len(text) >= 15 and len(text.split()) >= 3


# ---- GitHub ----

class GitHub:
    """The few REST calls the release jobs make, with the Actions token."""

    def __init__(self, repo, token, api="https://api.github.com"):
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, method, path, body=None, token=None):
        url = path if path.startswith("http") else "%s/repos/%s/%s" % (self.api, self.repo, path.lstrip("/"))
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "tico-release-train",
                   "X-GitHub-Api-Version": "2022-11-28"}
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
        return self.get("actions/workflows/ci.yml/runs?branch=main&event=workflow_dispatch&per_page=100")["workflow_runs"]

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

    def make_latest(self, release):
        code, data, _ = self.call("PATCH", "releases/%s" % release["id"], {"make_latest": "true"})
        if code != 200:
            raise Fail("marking %s latest: %s %s" % (release.get("tag_name"), code, data.get("message", "")))

    def issues(self, label, state="open"):
        query = urllib.parse.urlencode({"labels": label, "state": state, "per_page": 20})
        return [issue for issue in self.get("issues?" + query) if "pull_request" not in issue]

    def ensure_label(self, name, color, description):
        self.call("POST", "labels", {"name": name, "color": color, "description": description})   # 422: exists

    def create_issue(self, title_, body, labels):
        code, data, _ = self.call("POST", "issues", {"title": title_, "body": body, "labels": labels})
        if code != 201:
            raise Fail("opening the %s issue: %s %s" % (labels[0], code, data.get("message", "")))
        return data

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
    return "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
                                      os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", ""))


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
        role.lower(), role, role)


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
    `pulling`, then restarts the server): an earlier update's outcome, even one to the same version, is never judged."""
    want, name = rollout.plain(version), target["name"]
    snapshot = update_status(target)
    before = rollout.plain((snapshot or {}).get("running"))
    if not before:
        raise Fail("%s: its update status cannot be read (GET %s with the update key)" % (name, UPDATE_PATH))
    if before == want:
        note("%s already runs v%s" % (name, want))
        return
    try:
        rollout.call(target, "POST", UPDATE_PATH + "/check")      # 429 within a minute of another check is fine
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
            started = True                                          # the server is restarting
            continue
        state = status.get("state") or ""
        if rollout.plain(status.get("to")) == want and state in BUSY:
            started = True
        if started and rollout.plain(status.get("to")) == want and state in ("rolled_back", "failed"):
            raise Fail("%s: update to v%s %s (%s)" % (name, want, state, status.get("message") or ""))
        if state == "healthy" and rollout.plain(status.get("to")) == want and rollout.plain(status.get("running")) == want:
            note("%s updated v%s -> v%s, healthy" % (name, before, want))
            return
    raise Fail("%s did not report v%s healthy within %d minutes (last state %s)" % (name, want, timeout // 60,
                                                                                  state or "unknown"))


def run_command(argv, timeout=180):
    """(exit code, output) of a command (tests replace it)."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, (done.stderr or done.stdout or "").strip()


def mark_latest(gh, version):
    """Once the canary runs `version` healthy: point the images' `latest` tags at it and make it the latest GitHub
    release, which is what new installs, update checks and desktop apps follow."""
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


# ---- the window: what to do now ----

def event_run(env):
    """workflow_run only: the Release validation run that just finished, as {conclusion, title, url}."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_run" or env.get("RUN_EVENT") != "workflow_dispatch":
        return None
    return {"conclusion": env.get("RUN_CONCLUSION", ""), "title": env.get("RUN_TITLE", ""), "url": env.get("RUN_URL", "")}


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
        plan["stuck"] = ("%s is tagged but its release did not publish after %d Release runs (%s); publish it by hand: "
                         "docs/releasing.md#a-release-that-did-not-publish" % (tag, len(runs), links))
        plan["why"] = plan["stuck"]
        return plan
    started = [when for when in (parse_time(run.get("created_at")) for run in runs) if when]
    if started and now - min(started) < REPUBLISH_AFTER:
        plan["why"] = "%s is tagged but not published (%s); its Release workflow runs again at %s" % (
            tag, links, hhmm(min(started) + REPUBLISH_AFTER))
        return plan
    sha = gh.tag_commit(tag)
    if not sha:
        raise Fail("%s: the tag is gone" % tag)
    plan.update(action="ship", version=tag, sha=sha, republish=True, why=(
        "%s is tagged at %s but not published (%s): starting its Release workflow again" % (tag, sha[:12], links)))
    return plan


def plan_step(gh, now, finished=None, manual=False):
    """What the open window does next, worked out from GitHub alone. Returns {"action": "none" | "wait" | "ship" |
    "dispatch", "why", ...}: "red" is a candidate of this window that did not pass, and "stuck" a tag whose release did
    not publish, both to report. A manual run validates main's head again even after a red or cancelled run."""
    tags = gh.tags()
    newest = newest_stable(tags)
    if not newest:
        raise Fail("no vX.Y.Z tag to release after")
    start = window_start(now)
    plan = {"action": "none", "newest": newest, "start": start, "red": None, "stuck": None}
    published = gh.release(newest)
    if not published or published.get("draft"):
        return unpublished(gh, now, newest, plan)
    published_at = parse_time(published.get("published_at"))
    if published_at is None:
        plan["why"] = "%s has no publication time; holding" % newest
        return plan
    if published_at >= start:
        plan["why"] = "window closed: %s was published after the %s window opened; the next opens at %s" % (
            newest, hhmm(start), hhmm(next_window(now)))
        return plan
    version = next_patch(newest)
    plan["version"] = version
    if finished and finished["conclusion"] in FAILED:
        parsed = parse_title(finished["title"])
        if parsed and parsed[1:] == (version, newest):
            plan["red"] = {"sha": parsed[0], "conclusion": finished["conclusion"], "url": finished["url"]}
    head = gh.head("main")
    plan["sha"] = head
    if gh.compare(newest, head) != "ahead":
        plan["why"] = "window open since %s; main is at %s, nothing new" % (hhmm(start), newest)
        return plan
    head_failed = None
    for run in gh.validation_runs():
        parsed = parse_title(run.get("display_title"))
        if not parsed or parsed[1:] != (version, newest) or run.get("status") != "completed":
            continue
        sha = parsed[0]
        if run.get("conclusion") == "success":
            finished_at = parse_time(run.get("updated_at"))
            # The first green candidate of this window ships: one that finished after it opened, or main's head.
            if (sha == head or (finished_at and finished_at >= start)) and gh.compare(newest, sha) == "ahead" \
                    and gh.contains("main", sha):
                plan.update(action="ship", sha=sha, run=run, why="%s at %s is green: %s" % (
                    version, sha[:12], run.get("html_url")))
                return plan
        elif sha == head and head_failed is None and run.get("conclusion") in FAILED:
            head_failed = run
    if head_failed and not manual and not plan["red"]:
        plan["red"] = {"sha": head, "conclusion": head_failed.get("conclusion"), "url": head_failed.get("html_url")}
    busy = next((run for run in gh.recent_runs("ci.yml") if run.get("status") in UNFINISHED), None)
    if busy:
        plan.update(action="wait", why="window open since %s; waiting for %s" % (hhmm(start), busy.get("html_url")))
        return plan
    if head_failed and not manual:
        plan.update(action="wait", why="window open since %s; main's head %s ended %s (%s): the next push to main, or "
                    "a manual run, validates it again" % (hhmm(start), head[:12], head_failed.get("conclusion"),
                                                         head_failed.get("html_url")))
        return plan
    plan.update(action="dispatch", why="validate %s as %s from %s" % (head[:12], version, newest))
    return plan


def problems(plan):
    """The lines a plan reports on the release-train issue: the same text for the same problem, so it is posted once."""
    lines = []
    red = plan.get("red")
    if red:
        lines.append("Candidate %s at %s ended %s: %s" % (plan["version"], red["sha"][:12], red["conclusion"], red["url"]))
    if plan.get("stuck"):
        lines.append(plan["stuck"])
    return lines


def hotfix_busy(gh):
    return next((run for run in gh.recent_runs("hotfix.yml") if run.get("status") in UNFINISHED), None)


def cmd_guard(gh, env, now):
    """Read-only, outside the release concurrency group: decides whether the release job runs at all, so a push that
    finds nothing to do never enters the `release` environment or queues behind (or displaces) a hotfix."""
    event = env.get("GITHUB_EVENT_NAME", "")
    # Only a window cron (or a manual run) opens a window; the backstop, or a cron this does not know, acts like a push.
    opening = event == "workflow_dispatch" or (event == "schedule" and env.get("SCHEDULE", "") in CRONS)
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
            note("Window step: %s" % ("window opens (%s)" % env.get("SCHEDULE") if event == "schedule" else "manual run"))
        else:
            plan = plan_step(gh, now, event_run(env))
            lines = problems(plan)
            go = plan["action"] in ("ship", "dispatch") or bool(lines and not reported(gh, lines))
            note("Window step: %s" % plan["why"])
    output(go=str(go).lower(), open=str(go and opening).lower())
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
                raise Fail("the Release workflow for %s ended %s: %s" % (version, run.get("conclusion"), run.get("html_url")))
        if time.monotonic() >= deadline:
            raise Fail("no GitHub release for %s after %d minutes" % (version, timeout // 60))
        sleep(POLL)


def dispatch_candidate(gh, sha, version, previous, timeout=DISPATCH_WAIT):
    """Dispatch Release validation and wait until GitHub lists the run, so the next release job (which runs after
    this one, in the same concurrency group) sees it running and does not dispatch it again."""
    want, since = title(sha, version, previous), datetime.now(timezone.utc) - SKEW
    gh.dispatch("ci.yml", "main", {"candidate_sha": sha, "previous_release": previous, "version": version})
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


# ---- promote: canary -> stable ----

def running_since(status, version):
    """When the canary started running `version`: its release history's entry for it (backend/releases.py)."""
    for entry in reversed((status or {}).get("history") or []):
        if rollout.plain(entry.get("version")) == version:
            return parse_time(entry.get("since"))
    return None


def bake_gate(*, version, canary_status, now, bake_hours, hold, blockers):
    """Whether stable may take what the canary runs. Returns (waits, faults): waits hold the promotion quietly (bake
    time, the hold switch, a release-blocker issue); faults hold it and are reported. Anything this cannot tell (no
    answer, an unknown state, no start time) is a fault, never a pass."""
    waits, faults = [], []
    plain = rollout.plain(version)
    if not canary_status:
        faults.append("the canary's update status cannot be read")
    else:
        state = canary_status.get("state") or ""
        running_ = rollout.plain(canary_status.get("running"))
        if running_ != plain:
            faults.append("the canary's update status says it runs %s, not %s" % (running_ or "nothing", plain))
        elif state not in ("healthy", "idle"):
            faults.append("the canary's last update is %s: %s" % (state or "unknown", canary_status.get("message") or ""))
        since = running_since(canary_status, plain)
        if since is None:
            faults.append("the canary does not say since when it runs v%s" % plain)
        else:
            baked = (now - since).total_seconds() / 3600
            if baked < bake_hours:
                waits.append("the canary has run v%s %.1f h of %g h" % (plain, baked, bake_hours))
    return waits + holds(hold, blockers), faults


def holds(hold, blockers):
    """What holds stable where it is: the RELEASE_HOLD variable and open release-blocker issues."""
    found = ["RELEASE_HOLD is set: %s" % hold.strip()] if (hold or "").strip() else []
    return found + ["release-blocker #%s is open: %s" % (issue.get("number"), issue.get("title")) for issue in blockers]


def promote(gh, env, now=None):
    canary, stable = install("CANARY", env), install("STABLE", env)
    if not canary or not stable:
        note("Promote: skipped, " + missing("CANARY" if not canary else "STABLE"))
        return
    now = now or datetime.now(timezone.utc)
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
        return
    if not parse_version(want) or not parse_version(have) or parse_version(want) <= parse_version(have):
        note("Promote: skipped, the canary's %s is not a stable release ahead of stable's %s" % (want, have))
        return
    waits, faults = bake_gate(
        version=want, canary_status=status, now=now, bake_hours=float(env.get("BAKE_HOURS") or 4),
        hold=env.get("RELEASE_HOLD", ""), blockers=gh.issues(BLOCKER_LABEL))
    if faults:
        raise Fail("promote %s: holding, %s" % (want, "; ".join(faults)))
    if waits:
        note("Promote: holding %s on the canary, %s" % (want, "; ".join(waits)))
        return
    note("Promote: stable %s -> %s" % (have, want))
    update_install(stable, want)


# ---- the release-train issue ----

def marker(key):
    """A hidden line naming the problem, so the same failure is not posted twice in a row."""
    return "<!-- release-train %s -->" % hashlib.sha256("\n".join(key).encode()).hexdigest()[:16]


def reported(gh, key):
    """Whether the open release-train issue's last word is already this problem."""
    found = gh.issues(ISSUE_LABEL)
    return bool(found) and marker(key) in gh.last_text(found[0])


def report(gh, ok, lines, *, key=None, title_="Release window failing", close_title=None):
    """Failure: comment on the open release-train issue, or open one; not again when its last comment is the same
    problem (`key`, the failure lines; default `lines`). Success: close the open one. With close_title and nothing open,
    a success files a closed record (hotfixes, a republished release)."""
    gh.ensure_label(ISSUE_LABEL, "d93f0b", "Release windows and hotfixes: open means a release step failed")
    gh.ensure_label(BLOCKER_LABEL, "b60205", "Holds promotion of the canary release to stable")
    body = "\n".join(["Run: " + run_url(), ""] + ["- " + line for line in lines])
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
    """The release job: at a window's open (or a manual run) promote first; then ship the first green candidate, or
    validate main's head. Reports a failure, or a completed window, on the release-train issue."""
    now = now or datetime.now(timezone.utc)
    failures, promoted, shipped = [], False, False

    def step(name, action):
        try:
            action()
        except Fail as exc:
            failures.append(str(exc))
            note("FAILED %s: %s" % (name, exc))
        except Exception as exc:    # noqa: BLE001 - a crash still reaches the issue
            failures.append("%s crashed: %s: %s" % (name, type(exc).__name__, exc))
            note("FAILED %s: %s: %s" % (name, type(exc).__name__, exc))

    if opening:
        step("promote", lambda: promote(gh, env, now))
        promoted = True
    plan = {}
    manual = env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    step("plan", lambda: plan.update(plan_step(gh, now, event_run(env), manual=manual)))
    if plan:
        note("Window: " + plan["why"])
        for line in problems(plan):
            failures.append(line)
            note("REPORTED " + line)
        if plan["action"] == "ship":
            if not promoted:
                # Stable takes the canary's baked release before the canary moves on.
                step("promote", lambda: promote(gh, env, now))

            def ship():
                nonlocal shipped
                canary = install("CANARY", env)
                if not canary:
                    raise Fail("ship: %s; nothing tagged" % missing("CANARY"))
                publish(gh, plan["sha"], plan["version"])
                shipped = True
                update_install(canary, plan["version"])
                mark_latest(gh, plan["version"])
            step("ship", ship)
        elif plan["action"] == "dispatch":
            step("validate", lambda: dispatch_candidate(gh, plan["sha"], plan["version"], plan["newest"]))
    if failures:
        report(gh, False, list(LOG), key=failures)
    elif shipped:
        report(gh, True, list(LOG), close_title=(
            "Release %s republished" % plan["version"]) if plan.get("republish") else None)
    output(reported="true")
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


def hotfix(gh, env, sha, reason, promote_stable, override_hold=False):
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
    existing = next((run for run in gh.validation_runs() if run.get("display_title") == want
                     and (run.get("conclusion") == "success" or run.get("status") in UNFINISHED)), None)
    if existing and existing.get("conclusion") == "success":
        note("Validation already green: %s" % existing.get("html_url"))
    else:
        if existing:
            since = parse_time(existing.get("created_at")) or since
            note("Waiting for the running validation: %s" % existing.get("html_url"))
        else:
            gh.dispatch("ci.yml", "main", {"candidate_sha": sha, "previous_release": newest, "version": version})
            note("Dispatched Release validation: " + want)
        wait_validation(gh, want, since - SKEW)
    publish(gh, sha, version)
    canary = install("CANARY", env)
    if not canary:
        note("Canary: skipped, %s; stable not touched" % missing("CANARY"))
        mark_latest(gh, version)
        return version
    update_install(canary, version)
    mark_latest(gh, version)
    if not promote_stable:
        note("Stable: not promoted (promote_stable is off); the next window's bake gate decides")
        return version
    stable = install("STABLE", env)
    if not stable:
        note("Stable: skipped, " + missing("STABLE"))
        return version
    held = holds(env.get("RELEASE_HOLD", ""), gh.issues(BLOCKER_LABEL))
    if held and not override_hold:
        note("Stable: held, %s. The next window's bake gate decides, or run the hotfix again with override_hold"
             % "; ".join(held))
        return version
    if held:
        note("Stable: HOLD OVERRIDDEN by %s (override_hold): %s" % (env.get("GITHUB_ACTOR") or "unknown", "; ".join(held)))
    update_install(stable, version)
    return version


def cmd_hotfix(gh, env, sha, reason, promote_stable, override_hold=False):
    version, failure = "", None
    try:
        version = hotfix(gh, env, sha, reason, promote_stable, override_hold)
    except Fail as exc:
        failure = str(exc)
        note("FAILED hotfix: %s" % exc)
    except Exception as exc:    # noqa: BLE001 - a crash still reaches the issue
        failure = "%s: %s" % (type(exc).__name__, exc)
        note("FAILED hotfix: %s" % failure)
    short = " ".join(str(reason or "").split())[:80]
    report(gh, failure is None, list(LOG), key=[short, failure or ""], title_="Hotfix failing: " + short,
           close_title="Hotfix %s: %s" % (version, short))
    output(reported="true")
    return 1 if failure else 0


# ---- status ----

def cmd_status(gh, env, now):
    plan = plan_step(gh, now)
    out = {"window_start": plan["start"].isoformat(), "next_window": next_window(now).isoformat(),
           "newest_tag": plan["newest"], "next": plan["action"], "why": plan["why"],
           "hold": env.get("RELEASE_HOLD", ""), "blockers": [issue["number"] for issue in gh.issues(BLOCKER_LABEL)]}
    for role in ("CANARY", "STABLE"):
        target = install(role, env)
        status = update_status(target) if target else None
        out[role.lower()] = {"configured": bool(target), "running": (status or {}).get("running"),
                             "state": (status or {}).get("state")}
    print(json.dumps(out, indent=2))
    return 0


def main(argv=None, env=None, now=None):
    env = dict(os.environ if env is None else env)
    now = now or datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["guard", "window", "promote", "hotfix", "status"])
    parser.add_argument("--open", action="store_true", help="window: a window opens now, so promote first")
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
            return cmd_window(gh, env, args.open, now)
        if args.command == "hotfix":
            return cmd_hotfix(gh, env, args.sha, args.reason, args.promote_stable.strip().lower() == "true",
                              args.override_hold.strip().lower() == "true")
        if args.command == "status":
            return cmd_status(gh, env, now)
        promote(gh, env, now)
        return 0
    except Fail as exc:
        note("FAILED %s: %s" % (args.command, exc))
        if args.command == "guard":
            output(go="false", open="false")
        return 1


if __name__ == "__main__":
    sys.exit(main())
