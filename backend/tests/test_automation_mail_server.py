"""Workspace filing needs no computer: Gmail runs outside writes and shares the connector's settlement and audit."""

import json

import pytest

from backend import automation_actions as A, automations as M, gmail_live as G, google_workspace as W, mailbox_access
from backend.store import H
from backend.tests.helpers.api import post, runner  # noqa: F401
from backend.tests.helpers.automation_mail_actions import BOX, automate, claim, computer, last_run, later, owned_by_ana  # noqa: F401
from backend.tests.helpers.gmail_live import gmail_http  # noqa: F401
from backend.tests.helpers.google_workspace import personal
from backend.tests.helpers.google_workspace_authz import place, publish, workspace_key  # noqa: F401
from backend.tests.helpers.mail_api import seed_org
from backend.tests.helpers.mail_targets import declare
from connectors.mail.actions import label_name
from connectors.mail.labels import NEEDS_OWNER

SHARED = "legal@acme.example"
ARCHIVE = {"do": "archive"}
LABEL = {"do": "label", "args": {"tag": "billing"}}
LEAVE = {"do": "leave_for_you"}


@pytest.fixture(autouse=True)
def fresh_tokens():
    W.forget_tokens()
    yield
    W.forget_tokens()


@pytest.fixture
def live(api, workspace_key, gmail_http, monkeypatch):
    with api.app.state.store.transaction() as c:
        mailbox_access.ensure_mailbox(c, BOX, person_id="ana", created_by="human:ana")
        mailbox_access.ensure_mailbox(c, SHARED, kind="shared", created_by="human:ana")

    # Label creation and message changes both make network calls and must run after the lease write commits.
    def outside_write(fn):
        def checked(c, *args, **kwargs):
            assert not c.write_transaction and not c.in_transaction
            return fn(c, *args, **kwargs)

        return checked

    monkeypatch.setattr(G, "organize", outside_write(G.organize))
    monkeypatch.setattr(G, "resolve_label", outside_write(G.resolve_label))
    return gmail_http


def run_mail(api, *actions, mailbox=BOX, owner="person:ana"):
    step = {"id": "file", "type": "filter", "rules": [{"when": [], "then": list(actions)}]}
    aid = automate(api, step, owner=owner, trigger={"type": "message", "inbox": mailbox})
    run = post(
        api,
        f"automations/{aid}/run",
        {"input": {"message": {"mailbox": mailbox, "id": "m1", "from": "billing@vendor.example"}}},
    )["run"]
    return aid, run


def rows(api, aid=None):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM automation_mail_actions "
                + ("WHERE automation_id=? " if aid else "")
                + "ORDER BY created_at,rowid",
                (aid,) if aid else (),
            )
        ]


def queued(api, *kinds, step=None):
    with api.app.state.store.transaction() as c:
        owned_by_ana(c, "auto_mail")
        return [
            A._queue_mail(
                c,
                {"id": "auto_mail", "flow": {"owner": "person:ana"}},
                "run_mail",
                kind,
                {"mailbox": BOX, "id": "m1"},
                {"label": "billing"},
                step=step,
            )["mail_action"]
            for kind in kinds
        ]


def organizer(api):
    """A published computer for claim-route checks; filing itself never needs this setup."""
    seed_org(api)
    declare(api, BOX)
    machine = runner(api)
    place(api, machine)
    publish(api, machine["token"])
    return machine


@pytest.mark.parametrize("mailbox", [BOX, SHARED])
def test_workspace_archive_needs_no_computer_and_settles_the_run(api, live, mailbox):
    aid, run = run_mail(api, ARCHIVE, mailbox=mailbox)
    assert run["status"] == "ok" and run["finished_at"]
    assert run["steps"][0]["status"] == "ok"
    assert run["steps"][0]["outputs"]["actions"][0]["detail"] == "archived"
    (row,) = rows(api, aid)
    assert (row["state"], row["attempts"], row["claimed_by"]) == ("done", 1, M.SERVER_MAIL)
    assert json.loads(row["result"]) == {"ok": True, "detail": "archived"}
    assert [body for _, body, _ in live["writes"]] == [{"ids": ["m1"], "addLabelIds": [], "removeLabelIds": ["INBOX"]}]
    with api.app.state.store.read() as c:
        (event,) = c.execute("SELECT actor,target,detail FROM events WHERE action='mail.organize'").fetchall()
        assert (event["actor"], event["target"]) == ("human:ana", mailbox)
        assert json.loads(event["detail"]) == {
            "mailbox": mailbox,
            "action": "archive",
            "ids": ["m1"],
            "count": 1,
            "via": "automation:" + aid,
        }
        assert c.execute("SELECT count(*) FROM mail_messages").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM mail_mailboxes").fetchone()[0] == 0
    api.app.state.store.automations.drain()
    assert len(live["writes"]) == 1


