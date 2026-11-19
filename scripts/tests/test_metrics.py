"""Verification metrics from fixture data, without network or git processes."""

import json
import re
from types import SimpleNamespace

import pytest

from scripts import metrics

REPO = "acme/tico"
SHA = {number: f"{number:040x}" for number in range(1, 20)}


def pull(number, merged="2026-08-01T12:00:00Z", title=None, body=""):
    return dict(
        number=number, merged_at=merged, title=title or f"Change {number}", body=body, merge_commit_sha=SHA[number]
    )


def run(number, conclusion, created, finished=None, **fields):
    return dict(
        id=number,
        conclusion=conclusion,
        status="completed",
        head_branch="main",
        event="workflow_dispatch",
        created_at=created,
        updated_at=finished or created,
        display_title=f"Validate {SHA[number]} as v1.0.1 from v1.0.0",
        **fields,
    )


def fixtures():
    pulls = {
        1: pull(1),
        2: pull(2, "2026-10-31T23:00:00Z", 'Revert "Change 1"', f"This reverts commit {SHA[1]}."),
        3: pull(3, "2026-11-02T12:00:00Z", "Hotfix for #1"),
    }

    def resolve(sha):
        return next((pr for pr in pulls.values() if pr["merge_commit_sha"].startswith(sha)), None)

    return pulls, resolve


def test_episodes_count_closed_in_green_week_and_include_open_red():
    runs = [
        run(1, "failure", "2026-10-25T23:00:00Z"),
        run(2, "timed_out", "2026-10-26T01:00:00Z"),
        run(3, "cancelled", "2026-10-26T02:00:00Z"),
        run(4, "success", "2026-10-26T03:00:00Z"),
        run(5, "success", "2026-10-27T03:00:00Z"),
        run(6, "startup_failure", "2026-11-01T23:00:00Z"),
        run(7, "success", "2026-11-02T03:00:00Z"),
        run(8, "failure", "2026-11-03T03:00:00Z"),
    ]
    episodes = metrics.episodes(list(reversed(runs)))
    fixed = [row for row in episodes if row["green"]]
    assert episodes[-1]["red"]["id"] == 8
    assert episodes[-1]["green"] is None
    assert [(row["red"]["id"], row["green"]["id"]) for row in fixed] == [(1, 4), (6, 7)]
    assert [metrics.iso_week(metrics.timestamp(row["timestamp"])) for row in fixed] == ["2026-W44", "2026-W45"]
    assert metrics.iso_week(metrics.timestamp("2027-01-01T00:00:00Z")) == "2026-W53"


def test_superseded_runs_wrong_branches_and_missing_candidates_do_not_open_red():
    runs = [
        run(1, "failure", "2026-10-01T01:00:00Z", "2026-10-01T04:00:00Z"),
        run(2, "success", "2026-10-01T02:00:00Z", "2026-10-01T03:00:00Z"),
        run(3, "failure", "2026-10-02T00:00:00Z"),
        run(4, "failure", "2026-10-03T00:00:00Z"),
        run(5, "success", "2026-10-04T00:00:00Z"),
    ]
    runs[2]["head_branch"] = "gh-readonly-queue/main/pr-1"
    runs[3]["display_title"] = "Release validation"
    assert metrics.episodes(runs) == []


def test_successful_reruns_are_flaky_not_red_main_episodes():
    red = run(1, "failure", "2026-10-01T01:00:00Z", "2026-10-01T02:00:00Z", run_attempt=1)
    green = {**red, "conclusion": "success", "run_attempt": 2, "updated_at": "2026-10-02T02:00:00Z"}
    current_red = run(2, "failure", "2026-10-05T01:00:00Z", "2026-10-05T02:00:00Z", run_attempt=1)
    failed_retry = {**current_red, "run_attempt": 2, "updated_at": "2026-10-05T03:00:00Z"}
    passed_retry = {**current_red, "conclusion": "success", "run_attempt": 3, "updated_at": "2026-10-06T02:00:00Z"}
    episodes = metrics.episodes(
        [
            passed_retry,
            green,
            red,
            failed_retry,
            current_red,
            run(3, "failure", "2026-10-07T00:00:00Z"),
            run(4, "success", "2026-10-08T00:00:00Z"),
            run(5, "failure", "2026-10-09T00:00:00Z"),
        ]
    )
    page = metrics.report(
        REPO,
        episodes,
        [],
        set(),
        metrics.timestamp("2026-10-05T00:00:00Z"),
        metrics.timestamp("2026-10-12T00:00:00Z"),
        [{**pull(1, "2026-10-06T00:00:00Z"), "base": {"ref": "main"}}],
    )
    assert "Red → green episodes on main: 1 closed, 1 open" in page
    assert "Re-run passed (flaky): 1" in page
    assert "| 2026-W41 | 1 | 1 | 1 | 100.0 |" in page
    assert f"{REPO}/actions/runs/1)" not in page
    assert f"{REPO}/actions/runs/2)" not in page
    assert f"{REPO}/actions/runs/3)" in page
    assert f"{REPO}/actions/runs/5)" in page


