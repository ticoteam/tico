"""Which release holds a merge is found with a fixed number of database questions, however many releases a repository
has (P03): In a release's status, the background release re-read, the release a task names and the "already
released?" check before a cut. Each used to ask `github_release_commits` about one release at a time. Fake data only:
GitHub is backend/tests/helpers/fake_github.py and every release here is synthetic."""

from contextlib import contextmanager
import json

import pytest

from backend.item_sources import github as F
from backend.item_sources import github_release as GR
from backend.store import H
from backend.tests.helpers import release_cut as T
from backend.tests.helpers.fake_github import FakeGitHub

REPO = T.REPO
MERGED = "2026-11-10T00:00:00.000000Z"  # FakeGitHub.merge's merged_at
ONE_AT_A_TIME = "FROM github_release_commits WHERE release_id="  # as traced, with its values in


def old_release_of(conn, repo, commit_sha, *, prereleases):
    """The lookup before P03, word for word: newest-first list read backwards, one question per release."""
    for release in reversed(F.releases(conn, repo, prereleases=prereleases)):
        if F.included(conn, release["id"], commit_sha):
            return release
    return None


@pytest.fixture
def gh():
    found = FakeGitHub()
    found.history[REPO] = [found.sha()]
    found.publish(REPO, "v0.9.0")
    return found


def merged_sha(c, task_id):
    return next(str(link["metadata"]["merged_sha"]) for link in F.prs_of(c, task_id))


def at(minutes):
    """A publish time `minutes` after the merge (before it when negative)."""
    return H.shift(MERGED, minutes=minutes)


def releases(c, rows, repo=REPO):
    """Replace the repo's saved releases with `rows`: (tag, minutes after the merge, prerelease). Returns tag -> id."""
    c.execute("DELETE FROM github_release_commits")
    c.execute("DELETE FROM github_releases WHERE repo=?", (repo,))
    for tag, minutes, pre in rows:
        F.save_release(
            c, repo, {"tag": tag, "commit_sha": f"sha-{tag}", "prerelease": pre, "published_at": at(minutes)}, 1
        )
    F.saw_releases(c, repo, F.version_now())
    return {str(r["tag"]): str(r["id"]) for r in F.releases(c, repo, prereleases=True)}


# Every case the spec names: full and pre-release tags (marked, and a dash in the tag), two releases published at the
# same moment, code held by several releases, by none, releases not read yet, and code merged after some releases.
ROWS = [
    ("v1.0.0", -60, False),
    ("v1.1.0", 0, False),
    ("v1.2.0-rc.1", 10, False),  # a dash: a pre-release by its tag
    ("v1.2.0", 20, True),  # marked a pre-release
    ("v1.3.0", 30, False),
    ("v1.3.1", 30, False),  # the same moment as v1.3.0
    ("v1.4.0", 40, False),
]
CASES = {
    "held by several": {"v1_3_0": True, "v1_3_1": True, "v1_4_0": True, "v1_1_0": False},
    "held by none": {tag.replace(".", "_"): False for tag, _, _ in ROWS},
    "nothing read": {},
    "only a pre-release so far": {"v1_2_0-rc_1": True, "v1_1_0": False, "v1_3_0": False},
    "marked pre-release holds it": {"v1_2_0": True, "v1_4_0": True},
    "only one of a tie read": {"v1_3_1": True},
    "the other of a tie": {"v1_3_0": True},
    "read some, none hold it": {"v1_0_0": False, "v1_4_0": False},
}


def save_case(c, ids, sha, held):
    """Save what GitHub said of `sha` per tag ("v1_3_0" for v1.3.0); a tag left out is not read yet."""
    for tag, value in held.items():
        F.save_included(c, ids[tag.replace("_", ".")], sha, value)


@contextmanager
def rolled_back(company):
    """A transaction thrown away at the end, so a cut that goes ahead leaves nothing behind."""

    class Undo(Exception):
        pass

    try:
        with company.transaction() as c:
            yield c
            raise Undo
    except Undo:
        pass


def four_places(company, task_id, prereleases):
    """What each of the four places says: In a release's line, the release the task names, and whether the cut is
    refused as already released (the background re-read is `test_the_background_re_read_...`)."""
    out = {}
    with company.transaction() as c:
        out["in_release"] = T.line(c, task_id)
        out["released"] = GR.released(c, task_id)
    with rolled_back(company) as c:
        try:
            GR.cut(c, task_id, T.RM, seen_version=T.version(c, task_id))
            out["cut"] = "asked"
        except H.Refused as refused:
            out["cut"] = refused.detail
    return out


def set_prereleases(company, value):
    with company.transaction() as c:
        for rid, settings in c.execute(
            "SELECT id, settings FROM requirements WHERE condition='in_release' AND withdrawn_at IS NULL"
        ).fetchall():
            setting = json.loads(settings or "{}")
            setting["prereleases"] = value
            c.execute("UPDATE requirements SET settings=? WHERE id=?", (json.dumps(setting), rid))


