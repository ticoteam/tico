"""The Bookkeeper (backend/bookkeeper.py, docs/bookkeeper.md) and the bills address (backend/bills_address.py): built in
once Finance is set up, outside the org chart, with exactly its default Finance rights; an hourly check that opens one
task only when the queue has news (no task, no model call otherwise); emailed bills land once. Synthetic fixtures."""

import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from backend import bills_address as BA
from backend import bookkeeper as BK
from backend import groups, postal_mail
from backend.finance import actions as FA
from backend.finance import reads as FR
from backend.onboarding import helper_templates
from backend.store import H
from backend.tests.helpers.onboarding import environment, signed_in
from backend.tests.helpers.bookkeeper import DEFAULT_RIGHTS, TEMPLATE, built, check, checks, ensure

DIGEST = "a" * 64
PDF = b"%PDF-1.4 Acme invoice 1042"


def sees_everything(api):
    """The owner's own Everything grant to the Finance Owner, as an install from before the default view made it."""
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert (
            FA.act(c, owner, "view_grant", {"subject": BK.ACTOR, "what": "everything"}, "grant-view").status
            == "committed"
        )


def rights(c):
    (row,) = [t for t in FR.access(c, BK.ACTOR)["teammates"] if t["teammate"] == BK.ACTOR]
    return set(row["rights"])


def test_it_is_built_once_outside_the_org_chart_with_exactly_its_default_rights(environment):
    api = built(environment)
    ensure(api)
    ensure(api)
    with api.app.state.store.read() as c:
        assert H.bot(c, BK.SLUG)["state"] == "active" and BK.built(c)
        assert rights(c) == DEFAULT_RIGHTS
        assert not c.execute("SELECT 1 FROM tasks WHERE title=?", (BK.VIEW_TITLE,)).fetchone()
    assert api.app.state.auth.system_bot(BK.SLUG) and BK.SLUG in groups.SYSTEM_BOTS
    assert "finance-owner" in helper_templates(api.app.state.store.settings)
    store = api.app.state.store
    with store.transaction() as c:  # a revoke sticks: the next start does not grant it again
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert (
            FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.pay"}, "revoke-pay").status
            == "committed"
        )
    ensure(api)
    check(api)
    with store.read() as c:
        assert "finance.pay" not in rights(c)


