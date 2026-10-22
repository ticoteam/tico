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
from scripts import changelog_fragments
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
    "0.3.64": "bea6bd0adaa4856422b44119370878d3dceb79c2",
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
    edit. A pull request that would change a released section once merged is refused by
    test_pr_changes_no_changelog_lines instead.
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
    assert not edited, "your CHANGELOG line landed in the released %s section; merge main and move it into changes/unreleased/ " \
        "(python scripts/changelog_fragments.py adopt)" % (
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


def changelog_edits(root, main="origin/main", head="HEAD"):
    """Places in CHANGELOG.md that `head` changes once merged into main without a release trailer naming them, or None.

    Pull requests put their notes in changes/unreleased/ and never edit CHANGELOG.md; only the release commit, its
    notes merge-back or an erratum does, naming each released section it changes in a `Changelog-Release: vX.Y.Z`
    trailer. Removing lines from ## [Unreleased] stays open, which is how a branch moves its old-style line into a
    fragment. The branch is merged with main the way GitHub squashes it (git merge-tree), so a line the branch wrote
    under Unreleased before a release's notes landed, and that the merge would put in the released section, counts too.
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
    before, after, removed, added = regions(old), regions(new), [], []
    for hunk in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count, start2, count2 = (int(n) if n is not None else 1 for n in hunk.groups())
        # Without context lines a hunk's old side is only removed lines and its new side only added ones; a side
        # with count 0 (a pure addition or removal) reads no lines.
        removed += before[start - 1:start - 1 + count]
        added += after[start2 - 1:start2 - 1 + count2]
    allowed = set(RELEASE_TRAILER.findall(git("log", "--format=%B", main + ".." + head).stdout))
    allowed |= {"links", "preamble", "Unreleased"} if allowed else set()  # a release also moves the links
    return sorted((set(removed) - {"Unreleased"} | set(added)) - allowed)


def changelog_edits_message(places):
    names = {"links": "the link references", "preamble": "the preamble above ## [Unreleased]",
             "Unreleased": "## [Unreleased]"}
    where = "conflicts with main" if places == [CONFLICT] else "adds or changes lines in " + ", ".join(
        names.get(p, "the released [%s] section" % p) for p in places)
    return ("CHANGELOG.md: this branch %s. Pull requests don't edit CHANGELOG.md: put your entry in "
            "changes/unreleased/<branch>.md (`python scripts/changelog_fragments.py adopt` moves a line written in "
            "CHANGELOG.md there and gives it back main's copy); only a release commit, notes merge-back or erratum "
            "edits it, naming each section in a `Changelog-Release: vX.Y.Z` commit trailer" % where)


def test_pr_changes_no_changelog_lines():
    # Release validation runs this suite on a candidate from its release branch, whose release commit names its new
    # section in a trailer and which test_released_changelog_sections_match_their_tags checks against its tag; it may
    # not merge cleanly with a main that moved on. A pull request is always checked.
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request" and RELEASE_TRAILER.search(subprocess.run(
            ["git", "-C", str(ROOT), "log", "--format=%B", "origin/main..HEAD"], capture_output=True, text=True).stdout):
        pytest.skip("a release candidate or notes merge-back; its notes are checked against its tag")
    edits = changelog_edits(ROOT) if shutil.which("git") else None
    if edits is None:
        pytest.skip("no origin/main in this checkout to merge CHANGELOG.md with")
    assert not edits, changelog_edits_message(edits)


def test_changelog_fragments_parse():
    problems = changelog_fragments.check(ROOT)
    assert not problems, "changes/unreleased: " + "; ".join(problems)


def test_changelog_guard_refuses_pull_request_edits(tmp_path):
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
    assert changelog_edits(tmp_path) == []
    assert changelog_edits(tmp_path, main="origin/nothing") is None

    # A pull request adds its line in a fragment; a line under Unreleased or in a released section fails with the
    # one-line instruction.
    (tmp_path / "changes" / "unreleased").mkdir(parents=True)
    (tmp_path / "changes" / "unreleased" / "archive.md").write_text("### Added\n- Archive tasks.\n")
    git("add", "changes")
    git("commit", "-qm", "Fragment")
    assert changelog_edits(tmp_path) == []
    commit(shipped.replace("- Restore tasks.\n", "- Restore tasks.\n- Archive tasks.\n") + links)
    assert changelog_edits(tmp_path) == ["Unreleased"]
    message = changelog_edits_message(["Unreleased"])
    assert "adds or changes lines in ## [Unreleased]. Pull requests don't edit CHANGELOG.md" in message
    assert "changes/unreleased/<branch>.md" in message and "\n" not in message
    commit(shipped.replace("- Search tasks.", "- Search tasks across the team.") + links)  # changing one counts
    assert changelog_edits(tmp_path) == ["Unreleased"]
    commit(shipped.replace("- Browse.\n", "- Browse.\n- Archive tasks.\n") + links)
    assert changelog_edits(tmp_path) == ["0.1.0"]
    assert "the released [0.1.0] section" in changelog_edits_message(["0.1.0"])
    commit(shipped.replace("- Browse.\n", "") + links)  # and so does removing a shipped line
    assert changelog_edits(tmp_path) == ["0.1.0"]
    # Moving old-style lines out of Unreleased into fragments only removes lines there, which stays open.
    commit(shipped.replace("### Added\n- Search tasks.\n- Restore tasks.\n\n", "") + links)
    assert changelog_edits(tmp_path) == []

    # The release commit and its notes merge-back pass only with the trailer, and only for the section it names.
    git("checkout", "-q", "-b", "notes", fork)
    released = shipped.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n## [0.2.0] - 2026-10-02\n\n")
    moved = released + links.replace("v0.1.0...HEAD", "v0.2.0...HEAD") + "[0.2.0]: https://github.com/acme/app/compare/v0.1.0...v0.2.0\n"
    commit(moved, "Release notes: merge back v0.2.0")
    assert changelog_edits(tmp_path) == ["0.2.0", "links"]
    git("commit", "-q", "--amend", "-m", "Release notes: merge back v0.2.0\n\nChangelog-Release: v0.2.0")
    assert changelog_edits(tmp_path) == []
    merge_back = git("rev-parse", "HEAD").strip()

    # An erratum to an older section needs that section's own trailer.
    git("checkout", "-q", "-b", "erratum", fork)
    commit(shipped.replace("- Browse.", "- Browse the team.") + links, "Erratum\n\nChangelog-Release: v0.2.0")
    assert changelog_edits(tmp_path) == ["0.1.0"]
    git("commit", "-q", "--amend", "-m", "Erratum\n\nChangelog-Release: v0.1.0")
    assert changelog_edits(tmp_path) == []

    # A branch cut before the notes landed and still carrying an old-style line: merged now, it lands in [0.2.0].
    git("checkout", "-q", "-b", "old-branch", fork)
    commit(shipped.replace("- Restore tasks.\n", "- Restore tasks.\n- Archive tasks.\n") + links)
    git("update-ref", "refs/remotes/origin/main", merge_back)
    assert changelog_edits(tmp_path) == ["0.2.0"]
    git("merge", "-q", "--no-edit", "origin/main")
    assert changelog_edits(tmp_path) == ["0.2.0"]  # a clean main merge is not proof

    # A CHANGELOG conflict with main is named rather than read as a diff.
    git("checkout", "-q", "-b", "conflict", fork)
    commit(shipped.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n## [9.9.9]\n\n") + links)
    assert changelog_edits(tmp_path) == [CONFLICT]
    assert changelog_edits_message([CONFLICT]).startswith("CHANGELOG.md: this branch conflicts with main. Pull requests")


def test_fragments_compile_into_the_release_commit(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@acme.example", *args],
                              capture_output=True, text=True, check=True).stdout

    fragments = tmp_path / "changes" / "unreleased"
    fragments.mkdir(parents=True)
    (tmp_path / "changes" / "README.md").write_text("How fragments work.\n")
    links = ("[Unreleased]: https://github.com/acme/app/compare/v0.1.0...HEAD\n"
             "[0.1.0]: https://github.com/acme/app/releases/tag/v0.1.0\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n### Fixed\n- Written the old way.\n\n"
                                           "## [0.1.0] - 2026-10-01\n- Browse.\n\n" + links)
    (fragments / "search.md").write_text("### Added\n- **Search tasks.** Across the team,\n  with filters.\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "Start", "--date", "2026-10-01T00:00:00Z")
    git("tag", "v0.1.0")
    (fragments / "restore.md").write_text("### Fixed\n- Restore a task.\n\n### Added\n- Restore tasks.\n")
    git("add", "-A")
    git("commit", "-qm", "Restore")
    env = {**os.environ, "GIT_COMMITTER_DATE": "2030-01-01T00:00:00Z"}
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@acme.example", "commit",
                    "-q", "--amend", "--no-edit"], env=env, check=True)
    assert changelog_fragments.check(tmp_path) == []

    used = changelog_fragments.compile_release(tmp_path, "0.2.0", "2026-10-02")
    assert sorted(p.name for p in used) == ["restore.md", "search.md"]
    assert not list(fragments.glob("*.md")) and (tmp_path / "changes" / "README.md").exists()
    text = (tmp_path / "CHANGELOG.md").read_text()
    # Newest fragment first within each heading, then lines left under Unreleased; Unreleased stays, empty.
    assert text.startswith("# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2026-10-02\n\n### Added\n- Restore tasks.\n"
                           "- **Search tasks.** Across the team,\n  with filters.\n\n### Fixed\n- Restore a task.\n"
                           "- Written the old way.\n\n## [0.1.0] - 2026-10-01\n- Browse.\n\n")
    assert section(text, "Unreleased") == "" and section(text, "0.1.0") == "- Browse."
    assert text.endswith("[Unreleased]: https://github.com/acme/app/compare/v0.2.0...HEAD\n"
                         "[0.2.0]: https://github.com/acme/app/compare/v0.1.0...v0.2.0\n" + links.split("\n", 1)[1])

    # Nothing left to release, a section that exists and a tag whose notes are not merged back are all refused.
    with pytest.raises(changelog_fragments.FragmentError, match="nothing to release"):
        changelog_fragments.compile_release(tmp_path, "0.3.0", "2026-10-03")
    with pytest.raises(changelog_fragments.FragmentError, match="already has a \\[0.2.0\\] section"):
        changelog_fragments.compile_release(tmp_path, "0.2.0", "2026-10-03")
    git("tag", "v0.3.0")
    (fragments / "later.md").write_text("### Fixed\n- Later.\n")
    with pytest.raises(changelog_fragments.FragmentError, match="v0.3.0 is tagged but its notes are not in this"):
        changelog_fragments.compile_release(tmp_path, "0.4.0", "2026-10-04")
    assert (fragments / "later.md").exists()


def test_fragment_check_and_adopt(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@acme.example", *args],
                              capture_output=True, text=True, check=True).stdout

    fragments = tmp_path / "changes" / "unreleased"
    fragments.mkdir(parents=True)
    for name, text in {"heading.md": "### Fixes\n- Wrong heading.\n", "orphan.md": "- No heading.\n",
                       "empty.md": "### Fixed\n", "prose.md": "### Fixed\nNot an entry.\n"}.items():
        (fragments / name).write_text(text)
    problems = changelog_fragments.check(tmp_path)
    assert len(problems) == 4 and any("### Fixes is not one of Added" in p for p in problems)
    for path in fragments.iterdir():
        path.unlink()

    main = "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- Already on main.\n\n## [0.1.0] - 2026-10-01\n- Browse.\n"
    (tmp_path / "CHANGELOG.md").write_text(main)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-qm", "Start")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-q", "-b", "tico/1234-archive")
    (tmp_path / "CHANGELOG.md").write_text(main.replace("### Fixed\n", "### Added\n- Archive tasks,\n  in bulk.\n\n### Fixed\n"))
    path, moved = changelog_fragments.adopt(tmp_path)
    assert path == fragments / "1234-archive.md" and moved == [("Added", "- Archive tasks,\n  in bulk.")]
    assert path.read_text() == "### Added\n- Archive tasks,\n  in bulk.\n"
    assert (tmp_path / "CHANGELOG.md").read_text() == main
    changelog_fragments.add(tmp_path, "fixed", "Archived tasks keep their tags.")
    assert path.read_text() == "### Added\n- Archive tasks,\n  in bulk.\n\n### Fixed\n- Archived tasks keep their tags.\n"
    with pytest.raises(changelog_fragments.FragmentError, match="adds nothing under"):
        changelog_fragments.adopt(tmp_path)

    # Mid-merge with a main that changed the same lines, adopt reads the branch's copy, not the conflict markers.
    git("checkout", "-q", "-f", "main")
    (tmp_path / "CHANGELOG.md").write_text(main.replace("- Already on main.\n", "- Already on main.\n- Main moved on.\n"))
    git("commit", "-qam", "Main")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-q", "-b", "tico/55-pin", "HEAD~1")
    (tmp_path / "CHANGELOG.md").write_text(main.replace("- Already on main.\n", "- Already on main.\n- Pin tasks.\n"))
    git("commit", "-qam", "Pin")
    assert subprocess.run(["git", "-C", str(tmp_path), "merge", "-q", "origin/main"], capture_output=True).returncode
    path, moved = changelog_fragments.adopt(tmp_path)
    assert moved == [("Fixed", "- Pin tasks.")] and "<<<<" not in (tmp_path / "CHANGELOG.md").read_text()
    (tmp_path / "changes" / "notes.md").write_text("### Fixed\n- In the wrong folder.\n")
    assert changelog_fragments.check(tmp_path) == ["changes/notes.md: compile reads only changes/unreleased/*.md"]


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
        assert changelog_edits(ROOT, main=squash + "^", head=squash) == [version]
