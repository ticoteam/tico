"""The six bots a software company starts with are taught the Product and Engineering stages they own.

A bot does what its template says. Before 0.6 the Builder was told to merge when the merge rules passed, the reviewer
to write a "verdict" on the task, and the release bot was called a Release Owner that never touched a pull request; a
bot following that text would merge by hand around the merge queue or never attach the page its stage needs. So each
template names its stages and the commands that leave them, and the Release Owner (and only it) may push a fix to a pull
request's branch.

Where Tico's merge queue merges, the bots leave merging to it: that is their instructions, not a deny. Nov 17: a company
on git-flow with no queue stalled a release candidate because its Release Owner was denied `gh pr merge`. So no template
denies ordinary GitHub work (merging, tagging, releasing, `gh api`); each denies only what cannot be undone."""

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

from backend import pipeline_seats
from clients import catalog

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
SIX = sorted(set(pipeline_seats.TEMPLATES.values()))
OLD_WORDS = re.compile(r"\b(verdicts?|merge rules|QA pass|board|ticket)\b", re.IGNORECASE)


def texts(template):
    """Every instruction file a bot from `template` is given: the base, its shared files (skills in `.claude/skills`
    shared with other roles) and its own folder."""
    for rel, path in sorted(catalog.files(template).items()):
        parts = Path(rel).parts
        if path.suffix in (".md", ".yaml") and "software" not in parts and "examples" not in parts:
            yield path, path.read_text()


def agent(template):
    return " ".join((CATALOG / template / "AGENTS.md").read_text().split())


def settings(template):
    """The permissions a bot from `template` is given (its own file, else the catalog's base). Only `deny` counts: Claude
    Code runs with bypassPermissions (runner-rs/src/hosts/claude.rs), so a template ships no `allow` list."""
    rules = json.loads(catalog.files(template)[".claude/settings.json"].read_text())["permissions"]
    assert set(rules) == {"deny"}, template
    return rules


def card(template):
    return yaml.safe_load((CATALOG / template / "card.yaml").read_text())


ALL = sorted(c["template"] for c in catalog.cards())
# Ordinary, reversible GitHub work no template denies.
ORDINARY = (
    "gh pr merge",
    "gh pr create",
    "gh pr edit",
    "gh pr ready",
    "gh pr comment",
    "gh pr review",
    "gh api",
    "git tag",
    "--tags",
    "--follow-tags",
    "refs/tags/",
    "gh release create",
    "gh release edit",
    "gh workflow run",
    "gh run rerun",
    "gh run cancel",
)
# What cannot be undone, or is a credential, which every template denies.
FORCE = "--" + "force"
DESTRUCTIVE = (
    f"Bash(git push {FORCE})",
    f"Bash(git push {FORCE} *)",
    "Bash(git push -f*)",
    f"Bash(git push * {FORCE})",
    f"Bash(git push * {FORCE} *)",
    "Bash(gh repo delete *)",
    "Bash(gh release delete *)",
    "Bash(gh run delete *)",
    "Bash(gh cache delete *)",
    "Bash(gh secret *)",
    "Bash(gh variable *)",
    "Bash(gh workflow enable *)",
    "Bash(gh workflow disable *)",
)
QUEUE = (
    "Where {{app_name}}'s merge queue merges a repository, the queue merges: never merge by hand there. Where "
    "{{company_name}} merges and releases another way (its own skill or playbook, git-flow, no queue), merge, tag and "
    "release as that says. Never merge a pull request with red checks."
)


@pytest.mark.parametrize("template", ALL)
def test_templates_deny_only_what_cannot_be_undone(template):
    rules = settings(template)["deny"]
    for ordinary in ORDINARY:
        assert not any(ordinary in rule for rule in rules), (template, ordinary)
    assert not any(rule in ("Bash(gh *)", "Bash(gh release *)", "Bash(gh pr *)") for rule in rules), template
    for rule in DESTRUCTIVE:
        assert rule in rules, (template, rule)
    # A push with a lease (after a rebase) is not swallowed by a wider deny.
    assert not any(rule.startswith(f"Bash(git push {FORCE}*") for rule in rules), template


@pytest.mark.parametrize("template", ["builder", "feature-owner", "release-owner"])
def test_merging_follows_the_queue_where_there_is_one_and_the_companys_own_way_where_not(template):
    # Tico's own Engineering pipeline behaves as before: its repository has the merge queue, so the queue merges.
    assert QUEUE in agent(template)


