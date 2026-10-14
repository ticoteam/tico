"""One shipped-history flow, the per-person acknowledgement/privacy boundary and shipped notes staying as tagged."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import changelog, releases
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_mcp import call, rpc
from backend.tests.test_openapi_v2 import conforms
from scripts.changelog_notes import section

ROOT = Path(__file__).resolve().parents[2]
# Shipped notes corrected on purpose after their tag (a renamed bot, a moved docs anchor) are compared with the
# commit that corrected them instead, so they stay guarded from then on.
REVISED = {
    "0.2.13": "d1d4a7e2f8ae3af933f28af83a511cadd3bc96df",
    "0.2.15": "e6e2b8544f1d8548e4181932e60b6e0fcd3f4aa3",
    "0.3.24": "8d55fe27e343e06a5661cb7b074efba1b81c956c",
}

NOTES = """# Changelog
## [Unreleased]
- Unshipped work
## [0.3.0] - 2026-10-03
- Future work
## [0.2.0] - 2026-10-02
### Added
- Search your tasks
  across the team.
- Restore a deleted task.
## [0.1.0] - 2026-10-01
- Browse the team.
"""


def test_bundled_history_api_and_mcp(api, monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_VERSION", "0.2.0")
    monkeypatch.setattr(releases, "ROOT", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(NOTES)
    assert not (tmp_path / ".git").exists()
    assert api.get("/api/v2/changelog").status_code == 401
    first = api.get("/api/v2/changelog?kind=product&limit=1", headers=headers("ben-test")).json()
    assert first["entries"][0]["id"] == "release-0.2.0" and first["next_offset"] == 1
    assert first["entries"][0]["bullets"] == ["Search your tasks across the team.", "Restore a deleted task."]
    assert first["unread_count"] == 2
    second = api.get("/api/v2/changelog?kind=product&limit=1&offset=1", headers=headers("ben-test")).json()
    assert second["entries"][0]["id"] == "release-0.1.0" and second["next_offset"] is None
    err, result = call(api, "hub_changelog_list", {"since_version": "0.1.0", "q": "restore", "unread": True}, token="ben-test")
    assert not err and [r["version"] for r in result["entries"]] == ["0.2.0"]
    assert result["unread_count"] == 2
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    assert conforms(result, document["components"]["schemas"]["ChangelogList"], document) is None
    assert api.get("/api/v2/changelog?since_version=latest", headers=headers()).status_code == 422
    assert api.get("/api/changelog", headers=headers()).json()["entries"] == api.get("/api/v2/changelog", headers=headers()).json()["entries"]
    # Same-day announcements keep their ids when a registry list is reordered.
    monkeypatch.setattr(api.app.state.store.settings, "registry_dir", tmp_path)
    announcements = tmp_path / "product-updates.yaml"
    announcements.write_text('- title: A\n  bullets: [First]\n- title: B\n  bullets: [Second]\n')
    ids = {r["title"]: r["id"] for r in changelog.packaged_products(api.app.state.store.settings)}
    announcements.write_text('- title: B\n  bullets: [Second]\n- title: A\n  bullets: [First]\n')
    assert ids == {r["title"]: r["id"] for r in changelog.packaged_products(api.app.state.store.settings)}


def test_read_state_is_personal_and_only_acknowledges_shown_changes(api, monkeypatch, tmp_path):
    monkeypatch.setenv("TICO_VERSION", "0.2.0")
    monkeypatch.setattr(releases, "ROOT", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(NOTES)
    with api.app.state.store.transaction() as c:
        private = H.task_create(c, "human:ana", "Review the private packet", "Private packet", "bot:ops", private=True, lint=False)
        c.execute("UPDATE tasks SET status='done', note=? WHERE id=?", ("Private outcome only for the requester.", private["id"]))
        for i in range(125):
            task = H.task_create(c, "human:ana", "Check the public packet %s" % i, "Public packet", "bot:ops", lint=False)
            c.execute("UPDATE tasks SET status='done', note=? WHERE id=?", ("Public work completed and reported to the team.", task["id"]))
    listing = api.get("/api/v2/changelog", headers=headers("ben-test")).json()
    assert len([r for r in listing["entries"] if r["kind"] == "product"]) == 2
    assert private["id"] not in str(listing) and "Private outcome" not in str(listing)
    # Two tabs acknowledge different entries. The second write must merge, not replace the first.
    err, result = call(api, "hub_changelog_mark_read", {"ids": ["release-0.1.0", "release-0.3.0"]}, token="ben-test")
    assert not err and result["unread_count"] == 1
    assert api.post("/api/v2/changelog/read", json={"ids": ["release-0.2.0"]}, headers=headers("ben-test")).json()["unread_count"] == 0
    assert api.get("/api/v2/config", headers=headers("ben-test")).json()["changelog"]["unread_count"] == 0
    assert api.get("/api/v2/changelog?unread=true", headers=headers("ben-test")).json()["entries"] == []
    assert api.get("/api/v2/changelog?unread=true", headers=headers()).json()["unread_count"] == 2
    assert api.post("/api/changelog", json={"title": "Team news", "bullets": ["A new team feature."]}, headers=headers("ben-test")).status_code == 403
    assert api.post("/api/changelog", json={"title": "Team news", "bullets": ["A new team feature."]}, headers=headers()).status_code == 200
    unread = api.get("/api/v2/changelog?unread=true", headers=headers("ben-test")).json()
    assert unread["unread_count"] == 1 and unread["entries"][0]["title"] == "Team news"
    # A future release cannot be pre-acknowledged, and appears after this server upgrades.
    monkeypatch.setenv("TICO_VERSION", "0.3.0")
    unread = api.get("/api/v2/changelog?unread=true", headers=headers("ben-test")).json()
    assert {r["id"] for r in unread["entries"]} >= {"release-0.3.0"}
    tools = {r["name"] for r in rpc(api, "tools/list")["result"]["tools"]}
    assert {"hub_changelog_list", "hub_changelog_mark_read"} <= tools


def released_edits(root):
    """Released versions whose CHANGELOG section here differs from the one at their tag, or None when no tag is readable.

    A branch made before a release's notes landed has no section for that release yet; merging it would put its
    line under the nearest heading, which is the released one, so a missing section counts as an edit when this
    branch changes CHANGELOG.md, unless it is part of that release's history (an older commit checked out on purpose).
    """
    git = lambda *args, **kw: subprocess.run(["git", "-C", str(root), *args], capture_output=True, **kw)
    text = (root / "CHANGELOG.md").read_text()
    here = set(re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M))
    tags = git("tag", "--list", "v[0-9]*").stdout.decode().split()
    refs = "".join(REVISED.get(t[1:], "refs/tags/" + t) + ":CHANGELOG.md\n" for t in tags)
    out = git("cat-file", "--batch", input=refs.encode()).stdout
    edited, read = [], 0
    for tag in tags:
        header, _, out = out.partition(b"\n")
        if header.split()[1:2] == [b"blob"]:
            size = int(header.split()[2])
            shipped, out = section(out[:size].decode(), tag[1:]), out[size + 1:]
            read += 1
            if tag[1:] in here:
                edited += [tag[1:]] if section(text, tag[1:]) != shipped else []
            elif shipped and git("merge-base", "--is-ancestor", "HEAD", tag).returncode == 1:
                fork = git("merge-base", "HEAD", tag).stdout.decode().strip()
                # Only a branch that adds its own CHANGELOG line can have it land in the released section.
                if not fork or git("diff", "--quiet", fork, "--", "CHANGELOG.md").returncode:
                    edited.append(tag[1:])
    return edited if read else None


def test_released_changelog_sections_match_their_tags():
    edited = released_edits(ROOT) if shutil.which("git") else None
    if edited is None:
        pytest.skip("no release tags in this checkout (shallow clone or tags not fetched)")
    assert not edited, "your CHANGELOG line landed in the released %s section; merge main and move it under ## [Unreleased]" % (
        ", ".join("[%s]" % v for v in edited))
