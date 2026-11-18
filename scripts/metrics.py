"""Recompute main validation episodes, merges per week, post-release reverts and hotfixes, and merge queue health.

Read-only GitHub and git queries; no ledger is generated. Release validation is the
same ci.yml workflow dispatched on main that scripts/main_red.py watches. A hotfix is a
revert, a release-tag cherry-pick, or a PR titled "hotfix". The hotfix lane ships all of main,
so the PRs in its releases are not hotfixes.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import freeze_guard, main_red, release_train

MANUAL_FILES = ("escapes.jsonl", "green_but_wrong.jsonl")
REVERT_SHA = re.compile(r"\bThis reverts commit ([0-9a-f]{7,40})\b", re.IGNORECASE)
REVERT_OK = re.compile(r"^Revert-Ok:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
HOTFIX = re.compile(r"\bhotfix\b", re.IGNORECASE)
RELATION = re.compile(
    r"\b(?:reverts?|fixes|hotfix(?:\s+for)?|regression\s+(?:in|from)|originating-pr:)\s+"
    r"(?:PR\s+|pull request\s+)?(https://github\.com/[^/\s]+/[^/\s]+/pull/|[\w.-]+/[\w.-]+#|#)(\d+)\b",
    re.IGNORECASE,
)
FIX_SHA = re.compile(r"\b(?:fixes commit|regression from commit) ([0-9a-f]{7,40})\b", re.IGNORECASE)
QUEUE_REF = re.compile(r"^gh-readonly-queue/(?P<base>[^/]+)/pr-(?P<pr>\d+)-[0-9a-f]{40}$")
QUEUE_OUTCOMES = ("passed", "failed", "superseded", "removed")
# GraphQL aliases per query; each pull request reads its last TIMELINE_EVENTS queue events.
TIMELINE_BATCH, TIMELINE_EVENTS = 50, 50


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso_week(value):
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


class GitHub:
    """Bounded, paginated gh reads. Exhausting the budget fails instead of publishing partial counts."""

    def __init__(self, repo, budget=250):
        self.repo = repo
        self.calls = 0
        self.budget = budget

    def get(self, path, **params):
        self.calls += 1
        if self.calls > self.budget:
            raise ValueError("GitHub API budget exceeded")
        query = "?" + urlencode(params) if params else ""
        endpoint = f"repos/{self.repo}" + (f"/{path}" if path else "")
        done = subprocess.run(["gh", "api", endpoint + query], capture_output=True, text=True, timeout=60, check=False)
        if done.returncode:
            # API errors and workflow titles can contain free text; keep them out of the proof page and logs.
            raise ValueError(f"GitHub read failed: {path}")
        return json.loads(done.stdout)

    def pages(self, path, key=None, **params):
        result = []
        for page in range(1, 251):
            data = self.get(path, per_page=100, page=page, **params)
            if key and data.get("total_count", 0) > 1000:
                raise ValueError(f"GitHub search limit exceeded: {path}")
            rows = data[key] if key else data
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError("GitHub pagination limit exceeded")

    def graphql(self, query):
        self.calls += 1
        if self.calls > self.budget:
            raise ValueError("GitHub API budget exceeded")
        done = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"], capture_output=True, text=True, timeout=60, check=False
        )
        if done.returncode:
            raise ValueError("GitHub GraphQL read failed")
        data = json.loads(done.stdout)
        if data.get("errors") or not data.get("data"):
            raise ValueError("GitHub GraphQL read failed")
        return data["data"]

    def queue_runs(self, since, now):
        """One-day slices: a busy day has a few hundred merge queue runs, under the 1,000-result limit."""
        result, start = [], since
        while start <= now:
            end = min(now, start + timedelta(days=1) - timedelta(seconds=1))
            result.extend(
                self.pages(
                    "actions/runs",
                    "workflow_runs",
                    event="merge_group",
                    created=f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}",
                )
            )
            start = end + timedelta(seconds=1)
        return result

    def queue_timelines(self, numbers):
        """Each pull request's merge time and its merge queue add and remove events."""
        owner, name = self.repo.split("/", 1)
        result = {}
        numbers = sorted(numbers)
        for i in range(0, len(numbers), TIMELINE_BATCH):
            fields = "".join(
                f"p{number}: pullRequest(number: {number}) {{ number mergedAt baseRefName "
                f"timelineItems(itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT], "
                f"last: {TIMELINE_EVENTS}) {{ nodes {{ __typename "
                "... on AddedToMergeQueueEvent { createdAt } "
                "... on RemovedFromMergeQueueEvent { createdAt reason } } } }"
                for number in numbers[i : i + TIMELINE_BATCH]
            )
            data = self.graphql(f'{{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}')
            for pr in (data.get("repository") or {}).values():
                if pr:
                    result[pr["number"]] = pr
        return result

    def runs(self, workflow, since, now):
        """Date slices avoid Actions' 1,000-result search limit."""
        result, end = [], now
        while end >= since:
            start = max(since, end - timedelta(days=27))
            result.extend(
                self.pages(
                    f"actions/workflows/{workflow}/runs",
                    "workflow_runs",
                    branch="main",
                    event="workflow_dispatch",
                    created=f"{start:%Y-%m-%dT%H:%M:%SZ}..{end:%Y-%m-%dT%H:%M:%SZ}",
                )
            )
            end = start - timedelta(seconds=1)
        return result