def test_the_feature_owner_reads_ci_by_repository():
    # Validate runs on a `git archive` copy, which has no remote: gh, and the runner's token for the repository, need
    # the pull request's link or -R to know which repository to read.
    rules = settings("feature-owner")
    for read in ("gh pr checks", "gh pr diff", "gh run list", "gh run view"):
        assert not any(read in rule for rule in rules["deny"]), read
    playbook = (CATALOG / "feature-owner" / "playbooks" / "validate-in-production.md").read_text()
    assert "gh pr checks <pull request link>" in playbook and "-R <owner/name>" in playbook


# Token plan items 2 and 6 (Nov 13): a resumed turn already has the bot's files in context, so they are read when a session
# starts or after a compaction, never "every run"; and every bot that writes or reviews code is told to hand simple work to
# Haiku subagents, in its instructions rather than a hidden setting.
CODE_BOTS = [
    "builder",
    "feature-owner",
    "reviewer",
    "qa-owner",
    "release-owner",
    "devops-engineer",
    "infrastructure-owner",
]
SUBAGENTS = (
    "Use Haiku 5.5 extra-high-effort subagents whenever possible for simpler work: searching, reading logs, running "
    "tests, mechanical edits. Use Opus for complex work and for anything with substantial UI work."
)


def test_no_template_reads_its_files_on_every_run():
    for path in sorted(CATALOG.glob("*/AGENTS.md")):
        text = " ".join(path.read_text().split())
        assert "first, every run" not in text and "`knowledge/company.md` every run" not in text, path


@pytest.mark.parametrize("template", CODE_BOTS)
def test_code_bots_hand_simple_work_to_haiku_subagents(template):
    assert SUBAGENTS in agent(template)


def test_reviews_start_from_the_diff():
    assert "Start from the diff, the Spec page and the failing checks" in agent("reviewer")
    playbook = " ".join((CATALOG / "reviewer" / "playbooks" / "review-a-pull-request.md").read_text().split())
    assert "Start from the diff, the Spec page and the failing checks." in playbook


@pytest.mark.parametrize("template", ["builder", "feature-owner", "release-owner"])
def test_the_builder_and_release_manager_may_push_a_rebased_branch(template):
    # A conflict wake tells the Builder to rebase and push with a lease; the Release Owner rebases in Merge. A tag is a
    # release on a repository that releases from tags: where Tico releases, the approved rollout or `tico task release`
    # makes it (their instructions); where the company releases its own way, the bot tags as that says.
    rules = settings(template)
    # A bare forced push stays denied, and the lease is not swallowed by a wider deny.
    assert f"Bash(git push {FORCE} *)" in rules["deny"]
    assert not any(rule.startswith(f"Bash(git push {FORCE}*") for rule in rules["deny"])
    assert not any("git tag" in rule or "tags" in rule for rule in rules["deny"]), template
    assert not any("gh pr checkout" in rule for rule in settings("release-owner")["deny"])


@pytest.mark.parametrize("template", ["builder", "feature-owner", "reviewer", "qa-owner", "release-owner"])
def test_every_bot_that_meets_a_red_main_finds_the_one_task_waits_on_it_and_rebases_after(template):
    # Nov 13, main red twice (#3335, #3604): 13 duplicate "main red" tasks (3 from the QA and Release Owners, whose
    # templates said nothing), two fix PRs for one break, re-runs that tested the old merge again, and tasks snoozed to
    # guessed dates. Each bot that sees a red check is taught the same rules, in the same words.
    text = " ".join(" ".join(body.split()) for _path, body in texts(template))
    # Look first, snoozed ones included, and give a new one the title the next bot searches for.
    assert 'tico task list --q "Main red" --status open --snoozed all' in text
    assert re.search(r"`Main red: <[^>]+>, since <[^>]+>`", text), "the main-red title"
    # Filed from a private task, it must still be one the next bot's search finds: plain or --private it is hidden.
    assert "file it with `tico task create --public`" in text and "no `--private`" not in text
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


