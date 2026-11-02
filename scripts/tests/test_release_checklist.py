"""Release evidence is tied to the merged PR head and its own ticket, never a nearby pass."""
from copy import deepcopy
import json

import pytest

from scripts import release_checklist as checklist

HEAD, OLD, BASE = "a" * 40, "b" * 40, "c" * 40
PR = {"number": 12, "head": HEAD, "merge_commit": "d" * 40, "merged_at": "2026-01-02T00:00:00Z",
      "url": "https://github.com/acme/product/pull/12", "title": "Improve tasks"}


def message(task, body, *, actor=None, ident="receipt", created="2026-01-02T00:00:00Z", **fields):
    return {"id": ident, "body": body, "from_actor": actor or task["owner_id"], "created_at": created,
            "refs": {"task": task["id"]}, **fields}


def child(role, *, head=HEAD, verdict="approve"):
    task = {"id": role, "owner": "bot:checker", "title": f"{role}: PR #12", "status": "done", "links": []}
    task["messages"] = [message(task, f"VERDICT: {verdict} PR #12 head {head}")]
    return task


def complete():
    task = {"id": "dev-ticket", "number": 7, "title": "Improve tasks", "owner": "bot:builder",
            "step": {"name": "Pending Release"}, "tags": [{"key": "qa", "label": "QA"}],
            "links": [{"kind": "pr", "url": PR["url"]}],
            "children": [child("Review 1"), child("Review 2"), child("QA"), child("Docs")]}
    task["messages"] = [message(task, f"Tests: base {BASE} head {HEAD}: 10 passed, 0 failed")]
    task["children"][-1]["messages"] = [message(task["children"][-1], "Help updated; docs PR merged.")]
    return task


def build(task):
    return checklist.build([PR], [task], head=HEAD, previous="v1.0.0", previous_sha=BASE)


def test_complete_qa_ticket_has_no_gap_and_outputs_evidence():
    result = build(complete())
    assert result["gaps"] == 0
    text = checklist.markdown(result)
    assert "Pending Release" in text and f"approve {HEAD}" in text
    assert "10 passed, 0 failed" in text and "Help updated" in text
    assert "Outside the release pipeline" in text
    assert result["prs"][0]["tickets"][0]["qa"] is True


@pytest.mark.parametrize(("role", "replacement", "gap"), [
    ("Review 1", [], "Review 1: missing verdict"),
    ("Review 1", [child("Review 1", head=OLD)], "Review 1: verdict names older/different head"),
    ("QA", [], "QA: missing verdict"),
])
def test_missing_review_stale_head_and_qa_without_pass_are_flagged(role, replacement, gap):
    task = complete()
    task["children"] = [c for c in task["children"] if c["id"] != role] + deepcopy(replacement)
    assert gap in "\n".join(build(task)["prs"][0]["gaps"])


def test_latest_owner_verdict_wins_and_quoted_or_removed_approvals_cannot_clear_it():
    task = complete()
    review = task["children"][0]
    review["messages"] += [
        message(review, f"VERDICT: changes PR #12 head {HEAD}", ident="changes", created="2026-01-03"),
        message(review, f"VERDICT: approve PR #12 head {HEAD}", actor="bot:builder", created="2026-01-04"),
        message(review, f"VERDICT: approve PR #12 head {HEAD}", hidden_at="2026-01-05", created="2026-01-05"),
    ]
    assert "Review 1: changes requested" in "\n".join(build(task)["prs"][0]["gaps"])
    # A Review 2 task with no verdict is required, even though Review 2 is otherwise optional.
    task["children"][1]["messages"] = []
    assert "Review 2: missing verdict" in "\n".join(build(task)["prs"][0]["gaps"])


