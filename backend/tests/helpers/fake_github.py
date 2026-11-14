"""A GitHub in memory that answers `item_sources.github.GitHub` (backend/item_sources/github.py), for unit tests and the fault rig.

It holds PRs, the checks on each commit, a linear main history per repo (a merge appends its commit) and the published
releases, and the releases asked for (`cuts`). Calls are recorded in `calls`. `fail_next(call, reason)` makes the next call of that name raise with the
reason, as a GitHub error would. As on GitHub, enqueue refuses a draft and a PR the base branch's rules refuse
(`rule`), and, on a repo whose ruleset requires Tico's `tico/merge-ok` check (`require_merge_ok`), a PR whose head has
no successful one: what a bot's own `gh pr merge` meets. `group` makes a merge group and its `merge_group` webhook.
The rig (evals/never_stuck/fake_github.py) builds its faults on top: lost, late, repeated and reordered webhooks, and
queue drops.
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
        self.merge_ok_required = set()  # repos whose ruleset requires tico/merge-ok
        self.merge_ok = {}  # (repo, sha) -> [(ok, summary)] Tico posted, oldest first; the newest one counts
        self.checks_write = True  # the App has Checks: write; without it post_check posts nothing
        self.groups = {}  # (repo, merge group head sha) -> [PR numbers], first in the queue first
        self.cuts = []  # the releases asked for: (repo, kind, name, sha)
        self.withheld = set()  # permissions Tico's App lacks here ("actions", "contents"): cut_release answers no
        self.release_on_cut = None  # a tag: asking for a release publishes it at once, as a release workflow does
        self.diffs = {}  # (repo, sha) -> what the PR changes at that commit (pr_diff); each commit its own unless set
        self.commits = {}  # (repo, sha) -> the PR's [[sha, title]] up to that commit (pr_diff); None unless set
        self.files = {}  # (repo, sha) -> {file name: id of its part of the diff} at that commit (pr_diff); None unless set
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

    def rebase(self, repo, number, head=None):
        """A new commit on the PR that changes the same lines as the one before (a rebase, or a conflict fixed without
        changing the PR's own lines): `pr_diff` gives both the same id."""
        before = self.prs[(repo, number)]["head_sha"]
        head = self.push(repo, number, head)
        self.diffs[(repo, head)] = self.diffs.get((repo, before), f"diff-{before}")
        return head

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

    def mergeable(self, repo, number, state):
        """What GitHub says of merging the PR into its base branch: MERGEABLE, CONFLICTING or UNKNOWN (still working it
        out). `enqueue` refuses a conflicting PR and raises while it is unknown."""
        self.prs[(repo, number)]["mergeable"] = state

    def require_merge_ok(self, repo):
        """The repo's ruleset requires `tico/merge-ok` from the company's App."""
        self.merge_ok_required.add(repo)

    def merge_ok_on(self, repo, sha):
        """The newest `tico/merge-ok` on a commit: True (success), False (failure), "neutral" or None (none posted)."""
        posted = self.merge_ok.get((repo, sha))
        return posted[-1][0] if posted else None

    def group(self, repo, *numbers):
        """GitHub builds a merge group of the PRs (first in the queue first) and asks for its checks: the
        `merge_group` `checks_requested` webhook's payload."""
        head = self.sha()
        self.groups[(repo, head)] = list(numbers)
        return {
            "action": "checks_requested",
            "merge_group": {
                "head_sha": head,
                "head_ref": f"refs/heads/gh-readonly-queue/main/pr-{numbers[-1]}-{head}",
                "base_ref": "refs/heads/main",
            },
            "repository": {"full_name": repo},
        }

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

    def pr_diff(self, repo, number, sha):
        self._call("pr_diff", repo, number, sha)
        return {
            "diff_id": self.diffs.get((repo, sha), f"diff-{sha}"),
            "commits": self.commits.get((repo, sha)),
            "files": self.files.get((repo, sha)),
        }

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
        from backend.item_sources import github as GS

        if pr.get("mergeable") == "CONFLICTING":
            return {"ok": False, "how": None, "reason": GS.CONFLICTS}
        if pr.get("mergeable") == "UNKNOWN":
            raise GitHubError(GS.MERGEABLE_UNKNOWN)
        if repo in self.merge_ok_required and not self.merge_ok_on(repo, head_sha):
            return {"ok": False, "how": None, "reason": 'Required status check "tico/merge-ok" is expected.'}
        pr["queue_state"] = "queued" if self.queue else "auto_merge"
        pr.update(removed_reason=None, failing_check=None, failing_check_url=None)
        return {"ok": True, "how": pr["queue_state"], "reason": ""}

    def cut_release(self, repo, kind, name, sha):
        """Run the release workflow, or create the tag; with `release_on_cut` (or a tag) the release is published at
        once. A permission in `withheld` is a not-ok answer with the reason Tico's App gives."""
        from backend.item_sources import github_release as GR

        self._call("cut_release", repo, kind, name, sha)
        needed = "actions" if kind == "workflow" else "contents"
        if needed in self.withheld:
            return {
                "ok": False,
                "how": None,
                "reason": GR.MISSING_ACTIONS if kind == "workflow" else GR.MISSING_CONTENTS,
            }
        self.cuts.append((repo, kind, name, sha))
        tag = name if kind == "tag" else self.release_on_cut
        if tag:
            self.publish(repo, tag)
        return {"ok": True, "how": f"{kind} {name}", "reason": ""}

    def dequeue(self, repo, number):
        self._call("dequeue", repo, number)
        pr = self.prs.get((repo, number))
        if pr and pr["state"] == "merged":
            return {"ok": True, "how": "merged", "reason": ""}
        if not pr or pr["queue_state"] not in ("queued", "auto_merge"):
            return {"ok": True, "how": "absent", "reason": ""}
        pr.update(queue_state="none", removed_reason=None, failing_check=None, failing_check_url=None)
        return {"ok": True, "how": "dequeued", "reason": ""}

    def post_check(self, repo, sha, ok, summary):
        self._call("post_check", repo, sha, ok)
        if not self.checks_write:
            return False
        self.merge_ok.setdefault((repo, sha), []).append(("neutral" if ok is None else bool(ok), summary))
        return True

    def merge_group_prs(self, repo, base_ref, head_sha):
        self._call("merge_group_prs", repo, head_sha)
        numbers = self.groups.get((repo, head_sha), [])
        return [{"number": n, "head_sha": self.prs[(repo, n)]["head_sha"]} for n in numbers]