def validation_runs(api, born, since, now):
    """Include earlier attempts and a green baseline before the window, even across a long red episode."""
    result, end, start = [], now, max(born, since - timedelta(days=27))
    while end >= born:
        block = api.runs("ci.yml", start, end)
        for run in list(block):
            for attempt in range(1, run.get("run_attempt", 1)):
                block.append(api.get(f"actions/runs/{run['id']}/attempts/{attempt}"))
        result.extend(block)
        if any(
            row.get("status") == "completed"
            and row.get("conclusion") in main_red.GREEN
            and main_red.candidate(row)
            and timestamp(row["updated_at"]) < since
            for row in block
        ):
            break
        end = start - timedelta(seconds=1)
        start = max(born, end - timedelta(days=27))
    return result


def episodes(runs):
    """Closed episodes use the first effective green completion; an open episode uses its red completion.

    As in main_red.alarm, a newer finished validation supersedes an older one.
    Canceled runs and runs without candidate titles do not change the state.
    """
    valid = [
        run
        for run in runs
        if run.get("head_branch") == "main"
        and run.get("event") == "workflow_dispatch"
        and run.get("status") == "completed"
    ]
    newest, opened, result = "", None, []
    for run in sorted(valid, key=lambda row: (row["updated_at"], row["id"], row.get("run_attempt", 1))):
        if run["created_at"] < newest:
            continue
        newest = run["created_at"]
        if not main_red.candidate(run):
            continue
        if run["conclusion"] in main_red.RED and opened is None:
            opened = run
        elif run["conclusion"] in main_red.GREEN and opened is not None:
            result.append({"red": opened, "green": run, "timestamp": run["updated_at"]})
            opened = None
    if opened is not None:
        result.append({"red": opened, "green": None, "timestamp": opened["updated_at"]})
    return result


def references(text, repo):
    """The explicit PR/commit relationships also recognized by the revert guards."""
    prefixes = {"#", repo + "#", f"https://github.com/{repo}/pull/"}
    numbers = {int(number) for prefix, number in RELATION.findall(text) if prefix in prefixes}
    shas = set(REVERT_SHA.findall(text)) | set(FIX_SHA.findall(text))
    for token in REVERT_OK.findall(text):
        if re.fullmatch(r"[0-9a-f]{7,40}", token):
            shas.add(token)
        elif token.lstrip("#").isdigit():
            numbers.add(int(token.lstrip("#")))
        elif match := re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/(\d+)", token):
            numbers.add(int(match[1]))
    return numbers, shas


