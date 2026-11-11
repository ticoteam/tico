"""The browser connector's gate: Ana's social sessions are the Social Media Manager's alone (connectors/browser.py)."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "browser_connector", Path(__file__).resolve().parents[2] / "connectors" / "browser.py"
)
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


def manifest(sites, can=("read",)):
    return {"access": [{"service": "aside", "sites": list(sites), "can": list(can)}]}


def test_only_the_social_media_manager_may_declare_a_social_site():
    assert B.aside_access(manifest(["x.com", "linkedin.com", "news.google.com"]), "social-media-manager")["sites"]
    with pytest.raises(B.Refused, match="only social-media-manager"):
        B.aside_access(manifest(["upfluence.com", "instagram.com"]), "influencer")
    with pytest.raises(B.Refused):
        B.aside_access(manifest(["www.linkedin.com"]), "business-development")
    assert B.aside_access(manifest(["upfluence.com", "collabstr.com"]), "influencer")
    # Ad dashboards on the same domains are company ad accounts, not social reading.
    assert B.aside_access(
        manifest(["ads.reddit.com", "business.facebook.com", "adsmanager.facebook.com"]), "paid-marketing"
    )
    B.check_sites(
        "await openTab('https://ads.reddit.com/dashboard')",
        {"sites": ["ads.reddit.com"], "can": ["read"]},
        "paid-marketing",
    )


def test_another_bot_naming_a_social_host_in_code_is_refused_even_without_a_scheme():
    entry = {"sites": ["yelp.com", "bbb.org"], "can": ["read"]}
    assert B.check_sites("await openTab('https://www.yelp.com/biz/acme')", entry, "reputation") == ["www.yelp.com"]
    for code in (
        "await openTab('https://x.com/i/bookmarks')",
        "const u = 'https://' + 'reddit.com/user/acme_com/saved/'",
        "const host = 'www.linkedin.com'; await openTab('https://' + host)",
        "fetch('https://old.reddit.com/r/airbnb.json')",
    ):
        with pytest.raises(B.Refused, match="social sessions"):
            B.check_sites(code, entry, "reputation")
    # Words that merely contain a social domain's letters are not a social host.
    assert B.check_sites("const box = 'fax.company'; await openTab('https://www.yelp.com/')", entry, "reputation")
