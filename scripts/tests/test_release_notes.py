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
    ref="",
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
            "head": {"sha": head, "ref": ref},
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
        ("app/main.swift", "app"),
        ("runner_support/x.py", "runner"),
        ("recorder/src/lib.rs", "recorder"),
        ("templates/catalog/x.md", "templates"),
        (".github/workflows/train.yml", "ci"),
        ("README.md", "other"),
        ("backend/tests/test_tasks.py", "tests"),
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
    assert text.startswith("Changes since v0.5.200: 2 user-facing, 1 high risk, 1 backend, 1 internal\n")
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


def test_a_block_with_nothing_given_is_one_line_and_says_so():
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    block = text[text.index("### #13") : text.index("## Internal")]
    assert "No spec, demo steps or risk given." in block
    assert not any(word in block for word in ("- Spec:", "- Demo steps:", "- Risk:")), "no placeholder lines"
    assert "- Checks: none recorded" in block, "nothing ran, so the checks line says so"
    assert "- Review:" not in block, "a line only when there are reviews or receipts"


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
        text == "Changes since v0.5.200: 0 user-facing, 0 high risk, 0 backend, 0 internal\n\n"
        "No merged pull request names a commit in this range.\n"
    )


def test_a_range_that_is_cut_short_says_so(monkeypatch):
    monkeypatch.setattr(notes, "PAGE", 2)
    monkeypatch.setattr(notes, "COMMIT_PAGES", 1)
    text = notes.build(scenario(), "v0.5.200", "v0.5.201")
    assert "The range has more commits than these notes read" in text


def test_high_risk_and_cut_short_are_read_from_the_notes_built_here(monkeypatch):
    built = notes.build(scenario(), "v0.5.200", "v0.5.201")
    assert notes.high_risk(built) and not notes.cut_short(built)
    # Inside an approval issue's body, under its candidate line, the notes still read the same.
    assert notes.high_risk("Candidate v0.5.201 at abc (validated: u)\n\n" + built)
    empty = notes.build(FakeApi({"compare/v0.5.200...v0.5.201": {"commits": []}}), "v0.5.200", "v0.5.201")
    assert not notes.high_risk(empty), "the count line's '0 high risk' is no High risk item"
    assert not notes.high_risk("### #3 High risk of rain in the demo copy\n- Risk: low")
    monkeypatch.setattr(notes, "PAGE", 2)
    monkeypatch.setattr(notes, "COMMIT_PAGES", 1)
    assert notes.cut_short(notes.build(scenario(), "v0.5.200", "v0.5.201"))


def test_a_block_gives_only_the_fields_it_has_and_says_risk_not_stated_when_only_risk_is_missing():
    lines = notes.block(pull(number=3, sections={"spec": "https://spec.example/3"}))
    assert "- Spec: https://spec.example/3" in lines and "- Risk: not stated" in lines
    assert not any(line.startswith(("- Demo steps", "No spec")) for line in lines)
    lines = notes.block(pull(number=4, risk="low", risk_text="copy only"))
    assert "- Risk: low — copy only" in lines
    assert not any(line.startswith(("- Spec", "- Demo steps", "No spec")) for line in lines)


def test_checks_print_only_when_one_failed_or_none_ran():
    assert not any(line.startswith("- Checks") for line in notes.block(pull(checks="51 passed, 0 failed")))
    failed = notes.block(pull(checks="1 passed, 1 failed", failed=("e2e",)))
    assert "- Checks: 1 passed, 1 failed (failed: e2e)" in failed
    assert "- Checks: none recorded" in notes.block(pull(checks=notes.NONE_RECORDED))


# ---- the review page line a Dev ticket's pull request carries ----


def review_scenario():
    """Three merged pull requests: a Dev ticket's with its page, a Dev ticket's whose page could not be built, and one
    with no Dev ticket (no line, so exempt from the count)."""
    routes = {}
    routes.update(
        pr_routes(
            21,
            title="Dashboard",
            body="## Spec\nShows the week.\n\nReview page: https://tico.example/#/page/p21\n",
            files=("backend/dash.py",),
            runs=[check("unit")],
        )
    )
    routes.update(
        pr_routes(22, title="Copy tweak", body="Review page: none (could not build it)\n", files=("scripts/copy.py",))
    )
    routes.update(pr_routes(23, title="Lint config", files=("scripts/lint.py",)))
    commits = [("Dashboard (#21)", 21), ("Copy tweak (#22)", 22), ("Lint config (#23)", 23)]
    routes["compare/v0.5.200...v0.5.201"] = {
        "commits": [{"sha": SHA[number], "commit": {"message": subject}} for subject, number in commits]
    }
    return FakeApi(routes)


def test_the_notes_count_review_pages_among_the_pull_requests_that_have_a_line_and_link_each_one():
    text = notes.build(review_scenario(), "v0.5.200", "v0.5.201")
    assert "Review pages: 1 of 2\n" in text
    assert "- Review page: https://tico.example/#/page/p21\n" in text
    assert "- #22 Copy tweak (@alice) · Review page: none (could not build it)\n" in text
    assert "- #23 Lint config (@alice)\n" in text


def test_the_server_finds_a_dev_ticket_the_same_way_in_the_branch_title_or_body():
    assert notes.names_dev_ticket({"title": "Export fix (Dev 665)"})
    assert notes.names_dev_ticket({"title": "Export fix (Dev #665)"})
    assert notes.names_dev_ticket({"title": "Export fix, Dev ticket 665"})
    assert notes.names_dev_ticket({"head": {"ref": "fix/dev-665-export"}})
    assert notes.names_dev_ticket({"body": "Closes Dev 665."})
    assert not notes.names_dev_ticket({"title": "Bump the lockfile (#2851)", "body": "Closes #2851"})
    assert not notes.names_dev_ticket({"title": "Developer tools 12", "head": {"ref": "dev-tools"}})


def test_a_dev_ticket_pull_request_with_no_review_line_shows_missing_and_counts_in_the_review_pages():
    routes = {}
    routes.update(
        pr_routes(31, title="Export fix (Dev 665)", body="## Spec\nFixes the export.\n", files=("backend/export.py",))
    )
    routes.update(pr_routes(32, title="Lint config", files=("scripts/lint.py",)))
    routes.update(
        pr_routes(
            33, title="Quiet the log", ref="fix/dev-666-quiet", body="## Spec\nQuieter.\n", files=("backend/log.py",)
        )
    )
    routes.update(pr_routes(34, title="Tidy the docs (Dev 667)", files=("scripts/docs.py",)))
    commits = [("Export fix (#31)", 31), ("Lint config (#32)", 32), ("Quiet the log (#33)", 33), ("Tidy (#34)", 34)]
    routes["compare/v0.5.200...v0.5.201"] = {
        "commits": [{"sha": SHA[number], "commit": {"message": subject}} for subject, number in commits]
    }
    text = notes.build(FakeApi(routes), "v0.5.200", "v0.5.201")
    assert "Review pages: 0 of 3\n" in text, "the Lint config pull request names no Dev ticket: not counted"
    assert text.count("- Review page: missing\n") == 2
    assert "- #34 Tidy the docs (Dev 667) (@alice) · Review page: missing\n" in text
    assert "- #32 Lint config (@alice)\n" in text
