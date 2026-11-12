"""SM-09: two different GitHub webhooks for one PR can carry the same `updated_at`, and GitHub promises no delivery
order. Neither may leave a stale fact that is never read again.

- Only a merged PR is final; an unmerged close is read again like an open PR while a task waits on it.
- A save at the same version as the saved row that says something different marks the PR due, in the same
  transaction, so the next pass reads GitHub and the saved fact returns to what GitHub says.

Real task and links, webhooks the way the server takes them, the in-memory GitHub: the state-machines audit's B10
reproduction harness.
"""

import importlib.util
from pathlib import Path

import pytest

from backend import item_metadata as IM
from backend.item_sources import github as G
from backend.store import H
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import REPO, engineering, new

KEY = G.pr_key(REPO, 1)


class World:
    def __init__(self, store):
        self.store = store
        self.gh = FakeGitHub()

    def tx(self):
        return self.store.transaction()


class Seam:
    @staticmethod
    def webhook(c, event, payload, delivery=None):
        """A webhook as the server's route takes it (backend/github.py): a repeated delivery is dropped ("repeat"),
        then the metadata step, then the older PR pass."""
        from backend import github as GH

        if G.seen_delivery(c, delivery, event):
            return "repeat"
        found = G.webhook(c, event, payload)
        GH.pull_request(c, payload)
        return found

    @staticmethod
    def refresh(store, gh, now):
        return G.refresh(store, gh, now)


def pr_payload(gh, action, number=1, updated_at=None):
    """GitHub's pull_request webhook built from the fake's PR as it is now."""
    pr = gh.prs[(REPO, number)]
    snapshot = {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": "closed" if pr["state"] != "open" else "open",
        "merged": pr["state"] == "merged",
        "merge_commit_sha": pr["merged_sha"],
        "merged_at": pr["merged_at"],
        "draft": pr["draft"],
        "head": {"sha": pr["head_sha"], "ref": "feature"},
        "updated_at": updated_at,
    }
    return {"action": action, "pull_request": snapshot, "repository": {"full_name": REPO}}


def _stamp(seconds=0):
    """An `updated_at` as GitHub writes it (whole seconds, Z), `seconds` from now."""
    return H.shift(H.now(), seconds=seconds).split(".")[0] + "Z"


def _row(c):
    return IM.get(c, "github", "pr", KEY)


def _due(c, minutes=20):
    return G.due_reads(c, H.shift(H.now(), minutes=minutes))


def _opened(w):
    """A task in Build that links PR 1, open on GitHub and saved from its `opened` webhook a minute ago."""
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened", updated_at=_stamp(-60)), delivery="opened")
    return task_id, head


def _close_reopen(w, head, stamp):
    """GitHub closes the PR and reopens it in the same second: (close payload, reopen payload). GitHub ends open."""
    w.gh.close(REPO, 1)
    closed = pr_payload(w.gh, "closed", updated_at=stamp)
    w.gh.open_pr(REPO, 1, head=head)
    reopened = pr_payload(w.gh, "reopened", updated_at=stamp)
    return closed, reopened


@pytest.mark.parametrize("order", ["reopen_first", "close_first"])
def test_same_time_close_and_reopen_end_at_githubs_state_in_either_order(company, order):
    w = World(company)
    _, head = _opened(w)
    closed, reopened = _close_reopen(w, head, _stamp())
    first, second = (reopened, closed) if order == "reopen_first" else (closed, reopened)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", first, delivery="first")
        Seam.webhook(c, "pull_request", second, delivery="second")
        row = _row(c)
        # Whatever the second said, the tie is not final and the PR is read again on the next pass.
        assert row["final_at"] is None
        assert row["due_at"] is not None
        assert _due(c, minutes=0) == [(REPO, 1)]
    Seam.refresh(company, w.gh, H.now())
    with w.tx() as c:
        assert G.pr(c, REPO, 1)["state"] == "open" == w.gh.prs[(REPO, 1)]["state"]
        assert _row(c)["due_at"] is None


@pytest.mark.parametrize("order", ["newer_first", "older_first"])
def test_same_time_head_change_ends_at_githubs_head_in_either_order(company, order):
    w = World(company)
    _, head = _opened(w)
    stamp = _stamp()
    a = pr_payload(w.gh, "synchronize", updated_at=stamp)
    newest = w.gh.push(REPO, 1)
    b = pr_payload(w.gh, "synchronize", updated_at=stamp)
    first, second = (b, a) if order == "newer_first" else (a, b)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", first, delivery="sync-1")
        Seam.webhook(c, "pull_request", second, delivery="sync-2")
        assert _row(c)["due_at"] is not None
    Seam.refresh(company, w.gh, H.now())
    with w.tx() as c:
        assert G.pr(c, REPO, 1)["head_sha"] == newest != head


