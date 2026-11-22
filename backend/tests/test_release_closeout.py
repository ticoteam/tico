"""Release close-out of a task filed for a Jira issue (backend/release_closeout.py): its merge proven in an actual
release tag queues the Jira transition to Done, one release comment and a reply in the task's Slack thread, each once;
a Jira issue withdrawn mid-work or closed outside Tico freezes the close-out behind ONE owner ask, taken back when the
issue is back in Ready; an external outbound action whose outcome stays unknown asks its owner once
(backend/owner_asks.py). GitHub is backend/tests/helpers/fake_github.py and Jira a fake answer at the pass's sender;
nothing real is reached."""

import dataclasses
import json

import pytest

from backend import outbound_actions as OA
from backend import release_closeout as RC
from backend import slack_bots as SB
from backend.item_sources import claim
from backend.item_sources import github as F
from backend.item_sources import jira as JS
from backend.store import H
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.release_cut import REPO, engineering, new, stage, to_release

HOST = "acme.atlassian.net"
TOOL = "tool-jira-acme"
ISSUE = "HM-12"
THREAD = "https://acme.slack.com/archives/C0BUGS123/p1763700000123456?thread_ts=1763700000.000100&cid=C0BUGS123"


@pytest.fixture
def gh():
    found = FakeGitHub()
    found.history[REPO] = [found.sha()]
    found.publish(REPO, "v0.9.0")
    return found


@pytest.fixture
def jira(company, monkeypatch):
    """A Jira site that is a record integration with a shared read and write key, the #bugs channel on the Slack
    list with posting on, and every bot declaring Slack post access. `jira.status` is what the pass reads next."""
    now = H.now()
    with company.transaction() as c:
        c.execute(
            "INSERT INTO tools (id, name, service, base_url, auth_scheme, created_by, created_at, updated_at, "
            "record_owner) VALUES (?, 'Jira Acme', '', ?, 'bearer', 'human:ana', ?, ?, 'outside')",
            (TOOL, f"https://{HOST}", now, now),
        )
        c.execute(
            "INSERT INTO credentials (id, name, kind, ciphertext, created_at, updated_at, updated_by, tool_id, "
            "access_level) VALUES ('cred-jira', 'Jira key', 'api_key', X'00', ?, ?, 'human:ana', ?, 'read_write')",
            (now, now, TOOL),
        )
        c.execute(
            "INSERT INTO slack_channels (key, channel_id, name, post, created_at, updated_at) "
            "VALUES ('bugs', 'C0BUGS123', 'bugs', 1, ?, ?)",
            (now, now),
        )
    monkeypatch.setattr(SB, "slack_entry", lambda c, settings, bot: {"service": "slack", "can": ["read", "post"]})

    class Jira:
        status = ("Ready", "new")
        passes = 0

        def read(self, status, category="new"):
            """One Jira pass that reads the issue as `status` (`category` its status category)."""
            self.status = (status, category)
            self.passes += 1

            class Vault:
                class cipher:  # noqa: N801 - the vault's attribute
                    @staticmethod
                    def decrypt(c, credential):
                        return "jira-synthetic-token"

            def sender(url, headers, params, seconds):
                if self.status[0] is None:
                    return 404, {"errorMessages": ["Issue does not exist"]}
                found = {"name": self.status[0], "statusCategory": {"key": self.status[1]}}
                return 200, {"fields": {"status": found, "updated": f"2026-11-21T10:{self.passes:02d}:00.000+0000"}}

            JS.refresh(company, Vault(), now=H.shift(H.now(), minutes=10 * self.passes), sender=sender)

    return Jira()


def filed(c, task_id, slack=True):
    """The task's primary link to HM-12 (as find-or-create makes it), and its originating Slack thread."""
    H.task_link(c, "human:ana", task_id, f"https://{HOST}/browse/{ISSUE}", item=("jira", "issue", ISSUE, TOOL))
    claim(c, task_id, ("jira", "issue", ISSUE, TOOL))
    if slack:
        H.task_link(c, "human:ana", task_id, THREAD)


def actions(c, task_id):
    return {
        str(r["operation"]): dict(r)
        for r in c.execute("SELECT * FROM outbound_actions WHERE task_id=? ORDER BY created_at", (task_id,))
    }


def replies(c):
    return [dict(r) for r in c.execute("SELECT * FROM slack_posts ORDER BY created_at")]


def asks(c, task_id):
    """Tico's asks on the task: (person, line, withdrawn)."""
    return [
        (str(r[0]), str(r[1]), r[2] is not None)
        for r in c.execute(
            "SELECT r.person_id, r.line, r.withdrawn_at FROM requirements r JOIN visits v ON v.id = r.visit_id "
            "WHERE v.task_id=? AND r.added_by_id=? ORDER BY r.created_at",
            (task_id, H.TICO_ACTOR),
        )
    ]


def released(company, gh, tag="v1.0.0", minutes=20):
    gh.publish(REPO, tag)
    F.refresh(company, gh, H.shift(H.now(), minutes=minutes))


