"""The six bots a software company starts with are taught the Product and Engineering stages they own.

A bot does what its template says. Before 0.6 the Builder was told to merge when the merge rules passed, the reviewer
to write a "verdict" on the task, and the release bot was called a Release Owner that never touched a pull request; a
bot following that text would merge by hand around the merge queue or never attach the page its stage needs. So each
template names its stages and the commands that leave them, the Builder and both GitHub-writing bots are denied
`gh pr merge`, and the Release Owner (and only it) may push a fix to a pull request's branch."""

import json
import re
import sys
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


@pytest.mark.parametrize("template", ["builder", "reviewer", "qa-owner", "release-owner"])
def test_every_bot_that_meets_a_red_main_finds_the_one_task_waits_on_it_and_rebases_after(template):
    # Nov 13, main red twice (#3335, #3604): 13 duplicate "main red" tasks (3 from the QA and Release Owners, whose
    # templates said nothing), two fix PRs for one break, re-runs that tested the old merge again, and tasks snoozed to
    # guessed dates. Each bot that sees a red check is taught the same rules, in the same words.
    text = " ".join(" ".join(body.split()) for _path, body in texts(template))
    # Look first, snoozed ones included, and give a new one the title the next bot searches for.
    assert 'tico task list --q "Main red" --status open --snoozed all' in text
    assert re.search(r"`Main red: <[^>]+>, since <[^>]+>`", text), "the main-red title"
    # Filed from a private task, it must still be one the next bot's search finds: plain or --private it is hidden.
    assert "file it with `tico task create --from-private`" in text and "no `--private`" not in text
    # Say you are blocked on it with a comment: relating it to your task is refused across owners.
    assert re.search(r'tico task comment <main-red task> "Blocks #<[^>]+> \(PR #<[^>]+>\)', text)
    assert "tico task relate <your task> <main-red task>" not in text
    # Wait on it from your own task, or name the fix pull request; never a guessed date.
    assert "tico task relate <your task> depends_on <main-red task>" in text
    assert "waiting for #<fix pull request> to merge" in text
    # After main is fixed: a new head, not a re-run of the old merge (the QA Owner may not force-push, so it merges).
    assert re.search(r"git (rebase|merge) origin/<base branch>", text) and "never re-run" in text
    if template == "release-owner":
        # Its older rule sends every failed_checks removal back to the Builder; main red must be named as the exception.
        assert "Not after `failed_checks`, which is choice 3, except a main-red failure" in text


# The one page skill every bot that writes a spec, a check or a review uses (docs/software-factory.md, "Visual specs").
PAGE_SKILL = "build-the-page"
PAGE_WRITERS = {
    "segment-product-owner": ("playbooks/write-a-spec-page.md", "playbooks/validate-in-production.md"),
    "reviewer": ("playbooks/review-a-pull-request.md",),
    "builder": ("playbooks/qa.md",),
}


def page_skill(template="segment-product-owner"):
    folder = CATALOG / template / "skills" / PAGE_SKILL
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def page_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "page_tool", CATALOG / "segment-product-owner" / "skills" / PAGE_SKILL / "page.py"
    )
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ in the template's folder: a bot would be given it.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "dont_write_bytecode", True)
        spec.loader.exec_module(module)
    return module


def test_every_bot_that_writes_a_page_a_person_reads_uses_the_one_page_skill():
    # Spec, Validate, Review and QA pages were each written their own way, as text or drawn mock-ups, and
    # were sent back as unreadable. One skill builds them all; a copy that drifts teaches one bot an older page.
    canonical = page_skill()
    assert {"SKILL.md", "page.py"} <= set(canonical)
    assert {f"templates/{k}.html" for k in ("screen-spec", "change-spec", "validation", "review")} <= set(canonical)
    for template, playbooks in PAGE_WRITERS.items():
        assert page_skill(template) == canonical, template
        assert f"skills/{PAGE_SKILL}/SKILL.md" in agent(template), template
        for playbook in playbooks:
            text = " ".join((CATALOG / template / playbook).read_text().split())
            assert f"skills/{PAGE_SKILL}/SKILL.md" in text, playbook
            assert "tico page publish" in text and "tico file publish" not in text, playbook
            assert "render" in text, playbook
    validate = " ".join(
        (CATALOG / "segment-product-owner" / "playbooks" / "validate-in-production.md").read_text().split()
    )
    assert "a person never does the QA" in validate and "gh pr checks <pull request link>" in validate