def test_wrong_head_pass_and_new_head_failure_do_not_combine_into_passing_receipt():
    assert not checklist.passing_test(f"Tests at {HEAD} will pass after the fix", HEAD)
    assert not checklist.passing_test(f"Tests at {HEAD}: 0 passed, 5 skipped", HEAD)
    task = complete()
    task["messages"] = [message(task, f"Tests {OLD}: 10 passed\nTests {HEAD}: 2 passed, 1 failed")]
    task["children"][-1]["status"] = "doing"
    gaps = "\n".join(build(task)["prs"][0]["gaps"])
    assert "Tests: recorded failure" in gaps and "Docs:" not in gaps
    task["tags"] = []
    task["children"] = [task["children"][0]]
    task["messages"] = complete()["messages"]
    assert build(task)["gaps"] == 0  # internal ticket skips QA, docs and Review 2
    task["links"] = []
    assert build(task)["prs"][0]["gaps"] == ["No visible linked Dev ticket"]


def test_collection_pages_history_and_keeps_only_the_named_task(monkeypatch):
    task = complete()
    pages = iter([
        {"messages": [message(task, "new", ident="new"), {"refs": {"task": "unrelated"}}],
         "has_more": True, "next_before": "cursor"},
        {"messages": [message(task, "old", ident="old")], "has_more": False},
    ])
    calls = []

    def hub(*args):
        calls.append(args)
        return next(pages)

    monkeypatch.setattr(checklist, "hub", hub)
    task["conversation_id"] = "shared-room"
    assert [m["body"] for m in checklist.history(task)] == ["new", "old"]
    assert calls[-1][-2:] == ("--before", "cursor")
    monkeypatch.setattr(checklist, "hub", lambda *args: {"messages": [], "has_more": True})
    with pytest.raises(checklist.freeze.GuardError, match="history incomplete"):
        checklist.history(task)


def test_collect_limits_to_candidate_range_and_reads_cloud_tickets(monkeypatch):
    task = complete()
    calls = []
    linked_tickets = checklist.linked_tickets
    monkeypatch.setattr(checklist.freeze, "git", lambda repo, *args: HEAD if args[-1] == "candidate^{commit}" else BASE)
    monkeypatch.setattr(checklist.freeze, "first_parent_log", lambda repo, revisions: calls.append(revisions) or [
        {"sha": PR["merge_commit"], "touched": {"app.py"}, "subject": "Change (#12)", "body": ""},
        {"sha": "e" * 40, "touched": {"CHANGELOG.md"}, "subject": "Release notes", "body": ""},
    ])
    monkeypatch.setattr(checklist, "command", lambda *args: json.dumps({
        "head": {"sha": HEAD}, "title": PR["title"], "html_url": PR["url"], "merged_at": PR["merged_at"]}))
    monkeypatch.setattr(checklist, "linked_tickets", lambda slug, number: [task["id"]])
    monkeypatch.setattr(checklist, "collect_ticket", lambda tid: task)
    result = checklist.collect(".", "candidate", "v1.0.0", "acme/product")
    assert calls == [f"{BASE}..{HEAD}"] and [p["number"] for p in result["prs"]] == [12]
    assert result["gaps"] == 0
    monkeypatch.setattr(checklist, "hub", lambda *args: {"rows": [], "truncated": True})
    # Query truncation must fail collection rather than claim there are no tickets.
    with pytest.raises(checklist.freeze.GuardError, match="incomplete"):
        # Use the original helper, replaced above only to isolate PR collection.
        linked_tickets("acme/product", 12)


def test_cli_exits_distinguish_a_clean_checklist_gaps_and_collection_failure(monkeypatch, capsys):
    result = build(complete())
    monkeypatch.setattr(checklist, "collect", lambda *args: result)
    assert checklist.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gaps"] == 0
    result["gaps"] = 1
    assert checklist.main([]) == 1
    assert "1 evidence gaps" in capsys.readouterr().out

    def unavailable(*args):
        raise checklist.freeze.GuardError("ticket history incomplete")

    monkeypatch.setattr(checklist, "collect", unavailable)
    assert checklist.main([]) == 2
    output = capsys.readouterr()
    assert not output.out and "history incomplete" in output.err


