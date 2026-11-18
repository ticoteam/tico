"""A posting rule with apply_from posts past rows still waiting for a category, as a new row would be."""

from backend.tests.helpers.finance_banking import _txn
from backend.tests.helpers.finance_fixtures import (
    OWNER,
    FakeMercury,
    account,
    act,
    books,  # noqa: F401
    close_month,
    connect,
    finance_template,  # noqa: F401
    reopen_rows,
    store,  # noqa: F401
    sync_once,
)


def _rule(c, name, counterparty, code, **extra):
    payload = {
        "kind": "posting",
        "name": name,
        "when": {"counterparty": counterparty},
        "outcome": {"outcome": "post", "account_id": account(c, code)},
        "examples": [{"input": {"counterparty": counterparty}, "expect": "post"}],
        **extra,
    }
    done = act(c, OWNER, "rule_create", payload)
    assert done.status == "committed", done
    return done.result


def _feed(c, provider_id):
    return c.execute("SELECT id, status, entry_id FROM fin_feed_txns WHERE provider_id=?", (provider_id,)).fetchone()


def _codes(c, entry_id):
    return sorted(
        (str(r[0]), int(r[1]))
        for r in c.execute(
            "SELECT a.code, l.amount FROM fin_lines l JOIN fin_accounts a ON a.id=l.account_id WHERE l.entry_id=?",
            (entry_id,),
        )
    )


def _asks(c, feed_id):
    return c.execute(
        "SELECT count(*) FROM fin_events WHERE name='finance.feed.needs_category' AND subject=? AND drained_at IS NULL",
        (feed_id,),
    ).fetchone()[0]


def _past(books, *rows):
    fake = FakeMercury()
    connect(books, fake)
    for raw in rows:
        fake.txns[raw["id"]] = raw
    sync_once(books, fake)
    return fake


def test_a_new_rule_with_apply_from_posts_a_past_default_debit_and_a_past_waiting_credit(books):
    _past(
        books,
        _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
        _txn("client-1", "acct-chk", 250.0, "other", day="2026-10-03", counterpartyName="Example Client"),
        _txn("cloud-0", "acct-chk", -30.0, "other", day="2026-09-20", counterpartyName="Example Cloud"),
    )
    with books.transaction() as c:
        debit, credit, early = _feed(c, "cloud-1"), _feed(c, "client-1"), _feed(c, "cloud-0")
        assert ("6900", 4_000) in _codes(c, debit[2]) and _asks(c, debit[0]) == 1  # the ingest default
        assert tuple(credit[1:]) == ("new", None) and _asks(c, credit[0]) == 1
        done = _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-10-01")
        assert {k: done[k] for k in ("kind", "reposted", "skipped_closed", "capped")} == {
            "kind": "posting",
            "reposted": 1,
            "skipped_closed": 0,
            "capped": False,
        }
        assert _rule(c, "Client", "Example Client", "4000", apply_from="2026-10-01")["reposted"] == 1
        now = _feed(c, "cloud-1")
        assert now[1] == "posted" and now[2] != debit[2]
        assert ("6100", 4_000) in _codes(c, now[2]) and "6900" not in dict(_codes(c, now[2]))
        old = c.execute("SELECT status FROM fin_entries WHERE id=?", (debit[2],)).fetchone()[0]
        assert old == "reversed"  # the Other expenses entry is taken back, so the books stay balanced
        assert _asks(c, debit[0]) == 0
        paid = _feed(c, "client-1")
        assert paid[1] == "posted" and ("4000", -25_000) in _codes(c, paid[2]) and _asks(c, credit[0]) == 0
        assert _feed(c, "cloud-0")[2] == early[2]  # dated before apply_from: untouched
        assert c.execute("SELECT coalesce(sum(amount), 0) FROM fin_lines").fetchone()[0] == 0


def test_apply_from_leaves_a_row_a_person_categorized(books):
    _past(books, _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"))
    with books.transaction() as c:
        fid = _feed(c, "cloud-1")[0]
        kept = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6900")})
        assert kept.status == "committed", kept
        entry = _feed(c, "cloud-1")[2]
        assert _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-10-01")["reposted"] == 0
        assert _feed(c, "cloud-1")[2] == entry and ("6900", 4_000) in _codes(c, entry)


def test_a_higher_priority_older_rule_still_wins_a_row_both_match(books):
    _past(books, _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"))
    with books.transaction() as c:
        assert "reposted" not in _rule(c, "First", "Example Cloud", "6150", priority=10)
        assert ("6900", 4_000) in _codes(c, _feed(c, "cloud-1")[2])  # a rule alone changes no past row
        assert _rule(c, "Later", "Example Cloud", "6100", priority=50, apply_from="2026-10-01")["reposted"] == 1
        assert ("6150", 4_000) in _codes(c, _feed(c, "cloud-1")[2])


def test_a_row_in_a_closed_period_is_skipped_and_counted(books):
    _past(
        books,
        _txn("cloud-sep", "acct-chk", -30.0, "other", day="2026-09-20", counterpartyName="Example Cloud"),
        _txn("cloud-oct", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
    )
    with books.transaction() as c:
        assert close_month(c, "2026-09").status == "committed"
        reopen_rows(c)  # not reconciled any more; the month stays closed
        entry = _feed(c, "cloud-sep")[2]
        done = _rule(c, "Cloud", "Example Cloud", "6100", apply_from="2026-09-01")
        assert (done["reposted"], done["skipped_closed"]) == (1, 1)
        assert _feed(c, "cloud-sep")[2] == entry
        assert ("6100", 4_000) in _codes(c, _feed(c, "cloud-oct")[2])


def test_without_apply_from_past_rows_stay_and_a_gate_rule_refuses_it(books):
    _past(
        books,
        _txn("cloud-1", "acct-chk", -40.0, "other", day="2026-10-02", counterpartyName="Example Cloud"),
        _txn("client-1", "acct-chk", 250.0, "other", day="2026-10-03", counterpartyName="Example Client"),
    )
    with books.transaction() as c:
        entry = _feed(c, "cloud-1")[2]
        rid = _rule(c, "Cloud", "Example Cloud", "6100")["rule_id"]
        _rule(c, "Client", "Example Client", "4000")
        assert _feed(c, "cloud-1")[2] == entry and tuple(_feed(c, "client-1")[1:]) == ("new", None)
        moved = act(c, OWNER, "rule_update", {"rule_id": rid, "apply_from": "2026-10-01"})
        assert moved.status == "committed" and moved.result["reposted"] == 1  # rule_update takes it too
        gate = {
            "kind": "gate",
            "name": "Journals",
            "when": {"kind": "journal"},
            "outcome": {"outcome": "deny", "reason": "frozen"},
            "examples": [{"input": {"kind": "journal"}, "expect": "deny"}],
            "apply_from": "2026-10-01",
        }
        refused = act(c, OWNER, "rule_create", gate)
        assert refused.status != "committed" and "apply_from is for posting rules" in refused.reason, refused