def test_a_repeated_delivery_is_dropped_and_marks_nothing_due(company):
    w = World(company)
    _opened(w)
    w.gh.close(REPO, 1)
    closed = pr_payload(w.gh, "closed", updated_at=_stamp())
    with w.tx() as c:
        Seam.webhook(c, "pull_request", closed, delivery="close")
        assert Seam.webhook(c, "pull_request", closed, delivery="close") == "repeat"
        assert _row(c)["due_at"] is None
        assert G.pr(c, REPO, 1)["state"] == "closed"


def test_a_distinct_delivery_saying_the_same_at_the_same_time_marks_nothing_due(company):
    w = World(company)
    _opened(w)
    w.gh.close(REPO, 1)
    stamp = _stamp()
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed", updated_at=stamp), delivery="close-a")
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed", updated_at=stamp), delivery="close-b")
        assert _row(c)["due_at"] is None
        assert G.pr(c, REPO, 1)["state"] == "closed"


def test_an_older_webhook_still_loses(company):
    w = World(company)
    _, head = _opened(w)
    w.gh.close(REPO, 1)
    older = pr_payload(w.gh, "closed", updated_at=_stamp(-30))
    w.gh.open_pr(REPO, 1, head=head)
    newer = pr_payload(w.gh, "reopened", updated_at=_stamp())
    with w.tx() as c:
        Seam.webhook(c, "pull_request", newer, delivery="reopen")
        version = _row(c)["metadata_version"]
        assert Seam.webhook(c, "pull_request", older, delivery="late-close") == []
        assert G.pr(c, REPO, 1)["state"] == "open"
        assert _row(c)["metadata_version"] == version


def test_an_unmerged_close_is_read_again_and_a_reopen_without_a_webhook_is_seen(company):
    w = World(company)
    _, head = _opened(w)
    w.gh.close(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed", updated_at=_stamp()), delivery="close")
        assert _row(c)["final_at"] is None
        assert _due(c) == [(REPO, 1)]
    w.gh.open_pr(REPO, 1, head=head)  # the reopen's webhook never comes
    Seam.refresh(company, w.gh, H.shift(H.now(), minutes=20))
    with w.tx() as c:
        assert G.pr(c, REPO, 1)["state"] == "open"


def test_a_merged_pr_never_reopens(company):
    w = World(company)
    _, head = _opened(w)
    w.gh.merge(REPO, 1)
    stamp = _stamp()
    merged = pr_payload(w.gh, "closed", updated_at=stamp)
    w.gh.prs[(REPO, 1)].update(state="open", merged_sha=None, merged_at=None)
    reopened = pr_payload(w.gh, "reopened", updated_at=stamp)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", merged, delivery="merge")
        Seam.webhook(c, "pull_request", reopened, delivery="reopen")
        assert G.pr(c, REPO, 1)["state"] == "merged"
        assert _row(c)["final_at"] is not None
        assert _row(c)["due_at"] is None
        assert _due(c) == []


def test_tico_s_own_reads_never_share_a_version():
    seen = [G.version_now() for _ in range(1000)]
    assert seen == sorted(set(seen))


def test_the_migration_lets_an_unmerged_closed_pr_be_read_again(company):
    path = next(Path(G.__file__).parents[1].glob("migrations/*_unfinal_closed_github_prs.py"))
    spec = importlib.util.spec_from_file_location("unfinal", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    w = World(company)
    _opened(w)
    w.gh.merge(REPO, 1)
    with w.tx() as c:
        new(c, 2)
        w.gh.open_pr(REPO, 2)
        w.gh.close(REPO, 2)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed", 2, updated_at=_stamp()), delivery="c2")
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed", updated_at=_stamp()), delivery="m1")
        # As the old code left them: every non-open PR final.
        c.execute("UPDATE item_metadata SET final_at=updated_at WHERE source='github'")
        module.up(c)
        finals = dict(c.execute("SELECT item_key, final_at IS NOT NULL FROM item_metadata").fetchall())
    assert finals == {KEY: 1, G.pr_key(REPO, 2): 0}
