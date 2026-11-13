"""The six bots a software company starts with are taught the Product and Engineering stages they own.

A bot does what its template says. Before 0.6 the Builder was told to merge when the merge rules passed, the reviewer
to write a "verdict" on the task, and the release bot was called a Release Owner that never touched a pull request; a
bot following that text would merge by hand around the merge queue or never attach the page its stage needs. So each
template names its stages and the commands that leave them, the Builder and both GitHub-writing bots are denied
`gh pr merge`, and the Release Owner (and only it) may push a fix to a pull request's branch."""

import json
import re
from pathlib import Path

import pytest
import yaml

from backend import pipeline_seats

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
SIX = sorted(set(pipeline_seats.TEMPLATES.values()))
OLD_WORDS = re.compile(r"\b(verdicts?|merge rules|QA pass|board|ticket)\b", re.IGNORECASE)


def texts(template):
    folder = CATALOG / template
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".yaml") and "software" not in path.relative_to(folder).parts:
            if "examples" not in path.parts:
                yield path, path.read_text()


def agent(template):
    return " ".join((CATALOG / template / "AGENTS.md").read_text().split())


def settings(template):
    return json.loads((CATALOG / template / ".claude" / "settings.json").read_text())["permissions"]


def card(template):
    return yaml.safe_load((CATALOG / template / "card.yaml").read_text())


@pytest.mark.parametrize("template", ["builder", "reviewer", "release-owner"])
def test_no_github_bot_may_merge(template):
    rules = settings(template)
    assert "Bash(gh pr merge *)" in rules["deny"]
    assert not any("gh pr merge" in rule for rule in rules["allow"])


def test_the_product_owner_reads_ci_by_repository_and_never_writes_it():
    # Validate runs on a `git archive` copy, which has no remote: gh, and the runner's token for the repository, need
    # the pull request's link or -R to know which repository to read.
    rules = settings("segment-product-owner")
    for read in ("gh pr checks", "gh pr diff", "gh run list", "gh run view"):
        assert f"Bash({read} *)" in rules["allow"], read
    for write in ("gh pr merge", "gh pr review", "gh run rerun", "gh run cancel", "gh workflow run"):
        assert f"Bash({write} *)" in rules["deny"], write
        assert not any(write in rule for rule in rules["allow"]), write
    playbook = (CATALOG / "segment-product-owner" / "playbooks" / "validate-in-production.md").read_text()
    assert "gh pr checks <pull request link>" in playbook and "-R <owner/name>" in playbook


@pytest.mark.parametrize("template", ["builder", "release-owner"])
def test_the_builder_and_release_manager_may_push_a_rebased_branch_and_never_a_tag(template):
    # A conflict wake tells the Builder to rebase and push with a lease; the Release Owner rebases in Merge. A tag is a
    # release on a repository that releases from tags, and only a person or the approved rollout makes one.
    rules = settings(template)
    lease = "Bash(git push --" + "force-with-lease *)"
    assert lease in rules["allow"]
    # A bare force push stays denied, and the lease is not swallowed by a wider deny.
    assert "Bash(git push --" + "force *)" in rules["deny"]
    assert not any(rule.startswith("Bash(git push --" + "force*") for rule in rules["deny"])
    for tag in ("Bash(git tag *)", "Bash(git push*--tags*)", "Bash(git push* refs/tags/*)"):
        assert tag in rules["deny"], (template, tag)
    assert lease not in settings("reviewer")["allow"]
    assert "Bash(gh pr checkout *)" in settings("release-owner")["allow"]