def test_unreleased_revert_is_not_post_release():
    pulls, resolve = fixtures()
    records, unresolved = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: None, REPO)
    assert records == []
    assert unresolved == set()


def test_revert_and_later_hotfix_count_origin_once_in_discovery_month():
    pulls, resolve = fixtures()
    records, unresolved = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO)
    assert len(records) == 2
    unique = metrics.unique_post_release_records(records)
    # October is discovery (the revert merge); August is origin and November is a duplicate hotfix.
    assert [(row["pr"], row["timestamp"][:7]) for row in unique] == [(1, "2026-10")]
    assert unresolved == set()
    page = metrics.report(
        REPO,
        [],
        unique,
        unresolved,
        metrics.timestamp("2026-11-01T00:00:00Z"),
        metrics.timestamp("2026-11-10T00:00:00Z"),
    )
    assert "Post-release reverts and hotfixes: 0" in page


def test_late_discovery_is_counted_with_origin_outside_window():
    pulls, resolve = fixtures()
    del pulls[2]
    records, _ = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO)
    assert records[0]["timestamp"] == "2026-11-02T12:00:00Z"
    page = metrics.report(
        REPO, [], records, set(), metrics.timestamp("2026-11-02T00:00:00Z"), metrics.timestamp("2026-11-10T00:00:00Z")
    )
    assert "Post-release reverts and hotfixes: 1" in page
    assert "| 2026-11 | 1 |" in page


def test_post_release_header_and_months_use_same_in_window_records():
    records = [
        dict(pr=number, commit=SHA[number], timestamp=when, tag="v1.0.0", fix=number + 4)
        for number, when in enumerate(
            ["2026-10-26T11:59:59Z", "2026-10-31T23:00:00Z", "2026-11-01T00:00:00Z", "2026-11-01T01:00:01Z"],
            start=1,
        )
    ]
    page = metrics.report(
        REPO,
        [],
        records,
        set(),
        metrics.timestamp("2026-10-26T12:00:00Z"),
        metrics.timestamp("2026-11-01T01:00:00Z"),
    )
    assert "Post-release reverts and hotfixes: 2" in page
    assert "| 2026-10 | 1 |" in page
    assert "| 2026-11 | 1 |" in page
    assert f"| 2026-10 | [#2](https://github.com/{REPO}/pull/2)" in page
    assert f"| 2026-11 | [#3](https://github.com/{REPO}/pull/3)" in page
    assert f"{REPO}/pull/1)" not in page
    assert f"{REPO}/pull/4)" not in page


def test_unattributed_count_and_links_only_include_prs_merged_in_window():
    pulls = {
        number: pull(number, when, title="Hotfix")
        for number, when in enumerate(
            [
                "2026-10-25T23:59:59Z",
                "2026-10-26T00:00:00Z",
                "2026-11-02T00:00:00Z",
                "2026-11-10T00:00:00Z",
                "2026-11-10T00:00:01Z",
            ],
            start=1,
        )
    }
    records, unresolved = metrics.post_release_records(pulls, {}, lambda sha: None, lambda sha, before: None, REPO)
    page = metrics.report(
        REPO,
        [],
        records,
        unresolved,
        metrics.timestamp("2026-10-26T00:00:00Z"),
        metrics.timestamp("2026-11-10T00:00:00Z"),
        iter(pulls.values()),
    )
    assert "Unattributed revert/hotfix PRs or manual origins (in this window): 3." in page
    assert "all history" not in page
    for number in (2, 3, 4):
        assert f"- [#{number}](https://github.com/{REPO}/pull/{number})" in page
    for number in (1, 5):
        assert f"{REPO}/pull/{number})" not in page