@pytest.mark.parametrize("prereleases", [True, False])
def test_old_and_new_lookups_name_the_same_release_in_every_place(company, gh, monkeypatch, prereleases):
    task_id = T.to_release(company, gh)
    set_prereleases(company, prereleases)
    for name, held in CASES.items():
        with company.transaction() as c:
            sha = merged_sha(c, task_id)
            ids = releases(c, ROWS)
            save_case(c, ids, sha, held)
            for pre in (True, False):
                new, old = F.release_of(c, REPO, sha, prereleases=pre), old_release_of(c, REPO, sha, prereleases=pre)
                assert (new and new["id"]) == (old and old["id"]), (name, pre)
        now = four_places(company, task_id, prereleases)
        with monkeypatch.context() as m:
            m.setattr(F, "release_of", old_release_of)
            before = four_places(company, task_id, prereleases)
        assert now == before, name


def test_the_background_re_read_asks_github_about_the_same_releases_in_the_same_order(company, gh, monkeypatch):
    """Oldest first, only releases not read yet and published since the merge, stopping at the first that holds it;
    nothing at all when a release already holds it. Two waiting tasks share one repository."""
    first = T.to_release(company, gh, 1)
    second = T.to_release(company, gh, 2)
    with company.transaction() as c:
        ids = releases(c, ROWS)
        sha1, sha2 = merged_sha(c, first), merged_sha(c, second)
        save_case(c, ids, sha1, {"v1_1_0": False, "v1_3_1": False})
        save_case(c, ids, sha2, {"v1_4_0": True})  # already held: GitHub is not asked about it
    gh.history[REPO].append("sha-v1.3.0")  # only this release holds the first task's merge
    gh.calls.clear()
    F._refresh_releases(company, gh, H.now())
    asked = [args[1] for name, *args in gh.calls if name == "contains"]
    # Oldest first: v1.0.0 is from before the merge and v1.1.0 is read; then both pre-releases, v1.3.1 of the tie is
    # read, and it stops at v1.3.0, which holds it: v1.4.0 is never asked.
    assert asked == ["sha-v1.2.0-rc.1", "sha-v1.2.0", "sha-v1.3.0"]
    with company.transaction() as c:
        assert F.release_of(c, REPO, sha1, prereleases=True)["tag"] == "v1.3.0"


def counted(company, monkeypatch):
    """Every statement any pooled connection runs while the returned list is live."""
    seen: list[str] = []
    take = company._take

    def traced():
        c = take()
        c.set_trace_callback(seen.append)
        return c

    monkeypatch.setattr(company, "_take", traced)
    return seen


def release_questions(statements):
    return [s for s in statements if "github_release" in s and not s.lstrip().upper().startswith(("INSERT", "UPDATE"))]


def per_check(company, gh, monkeypatch, count):
    """Database questions about releases, with `count` saved releases none of which holds the merge (all read), for
    one status check of each kind and one background re-read with five waiting tasks."""
    tasks = [T.to_release(company, gh, n) for n in range(1, 6)]
    with company.transaction() as c:
        ids = releases(c, [(f"v{n}.0.0", n - count, False) for n in range(count)])
        for task_id in tasks:
            sha = merged_sha(c, task_id)
            for rid in ids.values():
                F.save_included(c, rid, sha, False)
    out = {}
    with monkeypatch.context() as m:
        for name, place in (
            ("in_release", lambda c: T.line(c, tasks[0])),
            ("released", lambda c: GR.released(c, tasks[0])),
            ("cut", lambda c: GR.cut(c, tasks[0], T.RM, seen_version=T.version(c, tasks[0]))),
        ):
            with rolled_back(company) as c:
                seen: list[str] = []
                c.set_trace_callback(seen.append)
                place(c)
                c.set_trace_callback(None)
            out[name] = release_questions(seen)
        seen = counted(company, m)
        F._refresh_releases(company, gh, H.now())
        out["background"] = release_questions(seen)
    with company.read() as c:
        c.set_trace_callback(None)
    return out


def test_a_status_check_asks_the_same_few_questions_with_10_or_300_releases(tmp_path, api_database, monkeypatch):
    from backend.tests.helpers.company import company_store

    results = {}
    for count in (10, 300):
        (tmp_path / str(count)).mkdir()
        store = company_store(tmp_path / str(count), api_database)
        try:
            gh = FakeGitHub()
            gh.history[REPO] = [gh.sha()]
            gh.publish(REPO, "v0.9.0")
            results[count] = per_check(store, gh, monkeypatch, count)
        finally:
            store.close()
    for place in ("in_release", "released", "cut", "background"):
        small, large = results[10][place], results[300][place]
        assert len(small) == len(large), (place, small, large)
        assert not any(ONE_AT_A_TIME in s for s in large), place
    # A status check: In a release's line, and the release a task names. A cut's own check is the same two reads, and
    # after its write it checks In a release again, so it is flat too but not within 4.
    for place in ("in_release", "released"):
        assert len(results[300][place]) <= 4, (place, results[300][place])
    # Five waiting tasks in one repository: its release list is read once in the pass.
    listed = [s for s in results[300]["background"] if "FROM github_releases WHERE repo=" in s]
    assert len(listed) == 1, listed
