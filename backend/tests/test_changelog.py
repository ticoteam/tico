"""Product updates: the list and its paging, the per-person read state and privacy boundary, and packaged ids."""

from backend import changelog
from backend.store import H
from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.helpers.mcp import call, rpc
from backend.tests.helpers.openapi_v2 import conforms

# Packaged announcements with fixed days, so their order does not depend on when the test runs.
PACKAGED = """\
- title: Search tasks
  shipped_at: "2026-10-02T00:00:00Z"
  bullets:
    - Search your tasks across the team.
    - Restore a deleted task.
- title: Browse the team
  shipped_at: "2026-10-01T00:00:00Z"
  bullets:
    - Browse the team.
"""


def test_product_updates_api_and_mcp(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api.app.state.store.settings, "registry_dir", tmp_path)
    (tmp_path / "product-updates.yaml").write_text(PACKAGED)
    assert api.get("/api/v2/changelog").status_code == 401
    first = api.get("/api/v2/changelog?kind=product&limit=1", headers=headers("ben-test")).json()
    assert first["entries"][0]["title"] == "Search tasks" and first["next_offset"] == 1
    assert first["entries"][0]["bullets"] == ["Search your tasks across the team.", "Restore a deleted task."]
    assert first["unread_count"] == 2
    second = api.get("/api/v2/changelog?kind=product&limit=1&offset=1", headers=headers("ben-test")).json()
    assert second["entries"][0]["title"] == "Browse the team" and second["next_offset"] is None
    err, result = call(api, "hub_changelog_list", {"q": "restore", "unread": True}, token="ben-test")
    assert not err and [r["title"] for r in result["entries"]] == ["Search tasks"]
    assert result["unread_count"] == 2
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    assert conforms(result, document["components"]["schemas"]["ChangelogList"], document) is None
    assert (
        api.get("/api/changelog", headers=headers()).json()["entries"]
        == api.get("/api/v2/changelog", headers=headers()).json()["entries"]
    )
    # Same-day announcements keep their ids when a registry list is reordered.
    announcements = tmp_path / "product-updates.yaml"
    announcements.write_text('- title: A\n  bullets: [First]\n- title: B\n  bullets: [Second]\n')
    ids = {r["title"]: r["id"] for r in changelog.packaged_products(api.app.state.store.settings)}
    announcements.write_text('- title: B\n  bullets: [Second]\n- title: A\n  bullets: [First]\n')
    assert ids == {r["title"]: r["id"] for r in changelog.packaged_products(api.app.state.store.settings)}


def test_read_state_is_personal_and_only_acknowledges_shown_updates(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api.app.state.store.settings, "registry_dir", tmp_path)
    (tmp_path / "product-updates.yaml").write_text(PACKAGED)
    with api.app.state.store.transaction() as c:
        private = H.task_create(
            c, "human:ana", "Review the private packet", "Private packet", "bot:ops", private=True, lint=False
        )
        H.task_done(c, "human:ana", private["id"], "Private outcome only for the requester.")
        for i in range(125):
            task = H.task_create(
                c, "human:ana", "Check the public packet %s" % i, "Public packet", "bot:ops", lint=False
            )
            H.task_done(c, "human:ana", task["id"], "Public work completed and reported to the team.")
    listing = api.get("/api/v2/changelog", headers=headers("ben-test")).json()
    assert len([r for r in listing["entries"] if r["kind"] == "product"]) == 2
    assert private["id"] not in str(listing) and "Private outcome" not in str(listing)
    ids = {r["title"]: r["id"] for r in changelog.packaged_products(api.app.state.store.settings)}
    # Two tabs acknowledge different entries. The second write must merge, not replace the first.
    err, result = call(api, "hub_changelog_mark_read", {"ids": [ids["Browse the team"]]}, token="ben-test")
    assert not err and result["unread_count"] == 1
    assert (
        api.post("/api/v2/changelog/read", json={"ids": [ids["Search tasks"]]}, headers=headers("ben-test")).json()[
            "unread_count"
        ]
        == 0
    )
    assert api.get("/api/v2/config", headers=headers("ben-test")).json()["changelog"]["unread_count"] == 0
    assert api.get("/api/v2/changelog?unread=true", headers=headers("ben-test")).json()["entries"] == []
    assert api.get("/api/v2/changelog?unread=true", headers=headers()).json()["unread_count"] == 2
    assert (
        api.post(
            "/api/changelog",
            json={"title": "Team news", "bullets": ["A new team feature."]},
            headers=headers("ben-test"),
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/changelog", json={"title": "Team news", "bullets": ["A new team feature."]}, headers=headers()
        ).status_code
        == 200
    )
    unread = api.get("/api/v2/changelog?unread=true", headers=headers("ben-test")).json()
    assert unread["unread_count"] == 1 and unread["entries"][0]["title"] == "Team news"
    tools = {r["name"] for r in rpc(api, "tools/list")["result"]["tools"]}
    assert {"hub_changelog_list", "hub_changelog_mark_read"} <= tools
