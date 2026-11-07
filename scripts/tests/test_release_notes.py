"""Release notes (scripts/release_notes.py): which merged pull requests a candidate's notes list, how they are grouped and
ordered, what each block says, and that a block prints no placeholder for evidence that is not there."""

import pytest

from scripts import release_notes as notes

SHA = {number: "%040x" % number for number in range(1, 40)}  # distinct commits, in and out of a range


def pull(number=1, **fields):
    values = dict(
        number=number,
        title="Change %d" % number,
        url="https://github.example/pull/%d" % number,
        author="alice",
        merged_at="2026-11-05T10:00:00Z",
        risk="",
        risk_text="",
        sections={},
        areas=("backend",),
        checks="1 passed, 0 failed",
        failed=(),
        reviews=(),
    )
    values.update(fields)
    return notes.Pull(**values)


class FakeApi:
    """GET answers from `routes`: a list is paged by per_page and page, anything else comes back whole."""

    def __init__(self, routes):
        self.routes = routes
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        base, _, query = path.partition("?")
        if base not in self.routes:
            raise AssertionError("no route for GET %s" % path)
        value = self.routes[base]
        if not isinstance(value, list):
            return value
        params = dict(item.split("=", 1) for item in query.split("&") if item)
        per, page = int(params.get("per_page", 30)), int(params.get("page", 1))
        return value[(page - 1) * per : page * per]