def test_a_release_queues_the_jira_done_comment_and_slack_reply_once(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh)
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"
        found = actions(c, task_id)
        assert set(found) == {"transition", "comment"}
        assert all(r["destination"] == ISSUE and r["connection_id"] == TOOL for r in found.values())
        assert json.loads(found["transition"]["payload"]) == {"to": "Done"} and found["transition"]["state"] == "queued"
        link = f"https://github.com/{REPO}/releases/tag/v1.0.0"
        assert f"Released in v1.0.0: {link}" in found["comment"]["payload"]
        [reply] = replies(c)
        assert (reply["channel"], reply["thread_ts"], reply["bot"]) == ("C0BUGS123", "1763700000.000100", "release")
        assert reply["text"] == f"Released in v1.0.0: {link}"
        # A replay of every trigger adds nothing: the move, the close-out itself, the release read again.
        assert RC.moved(c, task_id) == 0 and RC.close_out(c, task_id) == 0
    released(company, gh, tag="v1.0.1", minutes=40)
    with company.transaction() as c:
        assert len(actions(c, task_id)) == 2 and len(replies(c)) == 1


def test_no_slack_thread_no_reply(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id, slack=False)
    released(company, gh)
    with company.transaction() as c:
        assert set(actions(c, task_id)) == {"transition", "comment"} and replies(c) == []


def test_a_failed_transition_neither_blocks_nor_repeats_the_comment_or_reply(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh)
    with company.transaction() as c:
        found = actions(c, task_id)
        assert OA.settle(c, found["transition"], "failed", error="Jira offers no transition to 'Done'")
        assert RC.close_out(c, task_id) == 0
        after = actions(c, task_id)
        assert after["transition"]["state"] == "failed" and after["comment"]["state"] == "queued"
        assert after["comment"]["id"] == found["comment"]["id"] and len(after) == 2 and len(replies(c)) == 1


def test_withdrawn_mid_work_freezes_the_close_out_behind_one_ask_and_ready_again_resumes_the_same_task(
    company, gh, jira
):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    jira.read("Ready")  # the status it was filed in
    jira.read("Backlog")
    jira.read("Backlog")
    released(company, gh)
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"  # held by the ask
        assert actions(c, task_id) == {} and replies(c) == []
        [(person, line, withdrawn)] = asks(c, task_id)
        assert H.is_human(person) and line.startswith(f"{ISSUE} left Ready in Jira (Backlog)") and not withdrawn
    jira.read("Ready")
    with company.transaction() as c:
        [(_, _, withdrawn)] = asks(c, task_id)
        assert withdrawn and stage(c, task_id) == "Done"
        assert set(actions(c, task_id)) == {"transition", "comment"} and len(replies(c)) == 1
        assert c.execute("SELECT COUNT(*) FROM task_links WHERE is_primary=1").fetchone()[0] == 1  # the same task


def test_closed_outside_asks_settle_or_keep_once_and_a_reopen_takes_it_back(company, jira):
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 7)
        filed(c, task_id)
    jira.read("Ready")
    jira.read("Done", "done")
    jira.read("Done", "done")
    with company.transaction() as c:
        [(_, line, withdrawn)] = asks(c, task_id)
        assert line.startswith(f"{ISSUE} was closed in Jira (Done)") and "settle" in line and not withdrawn
    jira.read("Ready")
    with company.transaction() as c:
        assert [w for _, _, w in asks(c, task_id)] == [True]
    jira.read(None)  # deleted, or the key can no longer see it: a new ask
    with company.transaction() as c:
        assert [(line.startswith("Jira no longer shows"), w) for _, line, w in asks(c, task_id)][-1] == (True, False)


def test_an_unknown_outcome_asks_its_owner_once_looks_up_hourly_and_settling_takes_the_ask_back(
    company, gh, jira, monkeypatch
):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id, slack=False)
    released(company, gh)
    looked: list[str] = []
    adapter = OA.ADAPTERS["jira"]
    monkeypatch.setitem(
        OA.ADAPTERS, "jira", dataclasses.replace(adapter, reconcile=lambda store, row: looked.append(row["id"]))
    )
    with company.transaction() as c:
        row = actions(c, task_id)["transition"]
        assert OA.settle(c, row, "unknown", error="Jira answered 502")
        assert OA.get(c, row["id"])["owner_ask_id"] == ""  # not before a lookup could tell
    OA.reconcile_pass(company)
    OA.reconcile_pass(company)  # its owner was asked: not looked up again within the hour
    with company.transaction() as c:
        assert looked == [row["id"]]
        ask_id = OA.get(c, row["id"])["owner_ask_id"]
        ask_task = c.execute(
            "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (ask_id,)
        ).fetchone()[0]
        assert ask_task != task_id  # the task is Done: a task of its own was filed for the ask
        assert H.task(c, ask_task)["status"] == "open"
        OA.settle(c, OA.get(c, row["id"]), "done", receipt={"confirmed_by": "human:ana"})
        assert c.execute("SELECT withdrawn_at FROM requirements WHERE id=?", (ask_id,)).fetchone()[0]
        assert H.task(c, ask_task)["status"] != "open"
