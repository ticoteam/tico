"""Release close-out of a task filed for a Jira issue (backend/release_closeout.py): its merge proven in an actual
release tag queues the Jira transition to Done and one release comment, each once (no Slack reply yet);
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


@pytest.fixture
def gh():
    found = FakeGitHub()
    found.history[REPO] = [found.sha()]
    found.publish(REPO, "v0.9.0")
    return found


@pytest.fixture
def jira(company, monkeypatch):
    """A Jira site that is a record integration with a shared read and write key. `jira.read` runs one Jira pass."""
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
                if self.status[0] == "timeout":
                    raise TimeoutError("Jira took too long")
                if self.status[0] is None:
                    return 404, {"errorMessages": ["Issue does not exist"]}
                found = {"name": self.status[0], "statusCategory": {"key": self.status[1]}}
                return 200, {"fields": {"status": found, "updated": f"2026-11-21T10:{self.passes:02d}:00.000+0000"}}

            JS.refresh(company, Vault(), now=H.shift(H.now(), minutes=10 * self.passes), sender=sender)

    return Jira()


def filed(c, task_id, ready="Ready", issue=ISSUE):
    """The task's primary link to `issue` filed in `ready` (as find-or-create makes it with `primary_status`)."""
    H.task_link(c, "human:ana", task_id, f"https://{HOST}/browse/{issue}", item=("jira", "issue", issue, TOOL))
    claim(c, task_id, ("jira", "issue", issue, TOOL))
    if ready:
        RC.remember_ready(c, task_id, ready)


def actions(c, task_id):
    return {
        str(r["operation"]): dict(r)
        for r in c.execute("SELECT * FROM outbound_actions WHERE task_id=? ORDER BY created_at", (task_id,))
    }


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


def test_a_release_queues_the_jira_done_and_comment_once(company, gh, jira):
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
        frozen = [json.loads(r["payload"]) for r in found.values()]
        assert all(p["closeout"] and p["ready"] == "Ready" and p["done"] == "Done" for p in frozen)
        assert c.execute("SELECT COUNT(*) FROM slack_posts").fetchone()[0] == 0  # no Slack reply yet
        # A replay of every trigger adds nothing: the move, the close-out itself, the release read again.
        visit = release_visit(c, task_id)
        assert RC.moved(c, task_id, visit) == 0 and RC.close_out(c, task_id, visit) == 0
    released(company, gh, tag="v1.0.1", minutes=40)
    with company.transaction() as c:
        assert len(actions(c, task_id)) == 2