def post_release_records(pulls, texts, resolve, shipped, repo, backports=()):
    """Map fixes to originating PRs and require a containing release predating the fix merge.

    Discovery is the fix's merge time, not the originating merge or release month:
    a defect discovered late belongs to the month in which evidence first appears.
    Unattributed fixes stay visible, without guessing from issue numbers or prose.
    """
    records, unresolved = [], set()
    for fix in sorted(pulls.values(), key=lambda row: row["merged_at"]):
        number = fix["number"]
        text = fix["title"] + "\n" + (fix.get("body") or "") + "\n" + texts.get(number, "")
        revert = bool(
            re.match(r"^Revert\b", fix["title"], re.IGNORECASE) or REVERT_SHA.search(text) or REVERT_OK.search(text)
        )
        hotfix = bool(HOTFIX.search(fix["title"]) or number in backports)
        if not (revert or hotfix):
            continue
        numbers, shas = references(text, repo)
        for sha in shas:
            if original := resolve(sha):
                numbers.add(original["number"])
        if revert and not numbers:
            title = re.fullmatch(r'Revert "(.+)"', fix["title"], re.IGNORECASE)
            matches = [pr["number"] for pr in pulls.values() if title and pr["title"] == title[1]]
            if len(matches) == 1:
                numbers.update(matches)
        originals = [
            pulls[n] for n in numbers if n in pulls and n != number and pulls[n]["merged_at"] < fix["merged_at"]
        ]
        if not originals:
            unresolved.add(number)
        for original in originals:
            tag = shipped(original["merge_commit_sha"], fix["merged_at"])
            if tag:
                records.append(
                    dict(
                        pr=original["number"],
                        commit=original["merge_commit_sha"],
                        timestamp=fix["merged_at"],
                        fix=number,
                        tag=tag,
                    )
                )
    return records, unresolved


def manual_records(directory, pulls, resolve, shipped):
    """Read only identity and timestamp fields; never reproduce descriptions or customer evidence."""
    result, unresolved = [], set()
    for name in MANUAL_FILES:
        path = directory / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            original = pulls.get(row.get("pr")) or (resolve(row["commit"]) if row.get("commit") else None)
            if not original:
                unresolved.add(row.get("pr") or row["commit"])
                continue
            timestamp(row["timestamp"])
            tag = shipped(original["merge_commit_sha"], row["timestamp"])
            if tag:
                result.append(
                    dict(
                        pr=original["number"],
                        commit=original["merge_commit_sha"],
                        timestamp=row["timestamp"],
                        tag=tag,
                        source=f"reports/metrics/{name}",
                        confirmed_timestamp=row["timestamp"] if row.get("confirmed_customer") is True else None,
                    )
                )
    return result, unresolved


def unique_post_release_records(records):
    """Dedupe across all history before limiting the reporting window."""
    result = {}
    for row in sorted(records, key=lambda row: timestamp(row["timestamp"])):
        first = result.setdefault(row["pr"], dict(row))
        if row.get("confirmed_timestamp") and (
            not first.get("confirmed_timestamp")
            or timestamp(row["confirmed_timestamp"]) < timestamp(first["confirmed_timestamp"])
        ):
            first["confirmed_timestamp"] = row["confirmed_timestamp"]
    return list(result.values())


class History(main_red.Git):
    """Git containment plus release publication bounds for lightweight tags, which have no creation date."""

    def __init__(self, root, releases):
        super().__init__(root)
        if self.out("rev-parse", "--is-shallow-repository").strip() == "true":
            raise ValueError("full git history and release tags required")
        published = {
            row["tag_name"]: row["published_at"] for row in releases if not row["draft"] and row.get("published_at")
        }
        self.tags = {}
        for line in self.out(
            "for-each-ref", "--format=%(refname:short) %(taggerdate:iso-strict)", "refs/tags"
        ).splitlines():
            tag, _, tagged = line.partition(" ")
            if release_train.STABLE_TAG.fullmatch(tag) and (when := tagged or published.get(tag)):
                self.tags[tag] = timestamp(when)
        missing = {tag for tag in published if release_train.STABLE_TAG.fullmatch(tag)} - self.tags.keys()
        if missing:
            raise ValueError("missing release tags: " + ", ".join(sorted(missing)))
        self.containing = {}

    def commit_out(self, sha, *args):
        """Check external SHAs in this repository before any query; missing commits supply no evidence."""
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha) or not self.ok("cat-file", "-e", sha + "^{commit}"):
            return ""
        return self.out(*args, sha)

    def containing_tags(self, sha):
        if sha not in self.containing:
            self.containing[sha] = self.commit_out(sha, "tag", "--list", "v[0-9]*", "--contains").splitlines()
        return [tag for tag in self.containing[sha] if tag in self.tags]

    def shipped(self, sha, before):
        possible = [tag for tag in self.containing_tags(sha) if self.tags[tag] < timestamp(before)]
        return min(possible, key=self.tags.__getitem__) if possible else None