def png(path, width, height):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\xc8" * 3 * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def filled(kind, folder):
    """The skill's template for `kind`, filled in as a bot would, with real pictures where it names them."""
    text = page_skill()[f"templates/{kind}.html"].decode()
    for name in re.findall(r'data-shot="([^"]+)"', text):
        png(folder / name.replace("{{screen}}", "drawer").replace("{{page}}", "drawer"), 780, 600)
    (folder / "shots").mkdir(exist_ok=True)
    text = re.sub(r"\{\{x,y\}\} -> \{\{x,y\}\}", "600,120 -> 420,500", text)
    text = text.replace("{{screen}}", "drawer").replace("{{page}}", "drawer")
    text = re.sub(r"\{\{[^}]*\}\}", "the account row", text)
    for line in ("Cause:", "Fix:"):
        text = text.replace(f"<b>{line}</b> the account row", f"<b>{line}</b> the row is clipped")
    return text


@pytest.mark.parametrize("kind", ["screen-spec", "change-spec", "validation", "review"])
def test_a_page_built_from_each_template_passes_its_check_once_published(kind, tmp_path):
    from backend import visuals

    tool = page_tool()
    (tmp_path / "shots").mkdir()
    draft = tmp_path / "draft.html"
    draft.write_text(filled(kind, tmp_path))
    if "data-shot" in draft.read_text():
        assert tool.problems_in(draft.read_text(), kind) != []  # the screenshots are not in it yet
    tool.build(draft, tmp_path / "page.html")
    page = (tmp_path / "page.html").read_text()
    assert tool.problems_in(page, kind) == []
    # Every arrow is drawn, and nothing of its tag leaks into the page as text (an arrow is written "a -> b").
    assert page.count("<line ") == len(re.findall(r'data-arrows="', draft.read_text()))
    assert '">\n' not in re.sub(r"<[^>]+>", "", page)
    cleaned, removed = visuals.clean(page.encode(), "page")
    assert removed == []
    assert tool.problems_in(cleaned.decode(), kind) == []


def test_the_page_check_refuses_what_people_sent_back(tmp_path):
    tool = page_tool()
    (tmp_path / "shots").mkdir()
    draft = tmp_path / "draft.html"
    draft.write_text(filled("screen-spec", tmp_path))
    tool.build(draft, tmp_path / "page.html")
    good = (tmp_path / "page.html").read_text()
    assert tool.problems_in(good, "screen-spec") == []
    first = good.index("<figure")
    drawn = (
        good[:first]
        + '<figure><div class="card">Home · Tasks</div><figcaption>after</figcaption></figure>'
        + good[first:]
    )
    assert any("not a screenshot" in p for p in tool.problems_in(drawn, "screen-spec"))
    no_decision = re.sub(r"<p><b>Decide:</b>.*?</p>", "", good, count=1, flags=re.S)
    assert any("what to decide" in p for p in tool.problems_in(no_decision, "screen-spec"))
    late = good.replace('<section id="details">', '<section id="details"><figure class="x"></figure>', 1)
    assert any("in the details" in p for p in tool.problems_in(late, "screen-spec"))
    asks = good.replace('<section id="details">', "<p>Please check it on your phone.</p><section id=\"details\">", 1)
    assert any("never asks the reader" in p for p in tool.problems_in(asks, "screen-spec"))
