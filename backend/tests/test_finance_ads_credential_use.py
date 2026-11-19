"""An ad spend import (backend/finance/ad_import.py) reads only keys the person or bot who bound them may use (the vault's
rule, store/tico.py `may_use_credential`), as a bank connection does (test_finance_credential_use.py): binding a key
one may not use is refused, and a run refuses, logged, an import whose binder may no longer use one of its keys, before
any key is read or sent to Google or Meta."""

import logging
from typing import Any

import pytest

from backend import hubdb as H
from backend.credentials import Vault
from backend.finance import ad_import as AI
from backend.finance import ads as FAds
from backend.finance import banking
from backend.finance.core.errors import FinanceError
from backend.finance.store import ads as SA
from backend.store import Store
from backend.tests.helpers.finance_ad_spend import http, key, meta, tool
from backend.tests.helpers.finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401

CARA = "human:cara"


def grant(c: Any, credential: str, subject: str) -> str:
    gid = H.new_id()
    c.execute(
        "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
        (gid, credential, subject, OWNER, AT),
    )
    return gid


def finance_admin(c: Any, teammate: str) -> None:
    assert act(c, OWNER, "right_grant", {"teammate": teammate, "right": "finance.admin"}).status == "committed"


def configure(c: Any, viewer: str, **change: Any) -> dict[str, object]:
    base: dict[str, Any] = {"enabled": False, "history_from": None, "account_id": None, "credentials": None}
    return FAds.configure(c, viewer, "meta-ads", **{**base, **change}, at=AT)


def meta_key(store: Store) -> None:
    tool(store, "meta-ads", "meta-ads")
    key(store, "k-meta", "Meta · META_ACCESS_TOKEN", "META_ACCESS_TOKEN", "meta-secret-token", tool_id="meta-ads")


def test_a_finance_admin_binds_only_a_key_they_may_use(books: Store):
    meta_key(books)
    with books.transaction() as c:
        finance_admin(c, CARA)
        with pytest.raises(FinanceError) as refused:
            configure(c, CARA, credentials={"access_token": "k-meta"})
        assert (refused.value.code, str(refused.value)) == ("denied", banking.KEY_NOT_YOURS)
        assert SA.import_for(c, "meta-ads") is None
        # The picker's default set is a binding too: saving it binds those keys.
        with pytest.raises(FinanceError, match="not one you may use"):
            configure(c, CARA)
        grant(c, "k-meta", CARA)
        configure(c, CARA, credentials={"access_token": "k-meta"})
        saved = SA.import_for(c, "meta-ads")
        assert saved is not None and saved.credentials == {"access_token": "k-meta"} and saved.created_by == CARA


def test_whoever_changes_the_keys_binds_them_and_other_settings_leave_them_bound(books: Store):
    meta_key(books)
    key(books, "k-cara", "Cara · META_ACCESS_TOKEN", "META_ACCESS_TOKEN", "cara-secret-token", tool_id="meta-ads")
    with books.transaction() as c:
        finance_admin(c, CARA)
        configure(c, OWNER, credentials={"access_token": "k-meta"})
        # Cara can't use the owner's key, but a change that binds no key is hers to make: the owner still binds them.
        configure(c, CARA, history_from="2026-09-01")
        saved = SA.import_for(c, "meta-ads")
        assert saved is not None and saved.history_from == "2026-09-01" and saved.created_by == OWNER
        grant(c, "k-cara", CARA)
        configure(c, CARA, credentials={"access_token": "k-cara"})
        saved = SA.import_for(c, "meta-ads")
        assert saved is not None and saved.created_by == CARA  # the run now reads with Cara's rights


def test_a_run_refuses_a_key_its_binder_may_no_longer_use_before_reading_it(books: Store, caplog, monkeypatch):
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "spend": "10", "date_start": "2026-10-02"}]
    with books.transaction() as c:
        gid = grant(c, "k-meta", CARA)
        c.execute("UPDATE fin_ad_imports SET created_by=? WHERE id=?", (CARA, iid))
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"  # while Cara holds the key
    sent = len(net.calls)
    with books.transaction() as c:
        c.execute("UPDATE credential_grants SET revoked_at=? WHERE id=?", (AT, gid))

    def read(*_a: Any) -> str:
        raise AssertionError("a key its binder may not use was read")

    monkeypatch.setattr(AI.tico, "credential_secret", read)
    with caplog.at_level(logging.WARNING):
        done = AI.run(books, Vault(books), iid, now="2026-10-15T17:00:00.000000Z", http_=http(net))
    assert (done.status, done.error) == ("failed", banking.KEY_NOT_USABLE_NOW)
    assert len(net.calls) == sent  # nothing went to Meta
    with books.read() as c:
        after = SA.import_for(c, "meta-ads")
    assert after is not None and (after.status, after.last_error) == ("failed", banking.KEY_NOT_USABLE_NOW)
    assert "category=credential_not_usable" in caplog.text and f"id={iid}" in caplog.text
    assert "meta-secret-token" not in caplog.text


def test_an_owner_made_import_keeps_running(books: Store):
    """The owner is a credential administrator: the imports they set up keep reading their keys."""
    iid, net, insights = meta(books)
    insights["111"] = [{"campaign_id": "c1", "spend": "10", "date_start": "2026-10-02"}]
    assert AI.run(books, Vault(books), iid, now=AT, http_=http(net)).status == "ok"