def test_the_feature_owner_takes_a_feature_from_a_chat_to_production_in_one_session():
    # Nov 14 (one Product pipeline): the Product Owner and the Builder are one bot, so the code and the reasons are in
    # context when it builds. It writes the Spec, owns Build and Fixes, and never passes a person's approval.
    text = agent("feature-owner")
    assert card("feature-owner")["session"] == "task"
    assert yaml.safe_load((CATALOG / "feature-owner" / "bot.yaml").read_text())["session"] == "task"
    for stage in (
        "Spec",
        "Related input",
        "OK spec",
        "Build",
        "Fixes",
        "QA",
        "OK release",
        "Validate",
    ):
        assert f"**{stage}" in text, stage
    assert "Pitch" not in text  # a feature starts as a chat; its first approval is the Spec (Nov 16)
    assert "A person approves the Spec and the Release report: you never pass those yourself." in text
    assert '--task <id> --as "Release report"' in text
    for playbook in ("build-a-dev-task", "fix-what-review-found", "qa"):
        assert f"`playbooks/{playbook}.md`" in text, playbook
        assert (CATALOG / "feature-owner" / "playbooks" / f"{playbook}.md").is_file(), playbook
    # The Spec is written with the shared write-the-spec skill (Nov 18), which the Feature Owner is given.
    assert "`.claude/skills/write-the-spec/SKILL.md`" in text
    assert ".claude/skills/write-the-spec/SKILL.md" in catalog.files("feature-owner")
    # Nov 14, Tico Team trial: owners made from the Product Owner's settings could not edit or open a pull request.
    rules = settings("feature-owner")
    for write in ("gh pr create", "gh pr edit", "gh pr ready", "gh pr comment"):
        assert not any(write in rule for rule in rules["deny"]), write
    for read in ("gh release list", "gh release view", "gh issue view", "gh run view"):
        assert not any(read in rule for rule in rules["deny"]) and "Bash(gh release *)" not in rules["deny"], read
    # Token rules: files once per session, learnings once per task.
    assert "A resumed turn already has them: do not read them again." in text
    assert "When the task reaches Done or is closed, once per task and not every run" in text


# The one page skill every bot that writes a spec, a check or a review uses (docs/software-factory.md, "Visual specs").
PAGE_SKILL = "build-the-page"
PAGE_WRITERS = {
    "feature-owner": (
        ".claude/skills/write-the-spec/SKILL.md",
        "playbooks/qa.md",
        "playbooks/validate-in-production.md",
    ),
    "reviewer": ("playbooks/review-a-pull-request.md",),
    "builder": ("playbooks/qa.md",),
    "release-owner": ("playbooks/write-the-release-report.md",),
}
PAGE_KINDS = ("bug-spec", "screen-spec", "change-spec", "validation", "review", "qa", "release")


def page_skill(template="feature-owner"):
    """The page skill's files as a bot from `template` is given them, by their path in the skill's folder."""
    prefix = f".claude/skills/{PAGE_SKILL}/"
    given = catalog.files(template)
    return {rel[len(prefix) :]: path.read_bytes() for rel, path in given.items() if rel.startswith(prefix)}


def page_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "page_tool", catalog.files("feature-owner")[f".claude/skills/{PAGE_SKILL}/page.py"]
    )
    module = importlib.util.module_from_spec(spec)
    # No __pycache__ in the template's folder: a bot would be given it.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "dont_write_bytecode", True)
        spec.loader.exec_module(module)
    return module


def test_every_bot_that_writes_a_page_a_person_reads_uses_the_one_page_skill():
    # Spec, Validate, Review and QA pages were each written their own way, as text or drawn mock-ups, and
    # were sent back as unreadable. One skill builds them all, from one copy every page writer takes (_shared).
    canonical = page_skill()
    assert {"SKILL.md", "page.py"} <= set(canonical)
    assert {f"templates/{k}.html" for k in PAGE_KINDS} <= set(canonical)
    for template, playbooks in PAGE_WRITERS.items():
        assert page_skill(template) == canonical, template
        given = catalog.files(template)
        for playbook in playbooks:
            text = " ".join(given[playbook].read_text().split())
            assert f".claude/skills/{PAGE_SKILL}/SKILL.md" in text, playbook
            assert "tico page publish" in text and "tico file publish" not in text, playbook
            assert "render" in text, playbook
    validate = " ".join((CATALOG / "feature-owner" / "playbooks" / "validate-in-production.md").read_text().split())
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
    return text


@pytest.mark.parametrize("kind", PAGE_KINDS)
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


