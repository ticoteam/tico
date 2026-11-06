"""Finance and Tools keys (backend/api_tools.py): a Finance source reading one of an API tool's keys shows under Who
can use it as a read-only Finance row (and in Who can see it as Can use it), and while it reads a key, that key, its
tool and the vault credential cannot be deleted (409 key_in_use); replacing the value stays allowed.

Ana owns the company; Ben is an admin (both credential administrators)."""

import json

from backend.tests.test_api import api, get, headers  # noqa: F401  (fixture)
from backend.tests.test_tool_visibility import SECRET_R, bank


def finance_source(api, credential, name=""):
    """A Finance source (fin_connections, as backend/finance/store/schema.py makes it) reading `credential`."""
    with api.app.state.store.transaction() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS fin_connections (
          id TEXT PRIMARY KEY, provider TEXT NOT NULL,
          purpose TEXT NOT NULL CHECK (purpose IN ('bank','cards','books','payments')), credential_ref TEXT,
          status TEXT NOT NULL DEFAULT 'new', cursor TEXT, last_ok_at TEXT, last_error TEXT,
          created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, name TEXT NOT NULL DEFAULT '',
          settings TEXT NOT NULL DEFAULT '{}', events_cursor TEXT, last_sync_at TEXT, next_sync_at TEXT)""")
        c.execute("INSERT INTO fin_connections(id,provider,purpose,credential_ref,status,created_at,created_by,last_sync_at,"
                  "name) VALUES('fc1','acme-bank','bank',?,'active','2026-11-01T00:00:00Z','human:ana',"
                  "'2026-11-05T10:00:00Z',?)", (credential, name))


def test_the_finance_row_shows_in_who_can_use_it_and_seen_by(api):
    tid, rw, ro = bank(api)
    finance_source(api, ro)
    page = get(api, f"api-tools/{tid}")
    fin = [a for a in page["access"] if a["kind"] == "finance"]
    assert fin == [{"subject": "finance", "kind": "finance", "id": "finance", "name": "Finance", "initials": "F",
                    "level": "read", "last_used_at": "2026-11-05T10:00:00Z", "fixed": True}]
    assert ("finance", "Can use it") in [(r["subject"], r["why"]) for r in page["seen_by"]]
    assert "finance" not in json.dumps(page["audience"])          # read-only: never an audience member
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET credential_ref=? WHERE id='fc1'", (rw,))
    assert [a["level"] for a in get(api, f"api-tools/{tid}")["access"] if a["kind"] == "finance"] == ["read_write"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET status='disconnected' WHERE id='fc1'")
    assert not [a for a in get(api, f"api-tools/{tid}")["access"] if a["kind"] == "finance"]


def test_a_key_finance_reads_cannot_be_deleted_until_the_source_is_disconnected(api):
    tid, rw, ro = bank(api, None)
    finance_source(api, ro, "Acme checking")
    detail = "Finance uses this key. Disconnect Acme checking in Finance › Sources first."
    for r in (api.delete(f"/api/v2/api-tools/{tid}/keys/{ro}", headers=headers()),
              api.delete(f"/api/v2/api-tools/{tid}", headers=headers()),
              api.delete(f"/api/v2/credentials/{ro}", headers=headers())):
        assert r.status_code == 409 and r.json()["error"]["code"] == "key_in_use", r.text
        assert r.json()["error"]["detail"] == detail
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM credentials WHERE tool_id=?", (tid,)).fetchone()[0] == 2
    # Replacing its value is fine, and the other key still goes.
    r = api.patch(f"/api/v2/api-tools/{tid}/keys/{ro}", json={"value": SECRET_R + "-new"}, headers=headers())
    assert r.status_code == 200, r.text
    assert api.delete(f"/api/v2/api-tools/{tid}/keys/{rw}", headers=headers()).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET status='disconnected' WHERE id='fc1'")
    assert api.delete(f"/api/v2/api-tools/{tid}", headers=headers()).json()["deleted"]