def test_title_only_revert_trailers_and_commit_messages_attribute_origins():
    pulls, resolve = fixtures()
    pulls[2]["body"] = ""
    pulls[3]["title"] = "Restore earlier behavior"
    texts = {3: f"Revert-Ok: {SHA[1]}"}
    records, _ = metrics.post_release_records(pulls, texts, resolve, lambda sha, before: "v1.0.0", REPO)
    assert [(row["pr"], row["fix"]) for row in records] == [(1, 2), (1, 3)]
    numbers, shas = metrics.references(
        f"Reverts acme/tico#1\nRevert-Ok: #2\nRevert-Ok: https://github.com/acme/tico/pull/3\nThis reverts commit {SHA[4]}",
        REPO,
    )
    assert numbers == {1, 2, 3}
    assert shas == {SHA[4]}
    assert metrics.references("Hotfix for another/repo#1\nReverts https://github.com/another/repo/pull/2", REPO) == (
        set(),
        set(),
    )


def test_main_hotfix_lane_requires_explicit_origin_and_lists_unattributed_fix():
    pulls, resolve = fixtures()
    pulls[3].update(title="Repair behavior", body="Fixes #1")
    records, unresolved = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO, {3})
    assert records[-1]["fix"] == 3
    pulls[3]["body"] = "Customer text without an originating PR"
    _, unresolved = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO, {3})
    assert unresolved == {3}


def test_readme_jsonl_files_merge_with_automatic_history_without_text(tmp_path):
    pulls, resolve = fixtures()
    for name, discovered in [
        ("escapes.jsonl", "2026-10-01T01:00:00Z"),
        ("green_but_wrong.jsonl", "2026-11-01T01:00:00Z"),
    ]:
        row = dict(
            timestamp=discovered,
            commit=SHA[1],
            pr=1 if name == "escapes.jsonl" else None,
            description="PRIVATE CUSTOMER TEXT",
            evidence_url="https://private.example/customer",
        )
        (tmp_path / name).write_text(json.dumps(row) + "\n")
    records, _ = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO)
    manual, unresolved = metrics.manual_records(tmp_path, pulls, resolve, lambda sha, before: "v1.0.0")
    assert unresolved == set()
    records += manual
    unique = metrics.unique_post_release_records(records)
    assert len(unique) == 1
    assert unique[0]["timestamp"] == "2026-10-01T01:00:00Z"
    assert unique[0]["source"] == "reports/metrics/escapes.jsonl"
    page = metrics.report(
        REPO, [], unique, set(), metrics.timestamp("2026-09-28T00:00:00Z"), metrics.timestamp("2026-11-10T00:00:00Z")
    )
    assert "PRIVATE" not in page and "private.example" not in page
    assert "reports/metrics/escapes.jsonl" in page
    assert "PRIVATE" not in metrics.html_page(page)
    assert metrics.manual_records(tmp_path, pulls, resolve, lambda sha, before: None) == ([], set())


def test_manual_records_list_unmapped_origin_instead_of_claiming_zero(tmp_path):
    (tmp_path / "escapes.jsonl").write_text(json.dumps(dict(pr=999, timestamp="2026-10-01T00:00:00Z")))
    assert metrics.manual_records(tmp_path, {}, lambda sha: None, lambda sha, before: "v1.0.0") == ([], {999})


def test_containing_tag_must_exist_before_fix_not_just_contain_origin():
    history = object.__new__(metrics.History)
    history.tags = {"v1.0.0": metrics.timestamp("2026-10-02T00:00:00Z")}
    history.containing = {SHA[1]: ["v1.0.0"], SHA[2]: []}
    assert history.shipped(SHA[1], "2026-10-01T00:00:00Z") is None
    assert history.shipped(SHA[1], "2026-10-02T00:00:00Z") is None
    assert history.shipped(SHA[1], "2026-10-03T00:00:00Z") == "v1.0.0"
    assert history.shipped(SHA[2], "2026-10-03T00:00:00Z") is None


