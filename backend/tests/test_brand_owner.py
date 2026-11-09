"""The Brand Owner (templates/catalog/brand, docs/onboarding.md) is built in like the Librarian: a required bootstrap card,
outside the org chart, nobody archives it, a company from before it gets it on update (one that hired a bot under its slug
keeps that bot), and its monthly audit stays off until its own setup-done turns it on, once."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import routines as R
from backend.app import create_app
from backend.auth import Identity
from backend.store import H
from backend.tests.test_goal_manager import BASE
from backend.tests.test_onboarding import draft, environment, machine, signed_in  # noqa: F401
from backend import bot_settings as BS

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "brand"
AUDIT = H.BRAND_OWNER + ":monthly-brand-audit"


def with_brand(api):
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "brand")


def set_up(environment, card=True):
    api = environment(seed={}, cards=BASE)
    if card:
        with_brand(api)
    machine(api)
    draft(api)
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    return api, done.json()


def audit(api):
    with api.app.state.store.read() as c:
        return R.row(c, AUDIT)


def as_brand_owner(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            (H.BRAND_OWNER, "hermes", "hash-brand", "2026-01-01T00:00:00Z", "human:test"),
        )
    api.app.state.store.settings.test_identities["brand"] = Identity("bot:" + H.BRAND_OWNER, "bot", agent="hermes")
    return signed_in("brand")


def test_it_is_built_for_every_company_outside_the_chart_with_its_audit_off(environment):
    api, record = set_up(environment)
    row = next(row for row in record["bots"] if row["slug"] == H.BRAND_OWNER)
    assert row["setup_task_id"] is None  # the runner sets it up, not BotOps
    with api.app.state.store.read() as c:
        assert H.bot(c, H.BRAND_OWNER)["state"] == "active"
        assert not BS.read(c, H.BRAND_OWNER).get("team")  # its card names Marketing; built-ins stay outside groups
        assert not c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (H.BRAND_OWNER,)).fetchone()[0]
    assert api.app.state.auth.system_bot(H.BRAND_OWNER)
    assert audit(api)["enabled"] == 0
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot=?", (H.BRAND_OWNER,)).fetchone()[0]
    gone = api.post(f"/api/v2/bots/{H.BRAND_OWNER}/archive", json={"expected_revision": revision}, headers=signed_in())
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "system_bot"


def test_its_own_setup_done_turns_the_audit_on_once(environment):
    api, _ = set_up(environment)
    done = api.post(f"/api/v2/bots/{H.BRAND_OWNER}/onboarded", json={}, headers=as_brand_owner(api))
    assert done.status_code == 200, done.text
    assert done.json()["routine"] == "monthly-brand-audit" and audit(api)["enabled"] == 1
    with api.app.state.store.transaction() as c:
        R.update(c, "human:morgan", AUDIT, {"enabled": False})  # the owner turns it off
    again = api.post(f"/api/v2/bots/{H.BRAND_OWNER}/onboarded", json={}, headers=as_brand_owner(api))
    assert again.status_code == 200 and again.json()["routine"] is None and audit(api)["enabled"] == 0


def test_an_audit_the_owner_turned_off_before_setup_done_stays_off(environment):
    api, _ = set_up(environment)
    with api.app.state.store.transaction() as c:
        R.update(c, "human:morgan", AUDIT, {"enabled": True})
        R.update(c, "human:morgan", AUDIT, {"enabled": False})
    api.post(f"/api/v2/bots/{H.BRAND_OWNER}/onboarded", json={}, headers=as_brand_owner(api))
    assert audit(api)["enabled"] == 0


def test_a_company_from_before_gets_it_on_update_once(environment):
    api, _ = set_up(environment, card=False)
    with api.app.state.store.read() as c:
        assert not H.resolve_bot(c, H.BRAND_OWNER)  # this release's catalog has no card yet
    with_brand(api)
    with TestClient(create_app(api.app.state.store.settings)):  # a restart is an update
        pass
    with api.app.state.store.read() as c:
        assert H.bot(c, H.BRAND_OWNER)["state"] == "active"
        assert c.execute("SELECT count(*) FROM bots WHERE slug=?", (H.BRAND_OWNER,)).fetchone()[0] == 1
    assert audit(api)["enabled"] == 0
    with api.app.state.store.transaction() as c:
        assert api.app.state.onboarding.ensure_brand_owner(c) is None  # twice is still one


@pytest.mark.parametrize("bot_id", ["0190f1a2-0000-7000-8000-00000000b4a9", H.BRAND_OWNER], ids=["uuid", "legacy"])
def test_a_company_that_hired_a_brand_manager_keeps_it(environment, bot_id):
    api, _ = set_up(environment, card=False)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')",
            (bot_id, H.BRAND_OWNER, "Brand Manager"),
        )
        c.execute("INSERT INTO bot_config(bot,owner_id,repo) VALUES(?,?,?)", (bot_id, "morgan", "bot-brand"))
        BS.write(c, bot_id, {"template": "brand", "instructions": "# Our brand manager\n"})

    def theirs():
        with api.app.state.store.read() as c:
            return (
                [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,slug,display_name,state FROM bots WHERE slug=? OR id=?",
                        (H.BRAND_OWNER, H.BRAND_OWNER),
                    )
                ],
                {k: v for k, v in BS.read(c, bot_id).items() if k in ("template", "instructions", "display_name")},
                c.execute("SELECT count(*) FROM assignments WHERE bot=?", (bot_id,)).fetchone()[0],
                c.execute("SELECT count(*) FROM schedules WHERE bot=?", (bot_id,)).fetchone()[0],
            )

    before = theirs()
    with_brand(api)
    with TestClient(create_app(api.app.state.store.settings)):  # the update that builds the Brand Owner in
        pass
    with api.app.state.store.transaction() as c:
        assert api.app.state.onboarding.ensure_brand_owner(c) is None
    assert theirs() == before and len(before[0]) == 1