def test_a_task_with_two_primary_jira_links_closes_neither_out_and_asks_once(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
        filed(c, task_id, issue="HM-13")  # as a merge of two tasks filed for Jira issues leaves it
    released(company, gh)
    with company.transaction() as c:
        assert stage(c, task_id) == "Done" and actions(c, task_id) == {}
        assert RC.close_out(c, task_id, release_visit(c, task_id)) == 0
        skipped = [r[0] for r in c.execute("SELECT action FROM events WHERE target=?", (task_id,))]
        assert skipped.count("closeout.skipped") == 2
        filed_for = c.execute(
            "SELECT COUNT(*) FROM requirements WHERE added_by_id=? AND line LIKE 'This task is filed for 2 Jira%'",
            (H.TICO_ACTOR,),
        ).fetchone()[0]
        assert filed_for == 1  # asked once


def test_a_failed_transition_neither_blocks_nor_repeats_the_comment(company, gh, jira):
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
        assert after["comment"]["id"] == found["comment"]["id"] and len(after) == 2


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
        assert actions(c, task_id) == {}


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
        assert actions(c, task_id) == {}
        [(person, line, withdrawn)] = asks(c, task_id)
        assert H.is_human(person) and line.startswith(f"{ISSUE} left Ready in Jira (Backlog)") and not withdrawn
    jira.read("Ready")
    with company.transaction() as c:
        [(_, _, withdrawn)] = asks(c, task_id)
        assert withdrawn and stage(c, task_id) == "Done"
        assert set(actions(c, task_id)) == {"transition", "comment"}
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
        assert set(actions(c, task_id)) == {"transition", "comment"}


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


def test_with_no_saved_ready_a_to_do_status_never_pauses(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id, ready=None)  # filed before primary_status existed
    jira.read("Backlog")
    released(company, gh)
    with company.transaction() as c:
        assert asks(c, task_id) == [] and set(actions(c, task_id)) == {"transition", "comment"}


def test_a_read_error_after_a_404_keeps_the_pause(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    jira.read("Ready")
    jira.read(None)  # 404
    jira.read("timeout")  # tells nothing: still gone
    with company.transaction() as c:
        rid = c.execute(
            "SELECT r.id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE v.task_id=? AND r.added_by_id=?",
            (task_id, H.TICO_ACTOR),
        ).fetchone()[0]
        TC.task_answer(c, asks(c, task_id)[0][0], task_id, rid, "ok", own_click=True)
    released(company, gh)
    with company.transaction() as c:
        assert stage(c, task_id) == "Done" and actions(c, task_id) == {}


@pytest.mark.parametrize("v1_at", ["2026-01-01T00:00:00.000000Z", "2026-11-10T00:00:00.000000Z"])  # older, or a tie
def test_a_reopen_adding_a_pr_already_released_queues_nothing_new(company, gh, jira, v1_at):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh, tag="v2.0.0")
    with company.transaction() as c:
        assert len(actions(c, task_id)) == 2
        visit = release_visit(c, task_id)
        # PR 2, already shipped in the older v1.0.0, is linked after a reopen; leaving Release again sees {v1, v2}.
        H.task_link(c, "human:ana", task_id, f"https://github.com/{REPO}/pull/2", mover=True)
    gh.open_pr(REPO, 2)
    sha = gh.merge(REPO, 2)
    with company.transaction() as c:
        seen(c, gh, 2)
        F.save_release(c, REPO, {"tag": "v1.0.0", "commit_sha": sha, "published_at": v1_at}, 1)
        old = next(r for r in F.releases(c, REPO, prereleases=True) if r["tag"] == "v1.0.0")
        F.save_included(c, old["id"], sha, True)
        assert RC.close_out(c, task_id, visit) == 0
        assert len(actions(c, task_id)) == 2
        # PR 1 (in the newest, v2.0.0) is removed: the newest release now is v1.0.0, no newer than the one announced.
        c.execute("DELETE FROM task_links WHERE task_id=? AND number=1", (task_id,))
        assert RC.close_out(c, task_id, visit) == 0
        assert len(actions(c, task_id)) == 2


@pytest.mark.parametrize(
    ("status", "category", "why"),
    [
        ("Backlog", "new", "withdrawn"),
        ("Won't Do", "done", "closed in Jira as Won't Do"),
        (None, "", "no longer"),
        ("write 404", "new", "no longer"),  # Ready at the read, gone by the write
    ],
)
def test_a_close_out_write_waits_while_the_issue_is_withdrawn_gone_or_closed_otherwise_then_resumes(
    company, gh, jira, monkeypatch, status, category, why
):
    from backend import outbound_jira as OJ

    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh)
    sent: list[str] = []
    issue = {"status": status, "category": category}

    def call(method, url, headers, params, seconds=10.0, body=None, most=None):
        sent.append(method)
        if method == "POST" and issue["status"] == "write 404":
            sent.remove("POST")  # refused: nothing was written
            return JS.Reply(404, {"errorMessages": ["Issue does not exist"]})
        if method == "POST":
            return JS.Reply(204 if url.endswith("/transitions") else 201, {"id": "10001"})
        if url.endswith("/transitions"):
            return JS.Reply(200, {"transitions": [{"id": "31", "name": "Finish", "to": {"name": "Done"}}]})
        if url.endswith("/comment"):
            return JS.Reply(200, {"comments": [], "total": 0})
        if issue["status"] is None:
            return JS.Reply(404, {"errorMessages": ["Issue does not exist"]})
        name = "Ready" if issue["status"] == "write 404" else issue["status"]
        found = {"name": name, "statusCategory": {"key": issue["category"]}}
        return JS.Reply(200, {"fields": {"status": found, "updated": "2026-11-21T10:00:00.000+0000"}})

    monkeypatch.setattr(OJ, "_connect", lambda store, row, write=True: (f"https://{HOST}", {}, {}))
    monkeypatch.setattr(JS, "call", call)
    with company.transaction() as c:  # nine counted rejections already (rate limits): a pause still never fails it
        c.execute("UPDATE outbound_actions SET attempts=? WHERE task_id=?", (OA.MAX_SAFE_RETRIES - 1, task_id))
    for _ in range(12):  # past MAX_SAFE_RETRIES: a paused write never uses them up
        with company.transaction() as c:
            c.execute("UPDATE outbound_actions SET next_attempt_at=? WHERE state='queued'", (H.now(),))
        while (claimed := OA.claim(company)) is not None:
            assert OA.attempt(company, claimed) == "requeued"
    with company.transaction() as c:
        rows = actions(c, task_id).values()
        assert {r["state"] for r in rows} == {"queued"} and all(why in r["last_error"] for r in rows)
        assert all(r["next_attempt_at"] > H.shift(H.now(), minutes=50) for r in rows)
    assert "POST" not in sent
    issue.update(status="Ready", category="new")  # back in Ready: both go by themselves
    with company.transaction() as c:
        c.execute("UPDATE outbound_actions SET next_attempt_at=? WHERE state='queued'", (H.now(),))
    while (claimed := OA.claim(company)) is not None:
        assert OA.attempt(company, claimed) == "done"
    assert sent.count("POST") == 2


