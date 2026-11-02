"""The one `idempotency` table (backend/store.py): a stored answer replays and expires after the retry window, an
operation binding is kept and never swept, and an unknown outcome of a bound operation is refused without asking again.
The migration that moves an older file's rows across is tested in test_migrations.py."""

import pytest

from backend import invites
from backend.store import sweep_idempotency
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_invites import owner  # noqa: F401
from backend.tests.test_oidc import provider, signin  # noqa: F401  (the fixtures)


def test_a_repeat_returns_the_stored_answer_and_a_reused_key_with_other_content_is_refused(api):  # noqa: F811
    from backend.tests.test_storage_delivery import task
    url = f"/api/v2/tasks/{task(api)}/files"
    h = headers("ana-test")
    body = {"name": "notes.txt", "text": "first"}
    first = api.post(url, json=body, headers=h)
    assert first.status_code == 200, first.text
    again = api.post(url, json=body, headers=h)
    assert again.status_code == 200 and again.json() == first.json()
    changed = api.post(url, json={**body, "text": "second"}, headers=h)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT binding,state FROM idempotency WHERE operation=? AND key=?",
                        (url, h["Idempotency-Key"])).fetchone()
    assert tuple(row) == (None, "completed")


def test_bound_rows_survive_the_retention_sweep_and_stored_answers_do_not(api):  # noqa: F811
    store = api.app.state.store
    old = "2026-01-01T00:00:00.000000Z"
    bound = [("bind-invite", "invites", "cara", "completed"),
             ("bind-product", "github_product_repo", "Acme/tico-new", "pending"),
             ("bind-review", "github_review", "", "refused")]
    with store.transaction() as c:
        c.execute("INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at) "
                  "VALUES('human:ana','/api/v2/x','sweep-answer','h','{}',?)", (old,))
        for key, binding, subject, state in bound:
            c.execute("INSERT INTO idempotency(actor,operation,key,request_hash,response,created_at,binding,subject,state) "
                      "VALUES('human:ana','/api/v2/y',?,'h',NULL,?,?,?,?)", (key, old, binding, subject, state))
    assert sweep_idempotency(store, now="2026-03-01T00:00:00.000000Z") >= 1
    with store.read() as c:
        left = {r[0]: r[1] for r in c.execute("SELECT key,state FROM idempotency WHERE key LIKE 'sweep-%' "
                                              "OR key LIKE 'bind-%'")}
    assert left == {key: state for key, _, _, state in bound}


def test_an_invite_whose_outcome_is_unknown_is_refused_again_and_its_send_is_never_repeated(signin, monkeypatch):  # noqa: F811
    headers_ = owner(signin)
    url = "/api/v2/access/people/cara/invite"
    attempts = []

    def lost(auth, settings, person):
        attempts.append(person["id"])
        raise RuntimeError("connection reset while the issuer answered")
    monkeypatch.setattr(invites, "send", lost)
    with pytest.raises(RuntimeError):
        signin.api.post(url, json={}, headers=headers_)
    for _ in range(2):
        again = signin.api.post(url, json={}, headers=headers_)
        assert again.status_code == 422 and again.json()["error"]["code"] == "invite_unknown", again.text
    assert len(attempts) == 1, "the issuer is asked once: the outcome is unknown, so no copy asks again"
    with signin.auth.store.read() as c:
        row = c.execute("SELECT binding,state FROM idempotency WHERE key=?", (headers_["idempotency-key"],)).fetchone()
    assert tuple(row) == ("invites", "pending")
