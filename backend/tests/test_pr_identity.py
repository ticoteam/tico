"""A pull request is the same one only when its full identity matches: host, owner, repo and number
(backend/pr_identity.py). A matching title or a bare number never stands for a link."""

import pytest

from backend import pr_identity as PR

PR_1 = {"url": "https://github.com/Acme/atlas/pull/1", "number": 1}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/Acme/atlas/pull/1", True),
        ("https://GITHUB.com/ACME/ATLAS/pull/1/?tab=files#review-12", True),
        ("https://github.com/Acme/atlas/pull/12", False),
        ("https://github.example/Acme/atlas/pull/1", False),
        ("https://github.com/Other/atlas/pull/1", False),
        ("https://github.com/Acme/other/pull/1", False),
        ("https://github.com/Acme/atlas/pull/1extra", False),
        ("", False),
    ],
)
def test_a_request_names_a_pull_request_only_by_its_full_identity_even_when_the_title_matches(url, expected):
    check = {"title": "Review 1: PR #1", "body": f"Check this PR: ({url})."}
    assert PR.matches(check, PR_1) is expected


def test_equal_numbers_on_other_repos_or_hosts_are_other_pull_requests():
    own = "https://github.com/Acme/atlas/pull/12"
    for other in (
        "https://github.com/Acme/other/pull/12",
        "https://github.com/Other/atlas/pull/12",
        "https://github.example/Acme/atlas/pull/12",
    ):
        assert not PR.same(own, other)
    assert PR.same(own, {"url": "https://github.com/acme/ATLAS/pull/12#files"})


def test_missing_or_broken_references_never_match_each_other():
    assert PR.identity("") is None and PR.identity("https://github.com/Acme/atlas/pull/0") is None
    assert PR.identity("https://user:pw@github.com/Acme/atlas/pull/3") is None
    assert not PR.same("", "")
    assert not PR.same(None, {"url": None})


def test_a_title_number_resolves_only_when_one_linked_pull_request_has_it():
    atlas = {"url": "https://github.com/Acme/atlas/pull/12"}
    other = {"url": "https://github.com/Acme/other/pull/12"}
    nine = {"url": "https://github.com/Acme/atlas/pull/9"}
    assert PR.requested({"title": "Review PR #12"}, [atlas, nine]) == [atlas]
    assert PR.requested({"title": "Review PR #12"}, [atlas, other]) == [], "two repos share the number"
    assert PR.requested({"title": "Review", "body": other["url"]}, [atlas, other]) == [other]
    assert PR.requested({"title": "Review"}, [atlas, nine]) == []
    assert PR.requested({"title": "Review"}, [atlas, nine], all_if_unspecified=True) == [atlas, nine]