def test_a_page_of_its_own_light_colours_needs_dark_values_too(tmp_path):
    # The Spec that read near-black on the app's dark background: its own light colours and no dark values. The template's
    # tokens (fallbacks included), a picture's url() and the red arrows page.py draws are not its own colours and pass.
    tool = page_tool()
    (tmp_path / "shots").mkdir()
    draft = tmp_path / "draft.html"
    draft.write_text(filled("screen-spec", tmp_path))
    tool.build(draft, tmp_path / "page.html")
    good = (tmp_path / "page.html").read_text()
    assert tool.problems_in(good, "screen-spec") == []
    light_only = "colours are light-only"
    styled = good.replace("</style>", ".card{background:#fff;color:#1d1d1f;border:1px solid #e5e5e5}\n</style>", 1)
    assert any(light_only in p for p in tool.problems_in(styled, "screen-spec"))
    inline = good.replace('<section id="details">', '<section id="details"><p style="color:rgb(34, 34, 34)">x</p>', 1)
    assert any(light_only in p for p in tool.problems_in(inline, "screen-spec"))
    named = good.replace("</style>", "p.note{color:white}\n</style>", 1)
    assert any(light_only in p for p in tool.problems_in(named, "screen-spec"))
    dark = styled.replace(
        "</style>", "@media (prefers-color-scheme: dark){.card{background:#111;color:#eee}}\n</style>", 1
    )
    assert not any(light_only in p for p in tool.problems_in(dark, "screen-spec"))
    picture = good.replace(
        "</style>", ".hero{background:url(data:image/png;base64,iVBORw0KGgo=) no-repeat}\n</style>", 1
    )
    assert tool.problems_in(picture, "screen-spec") == []


def test_each_page_opens_the_way_its_reader_needs(tmp_path):
    # A bug spec opens with the report, a change spec with its goal; a release report names at most three risks and
    # its changelog is written for customers, so it can be published as it is (Chris, Nov 13).
    tool = page_tool()
    (tmp_path / "shots").mkdir()
    pages = {}
    for kind in ("bug-spec", "screen-spec", "release"):
        draft = tmp_path / f"{kind}.html"
        draft.write_text(filled(kind, tmp_path))
        tool.build(draft, tmp_path / f"{kind}-page.html")
        pages[kind] = (tmp_path / f"{kind}-page.html").read_text()
        assert tool.problems_in(pages[kind], kind) == []
    no_report = pages["bug-spec"].replace("<b>Report:</b>", "<b>Bug:</b>")
    assert any("opens with its report" in p for p in tool.problems_in(no_report, "bug-spec"))
    no_goal = pages["screen-spec"].replace("<b>Goal:</b>", "<b>Change:</b>")
    assert any("opens with its goal" in p for p in tool.problems_in(no_goal, "screen-spec"))
    four = re.sub(
        r'(<section id="risks">.*?)</ol>', r"\1" + "<li>more</li>" * 3 + "</ol>", pages["release"], count=1, flags=re.S
    )
    assert any("4 risks" in p for p in tool.problems_in(four, "release"))
    internal = pages["release"].replace("<p class=\"plus\">", "<p class=\"plus\">Fixed in PR #4325. ", 1)
    found = tool.problems_in(internal, "release")
    assert any("'PR' in the changelog" in p for p in found) and any("'#4325' in the changelog" in p for p in found)
    # Nov 18: a spec may be long when clarity needs it, so its length warns and passes; a release report's still fails.
    padding = "<p>" + " ".join(["row"] * 2000) + "</p></section>"
    long_spec = pages["screen-spec"].replace("</section>", padding, 1)
    assert tool.problems_in(long_spec, "screen-spec") == []
    assert any("words" in w for w in tool.warnings_in(long_spec, "screen-spec"))
    long_release = pages["release"].replace("</section>", padding, 1)
    assert any("words; a release page" in p for p in tool.problems_in(long_release, "release"))
    assert tool.warnings_in(long_release, "release") == []


def test_the_release_label_flow_stays_off_stages_that_wait_for_a_published_release():
    # The Engineering Release stage already leaves when GitHub publishes a release; labelling its tasks too would run two
    # release flows over one task.
    playbook = " ".join((CATALOG / "release-owner" / "playbooks" / "write-the-release-report.md").read_text().split())
    before_any_step = playbook.split("## 1.")[0]
    assert 'no automatic "in a release" requirement' in before_any_step
    assert "`playbooks/cut-a-release.md` as today and label nothing" in before_any_step
    routing = agent("release-owner")
    assert 'no automatic "in a release" requirement' in routing
    assert routing.index("labels nothing") < routing.index("release:YYYY-MM-DD.N")


# A change that adds or changes SQL: its author runs the quick perf check, and its reviewer reads the numbers it posts.
PERF_SKILL = ".claude/skills/quick-perf-check"


