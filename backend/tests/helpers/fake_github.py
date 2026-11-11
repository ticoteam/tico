"""A GitHub in memory that answers `item_sources.github.GitHub` (backend/item_sources/github.py), for unit tests and the fault rig.

It holds PRs, the checks on each commit, a linear main history per repo (a merge appends its commit) and the published
releases. Calls are recorded in `calls`. `fail_next(call, reason)` makes the next call of that name raise with the
reason, as a GitHub error would. As on GitHub, enqueue refuses a draft and a PR the base branch's rules refuse
(`rule`). The rig (evals/never_stuck/fake_github.py) builds its faults on top: lost, late,
repeated and reordered webhooks, and queue drops.
"""

import itertools


class GitHubError(RuntimeError):
    """A call GitHub refused; the message is GitHub's reason."""


class FakeGitHub:
    def __init__(self, *, queue=True):
        self.queue = queue  # the repo has a merge queue; without one enqueue turns on auto-merge
        self.prs = {}  # (repo, number) -> {state, draft, head_sha, merged_sha, merged_at, queue_state, removed_reason, ...}
        self.checks = {}  # (repo, sha) -> [{name, app, state, url, tico}]
        self.history = {}  # repo -> [sha, ...], oldest first: what main contains
        self.published = {}  # repo -> [{tag, commit_sha, prerelease, published_at}], newest first
        self.calls = []
        self._fail = {}
        self._shas = (f"{n:040x}" for n in itertools.count(1))
        self._run_ids = itertools.count(1)  # check run ids only grow, as GitHub's do

    # ------------------------------------------------------------------ what a test does to it
    def sha(self):
        """A fresh commit sha."""
        return next(self._shas)

    def open_pr(self, repo, number, head=None, draft=False):
        head = head or self.sha()
        self.prs[(repo, number)] = {
            "state": "open",
            "draft": draft,
            "head_sha": head,
            "merged_sha": None,
            "merged_at": None,
            "queue_state": "none",
            "removed_reason": None,
            "failing_check": None,
            "failing_check_url": None,
        }
        return head

    def push(self, repo, number, head=None):
        """A new commit on the PR: it leaves the queue, as GitHub does."""
        pr = self.prs[(repo, number)]
        pr["head_sha"] = head or self.sha()
        if pr["queue_state"] in ("queued", "auto_merge"):
            self._remove(pr, "A new commit was pushed")
        return pr["head_sha"]

    def set_checks(self, repo, sha, *states, tico=False):
        """The checks on a commit, one per state given: pending, success or failure."""
        self.checks[(repo, sha)] = [
            {"name": f"check-{i}", "app": "tico" if tico else "ci", "state": s, "url": "", "tico": tico}
            for i, s in enumerate(states, 1)
        ]

    def run(self, repo, sha, name, state, *, suite=1, app="ci"):
        """One more check run on a commit, as GitHub lists them: `suite` is its check suite (one per workflow run), and
        a run in a suite that already has that name is a re-run. Its id is newer than every run before it."""
        row = {"name": name, "app": app, "state": state, "url": "", "tico": False, "suite": suite}
        self.checks.setdefault((repo, sha), []).append({**row, "id": next(self._run_ids)})

    def close(self, repo, number):
        self.prs[(repo, number)]["state"] = "closed"

    def merge(self, repo, number, at="2026-11-10T00:00:00.000000Z"):
        """GitHub merges the PR: its merge commit goes on main."""
        pr = self.prs[(repo, number)]
        sha = self.sha()
        pr.update(state="merged", merged_sha=sha, merged_at=at, queue_state="none", removed_reason=None)
        self.history.setdefault(repo, []).append(sha)
        return sha

    def drop(self, repo, number, reason="Checks failed in the merge queue", check=None, url=None):
        """GitHub takes the PR out of the queue without merging it; `check` is the check that failed there."""
        self._remove(self.prs[(repo, number)], reason, check, url)

    def vanish(self, repo, number):
        """The PR leaves the queue unmerged and GitHub says nothing of it: no removal event, no reason."""
        self.prs[(repo, number)].update(
            queue_state="none", removed_reason=None, failing_check=None, failing_check_url=None
        )

    @staticmethod
    def _remove(pr, reason, check=None, url=None):
        pr.update(queue_state="removed", removed_reason=reason, failing_check=check, failing_check_url=url)

    def publish(self, repo, tag, prerelease=False, at="2026-11-10T00:00:00.000000Z"):
        """Publish a release at main's newest commit."""
        head = (self.history.get(repo) or [None])[-1] or self.sha()
        if head not in self.history.setdefault(repo, []):
            self.history[repo].append(head)
        release = {"tag": tag, "commit_sha": head, "prerelease": prerelease, "published_at": at}
        self.published.setdefault(repo, []).insert(0, release)
        return release

    def rule(self, repo, number, reason="At least 1 approving review is required by reviewers with write access."):
        """The base branch's rules refuse the PR (a required review or check is missing): enqueue answers no with
        `reason` until `rule(repo, number, None)` lifts it."""
        self.prs[(repo, number)]["rule"] = reason

    def fail_next(self, call, reason="GitHub is unavailable"):
        self._fail[call] = reason

    # ------------------------------------------------------------------ item_sources.github.GitHub
    def _call(self, name, *args):
        self.calls.append((name, *args))
        reason = self._fail.pop(name, None)
        if reason:
            raise GitHubError(reason)

    def read_pr(self, repo, number):
        self._call("read_pr", repo, number)
        found = self.prs.get((repo, number))
        return dict(found) if found else None

    def read_checks(self, repo, sha):
        self._call("read_checks", repo, sha)
        from backend.item_sources import github as GS

        return [dict(c) for c in GS.latest_checks(self.checks.get((repo, sha), []))]

    def read_releases(self, repo):
        self._call("read_releases", repo)
        return [dict(r) for r in self.published.get(repo, [])]

    def contains(self, repo, release_sha, commit_sha):
        self._call("contains", repo, release_sha, commit_sha)
        line = self.history.get(repo, [])
        if commit_sha not in line or release_sha not in line:
            return False
        return line.index(commit_sha) <= line.index(release_sha)

    def ready(self, repo, number):
        self._call("ready", repo, number)
        pr = self.prs.get((repo, number))
        if pr and pr["state"] == "open":
            pr["draft"] = False

    def enqueue(self, repo, number, head_sha):
        self._call("enqueue", repo, number, head_sha)
        pr = self.prs.get((repo, number))
        if not pr or pr["state"] != "open":
            return {"ok": False, "how": None, "reason": "The pull request is not open"}
        if pr["head_sha"] != head_sha:
            return {"ok": False, "how": None, "reason": "The head moved"}
        if pr["draft"]:  # GitHub queues and merges no draft
            return {"ok": False, "how": None, "reason": "Pull request is in draft state"}
        if pr.get("rule"):
            return {"ok": False, "how": None, "reason": pr["rule"]}
        pr["queue_state"] = "queued" if self.queue else "auto_merge"
        pr.update(removed_reason=None, failing_check=None, failing_check_url=None)
        return {"ok": True, "how": pr["queue_state"], "reason": ""}

    def dequeue(self, repo, number):
        self._call("dequeue", repo, number)
        pr = self.prs.get((repo, number))
        if pr and pr["state"] == "merged":
            return {"ok": True, "how": "merged", "reason": ""}
        if not pr or pr["queue_state"] not in ("queued", "auto_merge"):
            return {"ok": True, "how": "absent", "reason": ""}
        pr.update(queue_state="none", removed_reason=None, failing_check=None, failing_check_url=None)
        return {"ok": True, "how": "dequeued", "reason": ""}