def collect(api, history, pulls):
    """Use commit messages for ordinary merges and API associations for branch commits and cherry-picks."""
    by_sha = {pr["merge_commit_sha"]: pr for pr in pulls.values()}
    cache = {}

    def resolve(sha):
        full = history.commit_out(sha, "rev-parse", "--verify").strip()
        if not full:
            return None
        if full in by_sha:
            return by_sha[full]
        if full not in cache:
            subject, _, body = history.commit_out(full, "show", "-s", "--format=%s%n%b").partition("\n")
            number = next(
                (int(match[1]) for pattern in freeze_guard.PR_SUBJECT if (match := pattern.search(subject))), None
            )
            if number is None and (picked := freeze_guard.CHERRY_PICK.search(body)):
                subject = history.commit_out(picked[1], "log", "-1", "--format=%s").strip()
                number = next(
                    (int(match[1]) for pattern in freeze_guard.PR_SUBJECT if (match := pattern.search(subject))), None
                )
            associated = (
                [pulls[number]]
                if number in pulls
                else [pulls[pr["number"]] for pr in api.pages(f"commits/{full}/pulls") if pr["number"] in pulls]
            )
            cache[full] = min(associated, key=lambda pr: pr["merged_at"]) if associated else None
        return cache[full]

    texts = {}
    backports = {
        pr["number"]
        for pr in pulls.values()
        if pr.get("base", {}).get("ref", "main") != "main" and history.containing_tags(pr["merge_commit_sha"])
    }
    # Tags include older release branches that did not merge back to main. No patch or working tree is read.
    log = history.out("log", "origin/main", "--tags=v[0-9]*", "--format=%H%x00%s%x00%b%x00")
    fields = log.split("\0")
    for i in range(0, len(fields) - 2, 3):
        sha, subject, body = fields[i : i + 3]
        text = subject + "\n" + body
        if (
            REVERT_SHA.search(text)
            or REVERT_OK.search(text)
            or re.match(r"Revert\b", subject, re.IGNORECASE)
            or HOTFIX.search(subject)
            or freeze_guard.CHERRY_PICK.search(body)
        ):
            if fix := resolve(sha.strip()):
                texts[fix["number"]] = texts.get(fix["number"], "") + "\n" + text
                if freeze_guard.CHERRY_PICK.search(body) and history.containing_tags(sha.strip()):
                    backports.add(fix["number"])
    records, unresolved = post_release_records(pulls, texts, resolve, history.shipped, api.repo, backports)
    manual, unmapped = manual_records(ROOT / "reports/metrics", pulls, resolve, history.shipped)
    return unique_post_release_records(records + manual), unresolved | unmapped


def queue_events(pr):
    """A pull request's merge queue adds and (time, reason) removals, oldest first; reasons are lower case."""
    adds, removals = [], []
    for node in pr.get("timelineItems", {}).get("nodes", []):
        if node.get("__typename") == "AddedToMergeQueueEvent":
            adds.append(timestamp(node["createdAt"]))
        elif node.get("__typename") == "RemovedFromMergeQueueEvent":
            removals.append((timestamp(node["createdAt"]), (node.get("reason") or "unknown").lower()))
    return sorted(adds), sorted(removals)


def queue_outcomes(runs, timelines):
    """(run, outcome) for each finished merge queue run on main: passed, failed, superseded, removed or not read.

    Fail-fast ends most failing runs cancelled, as a rebuilt run ends, so a run that did not pass is read from
    the queue's own record: the first removal of its pull request after the run started and before that pull
    request's next run. failed_checks is a failure; another reason (manual, a conflict, an invalid merge commit)
    is removed; no removal means the queue rebuilt the group (superseded), unless the run itself ended failure
    or timed_out. A pull request whose events were not read leaves its unpassed runs not read.
    """
    by_pr = defaultdict(list)
    for run in runs:
        match = QUEUE_REF.match(run.get("head_branch") or "")
        if match and match["base"] == "main" and run.get("status") == "completed":
            by_pr[int(match["pr"])].append(run)
    result = []
    for number, rows in by_pr.items():
        rows.sort(key=lambda row: (row["created_at"], row["id"]))
        removals = queue_events(timelines[number])[1] if number in timelines else None
        for i, run in enumerate(rows):
            if run.get("conclusion") == "success":
                result.append((run, "passed"))
                continue
            if removals is None:
                result.append((run, "not read"))
                continue
            started = timestamp(run["created_at"])
            following = timestamp(rows[i + 1]["created_at"]) if i + 1 < len(rows) else None
            reason = next(
                (
                    why
                    for when, why in removals
                    if why != "merged" and when >= started and (following is None or when <= following)
                ),
                None,
            )
            if reason == "failed_checks" or (reason is None and run.get("conclusion") in ("failure", "timed_out")):
                result.append((run, "failed"))
            else:
                result.append((run, "removed" if reason else "superseded"))
    return result


