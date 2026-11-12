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


def test_the_product_owner_brings_the_proof_on_a_page():
    # Validate asked for a text report with no screenshot, so Product Owners asked a person to "go and check it on your
    # phone" (spec "The bot brings the proof on a page", step 3). Both pages are now built by a skill: one page, the
    # result first, screenshots at phone and desktop width with red numbered arrows, and the Validate playbook uses it.
    folder = CATALOG / "segment-product-owner"
    skills = {
        name: (folder / "skills" / name / "SKILL.md").read_text()
        for name in ("build-the-spec-page", "build-the-validation-page")
    }
    for name, text in skills.items():
        flat = " ".join(text.split())
        assert yaml.safe_load(text.split("---")[1])["name"] == name
        for rule in ("One page.", "390 px", "1280 px", "Red numbered arrows.", "var(--fail)", "tico bot app-shot"):
            assert rule in flat, (name, rule)
        assert (
            "do not ask a person for a screenshot" in flat.lower() or "never ask a person to test it" in flat.lower()
        ), name
    validation = " ".join(skills["build-the-validation-page"].split())
    for part in ("Verdict first.", "The version line", "screenshot or measurement", "Could not check: nothing."):
        assert part in validation, part
    assert "Result first." in skills["build-the-spec-page"] and "before and after" in skills["build-the-spec-page"]

    playbook = " ".join((folder / "playbooks" / "validate-in-production.md").read_text().split())
    assert "skills/build-the-validation-page/SKILL.md" in playbook and "tico bot app-shot" in playbook
    assert "a person never does the QA" in playbook and "Do not ask a person to test the change" in playbook
    assert "tico page publish reports/YYYY-MM-DD-checked-<slug>.html" in playbook
    assert "tico file publish" not in playbook and "checked-<slug>.md" not in playbook

    spec_playbook = (folder / "playbooks" / "write-a-spec-page.md").read_text()
    assert "tico page publish specs/<slug>.html --spec" in spec_playbook
    assert "publish the written spec as it is" not in spec_playbook
    instructions = agent("segment-product-owner")
    for name in skills:
        assert f"skills/{name}/SKILL.md" in instructions, name


def test_the_skills_arrow_drawing_survives_the_page_cleaner():
    # The arrows are an inline SVG over a data: picture. A page loses anything that would load from elsewhere when it
    # is published, so the skill's own example must come through whole.
    from backend import visuals

    text = (CATALOG / "segment-product-owner" / "skills" / "build-the-spec-page" / "SKILL.md").read_text()
    example = text[text.index("<figure") : text.index("</figure>") + len("</figure>")]
    cleaned, removed = visuals.clean(example.encode(), "page")
    assert removed == []
    for kept in ('<image href="data:image/png;base64,', 'marker-end="url(#a)"', "stroke:var(--fail)", "<figcaption>"):
        assert kept in cleaned.decode(), kept
