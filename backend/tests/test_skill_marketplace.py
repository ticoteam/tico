"""A person's skills as a Claude Code marketplace: latest or pinned per install, only their own, deterministic zips."""

import hashlib
import io
import json
import zipfile

from backend import skill_marketplace
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_skill_devices import add_skill, bearer, connect, install_for, rows
from clients.skills import RESERVED


def test_marketplace_is_the_persons_own_with_latest_and_pinned_versions(api):
    alpha, alpha_v = add_skill(api, "alpha", "# Alpha v1", "# Alpha v2")
    beta, beta_v = add_skill(api, "beta", "# Beta v1", "# Beta v2")
    gamma, _ = add_skill(api, "gamma", "# Gamma v1")
    install_for(api, alpha, "cara")
    install_for(api, beta, "cara", pinned=beta_v[0])
    install_for(api, gamma, "ben")
    device = bearer(connect(api)["token"])
    r = api.get("/api/v2/skills/marketplace.json", headers=device)
    assert r.status_code == 200 and r.headers["x-tico-reporting"] == "off"
    market = r.json()
    assert market["name"] == "tico" and market["owner"]["name"] and market["forceRemoveDeletedPlugins"] is True
    by_name = {p["name"]: p for p in market["plugins"]}
    assert list(by_name) == ["tico-skills", "alpha", "beta"]           # never Ben's gamma
    shas = {r["id"]: r["sha256"] for r in rows(api, "SELECT id,sha256 FROM skill_versions")}
    assert by_name["alpha"]["metadata"]["tico"] == {"version": 2, "bundle_sha256": shas[alpha_v[1]]}
    assert by_name["beta"]["metadata"]["tico"] == {"version": 1, "bundle_sha256": shas[beta_v[0]]}
    assert all("version" not in p for p in market["plugins"])        # the archive pin is the version
    # The person signed in sees the same; Ben sees only his own.
    assert api.get("/api/v2/skills/marketplace.json", headers=headers("cara-test")).json()["plugins"] == market["plugins"]
    assert [p["name"] for p in api.get("/api/v2/skills/marketplace.json", headers=headers("ben-test")).json()["plugins"]] \
        == ["tico-skills", "gamma"]
    for p in market["plugins"]:
        source = p["source"]
        assert source["source"] == "archive" and len(source["sha256"]) == 64
        path = source["url"].split("/api/v2/", 1)[1]
        data = api.get("/api/v2/" + path, headers=device)
        assert data.status_code == 200, (path, data.text)
        assert hashlib.sha256(data.content).hexdigest() == source["sha256"]
    alpha_zip = api.get("/api/v2/" + by_name["alpha"]["source"]["url"].split("/api/v2/", 1)[1], headers=device).content
    with zipfile.ZipFile(io.BytesIO(alpha_zip)) as z:
        assert z.namelist() == [".claude-plugin/plugin.json", "skills/alpha/SKILL.md", "skills/alpha/scripts/run.sh"]
        assert json.loads(z.read(".claude-plugin/plugin.json")) == {"name": "alpha", "description": "alpha v2"}
        assert z.read("skills/alpha/SKILL.md") == b"# Alpha v2"
    hooks = api.get("/api/v2/" + by_name["tico-skills"]["source"]["url"].split("/api/v2/", 1)[1], headers=device).content
    with zipfile.ZipFile(io.BytesIO(hooks)) as z:
        hook = json.loads(z.read("hooks/hooks.json"))["hooks"]
        assert hook["PostToolUse"][0]["matcher"] == "Skill" and "report --trigger model" in hook["PostToolUse"][0]["hooks"][0]["command"]
        assert "report --trigger person" in hook["UserPromptExpansion"][0]["hooks"][0]["command"]
        assert "sync --quiet" in hook["SessionStart"][0]["hooks"][0]["command"]
        helper = z.read("scripts/skill_helper.mjs").decode()
        assert "__TICO_SERVER__" not in helper and api.app.state.store.settings.public_url in helper


def test_zips_are_deterministic_and_a_tampered_version_is_never_served(api):
    alpha, versions = add_skill(api, "alpha", "# Alpha")
    install_for(api, alpha, "cara")
    sha = rows(api, "SELECT sha256 FROM skill_versions")[0]["sha256"]
    path = f"/api/v2/skills/plugins/alpha/{sha}.zip"
    first = api.get(path, headers=headers("cara-test")).content
    skill_marketplace._ZIPS.clear()
    second = api.get(path, headers=headers("cara-test")).content
    assert first == second and hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert skill_marketplace.bundle_sha([{"path": "b", "content": "2"}, {"path": "a", "content": "1"}]) == \
        hashlib.sha256(b'[{"content":"1","path":"a"},{"content":"2","path":"b"}]').hexdigest()
    assert api.get("/api/v2/skills/plugins/alpha/" + "0" * 64 + ".zip", headers=headers("cara-test")).status_code == 404
    assert api.get("/api/v2/skills/plugins/tico-skills/" + "0" * 64 + ".zip", headers=headers("cara-test")).status_code == 404
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE skill_versions SET files=? WHERE id=?",
                  (json.dumps([{"path": "SKILL.md", "content": "# changed"}]), versions[0]))
    skill_marketplace._ZIPS.clear()
    assert api.get(path, headers=headers("cara-test")).status_code == 404
    names = [p["name"] for p in api.get("/api/v2/skills/marketplace.json", headers=headers("cara-test")).json()["plugins"]]
    assert names == ["tico-skills"]


def test_the_helper_is_served_without_sign_in_and_nothing_else_is(api):
    r = api.get("/api/v2/skills/helper.mjs")
    assert r.status_code == 200 and api.app.state.store.settings.public_url in r.text and "__TICO_SERVER__" not in r.text
    assert api.get("/api/v2/skills/marketplace.json").status_code == 401
    assert api.get("/api/v2/skills/devices").status_code == 401


def test_the_registry_name_routes_never_shadow_the_helpers_routes(api):
    """backend/skills.py's GET skills/{name} mounts after these routes, and no skill may take their path words."""
    device = bearer(connect(api)["token"])
    r = api.get("/api/v2/skills/marketplace.json", headers=device)
    assert r.status_code == 200 and r.json()["name"] == "tico", r.text
    assert "devices" in api.get("/api/v2/skills/devices", headers=headers("cara-test")).json()
    assert api.get("/api/v2/skills/helper.mjs").status_code == 200
    for name in sorted(RESERVED):
        refused = api.post("/api/v2/skills/proposals", headers=headers("ana-test"), json={
            "name": name, "files": [{"path": "SKILL.md", "content": f"---\nname: {name}\ndescription: x\n---\n\nx\n"}]})
        assert refused.status_code == 422 and "reserved" in refused.text, (name, refused.text)