def queue_merges(timelines, since, now):
    """Pull requests merged into main through the queue in the window: minutes from first and last enqueue."""
    result = []
    for pr in timelines.values():
        if not pr.get("mergedAt") or pr.get("baseRefName") != "main":
            continue
        merged = timestamp(pr["mergedAt"])
        adds, removals = queue_events(pr)
        if not adds or not since <= merged <= now:
            continue
        result.append(
            dict(
                number=pr["number"],
                merged=merged,
                first=(merged - adds[0]).total_seconds() / 60,
                last=(merged - adds[-1]).total_seconds() / 60,
                thrown_out=any(why != "merged" and adds[0] <= when < merged for when, why in removals),
            )
        )
    return result


def percentile(values, share):
    """Nearest rank: the smallest value with at least `share` of the values at or below it."""
    values = sorted(values)
    return values[max(0, math.ceil(share * len(values)) - 1)] if values else None


def queue_report(runs, timelines, since, now):
    """The merge queue section: weekly outcomes of its runs, time to merge and removals."""
    outcomes = [
        (run, outcome)
        for run, outcome in queue_outcomes(runs, timelines)
        if since <= timestamp(run["created_at"]) <= now
    ]
    merges = queue_merges(timelines, since, now)
    removals = [
        (when, why)
        for pr in timelines.values()
        for when, why in queue_events(pr)[1]
        if why != "merged" and since <= when <= now
    ]
    lines = [
        "",
        "## Merge queue on main",
        "",
        "Goal: under 10% of queue runs fail for a week, and enqueue to merge p90 is 20 min or less.",
        "",
        "Each finished run of a merge queue group on main, in the week it started. Failed: the queue removed its "
        "pull request for failed checks (fail-fast ends most of these cancelled), or the run ended failure. Removed: "
        "taken out for another reason (manual, a conflict, an invalid merge commit). Superseded: cancelled and rebuilt "
        "with no removal, such as when a group ahead failed. Enqueue to merge runs from a merged pull request's first "
        "enqueue (time lost to removals included) and from its last one (the queue's own time); thrown out counts "
        "merged pull requests removed at least once. Removals count every removal of a pull request with a queue run "
        "in the window.",
        "",
        "| ISO week | Queue runs | Passed | Failed | Superseded | Removed | Merged | Enqueue to merge p50 / p90 (min) "
        "| Last enqueue to merge p90 (min) | Merged PRs thrown out | Removals by reason |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    week_outcomes = defaultdict(Counter)
    for run, outcome in outcomes:
        week_outcomes[iso_week(timestamp(run["created_at"]))][outcome] += 1
    week_merges = defaultdict(list)
    for row in merges:
        week_merges[iso_week(row["merged"])].append(row)
    week_removals = defaultdict(Counter)
    for when, why in removals:
        week_removals[iso_week(when)][why] += 1

    def share(count, total):
        return f"{count} ({100 * count / total:.1f}%)" if total else "0"

    def minutes(value):
        return "—" if value is None else f"{value:.0f}"

    cursor = since
    while cursor <= now:
        week = iso_week(cursor)
        counts, rows = week_outcomes[week], week_merges[week]
        total = sum(counts.values())
        first = [row["first"] for row in rows]
        reasons = ", ".join(f"{why} {count}" for why, count in sorted(week_removals[week].items())) or "—"
        lines.append(
            f"| {week} | {total} | "
            + " | ".join(share(counts[outcome], total) for outcome in QUEUE_OUTCOMES)
            + f" | {len(rows)} | {minutes(percentile(first, 0.5))} / {minutes(percentile(first, 0.9))} | "
            f"{minutes(percentile([row['last'] for row in rows], 0.9))} | "
            f"{share(sum(row['thrown_out'] for row in rows), len(rows))} | {reasons} |"
        )
        cursor += timedelta(weeks=1)
    unread = sum(outcome == "not read" for _, outcome in outcomes)
    if unread:
        lines += ["", f"Not read: {unread} queue runs whose pull request's queue events could not be read."]
    return lines


def report(repo, main_episodes, post_release, unresolved, since, now, pulls=(), queue=None):
    closed = [row for row in main_episodes if row["green"] and since <= timestamp(row["timestamp"]) <= now]
    flaky = sum(row["red"]["id"] == row["green"]["id"] for row in closed)
    closed = [row for row in closed if row["red"]["id"] != row["green"]["id"]]
    opened = [row for row in main_episodes if row["green"] is None and timestamp(row["timestamp"]) <= now]
    confirmed = [
        row
        for row in post_release
        if row.get("confirmed_timestamp") and since <= timestamp(row["confirmed_timestamp"]) <= now
    ]
    post_release = [row for row in post_release if since <= timestamp(row["timestamp"]) <= now]
    pulls = list(pulls)
    numbers = {pr["number"] for pr in pulls}
    unresolved = {
        pr["number"] for pr in pulls if pr["number"] in unresolved and since <= timestamp(pr["merged_at"]) <= now
    } | {origin for origin in unresolved if origin not in numbers}
    base = f"https://github.com/{repo}"
    weeks = Counter(iso_week(timestamp(row["timestamp"])) for row in closed)
    open_weeks = Counter(iso_week(timestamp(row["timestamp"])) for row in opened)
    merges = Counter(
        iso_week(timestamp(pr["merged_at"]))
        for pr in pulls
        if pr.get("base", {}).get("ref") == "main" and since <= timestamp(pr["merged_at"]) <= now
    )
    months = Counter(timestamp(row["timestamp"]).strftime("%Y-%m") for row in post_release)
    lines = [
        "# Verification metrics",
        "",
        f"UTC: {since:%Y-%m-%d} through {now:%Y-%m-%dT%H:%M:%SZ}.",
        "",
        f"## Red → green episodes on main: {len(closed)} closed, {len(opened)} open",
        "",
        f"Re-run passed (flaky): {flaky}",
        "",
        "Release validation (`ci.yml`), dispatched on `main`; closed episodes use the week of green completion. "
        "Open episodes use the week of red completion and remain visible even if they began before the window.",
        "",
        "Merges count merged PRs into main. Episodes per 100 merges uses closed episodes; — means no merges.",
        "",
        "| ISO week | Closed episodes | Open episodes | Merges into main | Episodes per 100 merges |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    cursor = since
    while cursor <= now:
        week = iso_week(cursor)
        rate = f"{100 * weeks[week] / merges[week]:.1f}" if merges[week] else "—"
        lines.append(f"| {week} | {weeks[week]} | {open_weeks[week]} | {merges[week]} | {rate} |")
        cursor += timedelta(weeks=1)
    lines += ["", "| ISO week | Status | Red run | Green run | Candidate SHA |", "| --- | --- | --- | --- | --- |"]
    for row in closed + opened:
        red, green = row["red"], row["green"]
        green_link = f"[{green['id']}]({base}/actions/runs/{green['id']})" if green else "—"
        lines.append(
            f"| {iso_week(timestamp(row['timestamp']))} | {'closed' if green else 'open'} | "
            f"[{red['id']}]({base}/actions/runs/{red['id']}) | {green_link} | {main_red.candidate(green or red)} |"
        )
    lines += [
        "",
        f"## Post-release reverts and hotfixes: {len(post_release)}",
        "",
        "One per originating PR; month of first fix merge or manual discovery. A containing release must predate that evidence.",
        "",
        "Lightweight tags use release publication as a conservative creation bound. Only retained GitHub runs can be reconstructed. Boundary weeks and months are partial.",
        "",
        f"Confirmed customer reports: {len(confirmed)}. "
        + ", ".join(f"[#{row['pr']}]({base}/pull/{row['pr']})" for row in confirmed),
        "",
        "| UTC month | Post-release reverts and hotfixes |",
        "| --- | ---: |",
    ]
    for month, count in sorted(months.items()):
        lines.append(f"| {month} | {count} |")
    lines += [
        "",
        "| UTC month | Originating PR | Merge SHA | Release | Fix or record |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in post_release:
        evidence = (
            f"[#{row['fix']}]({base}/pull/{row['fix']})"
            if row.get("fix")
            else f"[{row['source']}]({base}/blob/main/{row['source']})"
        )
        lines.append(
            f"| {timestamp(row['timestamp']):%Y-%m} | [#{row['pr']}]({base}/pull/{row['pr']}) | {row['commit']} | "
            f"[{row['tag']}]({base}/releases/tag/{row['tag']}) | {evidence} |"
        )
    lines += [
        "",
        f"Unattributed revert/hotfix PRs or manual origins (in this window): {len(unresolved)}. These require an originating PR or a manual record.",
        "",
    ]
    lines += [
        f"- [#{origin}]({base}/pull/{origin})" if isinstance(origin, int) else f"- manual record {origin}"
        for origin in sorted(unresolved, key=str)
    ]
    if isinstance(queue, str):
        lines += ["", "## Merge queue on main", "", f"Not read: {queue}."]
    elif queue is not None:
        lines += queue_report(*queue, now)
    return "\n".join(lines) + "\n"


def html_page(markdown):
    """Render this report's headings, tables and links without a Markdown dependency."""
    lines = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Verification metrics</title>",
        "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}table{border-collapse:collapse}td{border:1px solid #aaa;padding:8px;overflow-wrap:anywhere}a{color:#1659b7}</style><body>",
    ]
    table = False
    for line in markdown.splitlines():
        is_row = line.startswith("|")
        if table and not is_row:
            lines.append("</table>")
            table = False
        escaped = html.escape(line)
        escaped = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r'<a href="\2">\1</a>', escaped)
        if is_row:
            if re.fullmatch(r"[| :\-]+", line):
                continue
            if not table:
                lines.append("<table>")
                table = True
            lines.append(
                "<tr>" + "".join(f"<td>{cell.strip()}</td>" for cell in escaped.strip("|").split("|")) + "</tr>"
            )
        elif line.startswith("#"):
            heading, _, content = escaped.partition(" ")
            level = len(heading)
            lines.append(f"<h{level}>{content}</h{level}>")
        elif line:
            lines.append(f"<p>{escaped}</p>")
    return "\n".join(lines) + ("</table>" if table else "") + "</body></html>\n"


def queue_health(repo, since, now, weeks):
    """(runs, timelines, since) for the queue section, or why it could not be read: the rest of the report stands."""
    since = max(since, (now - timedelta(days=now.weekday(), weeks=weeks - 1)).replace(hour=0, minute=0, second=0))
    try:
        api = GitHub(repo, budget=400)
        # A day before the window: a pull request merged early in it may have started its queue runs then.
        runs = api.queue_runs(since - timedelta(days=1), now)
        numbers = {int(match["pr"]) for run in runs if (match := QUEUE_REF.match(run.get("head_branch") or ""))}
        return runs, api.queue_timelines(numbers), since
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=12)
    parser.add_argument("--html", type=Path, help="write the HTML artifact here")
    parser.add_argument("--queue-weeks", type=int, default=4, help="weeks of merge queue health to report")
    args = parser.parse_args(argv)
    if args.weeks < 1 or args.queue_weeks < 1:
        parser.error("--weeks and --queue-weeks must be positive")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    since = (now - timedelta(days=now.weekday(), weeks=args.weeks - 1)).replace(hour=0, minute=0, second=0)
    try:
        api = GitHub(os.environ.get("GITHUB_REPOSITORY", "ticoteam/tico"))
        born = timestamp(api.get("")["created_at"])
        pulls = {pr["number"]: pr for pr in api.pages("pulls", state="closed") if pr.get("merged_at")}
        history = History(ROOT, api.pages("releases"))
        runs = [run for run in validation_runs(api, born, since, now) if timestamp(run["updated_at"]) <= now]
        post_release, unresolved = collect(api, history, pulls)
        queue = queue_health(api.repo, since, now, args.queue_weeks)
        markdown = report(api.repo, episodes(runs), post_release, unresolved, since, now, pulls.values(), queue)
        print(markdown, end="")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a") as stream:
                stream.write(markdown)
        if args.html:
            args.html.write_text(html_page(markdown))
    except (OSError, ValueError, subprocess.SubprocessError, freeze_guard.GuardError) as error:
        print(f"metrics: cannot compute: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
