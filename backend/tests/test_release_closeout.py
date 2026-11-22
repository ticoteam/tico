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
from backend import task_calls as TC
from backend import task_engine as E
from backend import release_closeout as RC
from backend import slack_bots as SB
from backend.item_sources import claim
from backend.item_sources import github as F
from backend.item_sources import jira as JS
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.release_cut import REPO, engineering, new, seen, stage, to_release

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


def filed(c, task_id, slack=True, private=False):
    """The task's primary link to HM-12 (as find-or-create makes it), and its originating Slack thread."""
    H.task_link(c, "human:ana", task_id, f"https://{HOST}/browse/{ISSUE}", item=("jira", "issue", ISSUE, TOOL))
    claim(c, task_id, ("jira", "issue", ISSUE, TOOL))
    if slack:
        H.task_link(c, "human:ana", task_id, THREAD)
    c.execute("UPDATE tasks SET private=? WHERE id=?", (int(private), task_id))


def actions(c, task_id):
    return {
        str(r["operation"]): dict(r)
        for r in c.execute("SELECT * FROM outbound_actions WHERE task_id=? ORDER BY created_at", (task_id,))
    }


def replies(c):
    return [dict(r) for r in c.execute("SELECT * FROM slack_posts ORDER BY created_at")]


def asks(c, task_id):
    """Tico's asks on the task: (person, line, withdrawn), each people-only."""
    found = c.execute(
        "SELECT r.person_id, r.line, r.withdrawn_at, r.who_oks FROM requirements r JOIN visits v ON v.id = r.visit_id "
        "WHERE v.task_id=? AND r.added_by_id=? ORDER BY r.created_at, r.rowid",
        (task_id, H.TICO_ACTOR),
    ).fetchall()
    assert all(r[3] == "people_only" for r in found)
    return [(str(r[0]), str(r[1]), r[2] is not None) for r in found]