def check(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def pr_routes(
    number,
    *,
    title,
    author="alice",
    body="",
    labels=(),
    merged=True,
    merge_sha=None,
    files=("backend/x.py",),
    runs=(),
    reviews=(),
    comments=(),
):
    """The REST answers one pull request needs: its record, files, head checks, reviews and comments."""
    head = "%040x" % (1000 + number)
    return {
        "pulls/%d" % number: {
            "number": number,
            "title": title,
            "html_url": "https://github.example/pull/%d" % number,
            "user": {"login": author},
            "merged_at": "2026-11-05T10:00:00Z" if merged else None,
            "merge_commit_sha": merge_sha or SHA[number],
            "head": {"sha": head},
            "body": body,
            "labels": [{"name": name} for name in labels],
        },
        "pulls/%d/files" % number: [{"filename": name} for name in files],
        "commits/%s/check-runs" % head: {"check_runs": list(runs)},
        "pulls/%d/reviews" % number: list(reviews),
        "issues/%d/comments" % number: list(comments),
    }


def scenario():
    """The base v0.5.200 to the head v0.5.201: five merged pull requests, one open one, and one whose merge commit is not
    in the range (a pull request merged into another branch)."""
    routes = {}
    routes.update(
        pr_routes(
            11,
            title="Clients dashboard",
            author="bob",
            body="## Risk\nlow — copy only\n",
            files=("clients/web/app.ts",),
            runs=[check("unit")],
        )
    )
    routes.update(
        pr_routes(
            12,
            title="Migrate tasks table",
            body=(
                "## Spec\nhttps://spec.example/tasks\n\n<!-- prompt: link the spec -->\n\n"
                "## Demo steps\n1. Open Acme Lights\n2. Add a task\n\n"
                "## Risk\nhigh — rewrites task rows\n"
            ),
            files=("backend/migrations/0042_tasks.py", "backend/tasks.py"),
            runs=[check("unit"), check("e2e", "failure")],
            reviews=[{"state": "APPROVED", "user": {"login": "dave"}, "body": ""}],
        )
    )
    routes.update(pr_routes(13, title="Fix runner retry", author="erin", files=("runner-rs/src/retry.rs",), runs=[]))
    routes.update(pr_routes(14, title="Tidy docs", author="carol", files=("docs/releasing.md",)))
    routes.update(
        pr_routes(
            15,
            title="Settings tabs",
            author="frank",
            body="## Risk\nmedium: UI only\n",
            files=("ui/settings/tabs.tsx", "scripts/tests/test_settings.py"),
        )
    )
    routes.update(pr_routes(16, title="Work in progress", merged=False))
    routes.update(pr_routes(17, title="Merged elsewhere", merge_sha="%040x" % 999))
    commits = [
        ("Merge pull request #11 from acme/clients-dashboard", 11),
        ("Migrate tasks table (#12)", 12),
        ("Merge pull request #13 from acme/runner-retry", 13),
        ("Tidy docs (#14)", 14),
        ("Merge pull request #15 from acme/settings", 15),
        ("Work in progress (#16)", 16),
        ("Merge pull request #17 from acme/other", 17),
    ]
    routes["compare/v0.5.200...v0.5.201"] = {
        "commits": [{"sha": SHA[number], "commit": {"message": subject}} for subject, number in commits]
    }
    return FakeApi(routes)


# ---- what counts as a pull request, and where it sits ----


def test_pr_numbers_come_from_merge_and_squash_subjects_once_and_in_order():
    commits = [
        {"commit": {"message": "Merge pull request #5 from acme/x\n\nbody (#99)"}},
        {"commit": {"message": "Fix it (#6)\n\n(#7)"}},
        {"commit": {"message": "Fix it again (#6)"}},
        {"commit": {"message": "no number here"}},
    ]
    assert notes.pr_numbers(commits) == [5, 6]


@pytest.mark.parametrize(
    "path, area",
    [
        ("ui/src/App.tsx", "ui"),
        ("clients/mobile/x.ts", "clients"),
        ("app/main.swift", "app"),
        ("backend/tasks.py", "backend"),
        ("runner-rs/src/main.rs", "runner"),
        ("runner_support/x.py", "runner"),
        ("recorder/src/lib.rs", "recorder"),
        ("templates/catalog/x.md", "templates"),
        ("docs/releasing.md", "docs"),
        ("scripts/release_train.py", "scripts"),
        (".github/workflows/train.yml", "ci"),
        ("README.md", "other"),
        ("backend/tests/test_tasks.py", "tests"),
        ("scripts/tests/test_release_train.py", "tests"),
        ("ui/src/app.test.tsx", "ui"),
        ("conftest.py", "tests"),
    ],
)
def test_each_file_belongs_to_one_area(path, area):
    assert notes.area_of(path) == area


@pytest.mark.parametrize(
    "fields, group",
    [
        (dict(risk="high", areas=("ui",)), "high"),
        (dict(risk="high", areas=("docs",)), "high"),
        (dict(risk="medium", areas=("docs",)), "internal"),
        (dict(areas=("ui", "backend")), "user"),
        (dict(areas=("app",)), "user"),
        (dict(areas=("runner", "docs")), "backend"),
        (dict(areas=("docs",)), "internal"),
        (dict(areas=()), "internal"),
    ],
)
def test_a_pull_request_is_grouped_by_its_risk_then_its_areas(fields, group):
    assert notes.group_of(pull(**fields)) == group


def test_a_pull_request_is_in_the_notes_only_when_merged_and_its_merge_commit_is_in_the_range():
    routes = {}
    routes.update(pr_routes(21, title="Merged"))
    routes.update(pr_routes(22, title="Open", merged=False))
    routes.update(pr_routes(23, title="Elsewhere", merge_sha="%040x" % 999))
    routes.update(pr_routes(24, title="Tests only", files=("scripts/tests/test_x.py",)))
    api = FakeApi(routes)
    in_range = {SHA[21], SHA[22], SHA[23], SHA[24]}
    assert notes.load(api, 21, in_range).title == "Merged"
    assert notes.load(api, 22, in_range) is None, "not merged"
    assert notes.load(api, 23, in_range) is None, "merged, but its merge commit is outside the range"
    tests_only = notes.load(api, 24, in_range)
    assert tests_only.areas == () and notes.group_of(tests_only) == "internal"


def test_paged_reads_whole_pages_up_to_the_limit():
    api = FakeApi({"items": list(range(250))})
    assert notes.paged(api, "items") == list(range(250))
    assert notes.paged(api, "items", pages=2) == list(range(200))
    assert notes.paged(FakeApi({"runs": {"check_runs": [1, 2]}}), "runs", key="check_runs") == [1, 2]


# ---- what a pull request says about itself ----


def test_sections_are_read_by_heading_and_html_prompts_are_dropped():
    body = (
        "Intro\n## Spec\nhttps://spec.example/x\n<!-- prompt: link the spec -->\n"
        "## Demo steps\n<!--\nprompt\n-->\n\n## Risk\nlow\n"
    )
    assert notes.sections_of(body) == {"spec": "https://spec.example/x", "demo steps": "", "risk": "low"}


@pytest.mark.parametrize(
    "text, labels, level, rest",
    [
        ("high — rewrites task rows", [], "high", "rewrites task rows"),
        ("Low risk, copy only.", [], "low", "copy only."),
        ("medium: UI only", ["risk:low"], "medium", "UI only"),
        ("", ["risk:high"], "high", ""),
        ("Highly risky", [], "", "Highly risky"),
        ("", [], "", ""),
    ],
)
def test_risk_comes_from_the_section_first_then_the_label(text, labels, level, rest):
    assert notes.risk_of({"risk": text} if text else {}, labels) == (level, rest)


def test_checks_are_counted_and_the_failed_ones_named():
    assert notes.checks_of([]) == ("none recorded", ())
    runs = [check("unit"), check("e2e", "failure"), check("lint", status="in_progress")]
    assert notes.checks_of(runs) == ("1 passed, 1 failed, 1 not finished", ("e2e",))
    assert notes.checks_of([check(None, "timed_out")]) == ("0 passed, 1 failed", ("unnamed check",))


def test_reviews_and_receipts_are_listed_and_capped():
    reviews = [
        {"state": "APPROVED", "user": {"login": "dave"}, "body": ""},
        {"state": "CHANGES_REQUESTED", "user": {"login": "erin"}, "body": ""},
    ]
    comments = [
        {"body": "Looks fine\nVerdict: ship it\n", "user": {"login": "dave"}},
        {"body": "**Receipt:** run/99 green", "user": {"login": "bot"}},
    ]
    assert notes.reviews_of(reviews, comments) == (
        "approved by @dave",
        "changes requested by @erin",
        "verdict: ship it",
        "receipt: run/99 green",
    )
    many = [{"state": "APPROVED", "user": {"login": "u%d" % n}, "body": ""} for n in range(8)]
    assert len(notes.reviews_of(many, [])) == 6


# ---- the notes ----


def test_build_lists_high_risk_then_user_facing_then_backend_then_internal():
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    headings = [
        line
        for line in text.splitlines()
        if line.startswith("## ") or line.startswith("### ") or line.startswith("- #")
    ]
    assert headings == [
        "## High risk",
        "### #12 Migrate tasks table",
        "## User-facing",
        "### #11 Clients dashboard",
        "### #15 Settings tabs",
        "## Backend",
        "### #13 Fix runner retry",
        "## Internal",
        "- #14 Tidy docs (@carol)",
    ]
    assert text.startswith("Changes since v0.5.200: 5 merged pull requests.\n")
    assert "#16" not in text and "#17" not in text, "open, and merged outside the range"


def test_a_block_shows_spec_demo_risk_checks_review_and_what_it_touches():
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    block = text[text.index("### #12") : text.index("## User-facing")]
    assert "https://github.example/pull/12 · @alice · merged 2026-11-05" in block
    assert "- Spec: https://spec.example/tasks" in block
    assert "- Demo steps:\n  1. Open Acme Lights\n  2. Add a task" in block
    assert "- Risk: high — rewrites task rows" in block
    assert "- Checks: 1 passed, 1 failed (failed: e2e)" in block
    assert "- Review: approved by @dave" in block
    assert "- Touches: backend" in block


def test_a_block_says_when_its_spec_demo_steps_or_risk_are_missing():
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    block = text[text.index("### #13") : text.index("## Internal")]
    assert "- Spec: none linked" in block
    assert "- Demo steps: none written" in block
    assert "- Risk: not stated" in block
    assert "- Checks: none recorded" in block
    assert "- Review:" not in block, "a line only when there are reviews or receipts"


def test_user_facing_risk_is_shown_as_written():
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    block = text[text.index("### #15") : text.index("## Backend")]
    assert "- Risk: medium — UI only" in block
    assert "- Touches: ui" in block, "the tests a pull request touches are not an area"


def test_evidence_providers_add_their_lines_and_print_nothing_when_they_have_none(monkeypatch):
    assert notes.EVIDENCE == [], "no evidence provider is registered yet"
    assert "Recording" not in notes.build(scenario(), "v0.5.200", "v0.5.201")

    def recording(pull_request):
        return ["Recording: https://demo.example/%d" % pull_request.number] if pull_request.number == 12 else []

    monkeypatch.setattr(notes, "EVIDENCE", [recording])
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    block = text[text.index("### #12") : text.index("## User-facing")]
    assert "- Recording: https://demo.example/12" in block
    assert "Recording" not in text[text.index("### #11") : text.index("## Backend")]


def test_a_range_with_no_merged_pull_request_says_so():
    api = FakeApi({"compare/v0.5.200...v0.5.201": {"commits": []}})
    text = notes.build(api, "v0.5.200", "v0.5.201")
    assert (
        text
        == "Changes since v0.5.200: 0 merged pull requests.\n\nNo merged pull request names a commit in this range.\n"
    )


def test_a_range_that_is_cut_short_says_so(monkeypatch):
    monkeypatch.setattr(notes, "PAGE", 2)
    monkeypatch.setattr(notes, "COMMIT_PAGES", 1)
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    assert "The range has more commits than these notes read" in text


def test_notes_are_read_only():
    api = scenario()
    notes.build(api, "v0.5.200", "v0.5.201")
    assert api.paths and all(path.startswith(("compare/", "pulls/", "commits/", "issues/")) for path in api.paths)


def test_a_block_is_built_from_its_fields_without_placeholders_for_evidence():
    lines = notes.block(pull(number=3, title="Plain"))
    assert lines[0] == "### #3 Plain"
    assert "- Spec: none linked" in lines and "- Risk: not stated" in lines
    assert not any("Review" in line or "Recording" in line for line in lines)
    assert notes.risk_line(pull(risk="high", risk_text="rewrites rows")) == "- Risk: high — rewrites rows"
    assert notes.field("Demo steps", "1. Open\n2. Add", "none written") == ["- Demo steps:", "  1. Open", "  2. Add"]