def test_same_head_failure_stays_explicit_even_after_a_passing_rerun():
    task = complete()
    task["messages"] += [
        message(task, f"Tests: head {HEAD}: 0 passed, 1 failed", ident="failed", created="2026-01-03"),
        message(task, f"Tests: head {HEAD}: 10 passed, 0 failed", ident="rerun", created="2026-01-04"),
    ]
    assert "Tests: recorded failure" in "\n".join(build(task)["prs"][0]["gaps"])
    for failure in ("test_x failing", "browser job timed out"):
        task["messages"] = complete()["messages"] + [
            message(task, f"Tests: head {HEAD}: {failure}", ident="failed", created="2026-01-03"),
            message(task, f"Tests: head {HEAD}: 10 passed, 0 failed", ident="rerun", created="2026-01-04"),
        ]
        assert "Tests: recorded failure" in "\n".join(build(task)["prs"][0]["gaps"])
    task["messages"] = complete()["messages"] + [
        message(task, f"CI: head {HEAD}: pending", ident="pending", created="2026-01-03")]
    assert "Tests: no current passing receipt" in "\n".join(build(task)["prs"][0]["gaps"])
    task["messages"] = complete()["messages"] + [
        message(task, f"Tests: head {OLD}: 0 passed, 1 failed", ident="old", created="2026-01-03")]
    assert build(task)["gaps"] == 0


@pytest.mark.parametrize('where', ['note', 'keeper', 'review-child', 'review-child-builder'])
def test_receipts_are_collected_from_real_ticket_sources(where):
    task = complete()
    task['messages'] = []
    text = f'Tests: base {BASE} head {HEAD}: 356 pass / 0 fail'
    if where == 'note':
        task.update(note=text, updated_at='2026-01-04')
    elif where == 'keeper':
        task['messages'] = [message(task, 'Ready for review. ' + text, actor='keeper')]
    else:
        child = task['children'][0]
        child['messages'].append(message(child, text, actor=task['owner_id'] if where.endswith('builder') else child['owner_id']))
    result = build(task)
    assert result['gaps'] == 0
    evidence = result['prs'][0]['tickets'][0]['test_receipts']
    assert len(evidence) == 1 and evidence[0]['status'] == 'passed'
    assert text in checklist.markdown(result)


def test_unrecognised_missing_failure_and_base_head_are_distinct():
    task = complete()
    task['messages'] = []
    assert 'unrecognised' not in '\n'.join(build(task)['prs'][0]['gaps'])
    task['note'] = f'Tests: base {BASE} head {HEAD}: results attached'
    assert 'unrecognised receipt' in '\n'.join(build(task)['prs'][0]['gaps'])
    task['note'] = f'Tests: base {BASE} head {HEAD}: 40 passed, 2 failing/errored'
    assert 'recorded failure' in '\n'.join(build(task)['prs'][0]['gaps'])
    task['note'] = f'Tests: base {HEAD} head {OLD}: 9 passed, 0 failed'
    assert 'no current passing receipt' in '\n'.join(build(task)['prs'][0]['gaps'])


def test_mixed_note_scopes_failures_and_pending_to_the_named_head_result():
    task = complete()
    task['messages'] = []
    text = (f'Review 1: receipt gate implemented. Tests: base {BASE} head {HEAD}: '
            '185 passed, 0 failed, 0 skipped; exit 0, 78.10s. '
            'Two original existing-fixture failures were corrected before final validation. '
            'Independent Review 1 and hosted checks pending. '
            'Full suite, browsers/native builds and installed behavior not run.')
    task.update(note=text, updated_at='2026-01-04')
    assert build(task)['gaps'] == 0
    task['note'] = text.replace('185 passed, 0 failed', '183 passed, 2 failing/errored')
    assert 'Tests: recorded failure' in '\n'.join(build(task)['prs'][0]['gaps'])
    task['messages'] = [message(task, f'Tests: head {HEAD}: 185 passed, 0 failed', created='2026-01-05')]
    assert 'Tests: recorded failure' in '\n'.join(build(task)['prs'][0]['gaps'])


def test_review_two_recheck_title_is_not_mistaken_for_an_optional_absent_review():
    task = complete()
    task['children'][1]['title'] = 'Review 2 re-check: PR #12'
    task['children'][1]['messages'] = []
    assert 'Review 2: missing verdict' in '\n'.join(build(task)['prs'][0]['gaps'])