@pytest.mark.parametrize("template", ["builder", "feature-owner", "reviewer"])
def test_a_sql_change_gets_the_quick_perf_check_from_its_author_and_its_reviewer(template):
    assert PERF_SKILL in card(template)["shared_files"], template
    skill = catalog.files(template)[f"{PERF_SKILL}/SKILL.md"].read_text()
    assert len(skill.splitlines()) <= 25
    for words in ("EXPLAIN QUERY PLAN", "N+1", "copy of the data", "before the change merges"):
        assert words in skill, words
    assert f"{PERF_SKILL}/SKILL.md" in agent(template), template
    playbook = " ".join((CATALOG / "reviewer" / "playbooks" / "review-a-pull-request.md").read_text().split())
    assert f"{PERF_SKILL}/SKILL.md" in playbook


def test_the_qa_owner_records_one_choice_for_each_red_main_or_revert():
    text = agent("qa-owner")
    choice = "Record one choice on the task for each red main or revert"
    assert choice in text
    assert "a selection fix (test selection missed an existing test)" in text
    assert "a regression test (the behaviour was not covered)" in text
    assert "a flake or infrastructure remedy" in text
    assert text.index("## When main is red") < text.index(choice) < text.index("## Blockers only a person can fix")


def test_the_qa_page_check_names_each_part_a_person_reads_first(tmp_path):
    # Task #389 (Chris, Nov 13: the QA review "should look really, really clear"): Nov 14's five QA pages had the
    # criteria table but no verdict strip, no diagram of how the change works and no rollback. The check names each.
    tool = page_tool()
    (tmp_path / "shots").mkdir()
    draft = tmp_path / "draft.html"
    draft.write_text(filled("qa", tmp_path))
    tool.build(draft, tmp_path / "page.html")
    good = (tmp_path / "page.html").read_text()
    assert tool.problems_in(good, "qa") == []

    def cut(name):
        return re.sub(rf'<section id="{name}">.*?</section>', "", good, count=1, flags=re.S)

    for name in ("strip", "how", "risk"):
        assert f'no section id="{name}"' in tool.problems_in(cut(name), "qa"), name
    three = good.replace('<div class="tile ok">', '<div class="x">', 1)
    assert any("3 tiles; it has 4" in p for p in tool.problems_in(three, "qa"))
    unsaid = good.replace("Approve = ship to production.", "Approve.")
    assert any("Approve = ship to production" in p for p in tool.problems_in(unsaid, "qa"))
    no_diagram = good.replace('class="box changed"', 'class="box"')
    assert any("has no diagram" in p for p in tool.problems_in(no_diagram, "qa"))
    # A one-file change may name its file instead of a diagram: one row and the total (the total row is not a file).
    good = good.replace("<tr><td><b>the account row</b></td>", "<tr><td><b>Total: 2 files</b></td>", 1)
    arch, legend_end = good.index('<div class="arch">'), good.index("</p>", good.index('<p class="legend">')) + 4
    two_files = good[:arch] + good[legend_end:]
    assert any("has no diagram" in p for p in tool.problems_in(two_files, "qa")), "two files and the total"
    no_total = two_files.replace("<tr><td><b>Total: 2 files</b></td><td></td><td><b>the account row</b></td></tr>", "")
    assert any("has no diagram" in p for p in tool.problems_in(no_total, "qa")), "two files, no total row"
    one_file = two_files.replace(
        "<tr><td>the account row</td><td>the account row</td><td>the account row</td></tr>", ""
    )
    assert not any("diagram" in p for p in tool.problems_in(one_file, "qa"))
    # A diagram of the changed part alone gives no context.
    first = good.index('<div class="box changed">')
    lone = good[: first + 1] + re.sub(r'<div class="box[^"]*">.*?</div>', "", good[first + 1 :], flags=re.S)
    assert any("shows one box" in p for p in tool.problems_in(lone, "qa"))
    no_rollback = good.replace("<h3>Rollback</h3>", "<h3>Undo</h3>")
    assert any('no "Rollback" heading' in p for p in tool.problems_in(no_rollback, "qa"))
    no_data = good.replace("Stored data:", "Also:")
    assert any("whether stored data changes" in p for p in tool.problems_in(no_data, "qa"))
    log = "<pre>" + "\n".join(f"job {n}: pass" for n in range(15)) + "</pre>"
    raw = good.replace('<section id="findings">', log + '<section id="findings">', 1)
    assert any("15 lines: fold" in p for p in tool.problems_in(raw, "qa"))
    folded = good.replace(
        '<section id="findings">', f"<details><summary>CI</summary>{log}</details>" + '<section id="findings">', 1
    )
    assert tool.problems_in(folded, "qa") == []
