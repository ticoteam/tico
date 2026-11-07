"""A bot's merge request for a docs-only pull request is refused (policies/approvals.md): docs merge with no approval.
A mixed change, an unreadable one and any person's request go ahead as before."""

import pytest

from backend import docs_merge
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_groups import add_bots
from backend.tests.test_groups_leads import as_bot

DOCS_PR = {"repo": "ticoteam/tico", "pr": 2303, "head_sha": "a" * 40, "title": "Docs for v0.5.75"}


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeGitHub:
    """Stands in for the GitHub App: one page of changed files, or a refusal."""

    def __init__(self, files=None, status=200):
        self.files, self.status, self.reads = files or [], status, 0

    def mint(self, repos, permissions):
        return ("token", 0)

    def _call(self, method, path, **kw):
        self.reads += 1
        if self.status != 200:
            return FakeResponse(self.status)
        return FakeResponse(200, self.files)


def docs_bot(api):
    with api.app.state.store.transaction() as c:
        add_bots(c, docs=({}, "ana"))
    as_bot(api, "docs", "docs-test")


def changed(*names):
    return [{"filename": name} for name in names]


def test_a_docs_only_pull_request_is_refused_with_the_changelog_instruction():
    message = docs_merge.refusal(FakeGitHub(changed("docs/setup.md", "docs/api/index.mdx", "README.md")), DOCS_PR)
    assert message == docs_merge.DOCS_MESSAGE
    assert "needs no approval" in message and '"Changelog:"' in message and "merge it yourself" in message


def test_a_mixed_pull_request_still_asks_for_approval():
    github = FakeGitHub(changed("docs/setup.md", "backend/app.py"))
    assert docs_merge.refusal(github, DOCS_PR) is None


def test_a_rename_out_of_code_into_docs_still_asks_for_approval():
    files = [{"filename": "docs/moved.md", "previous_filename": "backend/moved.py"}]
    assert docs_merge.refusal(FakeGitHub(files), DOCS_PR) is None


def test_the_marketing_site_repository_is_refused_without_reading_its_files():
    github = FakeGitHub(changed("app/main.tsx"))
    site = {"repo": "ticoteam/tico-marketing-site", "pr": 135}
    assert docs_merge.refusal(github, site) == docs_merge.SITE_MESSAGE.format(repo="ticoteam/tico-marketing-site")
    assert github.reads == 0


@pytest.mark.parametrize(
    "github",
    [
        None,  # no GitHub App on this install
        FakeGitHub(status=404),  # the App cannot see the repository or the pull request
        FakeGitHub(files=[]),  # GitHub named no files
    ],
)
def test_files_that_cannot_be_read_keep_today_s_behavior(github):
    assert docs_merge.refusal(github, DOCS_PR) is None


def test_a_read_that_raises_keeps_today_s_behavior():
    class Broken(FakeGitHub):
        def mint(self, repos, permissions):
            raise RuntimeError("no installation")

    assert docs_merge.refusal(Broken(), DOCS_PR) is None


def test_a_request_without_a_repository_or_number_is_left_to_the_approval_checks():
    assert docs_merge.refusal(FakeGitHub(changed("docs/a.md")), {"pr": 3}) is None
    assert docs_merge.refusal(FakeGitHub(changed("docs/a.md")), {"repo": "ticoteam/tico", "pr": "soon"}) is None


def test_a_bot_asking_to_merge_docs_only_is_refused_with_the_code(api, monkeypatch):
    docs_bot(api)
    monkeypatch.setattr(docs_merge, "changed_files", lambda service, repo, number: ["docs/setup.md"])
    r = api.post("/api/v2/approvals", json={"kind": "merge", "payload": DOCS_PR}, headers=headers("docs-test"))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == docs_merge.REFUSAL
    assert "needs no approval" in r.json()["error"]["detail"]


def test_a_bot_asking_to_merge_mixed_changes_still_gets_an_approval(api, monkeypatch):
    docs_bot(api)
    monkeypatch.setattr(docs_merge, "changed_files", lambda service, repo, number: ["docs/a.md", "backend/x.py"])
    r = api.post("/api/v2/approvals", json={"kind": "merge", "payload": DOCS_PR}, headers=headers("docs-test"))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "merge"


def test_a_persons_merge_request_is_never_refused_for_docs(api, monkeypatch):
    monkeypatch.setattr(docs_merge, "changed_files", lambda service, repo, number: ["docs/setup.md"])
    r = api.post("/api/v2/approvals", json={"kind": "merge", "payload": DOCS_PR}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "merge"