def test_workspace_label_normalizes_and_creates_only_on_first_use(api, live):
    aid, first = run_mail(api, {"do": "label", "args": {"name": "  billing  "}})
    second = post(api, f"automations/{aid}/run", {"input": {"message": {"id": "m2"}}})["run"]
    assert first["status"] == second["status"] == "ok"
    name = label_name("  billing  ")
    assert [body for path, body, _ in live["writes"] if path.endswith("/labels")] == [{"name": name}]
    label = next(row["id"] for row in live["labels"] if row["name"] == name)
    assert [body for path, body, _ in live["writes"] if path.endswith("/batchModify")] == [
        {"ids": [mid], "addLabelIds": [label], "removeLabelIds": []} for mid in ("m1", "m2")
    ]
    assert all(json.loads(r["result"])["detail"] == "labelled " + name for r in rows(api, aid))


def test_workspace_leave_adds_the_connectors_needs_owner_label(api, live):
    aid, run = run_mail(api, LEAVE)
    assert run["status"] == "ok"
    assert live["writes"][0][1] == {"name": NEEDS_OWNER}
    label = next(row["id"] for row in live["labels"] if row["name"] == NEEDS_OWNER)
    assert live["writes"][-1][1] == {"ids": ["m1"], "addLabelIds": [label], "removeLabelIds": []}
    assert json.loads(rows(api, aid)[0]["result"]) == {"ok": True, "detail": "left for you"}


def test_a_chained_action_waits_for_a_successful_retry(api, live, monkeypatch):
    live["write_status"] = 503
    aid, run = run_mail(api, ARCHIVE, LABEL)
    first, second = rows(api, aid)
    assert second["after"] == first["id"]
    assert run["status"] == "waiting" and [r["attempts"] for r in (first, second)] == [1, 0]
    assert [r["state"] for r in (first, second)] == ["queued", "queued"]
    assert len(live["writes"]) == 1
    real = G.resolve_label

    def label_after_success(c, *args, **kwargs):
        assert c.execute("SELECT state FROM automation_mail_actions WHERE id=?", (first["id"],)).fetchone()[0] == "done"
        return real(c, *args, **kwargs)

    monkeypatch.setattr(G, "resolve_label", label_after_success)
    live["write_status"] = 200
    with later(M.MAIL_LEASE_MINUTES + 1):
        api.app.state.store.automations.drain()
    assert last_run(api, aid)["status"] == "ok"
    assert [r["attempts"] for r in rows(api, aid)] == [2, 1]


def test_a_chain_can_move_from_the_computer_to_the_server(api, live):
    machine = organizer(api)
    aid, run = run_mail(api, {"do": "unsubscribe"}, ARCHIVE)
    assert run["status"] == "waiting" and live["writes"] == []
    (first,) = claim(api, machine["token"])["actions"]
    assert first["action"] == "unsubscribe"
    assert claim(api, machine["token"])["actions"] == []
    post(
        api, f"automations/mail-actions/{first['id']}/result", {"ok": True, "detail": "unsubscribed"}, machine["token"]
    )
    M.tick(api.app.state.store)
    assert last_run(api, aid)["status"] == "ok"
    assert [r["claimed_by"] for r in rows(api, aid)] == [machine["runner_id"], M.SERVER_MAIL]
    assert len(live["writes"]) == 1


def test_chains_waiting_on_computers_do_not_crowd_out_ready_work(api, live):
    queued(api, "unsubscribe", "archive", step="first")
    queued(api, "unsubscribe", "archive", step="second")
    (ready,) = queued(api, "archive")
    assert M.drain_workspace_mail(api.app.state.store, limit=1) == 1
    assert next(r for r in rows(api) if r["id"] == ready)["state"] == "done"
    assert all(r["state"] == "queued" for r in rows(api) if r["id"] != ready)


def test_claim_excludes_workspace_filing_before_its_limit_and_keeps_other_actions(api, live):
    machine = organizer(api)
    queued(api, *(["archive", "label", "leave"] * 8), "draft", "send", "forward", "unsubscribe")
    actions = claim(api, machine["token"])["actions"]
    assert [a["action"] for a in actions] == ["draft", "send", "forward", "unsubscribe"]
    assert all(r["state"] == "queued" and r["attempts"] == 0 for r in rows(api)[:24])
    assert live["writes"] == []


