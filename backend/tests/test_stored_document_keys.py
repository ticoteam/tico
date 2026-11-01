"""Documents saved in the database before the column renames keep their own keys, and still read back.

The migrations rename columns, not the keys inside JSON documents. Each test here plants a document as an existing
install holds it, then reads it through the code that hands it out.
"""
import hashlib
import json

from backend import access, directory, gateway_connection, move_retry, onboarding, providers, redesign_migration
from backend import source_outages
from backend.store import H
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_login_holds import at
from backend.tests.test_source_outages import managers, notices

WHEN = "2026-09-01T10:00:00.000000Z"


def plant(c, key, document):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(document)))


def saved(c, key):
    return json.loads(c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()[0])


def test_owner_and_access_read_their_saved_time_and_keep_writing_that_key(api):
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        plant(c, access.OWNER, {"email": "ana@acme.example", "revision": 3, "updated": WHEN, "updated_by": "human:ana",
                                "source": "owner"})
        plant(c, access.ACCESS, {"allowed": [], "allowed_domains": [], "admins": [], "revision": 2, "updated": WHEN,
                                 "updated_by": "human:ana", "source": "owner"})
        assert access.load_owner(c, settings)["updated_at"] == WHEN
        assert access.load_access(c, settings)["updated_at"] == WHEN
        access.save_access(c, "human:ana", {"expected_revision": 2, "allowed": [], "allowed_domains": []},
                           "2026-09-02T00:00:00.000000Z", settings=settings)
        document = saved(c, access.ACCESS)
        assert document["updated"] == "2026-09-02T00:00:00.000000Z" and "updated_at" not in document


def test_provider_settings_and_the_setup_card_read_their_saved_time(api):
    with api.app.state.store.transaction() as c:
        plant(c, providers.KEY, {"enabled": ["claude"], "runtime": "claude", "model": "", "revision": 2, "updated": WHEN,
                                 "updated_by": "human:ana", "source": "owner"})
        assert providers.load(c)["updated_at"] == WHEN


def test_the_gateway_connection_reads_its_saved_time(api):
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        plant(c, gateway_connection.KEY, {"url": "", "revision": 2, "updated": WHEN, "updated_by": "human:ana"})
        assert gateway_connection.view(c, settings, manage=True)["updated_at"] == WHEN


def test_onboarding_reads_its_saved_time_and_keeps_writing_that_key(api):
    with api.app.state.store.transaction() as c:
        plant(c, onboarding.KEY, {"names": {}, "updated": WHEN, "updated_by": "human:ana"})
        record = onboarding.load(c)
        assert record["updated_at"] == WHEN
        assert onboarding.Onboarding._store(c, record, "human:ana")["updated_at"] != WHEN
        document = saved(c, onboarding.KEY)
        assert document["updated"] != WHEN and "updated_at" not in document


def test_a_scim_token_keeps_its_creation_time(api):
    store = api.app.state.store
    sync = directory.Directory(store.settings, store, None)
    with store.transaction() as c:
        plant(c, directory.KEY, {"scim": {"token_hash": "x", "created": WHEN}, "updated": WHEN})
        assert sync.view(c)["scim"]["created_at"] == WHEN


def test_a_converted_wake_rule_keeps_its_times_and_its_saved_keys(api):
    flow = {"trigger": {"type": "event", "name": "tico.wake"}, "steps": [{"id": "r1", "type": "filter", "rules": [{"when": [], "then": [
        {"do": "wake_bot", "args": {"outcome": "wake", "params": {}, "rule": {"id": "rule1", "created": WHEN,
                                                                              "updated": WHEN}}}]}]}]}
    row = redesign_migration.wake_rules_of("a1", flow)[0]
    assert row["created_at"] == WHEN and row["updated_at"] == WHEN


def test_a_recovery_saved_before_the_renames_still_beats_an_older_refusal(api, monkeypatch):
    at(monkeypatch, "2026-10-27T01:00:00.000000Z")
    task = post(api, "tasks", {"owner": "ops", "title": "Review the change", "body": "Read it."})
    source = "github:acme/project"
    monkeypatch.setattr(move_retry, "remembered", lambda *_: (None, {"outage": True, "at": WHEN, "since": WHEN}))
    monkeypatch.setattr("backend.review_gate.links", lambda *_: [{"repo": "acme/project"}])
    with api.app.state.store.transaction() as c:
        managers(c)
        c.execute("INSERT INTO task_events(task_id,created_at,actor,field) VALUES(?,?,?,?)",
                  (task["id"], H.now(), "bot:ops", move_retry.FIELD))
        plant(c, source_outages.PREFIX + hashlib.sha256(source.encode()).hexdigest()[:32],
              {"source": source, "kind": "GitHub checks access", "active": False, "since": WHEN, "waiting": 0,
               "until": None, "updated": "2026-09-02T00:00:00.000000Z"})
        source_outages.sync(c, api.app.state.store.settings)
        assert notices(c) == []
