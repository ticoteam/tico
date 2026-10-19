"""One shipped-history flow, the per-person acknowledgement/privacy boundary and shipped notes staying as tagged."""
import os
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
    "0.2.0": "af85539717e1a18b3c350a52068a60459cf3f0f6",
    "0.2.1": "af85539717e1a18b3c350a52068a60459cf3f0f6",
    "0.2.2": "af85539717e1a18b3c350a52068a60459cf3f0f6",
    "0.2.4": "af85539717e1a18b3c350a52068a60459cf3f0f6",
    "0.2.13": "d1d4a7e2f8ae3af933f28af83a511cadd3bc96df",
    "0.2.15": "e6e2b8544f1d8548e4181932e60b6e0fcd3f4aa3",
    "0.3.24": "8d55fe27e343e06a5661cb7b074efba1b81c956c",
    "0.3.58": "95018df598f1d5305f0bdfb094b150ec87958b2a",
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

    A release whose section is not here yet (tagged on its release branch, notes not merged back to main) is not an
    edit: lines main gains meanwhile stay under Unreleased. A pull request whose line would land in a released section
    once merged is refused by test_pr_changelog_lines_stay_under_unreleased instead.
    """
    git = lambda *args, **kw: subprocess.run(["git", "-C", str(root), *args], capture_output=True, **kw)
    text = (root / "CHANGELOG.md").read_text()
    here = set(re.findall(r"^## \[?(\d+\.\d+\.\d+)\b", text, re.M))  # the oldest headings have no brackets
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
            if tag[1:] in here and section(text, tag[1:]) != shipped:
                edited.append(tag[1:])
    return edited if read else None


def test_released_changelog_sections_match_their_tags():
    edited = released_edits(ROOT) if shutil.which("git") else None
    if edited is None:
        pytest.skip("no release tags in this checkout (shallow clone or tags not fetched)")
    assert not edited, "your CHANGELOG line landed in the released %s section; merge main and move it under ## [Unreleased]" % (
        ", ".join("[%s]" % v for v in edited))


# A Release Manager's notes merge-back or an erratum names each released section it may change in a commit trailer.
RELEASE_TRAILER = re.compile(r"^Changelog-Release:[ \t]*v?(\d+\.\d+\.\d+)[ \t]*$", re.M)
CONFLICT = "conflict"


def regions(text):
    """Each line's place in CHANGELOG.md: its `## ` heading's version, "Unreleased", "links" or "preamble"."""
    out, here = [], "preamble"
    for line in text.splitlines():
        if line.startswith("## "):
            match = re.match(r"## \[?(Unreleased|\d+\.\d+\.\d+)\b", line)
            here = match.group(1) if match else line[3:]
        out.append("links" if re.match(r"\[[^\]]+\]: ", line) else here)
    return out


def changes_outside_unreleased(root, main="origin/main", head="HEAD"):
    """Places outside ## [Unreleased] whose lines `head` adds or changes once merged into main, or None.

    The branch is merged with main the way GitHub squashes it (git merge-tree), so a line the branch wrote under
    Unreleased before a release's notes landed, and that the merge would put in the released section, counts too.
    None when there is no git or no `main` ref to compare with.
    """
    git = lambda *args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if git("rev-parse", "--verify", "--quiet", main + "^{commit}").returncode:
        return None
    merge = git("merge-tree", "--write-tree", "--name-only", main, head)
    tree, _, conflicted = merge.stdout.partition("\n")
    if merge.returncode not in (0, 1) or not tree:
        return None
    if merge.returncode and "CHANGELOG.md" in conflicted.splitlines():
        return [CONFLICT]
    old = git("show", main + ":CHANGELOG.md").stdout
    new = git("show", tree + ":CHANGELOG.md").stdout
    diff = git("diff", "--no-color", "--no-ext-diff", "-U0", main, tree, "--", "CHANGELOG.md").stdout
    before, after, places = regions(old), regions(new), []
    for hunk in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count, start2, count2 = (int(n) if n is not None else 1 for n in hunk.groups())
        # A side with count 0 (a pure addition or removal) reads no lines.
        places += before[start - 1:start - 1 + count] + after[start2 - 1:start2 - 1 + count2]
    allowed = set(RELEASE_TRAILER.findall(git("log", "--format=%B", main + ".." + head).stdout))
    allowed |= {"links", "preamble"} if allowed else set()  # a merge-back also moves the compare links
    return sorted(set(places) - allowed - {"Unreleased"})


def outside_unreleased_message(places):
    names = {"links": "the link references", "preamble": "the preamble above ## [Unreleased]"}
    where = "conflicts with main" if places == [CONFLICT] else "adds or changes lines in " + ", ".join(
        names.get(p, "the released [%s] section" % p) for p in places)
    return ("CHANGELOG.md: this branch %s; merge main and move your line under ## [Unreleased] (a release notes "
            "merge-back or erratum names each section it changes in a `Changelog-Release: vX.Y.Z` commit trailer)" % where)


def test_pr_changelog_lines_stay_under_unreleased():
    # Release validation runs this suite on a candidate from its release branch: an empty Unreleased and its own
    # new section, which test_released_changelog_sections_match_their_tags checks. A pull request is never one.
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request" and not section((ROOT / "CHANGELOG.md").read_text(), "Unreleased"):
        pytest.skip("a release candidate (empty Unreleased); its notes are checked against its tag")
    outside = changes_outside_unreleased(ROOT) if shutil.which("git") else None
    if outside is None:
        pytest.skip("no origin/main in this checkout to merge CHANGELOG.md with")
    assert not outside, outside_unreleased_message(outside)


def test_changelog_guard_refuses_lines_outside_unreleased(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@acme.example", *args],
                              capture_output=True, text=True, check=True).stdout

    def commit(text, message="Change"):
        (tmp_path / "CHANGELOG.md").write_text(text)
        git("commit", "-qam", message)
        return git("rev-parse", "HEAD").strip()

    shipped = "# Changelog\n\n## [Unreleased]\n\n### Added\n- Search tasks.\n- Restore tasks.\n\n## [0.1.0] - 2026-10-01\n- Browse.\n"
    links = "\n[Unreleased]: https://github.com/acme/app/compare/v0.1.0...HEAD\n"
    git("init", "-q", "-b", "main")
    (tmp_path / "CHANGELOG.md").write_text(shipped + links)
    git("add", "CHANGELOG.md")
    git("commit", "-qm", "Start")
    fork = git("rev-parse", "HEAD").strip()
    git("update-ref", "refs/remotes/origin/main", fork)
    assert changes_outside_unreleased(tmp_path) == []
    assert changes_outside_unreleased(tmp_path, main="origin/nothing") is None

    # A line under Unreleased passes; one inside the released section fails with the one-line instruction.
    commit(shipped.replace("- Restore tasks.\n", "- Restore tasks.\n- Archive tasks.\n") + links)
    assert changes_outside_unreleased(tmp_path) == []
    commit(shipped.replace("- Browse.\n", "- Browse.\n- Archive tasks.\n") + links)
    assert changes_outside_unreleased(tmp_path) == ["0.1.0"]
    message = outside_unreleased_message(["0.1.0"])
    assert "the released [0.1.0] section" in message and "merge main and move your line under ## [Unreleased]" in message
    assert "\n" not in message
    commit(shipped.replace("- Browse.", "- Browse the team.") + links)  # changing a shipped line counts too
    assert changes_outside_unreleased(tmp_path) == ["0.1.0"]
    commit(shipped.replace("- Browse.\n", "") + links)  # and so does removing one
    assert changes_outside_unreleased(tmp_path) == ["0.1.0"]

    # The Release Manager's notes merge-back passes only with its trailer, and only for the section it names.
    git("checkout", "-q", "-b", "notes", fork)
    released = shipped.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n## [0.2.0] - 2026-10-02\n\n")
    moved = released + links.replace("v0.1.0...HEAD", "v0.2.0...HEAD") + "[0.2.0]: https://github.com/acme/app/compare/v0.1.0...v0.2.0\n"
    commit(moved, "Release notes: merge back v0.2.0")
    assert changes_outside_unreleased(tmp_path) == ["0.2.0", "links"]
    git("commit", "-q", "--amend", "-m", "Release notes: merge back v0.2.0\n\nChangelog-Release: v0.2.0")
    assert changes_outside_unreleased(tmp_path) == []
    merge_back = git("rev-parse", "HEAD").strip()

    # An erratum to an older section needs that section's own trailer.
    git("checkout", "-q", "-b", "erratum", fork)
    commit(shipped.replace("- Browse.", "- Browse the team.") + links, "Erratum\n\nChangelog-Release: v0.2.0")
    assert changes_outside_unreleased(tmp_path) == ["0.1.0"]
    git("commit", "-q", "--amend", "-m", "Erratum\n\nChangelog-Release: v0.1.0")
    assert changes_outside_unreleased(tmp_path) == []

    # A branch cut before the notes landed wrote its line under Unreleased, but merging it now puts it in [0.2.0].
    git("checkout", "-q", "-b", "old-branch", fork)
    commit(shipped.replace("- Restore tasks.\n", "- Restore tasks.\n- Archive tasks.\n") + links)
    git("update-ref", "refs/remotes/origin/main", merge_back)
    assert changes_outside_unreleased(tmp_path) == ["0.2.0"]
    git("merge", "-q", "--no-edit", "origin/main")
    assert changes_outside_unreleased(tmp_path) == ["0.2.0"]  # a clean main merge is not proof
    text = (tmp_path / "CHANGELOG.md").read_text().replace("- Archive tasks.\n", "")
    commit(text.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n### Added\n- Archive tasks.\n\n"), "Move the line")
    assert changes_outside_unreleased(tmp_path) == []

    # A CHANGELOG conflict with main is named rather than read as a diff.
    git("checkout", "-q", "-b", "conflict", fork)
    commit(shipped.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n## [9.9.9]\n\n") + links)
    assert changes_outside_unreleased(tmp_path) == [CONFLICT]
    assert outside_unreleased_message([CONFLICT]).startswith("CHANGELOG.md: this branch conflicts with main; merge main")


def test_tag_before_its_notes_reach_main_is_not_an_edit(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@acme.example", *args],
                              capture_output=True, text=True, check=True).stdout

    def commit(text):
        (tmp_path / "CHANGELOG.md").write_text(text)
        git("commit", "-qam", "Change")

    git("init", "-q", "-b", "main")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n- Search tasks.\n\n## [1.0.0] - 2026-10-01\n- Browse.\n")
    git("add", "CHANGELOG.md")
    git("commit", "-qm", "Start")
    git("tag", "v1.0.0")
    # The release is tagged on its own branch; main gains a line before the notes are merged back.
    git("checkout", "-q", "-b", "release")
    notes = "# Changelog\n\n## [Unreleased]\n\n## [1.1.0] - 2026-10-02\n- Search tasks.\n\n## [1.0.0] - 2026-10-01\n- Browse.\n"
    commit(notes)
    git("tag", "v1.1.0")
    git("checkout", "-q", "main")
    commit("# Changelog\n\n## [Unreleased]\n- Archive tasks.\n- Search tasks.\n\n## [1.0.0] - 2026-10-01\n- Browse.\n")
    assert released_edits(tmp_path) == []
    # Once merged back, a released section that differs from its tag still fails.
    commit(notes.replace("## [Unreleased]\n", "## [Unreleased]\n- Archive tasks.\n"))
    assert released_edits(tmp_path) == []
    commit(notes.replace("- Search tasks.", "- Search tasks across the team."))
    assert released_edits(tmp_path) == ["1.1.0"]


def test_guard_refuses_lines_that_landed_in_released_sections():
    # #831 and #855 put their lines in the section just released; each squash against its parent replays that merge.
    replays = {"0f93d87a4f66f8880b948f75c4df1685132f6709": "0.3.55", "6b180f1fc191b098593fe5b5a29d427461168d7a": "0.3.56"}
    if not shutil.which("git") or any(subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", c + "^"]).returncode for c in replays):
        pytest.skip("no full history in this checkout")
    for squash, version in replays.items():
        assert changes_outside_unreleased(ROOT, main=squash + "^", head=squash) == [version]