def test_closed_as_anything_but_done_after_the_close_out_asks_the_owner(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
    released(company, gh)
    jira.read("Done", "done")  # Tico's own Done: nobody is asked
    with company.transaction() as c:
        assert c.execute("SELECT COUNT(*) FROM requirements WHERE line LIKE ?", (f"{ISSUE} %",)).fetchone()[0] == 0
    jira.read("Won't Do", "done")  # closed some other way while the writes wait: the owner hears once
    jira.read("Won't Do", "done")
    with company.transaction() as c:
        lines = [r[0] for r in c.execute("SELECT line FROM requirements WHERE line LIKE ?", (f"{ISSUE} %",))]
        assert len(lines) == 1 and lines[0].startswith(f"{ISSUE} was closed in Jira (Won't Do)")
    jira.read("Done", "done")  # then into Tico's own Done: that question is moot and taken back
    with company.transaction() as c:
        found = c.execute("SELECT withdrawn_at FROM requirements WHERE line LIKE ?", (f"{ISSUE} %",)).fetchall()
        assert [r[0] is not None for r in found] == [True]


def test_release_times_compare_as_times_not_text(company, gh):
    with company.transaction() as c:  # as text "…00Z" sorts after "…00.500000Z", though it is half a second older
        F.save_release(c, REPO, {"tag": "v-a", "commit_sha": gh.sha(), "published_at": "2026-11-21T10:00:00Z"}, 1)
        later = {"tag": "v-b", "commit_sha": gh.sha(), "published_at": "2026-11-21T10:00:00.500000Z"}
        F.save_release(c, REPO, later, 1)
        assert RC._newest(c, [(REPO, "v-a"), (REPO, "v-b")])[1] == "v-b"


def test_only_tico_queues_a_close_out_key(company, gh, jira):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
        with pytest.raises(OA.Problem) as refused:
            OA.enqueue(
                c,
                None,
                actor="bot:release",
                channel="jira",
                operation="comment",
                destination=ISSUE,
                occurrence_key=f"closeout:{task_id}:{REPO}@v9.9.9:jira:comment",
                payload={"body": "Released."},
                metadata={"published_at": "2099-01-01T00:00:00.000000Z"},
                connection_id=TOOL,
                task_id=task_id,
            )
        assert refused.value.status == 422
    released(company, gh)  # nothing suppresses the real close-out
    with company.transaction() as c:
        assert set(actions(c, task_id)) == {"transition", "comment"}


def test_a_create_retried_across_the_upgrade_hashes_as_before():
    from backend import models as M

    assert "primary_link" not in M.TaskFile(title="Ship it").model_dump()
    assert "primary_status" not in M.TaskFile(title="Ship it").model_dump()
    assert M.TaskFile(title="x", primary_link="jira:issue:HM-1@t").model_dump()["primary_link"] == "jira:issue:HM-1@t"


def test_an_unknown_outcome_asks_its_owner_once_looks_up_hourly_and_settling_takes_the_ask_back(
    company, gh, jira, monkeypatch
):
    task_id = to_release(company, gh)
    with company.transaction() as c:
        filed(c, task_id)
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


def jira_tool(c):
    now = H.now()
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


def test_intake_saves_the_status_the_issue_was_filed_in_as_its_ready(api):
    store = api.app.state.store
    with store.transaction() as c:
        jira_tool(c)
    body = {
        "title": "Fix the export",
        "owner": "ben",
        "primary_link": f"jira:issue:{ISSUE}@{TOOL}",
        "primary_status": "Ready",
    }
    r = api.post("/api/v2/tasks", json=body, headers=headers("ana-test"))
    assert r.status_code in (200, 201), r.text
    with store.read() as c:
        task_id = c.execute("SELECT task_id FROM task_links WHERE is_primary=1").fetchone()[0]
        assert json.loads(RC.primary(c, task_id)["detail"])["ready"] == "Ready"
    r = api.post(
        "/api/v2/tasks",
        json={"title": "No link", "owner": "ben", "primary_status": "Ready"},
        headers=headers("ana-test"),
    )
    assert r.status_code == 422


def test_the_person_asked_about_an_unknown_action_reads_and_confirms_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        jira_tool(c)
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
        ask_id = OA.get(c, row["id"])["owner_ask_id"]
        ask_task = c.execute(
            "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (ask_id,)
        ).fetchone()[0]
        assert ask_task != task["id"]  # a small task of its own, never an ask holding the open task
        said = [str(m[0]) for m in c.execute("SELECT body FROM messages WHERE body LIKE '%confirm-sent%'")]
        assert any(f"tico outbound confirm-sent {row['id']}" in m for m in said)
    assert api.get(url, headers=headers("cara-test")).status_code == 200
    r = api.post(url + "/confirm-sent", headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    with store.read() as c:
        assert OA.get(c, row["id"])["state"] == "done"