def release_visit(c, task_id):
    return c.execute(
        "SELECT v.id FROM visits v JOIN stages s ON s.id = v.stage_id WHERE v.task_id=? AND s.name='Release' "
        "ORDER BY v.entered_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()[0]


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
        assert json.loads(found["transition"]["payload"])["to"] == "Done" and found["transition"]["state"] == "queued"
        link = f"https://github.com/{REPO}/releases/tag/v1.0.0"
        assert f"Released in v1.0.0: {link}" in found["comment"]["payload"]
        [reply] = replies(c)
        assert (reply["channel"], reply["thread_ts"], reply["bot"]) == ("C0BUGS123", "1763700000.000100", "release")
        assert reply["text"] == f"Released in v1.0.0: {link}"
        # A replay of every trigger adds nothing: the move, the close-out itself, the release read again.
        visit = release_visit(c, task_id)
        assert RC.moved(c, task_id, visit) == 0 and RC.close_out(c, task_id, visit) == 0
    released(company, gh, tag="v1.0.1", minutes=40)
    with company.transaction() as c:
        assert len(actions(c, task_id)) == 2 and len(replies(c)) == 1


def test_no_slack_thread_or_a_private_task_no_reply(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id, slack=False)
    released(company, gh)
    other = to_release(company, gh, number=2)
    with company.transaction() as c:
        filed_on = c.execute("SELECT id FROM task_links WHERE task_id=? AND is_primary=1", (task_id,)).fetchone()
        c.execute("UPDATE task_links SET is_primary=0, item_key='HM-1' WHERE id=?", (filed_on[0],))
        filed(c, other, private=True)
    released(company, gh, tag="v1.1.0", minutes=40)
    with company.transaction() as c:
        assert set(actions(c, task_id)) == {"transition", "comment"} and set(actions(c, other)) == {
            "transition",
            "comment",
        }
        assert replies(c) == []
        assert "closeout.skipped" in [r[0] for r in c.execute("SELECT action FROM events WHERE target=?", (other,))]


def test_a_failed_transition_neither_blocks_nor_repeats_the_comment_or_reply(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh)
    with company.transaction() as c:
        found = actions(c, task_id)
        assert OA.settle(c, found["transition"], "failed", error="Jira offers no transition to 'Done'")
        assert RC.close_out(c, task_id, release_visit(c, task_id)) == 0
        after = actions(c, task_id)
        assert after["transition"]["state"] == "failed" and after["comment"]["state"] == "queued"
        assert after["comment"]["id"] == found["comment"]["id"] and len(after) == 2 and len(replies(c)) == 1


def test_only_leaving_release_closes_out(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
        E.move(c, task_id, "eng-build", "human:ana", "A fix needs a second PR")
        sha = next(str(link["metadata"]["merged_sha"]) for link in F.prs_of(c, task_id))
        gh.publish(REPO, "v1.0.0")
        F.save_release(c, REPO, gh.published[REPO][0], F.version_now())
        release = F.release_of(c, REPO, sha, prereleases=True) or F.releases(c, REPO, prereleases=True)[0]
        F.save_included(c, release["id"], sha, True)  # PR 1 is in v1.0.0
        H.task_link(c, "human:ana", task_id, f"https://github.com/{REPO}/pull/2")
    gh.set_checks(REPO, gh.open_pr(REPO, 2), "success")
    with company.transaction() as c:
        seen(c, gh, 2)
        E.advance(c, task_id)
        assert stage(c, task_id) == "Sign off"  # PR 2 still open
        assert actions(c, task_id) == {} and replies(c) == []


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


def test_an_ok_never_unfreezes_only_jira_does_even_after_done(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    jira.read("Ready")
    jira.read("Backlog")
    released(company, gh)
    with company.transaction() as c:
        rid = c.execute(
            "SELECT r.id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE v.task_id=? AND r.added_by_id=?",
            (task_id, H.TICO_ACTOR),
        ).fetchone()[0]
        TC.task_answer(c, asks(c, task_id)[0][0], task_id, rid, "ok", own_click=True)  # the owner keeps the task
        assert stage(c, task_id) == "Done" and actions(c, task_id) == {}  # still frozen: Jira says Backlog
    jira.read("Backlog")
    with company.transaction() as c:
        assert actions(c, task_id) == {}
    jira.read("Ready")  # the Done task's issue is still read; back in Ready runs the close-out it missed
    with company.transaction() as c:
        assert set(actions(c, task_id)) == {"transition", "comment"} and len(replies(c)) == 1


def test_closed_outside_asks_once_per_standing_and_a_reopen_takes_it_back(company, jira):
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
    jira.read(None)  # deleted, or the key can no longer see it: a new ask replaces the old
    with company.transaction() as c:
        found = asks(c, task_id)
        assert [w for _, _, w in found] == [True, False] and found[1][1].startswith("Jira no longer shows")
    jira.read("Ready")
    with company.transaction() as c:
        assert [w for _, _, w in asks(c, task_id)] == [True, True]


def test_ready_is_learned_only_from_the_first_read(company, jira):
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 8)
        filed(c, task_id)
    jira.read("In Progress", "indeterminate")  # already further on at the first read
    jira.read("Backlog")
    with company.transaction() as c:
        [(_, line, _)] = asks(c, task_id)
        assert line.startswith(f"{ISSUE} left Ready in Jira (Backlog)")


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
    OA.reconcile_pass(company)  # looked up once: not again within the hour
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


def test_the_person_asked_about_an_unknown_action_reads_and_confirms_it(api):
    store = api.app.state.store
    now = H.now()
    with store.transaction() as c:
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
        task = H.task_create(c, "human:ana", "Ship the export fix", "", "human:cara", lint=False)
        row, _ = OA.enqueue(
            c,
            None,
            actor=H.TICO_ACTOR,
            channel="jira",
            operation="comment",
            destination=ISSUE,
            occurrence_key="k-unknown",
            payload={"body": "Released."},
            connection_id=TOOL,
            task_id=task["id"],
        )
        OA.settle(c, row, "unknown", error="Jira answered 502")
    url = f"/api/v2/outbound-actions/{row['id']}"
    assert api.get(url, headers=headers("cara-test")).status_code == 404  # nobody asked Cara yet
    with store.transaction() as c:
        OA.ask_owner(c, OA.get(c, row["id"]))
        assert OA.get(c, row["id"])["owner_ask_id"]
    assert api.get(url, headers=headers("cara-test")).status_code == 200
    r = api.post(url + "/confirm-sent", headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    with store.read() as c:
        assert OA.get(c, row["id"])["state"] == "done"