def test_missing_shas_never_reach_git_queries_or_crash_collection(monkeypatch, tmp_path):
    pulls, _ = fixtures()
    pulls[2]["base"] = {"ref": "release-1.0"}
    pulls[2]["title"] = "Revert removed change"
    pulls[2]["body"] = f"This reverts commit {SHA[9]}."
    pulls[3]["body"] = f"Regression from commit {SHA[9]}"
    monkeypatch.setattr(metrics, "ROOT", tmp_path)
    directory = tmp_path / "reports/metrics"
    directory.mkdir(parents=True)
    (directory / "escapes.jsonl").write_text(json.dumps(dict(commit=SHA[9], timestamp=pulls[3]["merged_at"])))
    calls = []

    def git(args, **kwargs):
        assert kwargs["cwd"] == "fixture-repository"
        calls.append(args[1:])
        if args[1] == "cat-file":
            return SimpleNamespace(returncode=1, stdout="")
        raise AssertionError(args)

    monkeypatch.setattr(metrics.subprocess, "run", git)
    history = object.__new__(metrics.History)
    history.cwd = "fixture-repository"
    history.tags = {"v1.0.0": metrics.timestamp("2026-09-01T00:00:00Z")}
    history.containing = {}
    monkeypatch.setattr(history, "out", lambda *args: "" if args[0] == "log" else pytest.fail(str(args)))

    class Api:
        repo = REPO

        def pages(self, path):
            raise AssertionError(path)

    assert history.containing_tags(SHA[9]) == []
    assert history.shipped(SHA[9], pulls[3]["merged_at"]) is None
    records, unresolved = metrics.collect(Api(), history, pulls)
    assert records == []
    assert unresolved == {2, SHA[9]}
    assert all(args[0] == "cat-file" for args in calls)
    page = metrics.report(
        REPO,
        [],
        records,
        unresolved,
        metrics.timestamp("2026-10-01T00:00:00Z"),
        metrics.timestamp("2026-11-10T00:00:00Z"),
    )
    assert f"Commit {SHA[9]}" not in page


def test_commit_queries_check_existence_in_repository_before_reading(monkeypatch):
    calls = []

    def git(args, **kwargs):
        assert kwargs["cwd"] == "fixture-repository"
        calls.append(args[1:])
        return SimpleNamespace(returncode=0, stdout="v1.0.0\n" if args[1] == "tag" else SHA[1] + "\n")

    monkeypatch.setattr(metrics.subprocess, "run", git)
    history = object.__new__(metrics.History)
    history.cwd = "fixture-repository"
    history.tags = {"v1.0.0": metrics.timestamp("2026-09-01T00:00:00Z")}
    history.containing = {}
    assert history.containing_tags(SHA[1]) == ["v1.0.0"]
    assert history.commit_out(SHA[1], "rev-parse", "--verify").strip() == SHA[1]
    assert calls == [
        ["cat-file", "-e", SHA[1] + "^{commit}"],
        ["tag", "--list", "v[0-9]*", "--contains", SHA[1]],
        ["cat-file", "-e", SHA[1] + "^{commit}"],
        ["rev-parse", "--verify", SHA[1]],
    ]


def test_weekly_merges_rates_and_open_episode_before_window():
    episodes = metrics.episodes([run(1, "failure", "2026-10-01T00:00:00Z")])
    since, now = metrics.timestamp("2026-10-05T00:00:00Z"), metrics.timestamp("2026-10-12T00:00:00Z")
    pulls = [
        {**pull(1, "2026-10-06T00:00:00Z"), "base": {"ref": "main"}},
        {**pull(2, "2026-10-07T00:00:00Z"), "base": {"ref": "main"}},
        {**pull(3, "2026-10-07T00:00:00Z"), "base": {"ref": "release-1.0"}},
        {**pull(4, "2026-10-04T23:00:00Z"), "base": {"ref": "main"}},
        {**pull(5, "2026-10-13T00:00:00Z"), "base": {"ref": "main"}},
    ]
    page = metrics.report(REPO, episodes, [], set(), since, now, pulls)
    assert "0 closed, 1 open" in page
    assert "| 2026-W40 | open |" in page
    assert "| 2026-W41 | 0 | 0 | 2 | 0.0 |" in page
    closed = metrics.episodes(
        [
            run(1, "failure", "2026-10-05T01:00:00Z"),
            run(2, "success", "2026-10-06T00:00:00Z"),
            run(3, "failure", "2026-10-08T00:00:00Z"),
        ]
    )
    page = metrics.report(REPO, closed, [], set(), since, now, pulls)
    assert "| 2026-W41 | 1 | 1 | 2 | 50.0 |" in page
    assert "| 2026-W42 | 0 | 0 | 0 | — |" in page