def test_personal_connection_still_queues_for_its_computer(api, live):
    machine = organizer(api)
    personal(api, "ana", BOX, holder=machine["runner_id"])
    aid, run = run_mail(api, ARCHIVE, LABEL, LEAVE)
    assert run["status"] == "waiting" and live["calls"] == []
    assert all(r["state"] == "queued" and r["attempts"] == 0 for r in rows(api, aid))
    assert [a["action"] for a in claim(api, machine["token"])["actions"]] == ["archive"]


def grant_ops(api, level):
    with api.app.state.store.transaction() as c:
        box = mailbox_access._row(c, BOX)
        mailbox_access.grant(c, "human:ana", box["id"], "bot", "ops", level)


def test_a_read_only_owner_is_refused_when_queued(api, live):
    grant_ops(api, "read")
    aid, run = run_mail(api, ARCHIVE, owner="bot:ops")
    (row,) = rows(api, aid)
    assert run["status"] == row["state"] == "failed" and row["attempts"] == 0
    assert "owner may not organize" in json.loads(row["result"])["detail"]
    assert live["calls"] == []


@pytest.mark.parametrize("revocation", ["owner", "company"])
def test_access_is_rechecked_after_the_lease_and_before_gmail(api, live, monkeypatch, revocation):
    grant_ops(api, "read_write")
    real = M._claim_workspace_mail

    def revoke_after_lease(store, mid):
        row = real(store, mid)
        if row:
            if revocation == "owner":
                grant_ops(api, "read")
            else:
                with store.transaction() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO registry_metadata(key,value) VALUES(?,?)",
                        (W.PERMISSIONS_KEY, json.dumps({"organize_mail": False})),
                    )
        return row

    monkeypatch.setattr(M, "_claim_workspace_mail", revoke_after_lease)
    aid, run = run_mail(api, ARCHIVE, LABEL, owner="bot:ops")
    first, second = rows(api, aid)
    assert run["status"] == first["state"] == "failed" and second["state"] == "skipped"
    assert first["attempts"] == 1 and second["attempts"] == 0
    detail = json.loads(first["result"])["detail"]
    assert ("Who can use it" if revocation == "owner" else "What Tico may do") in detail
    assert live["calls"] == []


def test_retryable_gmail_errors_retry_then_fail_at_mail_tries(api, live):
    live["write_status"] = 429
    aid, run = run_mail(api, ARCHIVE, LABEL)
    assert run["status"] == "waiting"
    for attempt in range(2, M.MAIL_TRIES + 1):
        with later((M.MAIL_LEASE_MINUTES + 1) * (attempt - 1)):
            M.tick(api.app.state.store)
        first, second = rows(api, aid)
        assert first["attempts"] == attempt and second["attempts"] == 0
        assert first["state"] == ("failed" if attempt == M.MAIL_TRIES else "queued")
    assert last_run(api, aid)["status"] == "failed" and second["state"] == "skipped"
    assert "quota" in json.loads(first["result"])["detail"]
    with later((M.MAIL_LEASE_MINUTES + 1) * M.MAIL_TRIES):
        api.app.state.store.automations.drain()
    assert len(live["writes"]) == M.MAIL_TRIES
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='mail.organize'").fetchone()[0] == 0


def test_a_gmail_refusal_fails_without_retrying_or_running_the_chain(api, live):
    live["write_status"] = 403
    aid, run = run_mail(api, ARCHIVE, LABEL)
    first, second = rows(api, aid)
    assert run["status"] == first["state"] == "failed" and second["state"] == "skipped"
    assert first["attempts"] == 1 and "Gmail refused" in json.loads(first["result"])["detail"]
    with later(M.MAIL_LEASE_MINUTES + 1):
        api.app.state.store.automations.drain()
    assert len(live["writes"]) == 1


def test_a_label_without_a_name_is_a_permanent_connector_refusal(api, live):
    aid, run = run_mail(api, {"do": "label"}, ARCHIVE)
    first, second = rows(api, aid)
    assert run["status"] == first["state"] == "failed" and second["state"] == "skipped"
    assert json.loads(first["result"]) == {"ok": False, "detail": "label needs a label name."}
    assert live["calls"] == []