def test_an_install_from_before_rules_gets_that_one_right_once(environment):
    """An install built before finance.rules was a default has the five rights and no rights_version event. Its next
    start grants rules once, and a later revoke of rules sticks."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert (
            FA.act(c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.rules"}, "revoke-rules").status
            == "committed"
        )
        c.execute("DELETE FROM events WHERE action='bookkeeper.rights_version'")
    ensure(api)
    with store.read() as c:
        assert rights(c) == DEFAULT_RIGHTS
        assert c.execute("SELECT count(*) FROM events WHERE action='bookkeeper.rights_version'").fetchone()[0] == 1
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert (
            FA.act(
                c, owner, "right_revoke", {"teammate": BK.ACTOR, "right": "finance.rules"}, "revoke-rules-again"
            ).status
            == "committed"
        )
    ensure(api)
    with store.read() as c:
        assert "finance.rules" not in rights(c)


def _view(c):
    from backend.finance.context import load

    return load(c).view(BK.ACTOR)


def _view_grants(c, status=None):
    sql = "SELECT id, status FROM fin_view_grants WHERE subject=? AND what='everything'"
    rows = c.execute(sql, (BK.ACTOR,)).fetchall()
    return [r["id"] for r in rows if status is None or r["status"] == status]


def _granted_events(c):
    return c.execute("SELECT count(*) FROM events WHERE action='bookkeeper.view_granted'").fetchone()[0]


def _private_payroll(c):
    from backend.finance.context import owner as finance_owner

    owner = finance_owner(c)
    made = FA.act(c, owner, "account_create", {"code": "6500", "name": "Salaries", "type": "expense"}, "acct-6500")
    assert made.status == "committed", made
    account = str(made.result["account_id"])
    assert (
        FA.act(
            c, owner, "private_lines_set", {"account_id": account, "private": True, "others_see": "hidden"}, "pl-6500"
        ).status
        == "committed"
    )
    return account


def _revoke(api, grant_id, tag):
    with api.app.state.store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert FA.act(c, owner, "view_revoke", {"grant_id": grant_id}, tag).status == "committed"


def test_a_new_install_lets_the_finance_owner_see_the_books_but_not_private_payroll(environment):
    api = built(environment)
    ensure(api)
    ensure(api)  # a second start grants nothing more
    with api.app.state.store.transaction() as c:
        payroll = _private_payroll(c)
        view = _view(c)
        assert view.everything and payroll not in view.full_detail and view.full_detail == frozenset()
        assert len(_view_grants(c, "active")) == 1 and _granted_events(c) == 1
        assert not c.execute("SELECT 1 FROM tasks WHERE title=?", (BK.VIEW_TITLE,)).fetchone()
        owner = "human:" + api.app.state.auth.owner_id(c)
        (by,) = c.execute("SELECT granted_by FROM fin_view_grants WHERE subject=?", (BK.ACTOR,)).fetchone()
        assert by == owner  # the owner's grant, checked like any owner's grant
        notices = c.execute(
            "SELECT body FROM messages WHERE kind='notice' AND recipient_id=? AND body=?", (owner, BK.VIEW_NOTICE)
        ).fetchall()
        assert len(notices) == 1  # told once, after two starts


def test_the_default_view_stops_standing_when_the_company_changes_owner(environment):
    from dataclasses import replace

    from backend.finance.context import load
    from backend.finance.core import view as VW
    from backend.finance.store import access as SA

    api = built(environment)
    ensure(api)
    with api.app.state.store.read() as c:
        ctx = load(c)
        (grant,) = [g for g in SA.grants(c) if g.subject == BK.ACTOR and g.what == "everything"]
        assert VW.standing(ctx.org, grant, SA.defaults(c), ctx.accounts)
        new_owner = replace(ctx.org, owner="human:someone-new")
        assert not VW.standing(new_owner, grant, SA.defaults(c), ctx.accounts)


def test_with_no_owner_yet_nothing_is_granted_and_the_next_start_grants_it(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        BK.grant_defaults(c)
        assert BK.grant_view(c, "") is None
        assert _view_grants(c) == [] and _granted_events(c) == 0
    ensure(api)
    with store.read() as c:
        assert len(_view_grants(c, "active")) == 1 and _granted_events(c) == 1


def test_an_older_install_gets_the_view_once_and_its_open_ask_is_closed_for_the_owner(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:  # an install from before: the view was asked for, never granted
        FA.ensure_setup(c, store.settings.owner_email)
        BK.grant_defaults(c)
        owner = "human:" + api.app.state.auth.owner_id(c)
        ask = H.task_create(c, H.KEEPER, BK.VIEW_TITLE, "Open Finance → Access.", owner, lint=False)["id"]
    ensure(api)
    ensure(api)
    with store.read() as c:
        assert len(_view_grants(c, "active")) == 1 and _granted_events(c) == 1
        assert H.task(c, ask)["status"] == "closed"
        notes = c.execute(
            "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='move'", (ask,)
        ).fetchall()
        assert [r["note"] for r in notes if r["note"]] == [BK.VIEW_CLOSED_NOTE]


def test_an_older_install_whose_owner_already_granted_everything_gets_no_second_grant(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        BK.grant_defaults(c)
    sees_everything(api)
    ensure(api)
    with store.read() as c:
        assert len(_view_grants(c, "active")) == 1 and _granted_events(c) == 1


def test_an_older_install_whose_owner_revoked_the_view_gets_nothing(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        BK.grant_defaults(c)
    sees_everything(api)
    with store.read() as c:
        (grant,) = _view_grants(c, "active")
    _revoke(api, grant, "revoke-old")
    ensure(api)
    with store.read() as c:
        assert _view_grants(c, "active") == [] and _granted_events(c) == 0
        assert not _view(c).everything


def test_a_revoke_after_the_default_grant_sticks(environment):
    api = built(environment)
    ensure(api)
    with api.app.state.store.read() as c:
        (grant,) = _view_grants(c, "active")
    _revoke(api, grant, "revoke-default")
    ensure(api)
    check(api)
    with api.app.state.store.read() as c:
        assert _view_grants(c, "active") == [] and _granted_events(c) == 1
        assert not _view(c).everything


def test_a_paused_finance_owner_is_not_granted_the_view(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        BK.grant_defaults(c)
        c.execute("UPDATE bots SET state='paused' WHERE id=?", (BK.SLUG,))
    ensure(api)
    with store.read() as c:
        assert _view_grants(c) == [] and _granted_events(c) == 0


def test_an_empty_queue_is_a_quiet_check_and_news_opens_one_private_task_once(environment):
    api = built(environment)
    ensure(api)
    sees_everything(api)
    assert check(api)["words"] == "Nothing new"
    assert checks(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (BK.SLUG,)).fetchone()[0] == 0
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,state,file_id,"
            "updated_at) VALUES('bil_1','d1','2026-10-30T10:00:00Z','ap@acme.example','Invoice 1042',"
            "'invoice.pdf','application/pdf',?,'needs_entry','fil_1',?)",
            (DIGEST, H.now()),
        )
    first = check(api)
    assert first["items"] == 1 and first["state"] == "opened"
    (task,) = checks(api)
    assert task["title"] == "Books check (1)" and task["private"] == 1 and "bil_1" in task["body"]
    assert check(api)["words"] == "Nothing new" and len(checks(api)) == 1


def _email(inbox):
    return {
        "kind": "email",
        "inbox_id": inbox,
        "delivery_id": "dlv_1",
        "from": "ap@acme.example",
        "subject": "Invoice 1042",
        "received_at": 1793440800,
        "attachments": [
            {"name": "invoice.pdf", "type": "application/pdf", "sha256": DIGEST, "size": len(PDF)},
            {"name": "logo.gif", "type": "image/gif", "sha256": "b" * 64, "size": 10},
        ],
    }


def test_a_bill_emailed_to_the_address_lands_once_in_the_library_and_in_the_bookkeepers_queue(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO inbound_inboxes(inbox_id,kind,name,address,state,created_by) "
            "VALUES('ibx_1','email','Bills','bills@acme.example','active','human:test')"
        )
        assert BA.emailed(c, _email("ibx_1")) == 0  # no bills address set yet
    named = api.put("/api/v2/finance/bills-address", json={"address": "Bills@acme.example"}, headers=signed_in())
    assert named.status_code == 200 and named.json() == {"bills_email": "bills@acme.example", "inbox_id": "ibx_1"}
    with store.read() as c:
        assert FR.bills_email(c) == "bills@acme.example"
    with store.transaction() as c:
        assert BA.emailed(c, _email("ibx_1")) == 1  # the PDF; the GIF is not a bill
        assert BA.emailed(c, _email("ibx_1")) == 0  # a re-delivered page adds nothing
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
        assert row["state"] == "filed" and row["file_id"], row
        assert c.execute("SELECT bot_id FROM bot_files WHERE id=?", (row["file_id"],)).fetchone()["bot_id"] == BK.SLUG
        queue = FR.inbox(c, BK.ACTOR)
        assert [i["file_id"] for i in queue["items"] if i["kind"] == "unmatched_receipt"] == [row["file_id"]]
    with store.transaction() as c:  # the hand-over's key replays: Finance acts once
        again = BA._hand_over(c, row, row["file_id"])
    assert again[1] == row["action_id"]
    first = check(api)
    (task,) = checks(api)
    assert first["items"] == 1 and "Emailed bill from ap@acme.example: Invoice 1042" in task["body"]
    assert row["file_id"] in task["body"]


def test_a_retired_template_is_offered_nowhere_but_a_bot_built_from_it_keeps_its_card(environment):
    from backend import recruit

    api = built(environment)
    settings = api.app.state.store.settings
    # A retired card of its own: no catalog template is retired today, so the test makes one from the Finance Owner's.
    folder = Path(settings.catalog_dir) / "old-books"
    shutil.copytree(TEMPLATE, folder)
    card = yaml.safe_load((folder / "card.yaml").read_text())
    card.update(template="old-books", slug="old-books", name="Old Books", retired=True)
    (folder / "card.yaml").write_text(yaml.safe_dump(card))
    onboarding = api.app.state.onboarding
    with api.app.state.store.read() as c:
        assert "old-books" not in {card["template"] for card in onboarding.catalog(c)}
    assert "old-books" not in {card["template"] for card in recruit.catalog(settings)["cards"]}
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('old-books','old-books','Acme Books','active')")
        assert "old-books" in {card["template"] for card in onboarding.catalog(c)}


def _addressed(api):
    named = api.put("/api/v2/finance/bills-address", json={"address": "bills@acme.example"}, headers=signed_in())
    assert named.status_code == 200, named.text
    return api.app.state.store


def test_only_the_exact_bills_address_among_the_recipients_counts(environment):
    api = built(environment)
    ensure(api)
    store = _addressed(api)
    spoofs = [
        "bills+x@evil.example",
        '"bills@acme.example" <x@evil.example>',
        "x@evil.example (bills@acme.example)",
        "evilbills@acme.example",
        ["ap@acme.example", "bills@acme.example.evil.example"],
    ]
    with store.transaction() as c:
        for n, to in enumerate(spoofs):
            assert BA.emailed(c, {**_email("ibx_other"), "delivery_id": f"dlv_s{n}", "to": to}) == 0, to
        assert BA.emailed(c, {**_email("ibx_other"), "to": "Acme AP <a@acme.example>, Bills <BILLS@acme.example>"}) == 1


def test_a_check_reads_every_page_of_the_queue(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    rows = [{"kind": "uncategorized", "id": f"fd_{i}", "label": f"Row {i}"} for i in range(450)]
    rows += [{"kind": "bill_to_enter", "id": "bil_late", "label": "Acme Supplies 7"}]

    def pages(c, viewer, limit=50, cursor=None, kind=None, at=None):  # each kind pages from its own query
        mine = [r for r in rows if r["kind"] == kind]
        start = int(cursor or 0)
        nxt = str(start + limit) if start + limit < len(mine) else None
        return {
            "items": mine[start : start + limit],
            "counts": {kind: start + len(mine[start : start + limit])},
            "next_cursor": nxt,
        }

    monkeypatch.setattr(FR, "inbox", pages)
    assert check(api)["items"] == 451  # past the inbox's 200 per kind
    (task,) = checks(api)
    assert "bil_late" in task["body"] and "uncategorized (450)" in task["body"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM bookkeeper_seen WHERE key='bill_to_enter:bil_late'").fetchone()


def test_a_failing_fetch_backs_off_then_is_listed_once_and_filed_work_closes(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    sees_everything(api)
    store = _addressed(api)
    with store.transaction() as c:
        c.execute(
            "INSERT INTO inbound_inboxes(inbox_id,kind,name,address,state,created_by) "
            "VALUES('ibx_1','email','Bills','bills@acme.example','active','human:test')"
        )
        assert BA.emailed(c, _email("ibx_1")) == 1
    tries = []

    def down(url, headers):
        tries.append(url)
        raise postal_mail.Unavailable("hq_down")

    monkeypatch.setattr(postal_mail, "_get_bytes", down)
    service = SimpleNamespace(off=False, url="https://hq.example", key="k")
    blobs, files = api.app.state.blobs, api.app.state.files
    BA.drain(service, store, blobs, files)
    BA.drain(service, store, blobs, files)  # still inside its backoff: not fetched again
    assert len(tries) == 1
    with store.transaction() as c:
        c.execute("UPDATE bills_inbound SET tries=?, updated_at=?", (BA.MAX_TRIES - 1, H.shift(H.now(), days=-1)))
    BA.drain(service, store, blobs, files)
    assert len(tries) == 2
    with store.read() as c:
        assert c.execute("SELECT state FROM bills_inbound").fetchone()["state"] == "failed"
    assert check(api)["items"] == 1 and "Invoice 1042" in checks(api)[0]["body"]
    with store.transaction() as c:
        assert c.execute("SELECT state FROM bills_inbound").fetchone()["state"] == "reported"
        later = BK.check(c, api.app.state.auth, H.shift(H.now(), hours=BK.REPEAT_HOURS + 1))
    assert later["words"] == "Nothing new"

    monkeypatch.setattr(postal_mail, "_get_bytes", lambda url, headers: (PDF, "application/pdf"))
    with store.transaction() as c:  # a hand-over Finance refused, later filed by hand
        assert BA.emailed(c, {**_email("ibx_1"), "delivery_id": "dlv_2"}) == 1
    BA.drain(service, store, blobs, files)
    with store.transaction() as c:
        c.execute("UPDATE bills_inbound SET state='needs_entry', code='denied' WHERE delivery='dlv_2'")
        assert BA.reconcile(c) == 1
        assert c.execute("SELECT state FROM bills_inbound WHERE delivery='dlv_2'").fetchone()["state"] == "filed"
        assert BA.for_bookkeeper(c) == []


def test_only_the_failed_bills_the_body_lists_are_reported(environment, monkeypatch):
    api = built(environment)
    ensure(api)
    sees_everything(api)
    store = api.app.state.store
    with store.transaction() as c:
        for i in range(50):
            c.execute(
                "INSERT INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,state,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,'failed',?)",
                (
                    f"bin_{i:02d}",
                    f"dlv_{i}",
                    f"2026-10-01T00:{i:02d}:00Z",
                    "ap@acme.example",
                    f"Invoice {i}",
                    "invoice.pdf",
                    "application/pdf",
                    DIGEST,
                    H.now(),
                ),
            )
    assert check(api)["items"] == 50
    body = checks(api)[0]["body"]
    with store.read() as c:
        reported = {r["id"] for r in c.execute("SELECT id FROM bills_inbound WHERE state='reported'")}
    assert len(reported) == BK.PER_KIND and all(f"- {i} " in body for i in reported)
    monkeypatch.setattr(BK, "BODY_BYTES", 900)  # a body cut short reports only its whole lines
    items = [
        {"kind": "bill_email", "id": f"bin_{i:02d}", "label": "Acme Supplies: Invoice " + "x" * 40, "key": str(i)}
        for i in range(25)
    ]
    text, shown = BK.render_shown(items, {"bill_email": 25})
    assert 0 < len(shown) < 25 and all(f"- {x['id']} " in text for x in shown)
    assert all(f"- {x['id']} " not in text or x in shown for x in items[len(shown) + 1 :])


def test_the_tick_that_sets_finance_up_builds_the_bookkeeper_too(environment):
    """Finance set up by the hourly scheduler tick (not at a start) builds the Bookkeeper in that same tick. The
    scheduler gets the onboarding and auth the app built it with (backend/app.py), else the Bookkeeper waited for the
    next restart or a computer's enrollment."""
    from backend.scheduler import Scheduler

    api = built(environment)
    store = api.app.state.store

    def granted(c):  # the Bookkeeper's Finance rights; only ensure grants them, once Finance is set up
        if not FR.is_set_up(c):
            return set()
        return {r for t in FR.access(c, BK.ACTOR)["teammates"] if t["teammate"] == BK.ACTOR for r in t["rights"]}

    with store.read() as c:
        assert not FR.is_set_up(c) and granted(c) == set()
    failures = []
    Scheduler(
        store, api.app.state.execution, onboarding=api.app.state.onboarding, auth=api.app.state.auth
    ).finance_tick(H.parse_ts(H.now()), failures)
    assert failures == []
    with store.read() as c:
        assert FR.is_set_up(c) and granted(c) == DEFAULT_RIGHTS