def test_confirmed_customer_report_keeps_confirmation_date_after_earlier_revert(tmp_path):
    pulls, resolve = fixtures()
    (tmp_path / "escapes.jsonl").write_text(
        json.dumps(dict(pr=1, timestamp="2026-11-02T00:00:00Z", confirmed_customer=True, description="PRIVATE")) + "\n"
    )
    manual, unresolved = metrics.manual_records(tmp_path, pulls, resolve, lambda sha, before: "v1.0.0")
    automatic, _ = metrics.post_release_records(pulls, {}, resolve, lambda sha, before: "v1.0.0", REPO)
    records = metrics.unique_post_release_records(automatic + manual + manual)
    assert len(records) == 1
    assert records[0]["timestamp"] == "2026-10-31T23:00:00Z"
    page = metrics.report(
        REPO,
        [],
        records,
        unresolved,
        metrics.timestamp("2026-11-01T00:00:00Z"),
        metrics.timestamp("2026-11-10T00:00:00Z"),
    )
    assert "Post-release reverts and hotfixes: 0" in page
    assert "Confirmed customer reports: 1. [#1]" in page
    assert "PRIVATE" not in page


def test_lightweight_tag_uses_publication_not_commit_date(monkeypatch):
    def out(self, *args):
        if args[0] == "rev-parse":
            return "false\n"
        if args[0] == "for-each-ref":
            return "v1.0.0 \nv1.0.1 2026-10-02T00:00:00Z\nv1.0.2 \n"
        raise AssertionError(args)

    monkeypatch.setattr(metrics.History, "out", out)
    history = metrics.History(
        "unused",
        [dict(tag_name="v1.0.0", published_at="2026-10-01T00:00:00Z", created_at="2026-08-01T00:00:00Z", draft=False)],
    )
    assert history.tags == {
        "v1.0.0": metrics.timestamp("2026-10-01T00:00:00Z"),
        "v1.0.1": metrics.timestamp("2026-10-02T00:00:00Z"),
    }


def test_release_branch_and_tagged_cherry_pick_fixes_use_origin_pr(monkeypatch, tmp_path):
    pulls, _ = fixtures()
    pulls[2].update(title="Repair behavior", body="Fixes #1", base={"ref": "release-1.0"})
    pulls[3].update(title="Repair behavior", body="Fixes #1")
    monkeypatch.setattr(metrics, "ROOT", tmp_path)
    calls = []

    class Api:
        repo = REPO

        def pages(self, path):
            raise AssertionError(path)

    class History:
        cwd = "unused"

        def out(self, *args):
            calls.append(args)
            if args[0] == "log":
                return f"{SHA[4]}\0Repair behavior (#3)\0(cherry picked from commit {SHA[3]})\0\n"
            if args[0] == "rev-parse":
                return args[-1].split("^")[0]
            if args[0] == "show":
                return f"Repair behavior (#3)\n(cherry picked from commit {SHA[3]})"
            raise AssertionError(args)

        def commit_out(self, sha, *args):
            return self.out(*args, sha)

        def containing_tags(self, sha):
            return ["v1.0.1"]

        def shipped(self, sha, before):
            return "v1.0.0"

    records, unresolved = metrics.collect(Api(), History(), pulls)
    assert unresolved == set()
    assert [(row["pr"], row["fix"]) for row in records] == [(1, 2)]
    # The tagged cherry-pick was resolved to PR #3, then deduped against the earlier release-branch fix.
    assert ("show", "-s", "--format=%s%n%b", SHA[4]) in calls