def test_a_server_lease_is_exclusive_and_recovers_after_expiry(api, live):
    (mid,) = queued(api, "archive")
    first = M._claim_workspace_mail(api.app.state.store, mid)
    assert first["attempts"] == 1
    assert M._claim_workspace_mail(api.app.state.store, mid) is None
    assert M.drain_workspace_mail(api.app.state.store) == 0 and not live["writes"]
    with later(M.MAIL_LEASE_MINUTES + 1):
        assert M.drain_workspace_mail(api.app.state.store) == 1
    (row,) = rows(api)
    assert row["state"] == "done" and row["attempts"] == 2


def test_only_the_server_recovers_expired_workspace_computer_leases(api, live):
    machine = organizer(api)
    (mid,) = queued(api, "archive")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE automation_mail_actions SET state='claimed',claimed_by=?,claimed_at=?,attempts=1 WHERE id=?",
            (machine["runner_id"], H.shift(H.now(), minutes=-M.MAIL_LEASE_MINUTES - 1), mid),
        )
    assert claim(api, machine["token"])["actions"] == []
    assert rows(api)[0]["state"] == "claimed" and not live["writes"]
    M.drain_workspace_mail(api.app.state.store)
    (row,) = rows(api)
    assert (row["state"], row["claimed_by"], row["attempts"]) == ("done", M.SERVER_MAIL, 2)
    assert len(live["writes"]) == 1


def test_an_unanswered_last_lease_fails_without_an_extra_gmail_call(api, live):
    (mid,) = queued(api, "archive")
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE automation_mail_actions SET state='claimed',claimed_by=?,claimed_at=?,attempts=? WHERE id=?",
            (M.SERVER_MAIL, H.shift(H.now(), minutes=-M.MAIL_LEASE_MINUTES - 1), M.MAIL_TRIES, mid),
        )
    M.drain_workspace_mail(api.app.state.store)
    assert rows(api)[0]["state"] == "failed" and not live["calls"]


@pytest.mark.parametrize("mode", ["demo", "rehearsal"])
def test_demo_and_rehearsal_never_change_gmail(api, live, monkeypatch, mode):
    queued(api, "archive")
    monkeypatch.setattr(api.app.state.store.settings, mode, True)
    M.drain_workspace_mail(api.app.state.store)
    assert rows(api)[0]["state"] == "failed" and not live["calls"]


def test_computer_filing_leaves_the_same_ids_only_audit(api, computer):
    aid = automate(api, {"id": "file", "type": "action", "do": "archive"})
    computer.sync()
    assert computer.worker.mail_action_tick() == 1
    with api.app.state.store.read() as c:
        (event,) = c.execute("SELECT actor,target,detail FROM events WHERE action='mail.organize'").fetchall()
    assert (event["actor"], event["target"]) == ("human:ana", BOX)
    assert json.loads(event["detail"]) == {
        "mailbox": BOX,
        "action": "archive",
        "ids": ["m1"],
        "count": 1,
        "via": "automation:" + aid,
    }


def test_the_loop_requeues_a_server_lease_its_stuck_worker_never_reported(api, live):
    """AC-6: the lease's upkeep is the loop's (automations.tick, without draining), not the stuck worker's."""
    (mid,) = queued(api, "archive")
    store = api.app.state.store
    lease = M._claim_workspace_mail(store, mid)  # the worker took it, then stopped answering
    assert lease and next(r for r in rows(api) if r["id"] == mid)["state"] == "claimed"
    M.tick(store, drain=False)
    assert next(r for r in rows(api) if r["id"] == mid)["state"] == "claimed", "its lease still holds"
    with later(M.MAIL_LEASE_MINUTES + 1):
        M.tick(store, drain=False)
        assert next(r for r in rows(api) if r["id"] == mid)["state"] == "queued"
        # Its next pass takes it again; the stuck worker's result, should it come, finds the lease gone.
        assert M.drain_workspace_mail(store) == 1
    row = next(r for r in rows(api) if r["id"] == mid)
    assert (row["state"], row["attempts"]) == ("done", 2) and len(live["writes"]) == 1


def test_the_loop_fails_a_lease_that_ran_out_on_its_last_try(api, live):
    (mid,) = queued(api, "archive")
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE automation_mail_actions SET attempts=? WHERE id=?", (M.MAIL_TRIES - 1, mid))
    assert M._claim_workspace_mail(store, mid)
    with later(M.MAIL_LEASE_MINUTES + 1):
        M.tick(store, drain=False)
    row = next(r for r in rows(api) if r["id"] == mid)
    assert row["state"] == "failed" and "no result after" in json.loads(row["result"])["detail"]
    assert live["writes"] == []