def _bot_payment_rules(c):
    return c.execute("SELECT id, active, condition FROM fin_rules WHERE id=?", (BK.BOT_PAYMENT_RULE,)).fetchall()


def _bot_payment_events(c):
    return c.execute("SELECT count(*) FROM events WHERE action='bookkeeper.bot_payment_rule'").fetchone()[0]


def test_a_bots_payment_rule_is_seeded_once_and_an_owners_change_sticks(environment):
    """The Security Owner's condition for the default view: one editable gate rule, "a bot's payment asks a finance
    admin", seeded once per install with one event. The owner turns it off or deletes it on purpose and no start
    restores it."""
    api = built(environment)
    ensure(api)
    ensure(api)
    store = api.app.state.store
    with store.read() as c:
        ((rid, on, _),) = _bot_payment_rules(c)
        assert on == 1 and _bot_payment_events(c) == 1
    with store.transaction() as c:
        owner = "human:" + api.app.state.auth.owner_id(c)
        assert FA.act(c, owner, "rule_disable", {"rule_id": rid}, "rule-off").status == "committed"
    ensure(api)
    with store.read() as c:
        assert [r["active"] for r in _bot_payment_rules(c)] == [0] and _bot_payment_events(c) == 1
    with store.transaction() as c:  # deleted outright: still not restored
        c.execute("DELETE FROM fin_rules WHERE id=?", (rid,))
    ensure(api)
    check(api)
    with store.read() as c:
        assert _bot_payment_rules(c) == [] and _bot_payment_events(c) == 1


def test_an_install_that_already_has_the_default_view_gets_the_bots_payment_rule_at_its_next_start(environment):
    """An rc.23 install: the view grant happened before the rule existed. Its next start seeds the rule anyway, and
    the view grant is not made again."""
    api = built(environment)
    ensure(api)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("DELETE FROM fin_rules WHERE id=?", (BK.BOT_PAYMENT_RULE,))
        c.execute("DELETE FROM events WHERE action='bookkeeper.bot_payment_rule'")
        assert _granted_events(c) == 1
    ensure(api)
    with store.read() as c:
        assert [r["active"] for r in _bot_payment_rules(c)] == [1] and _bot_payment_events(c) == 1
        assert len(_view_grants(c, "active")) == 1 and _granted_events(c) == 1


def test_a_paused_finance_owner_still_gets_the_bots_payment_rule(environment):
    api = built(environment)
    store = api.app.state.store
    with store.transaction() as c:
        FA.ensure_setup(c, store.settings.owner_email)
        c.execute("UPDATE bots SET state='paused' WHERE id=?", (BK.SLUG,))
    ensure(api)
    with store.read() as c:
        assert len(_bot_payment_rules(c)) == 1 and _bot_payment_events(c) == 1