def test_missing_cherry_pick_origin_uses_guard_before_api_fallback(monkeypatch, tmp_path):
    pulls, _ = fixtures()
    monkeypatch.setattr(metrics, "ROOT", tmp_path)
    body = f"(cherry picked from commit {SHA[9]})"
    calls = []

    def git(args, **kwargs):
        calls.append(args[1:])
        command = args[1]
        if command == "cat-file":
            return SimpleNamespace(returncode=int(args[-1].startswith(SHA[9])), stdout="")
        if command == "rev-parse":
            return SimpleNamespace(stdout=args[-1])
        if command == "show":
            return SimpleNamespace(stdout=f"Repair behavior\n{body}")
        if command == "log":
            assert args[2] == "origin/main"
            return SimpleNamespace(stdout=f"{SHA[4]}\0Repair behavior\0{body}\0")
        if command == "tag":
            return SimpleNamespace(stdout="")
        raise AssertionError(args)

    monkeypatch.setattr(metrics.subprocess, "run", git)
    history = object.__new__(metrics.History)
    history.cwd = "fixture-repository"
    history.tags = {}
    history.containing = {}

    class Api:
        repo = REPO

        def pages(self, path):
            assert path == f"commits/{SHA[4]}/pulls"
            return [{"number": 3}]

    assert metrics.collect(Api(), history, pulls) == ([], set())
    assert ["cat-file", "-e", SHA[9] + "^{commit}"] in calls
    assert not any(args[0] == "log" and args[-1] == SHA[9] for args in calls)


def test_validation_history_extends_before_window_and_includes_rerun_attempts():
    calls = []

    class Api:
        def runs(self, workflow, since, now):
            calls.append((since, now))
            if len(calls) == 1:
                return [run(2, "success", "2026-11-02T00:00:00Z")]
            return [run(1, "success", "2026-09-01T00:00:00Z", run_attempt=2)]

        def get(self, path):
            assert path == "actions/runs/1/attempts/1"
            return run(1, "failure", "2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z", run_attempt=1)

    runs = metrics.validation_runs(
        Api(),
        metrics.timestamp("2026-01-01T00:00:00Z"),
        metrics.timestamp("2026-11-02T00:00:00Z"),
        metrics.timestamp("2026-11-03T00:00:00Z"),
    )
    assert len(calls) == 2
    assert len(runs) == 3
    assert calls[1][1] < calls[0][0]


def test_github_pages_and_date_slices_are_bounded(monkeypatch):
    api = metrics.GitHub(REPO)
    calls = []

    def get(path, **params):
        calls.append((path, params))
        return [dict(number=i) for i in range(100)] if params["page"] == 1 else [dict(number=101)]

    monkeypatch.setattr(api, "get", get)
    assert len(api.pages("pulls", state="closed")) == 101
    assert calls[-1][1]["page"] == 2
    calls.clear()

    def pages(path, key, **params):
        calls.append(params["created"])
        return []

    monkeypatch.setattr(api, "pages", pages)
    api.runs("ci.yml", metrics.timestamp("2026-10-01T00:00:00Z"), metrics.timestamp("2026-11-01T00:00:00Z"))
    assert len(calls) == 2
    api.calls = 250
    fresh = metrics.GitHub(REPO)
    fresh.calls = 250
    with pytest.raises(ValueError, match="budget"):
        fresh.get("pulls")


def test_markdown_and_html_show_separate_numbers_and_safe_links():
    fixed = metrics.episodes([run(1, "failure", "2026-10-01T00:00:00Z"), run(2, "success", "2026-10-05T00:00:00Z")])
    page = metrics.report(
        REPO, fixed, [], {3}, metrics.timestamp("2026-10-05T00:00:00Z"), metrics.timestamp("2026-10-12T00:00:00Z")
    )
    assert "Red → green episodes on main: 1 closed, 0 open" in page and "Post-release reverts and hotfixes: 0" in page
    assert "| 2026-W41 | 1 | 0 | 0 | — |" in page and "| 2026-W42 | 0 | 0 | 0 | — |" in page
    rendered = metrics.html_page(page)
    assert '<a href="https://github.com/acme/tico/actions/runs/1">1</a>' in rendered
    assert rendered.count("<table>") == rendered.count("</table>")
    assert "&lt;script&gt;" in metrics.html_page("# <script>")


def test_revert_ok_trailer_with_a_reason_names_its_origin():
    numbers, shas = metrics.references("Revert-Ok: #12 intentional rollback\nRevert-Ok: abc1234 bad deploy", REPO)
    assert numbers == {12}
    assert shas == {"abc1234"}


