"""A listening's GitHub source (backend/listening_github.py, `read_github` in backend/listening.py): one item per PR
merged into the default branch, or per release, deduped by the repository and number."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import time_machine

from backend import listening as L
from backend import listening_github as LG
from backend.store import Problem
from backend.tests.helpers.api import get, post
from backend.tests.helpers.automation_api import patch
from backend.tests.helpers.automation_runs import sql
from backend.tests.helpers.listening import NOTE, FakeJudge, listening, source_id

START = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
WIDGETS = {"id": "g1", "kind": "github", "target": "acme/widgets"}


def at(minutes):
    return START + timedelta(minutes=minutes)


def gh(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """GitHub's REST answers for the calls the reader makes: the repository, its closed PRs into one base, newest
    updated first, and its releases, newest first; both paged. `refuse` maps a call number to a status."""

    def __init__(self):
        self.repos = {"acme/widgets": {"default_branch": "main"}}
        self.prs, self.releases, self.calls, self.refuse = [], [], [], {}

    def merge(self, number, title, when, base="main", labels=(), body=""):
        self.prs.append(
            {
                "number": number,
                "title": title,
                "merged_at": gh(when),
                "updated_at": gh(when),
                "base": base,
                "labels": [{"name": n} for n in labels],
                "body": body,
                "user": {"login": "sam"},
                "html_url": f"https://github.com/acme/widgets/pull/{number}",
            }
        )

    def release(self, rid, tag, when, name="", body="", draft=False, prerelease=False):
        self.releases.append(
            {
                "id": rid,
                "tag_name": tag,
                "name": name,
                "body": body,
                "draft": draft,
                "prerelease": prerelease,
                "published_at": None if draft else gh(when),
                "author": {"login": "sam"},
                "html_url": f"https://github.com/acme/widgets/releases/tag/{tag}",
            }
        )

    def __call__(self, path, token):
        self.calls.append(path)
        if len(self.calls) in self.refuse:
            status = self.refuse[len(self.calls)]
            headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1793520000"} if status == 403 else {}
            return status, headers, {"message": "API rate limit exceeded" if status in (403, 429) else "Not Found"}
        parts = urlsplit(path)
        bits, query = parts.path.strip("/").split("/"), parse_qs(parts.query)
        repo = "/".join(bits[1:3]).lower()  # GitHub's names are not case-sensitive
        if repo not in self.repos:
            return 404, {}, {"message": "Not Found"}
        if len(bits) == 3:
            return 200, {}, self.repos[repo]
        page, per = int(query["page"][0]), int(query["per_page"][0])
        if bits[3] == "pulls":
            rows = sorted(
                (p for p in self.prs if p["base"] == query["base"][0]), key=lambda p: p["updated_at"], reverse=True
            )
        else:
            rows = sorted(self.releases, key=lambda r: r["published_at"] or "9", reverse=True)
        return 200, {}, rows[(page - 1) * per : page * per]


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(LG, "FETCH", fake)
    return fake


def made(api, sources=(WIDGETS,), rules=()):
    with time_machine.travel(START, tick=False):
        return listening(api, sources=sources, rules=rules, steps=[NOTE])["id"]


def run(api, when, judge=None):
    store = api.app.state.store
    L.run_pass(store, when, judge)


def items(api, aid):
    return sql(
        api,
        "SELECT native_id, title, url, author, posted_at, status, text FROM listening_items WHERE automation_id=? "
        "ORDER BY rowid",
        (aid,),
    )


def source(api, aid, key="g1"):
    return sql(api, "SELECT * FROM listening_sources WHERE id=?", (source_id(api, aid, key),))[0]


def test_the_first_read_only_sets_where_later_reads_start(api, github):
    github.merge(1, "feat: old work", at(-600))
    aid = made(api)
    run(api, START)
    row = source(api, aid)
    assert row["status"] == "ok" and row["last_read_at"]
    assert items(api, aid) == []
    assert json.loads(row["snapshot"])["follow"] == "prs"


def test_a_pr_merged_after_the_first_read_is_one_item_with_its_title_link_author_and_time(api, github):
    github.merge(1, "feat: old work", at(-600))
    aid = made(api)
    run(api, START)
    github.merge(7, "feat: worktree switcher", at(20), labels=["ui"], body="Adds a switcher.\n" + "x" * 3000)
    run(api, at(61))
    [one] = items(api, aid)
    assert one["native_id"] == "gh:acme/widgets#pr:7"
    assert one["title"] == "feat: worktree switcher (#7)"
    assert one["url"] == "https://github.com/acme/widgets/pull/7" and one["author"] == "sam"
    assert one["posted_at"].startswith("2026-11-02T07:20:00")
    assert one["text"].startswith("feat: worktree switcher\nLabels: ui\n\nAdds a switcher.")
    assert len(one["text"]) < 1_600  # the body's start, never all of it


def test_a_source_is_not_read_again_within_the_hour(api, github):
    aid = made(api)
    run(api, START)
    calls = len(github.calls)
    run(api, at(30))
    assert len(github.calls) == calls
    run(api, at(61))
    assert len(github.calls) == calls + 2  # a quiet hour: the repository and one page
    assert source(api, aid)["status"] == "ok"


def test_a_pr_read_twice_or_by_two_sources_on_one_repo_is_one_item(api, github):
    twin = {"id": "g2", "kind": "github", "target": "https://github.com/acme/widgets"}
    shouting = {"id": "g3", "kind": "github", "target": "Acme/Widgets"}
    aid = made(api, sources=(WIDGETS, twin, shouting))
    run(api, START)
    github.merge(7, "feat: worktree switcher", at(20))
    run(api, at(61))
    # The cursor is put back, as a read the server stopped after saving would leave it: the PR is listed again.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE listening_sources SET snapshot=?,last_read_at=NULL",
            (json.dumps({"follow": "prs", "since": gh(at(0))}),),
        )
    run(api, at(125))
    assert [i["native_id"] for i in items(api, aid)] == ["gh:acme/widgets#pr:7"]


def test_two_prs_with_the_same_title_are_two_items_and_neither_is_a_copy(api, github):
    aid = made(api, rules=[{"do": "keep", "tag": "deps", "text": "Dependency updates [r0]"}])
    run(api, START)
    title = "chore(deps): bump the npm group across 4 directories with 12 updates [r0]"
    github.merge(8, title, at(10))
    github.merge(9, title, at(12))
    run(api, at(61), FakeJudge())
    rows = items(api, aid)
    assert [r["native_id"] for r in rows] == ["gh:acme/widgets#pr:8", "gh:acme/widgets#pr:9"]
    assert "duplicate" not in {r["status"] for r in rows}
    assert sql(api, "SELECT count(*) AS n FROM listening_items WHERE duplicate_of_id IS NOT NULL")[0]["n"] == 0


def test_a_pr_merged_into_another_branch_makes_no_item(api, github):
    aid = made(api)
    run(api, START)
    github.merge(10, "fix: on a release branch", at(10), base="release-1")
    github.merge(11, "fix: on main", at(11))
    run(api, at(61))
    assert [i["native_id"] for i in items(api, aid)] == ["gh:acme/widgets#pr:11"]
    assert any("base=main" in path for path in github.calls)


def test_releases_make_one_item_each_pre_releases_too_drafts_never(api, github):
    aid = made(api, sources=({**WIDGETS, "follow": "releases"},))
    github.release(1, "v1.0.0", at(-600), name="First")
    run(api, START)
    assert items(api, aid) == []
    github.release(2, "v1.1.0", at(10), name="Faster boards", body="Boards open twice as fast.")
    github.release(3, "v1.2.0-nightly.1", at(20), prerelease=True)
    github.release(4, "v2.0.0", at(30), draft=True)
    run(api, at(61))
    rows = items(api, aid)
    assert [r["title"] for r in rows] == ["v1.1.0 — Faster boards", "v1.2.0-nightly.1"]
    assert rows[0]["native_id"] == "gh:acme/widgets#release:2"
    assert rows[0]["text"] == "v1.1.0 — Faster boards\n\nBoards open twice as fast."


def test_a_repo_tico_cannot_see_is_blocked_and_the_other_sources_still_read(api, github):
    hidden = {"id": "g2", "kind": "github", "target": "acme/secret"}
    aid = made(api, sources=(WIDGETS, hidden))
    run(api, START)
    blocked = source(api, aid, "g2")
    assert blocked["status"] == "blocked"
    assert blocked["last_error"].startswith("Tico can't see acme/secret")
    assert source(api, aid)["status"] == "ok"
    with time_machine.travel(at(1), tick=False):
        seen = get(api, f"automations/{aid}")["listening"]["sources"]
    assert {s["key"]: s["dot"] for s in seen} == {"g1": "filled", "g2": "hollow"}


def test_a_rate_limit_mid_read_loses_no_pr_the_next_read_saves_them_all(api, github, monkeypatch):
    monkeypatch.setattr(LG, "PER_PAGE", 2)
    aid = made(api)
    run(api, START)
    for n in range(1, 6):
        github.merge(n, f"feat: change {n}", at(n))
    github.refuse = {len(github.calls) + 3: 403}  # the repository, page 1, then page 2 is refused
    run(api, at(61))
    row = source(api, aid)
    assert row["status"] == "rate_limited" and "rate limit" in row["last_error"]
    assert items(api, aid) == []
    github.merge(6, "feat: change 6", at(70))
    run(api, at(125))
    assert [i["native_id"] for i in items(api, aid)] == [f"gh:acme/widgets#pr:{n}" for n in range(1, 7)]
    assert source(api, aid)["status"] == "ok"


def test_a_listing_longer_than_a_read_carries_on_at_the_next_read_and_skips_nothing(api, github, monkeypatch):
    monkeypatch.setattr(LG, "PER_PAGE", 2)
    monkeypatch.setattr(LG, "MAX_PAGES", 2)
    aid = made(api)
    run(api, START)
    for n in range(1, 10):  # 9 PRs: a read lists 4
        github.merge(n, f"feat: change {n}", at(n))
    run(api, at(61))
    assert [i["native_id"] for i in items(api, aid)] == [f"gh:acme/widgets#pr:{n}" for n in range(6, 10)]
    assert source(api, aid)["status"] == "ok"
    # Between reads a new PR is merged, and PR 1, not read yet, is edited: it jumps to the head of the listing.
    github.merge(20, "feat: change 20", at(100))
    github.prs[0]["updated_at"] = gh(at(101))
    for hour in range(2, 7):  # the head pushes what is left down the listing: some rows are read twice, none skipped
        run(api, at(hour * 65))
    ids = sorted(int(i["native_id"].split(":")[-1]) for i in items(api, aid))
    assert ids == [*range(1, 10), 20]
    assert "carry" not in json.loads(source(api, aid)["snapshot"])  # read to its end: the cursor moved on
    github.merge(21, "feat: change 21", at(450))
    run(api, at(520))
    assert items(api, aid)[-1]["native_id"] == "gh:acme/widgets#pr:21"


def test_more_releases_than_a_read_lists_are_saved_as_far_as_listed_and_the_gap_is_shown(api, github, monkeypatch):
    monkeypatch.setattr(LG, "PER_PAGE", 2)
    monkeypatch.setattr(LG, "MAX_PAGES", 2)
    aid = made(api, sources=({**WIDGETS, "follow": "releases"},))
    run(api, START)
    for n in range(1, 7):
        github.release(n, f"v1.{n}.0", at(n))
    run(api, at(61))
    assert len(items(api, aid)) == 4
    row = source(api, aid)
    assert row["status"] == "error" and row["last_error"].startswith("More than 4 changes in acme/widgets since")
    github.release(7, "v1.7.0", at(70))
    run(api, at(125))
    assert items(api, aid)[-1]["native_id"] == "gh:acme/widgets#release:7"  # moved on, not stuck at the gap
    assert source(api, aid)["status"] == "ok"


def test_changing_what_a_source_follows_starts_it_again_from_now(api, github):
    aid = made(api)
    run(api, START)
    github.release(1, "v1.0.0", at(10))
    with time_machine.travel(at(20), tick=False):
        flow = get(api, f"automations/{aid}")
        trigger = {**flow["trigger"], "sources": [{**WIDGETS, "follow": "releases"}]}
        patch(api, aid, {"trigger": trigger})
    run(api, at(61))
    assert items(api, aid) == []  # v1.0.0 was there before it followed releases
    github.release(2, "v1.1.0", at(70))
    run(api, at(125))
    assert [i["native_id"] for i in items(api, aid)] == ["gh:acme/widgets#release:2"]


def test_a_github_source_is_owner_repo_and_a_pasted_address_is_trimmed(api):
    def body(target, **extra):
        trigger = {
            "type": "listening",
            "sources": [{"id": "g", "kind": "github", "target": target, **extra}],
            "limits": [{"count": 5, "per": "day"}],
            "at": "08:00",
            "tz": "UTC",
        }
        return {"name": "T", "owner": "person:ana", "trigger": trigger, "steps": [NOTE]}

    post(api, "automations", body("widgets"), expected=422)
    post(api, "automations", body("https://acme.example/widgets"), expected=422)
    post(api, "automations", body("acme/widgets", follow="issues"), expected=422)
    page = body("https://acme.example/p")
    page["trigger"]["sources"] = [{"id": "p", "kind": "page", "target": "https://acme.example/p", "follow": "prs"}]
    post(api, "automations", page, expected=422)
    made_ = post(api, "automations", body("https://github.com/acme/widgets/releases"))
    assert made_["trigger"]["sources"][0] == {"id": "g", "kind": "github", "target": "acme/widgets", "follow": "prs"}


def test_repo_of_reads_names_and_github_addresses():
    assert LG.repo_of("acme/widgets") == "acme/widgets"
    assert LG.repo_of(" https://www.github.com/acme/widgets.git ") == "acme/widgets"
    assert LG.repo_of("github.com/acme/widgets/pull/3?x=1") == "acme/widgets"
    assert LG.repo_of("widgets") is None
    assert LG.repo_of("acme/..") is None and LG.repo_of("acme/.") is None
    assert LG.repo_of("acme/.github") == "acme/.github"
    assert LG.repo_of("https://gitlab.com/acme/widgets") is None


def test_the_token_is_the_repos_else_the_installations_else_none():
    class App:
        def __init__(self, refuse):
            self.refuse, self.asked = refuse, []

        def mint(self, repos, permissions, diagnose=True):
            self.asked.append(list(repos))
            if len(self.asked) <= self.refuse:
                raise Problem("github_access", "not installed", 403)
            return ("tok", "later")

    assert LG.token_for(App(0), "acme/widgets") == "tok"
    installed = App(1)
    assert LG.token_for(installed, "acme/widgets") == "tok" and installed.asked == [["acme/widgets"], []]
    assert LG.token_for(App(2), "acme/widgets") == ""
    assert LG.token_for(None, "acme/widgets") == ""
