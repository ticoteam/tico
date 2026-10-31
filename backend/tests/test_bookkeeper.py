"""The Bookkeeper (backend/bookkeeper.py, docs/bookkeeper.md) and the bills address (backend/bills_address.py): built in
once Finance is set up, outside the org chart, with exactly its default Finance rights; an hourly check that opens one
task only when the queue has news (no task, no model call otherwise); emailed bills land once. Synthetic fixtures."""

import shutil
from pathlib import Path
from types import SimpleNamespace

from backend import bills_address as BA
from backend import bookkeeper as BK
from backend import groups, postal_mail
from backend.finance import actions as FA
from backend.finance.core import rights as R
from backend.finance.store import access as SA
from backend.onboarding import helper_templates
from backend.store import H
from backend.tests.test_goal_manager import BASE
from backend.tests.test_onboarding import draft, environment, machine, signed_in  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "bookkeeper"
DIGEST = "a" * 64
PDF = b"%PDF-1.4 Acme invoice 1042"


def built(environment):
    api = environment(seed={}, cards=BASE)
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "bookkeeper")
    machine(api)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    return api


def ensure(api):
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        return BK.ensure(c, api.app.state.onboarding, api.app.state.auth)


def check(api):
    with api.app.state.store.transaction() as c:
        return BK.check(c, api.app.state.auth, H.now())


def checks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body,status,private FROM tasks WHERE owner=?", (BK.ACTOR,))]


def test_it_is_built_once_outside_the_org_chart_with_exactly_its_default_rights(environment):
    api = built(environment)
    ensure(api)
    ensure(api)
    with api.app.state.store.read() as c:
        assert H.bot(c, BK.SLUG)["state"] == "active" and BK.built(c)
        assert SA.rights_of(c, BK.ACTOR, H.now()) == frozenset(R.BOOKKEEPER)
        assert c.execute("SELECT count(*) FROM events WHERE action='bookkeeper.view_asked'").fetchone()[0] == 1
        asks = c.execute("SELECT owner FROM tasks WHERE title=?", (BK.VIEW_TITLE,)).fetchall()
        assert len(asks) == 1 and asks[0]["owner"].startswith("human:")
    assert api.app.state.auth.system_bot(BK.SLUG) and BK.SLUG in groups.SYSTEM_BOTS
    assert "bookkeeper" in helper_templates(api.app.state.store.settings)
    store = api.app.state.store
    with store.transaction() as c:                  # a revoke sticks: the next start replays, it does not grant again
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": R.PAY}, "revoke-pay").status == "committed"
    ensure(api)
    with store.read() as c:
        assert R.PAY not in SA.rights_of(c, BK.ACTOR, H.now())


def test_an_empty_queue_is_a_quiet_check_and_news_opens_one_private_task_once(environment):
    api = built(environment)
    ensure(api)
    assert check(api)["words"] == "Nothing new"
    assert checks(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot=?", (BK.SLUG,)).fetchone()[0] == 0
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,state,file_id,"
                  "updated_at) VALUES('bil_1','d1','2026-10-30T10:00:00Z','ap@acme.example','Invoice 1042',"
                  "'invoice.pdf','application/pdf',?,'needs_entry','fil_1',?)", (DIGEST, H.now()))
    first = check(api)
    assert first["items"] == 1 and first["state"] == "opened"
    (task,) = checks(api)
    assert task["title"] == "Books check (1)" and task["private"] == 1 and "bil_1" in task["body"]
    assert check(api)["words"] == "Nothing new" and len(checks(api)) == 1


def _email(inbox):
    return {"kind": "email", "inbox_id": inbox, "delivery_id": "dlv_1", "from": "ap@acme.example",
            "subject": "Invoice 1042", "received_at": 1793440800,
            "attachments": [{"name": "invoice.pdf", "type": "application/pdf", "sha256": DIGEST, "size": len(PDF)},
                            {"name": "logo.gif", "type": "image/gif", "sha256": "b" * 64, "size": 10}]}


def test_a_bill_emailed_to_the_address_lands_once_in_the_library_and_asks_finance_once(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO inbound_inboxes(inbox_id,kind,name,address,state,created_by) "
                  "VALUES('ibx_1','email','Bills','bills@acme.example','active','human:test')")
        assert BA.emailed(c, _email("ibx_1")) == 0                   # no bills address named yet
    named = api.put("/api/v2/finance/bills-address", json={"inbox_id": "ibx_1"}, headers=signed_in())
    assert named.status_code == 200 and named.json()["bills_address"]["address"] == "bills@acme.example", named.text
    with store.transaction() as c:
        assert BA.emailed(c, _email("ibx_1")) == 1                   # the PDF; the GIF is not a bill
        assert BA.emailed(c, _email("ibx_1")) == 0                   # a re-delivered page adds nothing
    fetched = []

    def fake_get(url, headers):
        fetched.append(url)
        return PDF, "application/pdf"
    monkeypatch.setattr(postal_mail, "_get_bytes", fake_get)
    service = SimpleNamespace(off=False, url="https://hq.example", key="k")
    assert BA.drain(service, store, api.app.state.blobs, api.app.state.files) == 1
    assert BA.drain(service, store, api.app.state.blobs, api.app.state.files) == 0
    assert len(fetched) == 1 and fetched[0].endswith("/v1/inbound/messages/dlv_1/attachments/" + DIGEST)
    with store.read() as c:
        (row,) = [dict(r) for r in c.execute("SELECT * FROM bills_inbound")]
        assert row["state"] in ("entered", "needs_entry") and row["file_id"]
        assert c.execute("SELECT bot FROM bot_files WHERE id=?", (row["file_id"],)).fetchone()["bot"] == BK.SLUG
    with store.transaction() as c:                                   # the draft's key replays: Finance is asked once
        again = BA._bill(c, row, row["file_id"])
    assert again[1] == row["action_id"]