def test_unmapped_manual_origin_stays_listed():
    page = metrics.report(
        REPO,
        [],
        [],
        {"0123abc", 2},
        metrics.timestamp("2026-11-02T00:00:00Z"),
        metrics.timestamp("2026-11-10T00:00:00Z"),
        [pull(2, merged="2026-08-01T12:00:00Z")],
    )
    assert "(in this window): 1." in page
    assert "- manual record 0123abc" in page
    assert "pull/2)" not in page


QUEUE_SHA = "a" * 40


def queue_run(number, pr, conclusion, created, base="main"):
    return dict(
        id=number,
        status="completed",
        conclusion=conclusion,
        created_at=created,
        head_branch=f"gh-readonly-queue/{base}/pr-{pr}-{QUEUE_SHA}",
    )


def timeline(number, *events, merged=None, base="main"):
    nodes = [
        dict(__typename="AddedToMergeQueueEvent", createdAt=when)
        if why is None
        else dict(__typename="RemovedFromMergeQueueEvent", createdAt=when, reason=why)
        for when, why in events
    ]
    return dict(number=number, mergedAt=merged, baseRefName=base, timelineItems=dict(nodes=nodes))


def test_queue_outcomes_read_cancelled_runs_from_the_queue_removal_record():
    runs = [
        # PR 1: its first run is cancelled by fail-fast and the queue removes it for failed checks;
        # the second run passes.
        queue_run(1, 1, "cancelled", "2026-11-16T10:00:00Z"),
        queue_run(2, 1, "success", "2026-11-16T11:00:00Z"),
        # PR 2: cancelled and rebuilt behind a failing group, no removal before its next run.
        queue_run(3, 2, "cancelled", "2026-11-16T10:01:00Z"),
        queue_run(4, 2, "cancelled", "2026-11-16T10:20:00Z"),
        # PR 3: a run that ended failure with no removal read is still a failure.
        queue_run(5, 3, "failure", "2026-11-16T10:02:00Z"),
        # PR 4: taken out by hand.
        queue_run(6, 4, "cancelled", "2026-11-16T10:03:00Z"),
        # PR 5: its queue events were not read.
        queue_run(7, 5, "cancelled", "2026-11-16T10:04:00Z"),
        # Not counted: a release branch queue and a run still going.
        queue_run(8, 6, "cancelled", "2026-11-16T10:05:00Z", base="release"),
        dict(queue_run(9, 1, None, "2026-11-16T12:00:00Z"), status="in_progress"),
    ]
    timelines = {
        1: timeline(1, ("2026-11-16T09:59:00Z", None), ("2026-11-16T10:06:00Z", "FAILED_CHECKS")),
        # PR 2's later removal comes after its second run started, so only that run carries it.
        2: timeline(2, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:30:00Z", "merge_conflict")),
        3: timeline(3, ("2026-11-16T10:00:00Z", None)),
        4: timeline(4, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:04:00Z", "manual")),
    }
    outcomes = {run["id"]: outcome for run, outcome in metrics.queue_outcomes(runs, timelines)}
    assert outcomes == {
        1: "failed",
        2: "passed",
        3: "superseded",
        4: "removed",
        5: "failed",
        6: "removed",
        7: "not read",
    }


def test_queue_merges_time_from_first_and_last_enqueue_and_count_throw_outs():
    since, now = metrics.timestamp("2026-11-16T00:00:00Z"), metrics.timestamp("2026-11-22T23:59:59Z")
    timelines = {
        1: timeline(
            1,
            ("2026-11-16T10:00:00Z", None),
            ("2026-11-16T10:10:00Z", "failed_checks"),
            ("2026-11-16T11:00:00Z", None),
            ("2026-11-16T11:15:00Z", "merged"),
            merged="2026-11-16T11:15:00Z",
        ),
        2: timeline(
            2, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:12:00Z", "merged"), merged="2026-11-16T10:12:00Z"
        ),
        # Merged without the queue, into a release branch, or before the window: not counted.
        3: timeline(3, merged="2026-11-16T10:00:00Z"),
        4: timeline(4, ("2026-11-16T10:00:00Z", None), merged="2026-11-16T10:05:00Z", base="release"),
        5: timeline(5, ("2026-11-15T10:00:00Z", None), merged="2026-11-15T10:05:00Z"),
        6: timeline(6, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:05:00Z", "manual")),
        # Taken out of the queue by hand, then merged directly: queue events, but the queue did not merge it.
        7: timeline(
            7, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:05:00Z", "manual"), merged="2026-11-16T11:00:00Z"
        ),
    }
    merges = {row["number"]: row for row in metrics.queue_merges(timelines, since, now)}
    assert set(merges) == {1, 2}
    assert (merges[1]["first"], merges[1]["last"], merges[1]["thrown_out"]) == (75, 15, True)
    assert (merges[2]["first"], merges[2]["last"], merges[2]["thrown_out"]) == (12, 12, False)


def test_queue_report_rows_give_weekly_shares_and_nearest_rank_p90():
    since, now = metrics.timestamp("2026-11-16T00:00:00Z"), metrics.timestamp("2026-11-23T12:00:00Z")
    runs = [queue_run(i, i, "success", "2026-11-16T10:00:00Z") for i in range(1, 10)]
    runs.append(queue_run(10, 10, "cancelled", "2026-11-16T10:00:00Z"))
    runs.append(queue_run(11, 11, "success", "2026-11-15T10:00:00Z"))  # before the window
    merged = {i: f"2026-11-16T{10 + i // 6}:{i * 5 % 60:02d}:00Z" for i in range(1, 10)}
    # A merge is a removal event too; it is not counted among the removals.
    timelines = {
        i: timeline(i, ("2026-11-16T10:00:00Z", None), (merged[i], "merged"), merged=merged[i]) for i in range(1, 10)
    }
    timelines[10] = timeline(10, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:03:00Z", "failed_checks"))
    # A release branch queue's removal stays out of main's removal counts.
    timelines[12] = timeline(12, ("2026-11-16T10:00:00Z", None), ("2026-11-16T10:04:00Z", "manual"), base="release")
    lines = metrics.queue_report(runs, timelines, since, now)
    rows = [line for line in lines if line.startswith("| 2026-W")]
    # Merge minutes 5, 10, 15, 20, 25, 90, 95, 100, 105: p50 is the 5th of 9, p90 the 9th (nearest rank).
    assert rows[0] == (
        "| 2026-W47 | 10 | 9 (90.0%) | 1 (10.0%) | 0 (0.0%) | 0 (0.0%) | 9 | 25 / 105 | 105 | 0 (0.0%) | failed_checks 1 |"
    )
    assert rows[1] == "| 2026-W48 | 0 | 0 | 0 | 0 | 0 | 0 | — / — | — | 0 | — |"
    assert not any(line.startswith("Not read") for line in lines)


def test_unread_queue_is_named_and_the_rest_of_the_report_stands(monkeypatch):
    class Refused(metrics.GitHub):
        def queue_runs(self, since, now):
            raise ValueError("GitHub read failed: actions/runs")

    monkeypatch.setattr(metrics, "GitHub", Refused)
    now = metrics.timestamp("2026-11-18T12:00:00Z")
    queue = metrics.queue_health(REPO, now, 4)
    assert queue == "GitHub read failed: actions/runs"
    markdown = metrics.report(REPO, [], [], set(), now - metrics.timedelta(weeks=1), now, queue=queue)
    assert "## Red → green episodes on main" in markdown
    assert markdown.endswith("## Merge queue on main\n\nNot read: GitHub read failed: actions/runs.\n")


def test_queue_timelines_batch_pull_requests_into_aliased_queries(monkeypatch):
    queries = []

    def graphql(self, query):
        queries.append(query)
        numbers = [int(n) for n in re.findall(r"pullRequest\(number: (\d+)\)", query)]
        return {"repository": {f"p{n}": timeline(n) for n in numbers} | {"p0": None}}

    monkeypatch.setattr(metrics.GitHub, "graphql", graphql)
    found = metrics.GitHub(REPO).queue_timelines(range(1, metrics.TIMELINE_BATCH + 2))
    assert len(queries) == 2 and 'repository(owner: "acme", name: "tico")' in queries[0]
    assert sorted(found) == list(range(1, metrics.TIMELINE_BATCH + 2))
