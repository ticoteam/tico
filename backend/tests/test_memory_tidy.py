"""Pure synthetic snapshots: no live repository, credentials or database."""
import json
from datetime import date, datetime, timezone

import pytest

from backend.memory_tidy import ARCHIVE, LEARNINGS, build_packet, evaluate, parse

TODAY = date(2026, 11, 1)


def snapshot(log="", heading="# Working rules"):
    return {"AGENT.md": "Never publish without approval.\n", LEARNINGS:
            heading + "\n" + "".join(f"- Rule {n}. [source: 2026-10-{n+1:02}]\n" for n in range(8)) + log,
            "memory/evidence/private.txt": "Do not send to the child.",
            "knowledge/fact.md": "Outside text must not reach the child.", "state.md": "untouched\n"}


def proposal(snap, op="generalise", count=1, replacement=None, sources=True, **extra):
    entries = parse(snap[LEARNINGS]).rules[:count]
    ids = [e.entry_id for e in entries]
    return {"edits": [{"number": 1, "operation": op,
             "targets": [{"entry_id": e.entry_id, "old_text": e.text} for e in entries],
             "replacement": replacement if replacement is not None else "- General rule. [sources: " + ", ".join(ids) + "]\n",
             "sources": ids if sources else [], **extra}], "diary": "One generalisation."}


def run(snap, obj, **kw):
    return evaluate(snap, json.dumps(obj), since=None, today=TODAY,
                    checkout_before={"AGENT.md": "a", LEARNINGS: "b"},
                    checkout_after=kw.pop("checkout_after", {"AGENT.md": "a", LEARNINGS: "b"}),
                    conflict_check=kw.pop("conflict_check", lambda agent, rules: []), **kw)


def test_scratch_diff_preserves_originals_and_input_is_unchanged():
    snap = snapshot()
    original = dict(snap)
    result = run(snap, proposal(snap, count=2))
    assert result.status == "offer"
    assert snap == original
    for entry in parse(snap[LEARNINGS]).rules[:2]:
        assert entry.text in result.files[ARCHIVE]
        assert entry.text not in result.files[LEARNINGS]
    assert result.files["AGENT.md"] == snap["AGENT.md"]
    assert "a/memory/learnings.md" in result.diff
    assert len(result.diary.splitlines()) == 3
    assert "20,000" in result.diary
    assert json.loads(result.edits_json)["edits"][0]["number"] == 1


def test_quarter_is_allowed_but_more_is_flagged():
    snap = snapshot()
    assert run(snap, proposal(snap, count=2)).status == "offer"
    result = run(snap, proposal(snap, count=3))
    assert result.status == "flagged" and any(f.startswith("5:") for f in result.flags)
    assert not result.files and not result.diff and not result.edits_json


@pytest.mark.parametrize("change", ["sources", "old_text", "number", "repeated", "outside_source", "whole_file"])
def test_bad_anchors_and_provenance_cannot_offer(change):
    snap = snapshot()
    obj = proposal(snap)
    edit = obj["edits"][0]
    if change == "sources":
        edit["sources"] = []
    elif change == "old_text":
        edit["targets"][0]["old_text"] = snap[LEARNINGS]
    elif change == "number":
        edit["number"] = True
    elif change == "repeated":
        obj["edits"].append({**edit, "number": 2})
    elif change == "outside_source":
        edit["sources"].append("run transcript")
    else:
        edit["replacement"] += "# New instructions\n- Another rule\n"
    assert run(snap, obj).status == "flagged"


@pytest.mark.parametrize("path", ["AGENT.md", LEARNINGS, "untracked.txt", "knowledge/link"])
def test_every_child_mutation_discards_proposal_including_allowed_paths(path):
    snap = snapshot()
    after = {"AGENT.md": "a", LEARNINGS: "b", path: "changed-mode-or-content-or-link"}
    result = run(snap, proposal(snap), checkout_after=after)
    assert result.status == "flagged" and any(f.startswith("8:") for f in result.flags)
    assert not result.files


@pytest.mark.parametrize("dest", ["AGENT.md", "knowledge/../AGENT.md", "/knowledge/fact.md",
                                   "memory/fact.md", "knowledge//fact.md", "knowledge/evil\\file.md"])
def test_destination_cannot_escape(dest):
    snap = snapshot()
    entry = parse(snap[LEARNINGS]).rules[0]
    assert run(snap, proposal(snap, "move_fact", replacement=entry.text, destination=dest)).status == "flagged"


def test_domain_fact_moves_verbatim_and_retires_leave_a_marker():
    snap = snapshot()
    entry = parse(snap[LEARNINGS]).rules[0]
    result = run(snap, proposal(snap, "move_fact", replacement=entry.text, destination="knowledge/domain.md"))
    assert result.status == "offer" and entry.text in result.files["knowledge/domain.md"]
    result = run(snap, proposal(snap, "retire", replacement=""))
    assert result.status == "offer" and "retired 2026-11-01, replaced by edit 1" in result.files[ARCHIVE]


def test_old_log_is_required_and_moves_verbatim_without_counting_as_rule_removal():
    snap = snapshot("\n## 2026-09-30 — old\nOld paragraph.\n- A detail.\n\n## 2026-10-02 — boundary\nKeep me.\n")
    packet = build_packet(snap, since=None)
    old, boundary = packet.all_logs
    assert "A detail" in old.text
    assert len(packet.rules) == 8
    assert any(f.startswith("10:") for f in run(snap, proposal(snap)).flags)
    edit = {"number": 1, "operation": "archive_log", "targets": [{"entry_id": old.entry_id, "old_text": old.text}],
            "replacement": "", "sources": [old.entry_id]}
    result = run(snap, {"edits": [edit], "diary": "Archived one."})
    assert result.status == "offer"
    assert old.text in result.files[ARCHIVE] and old.text not in result.files[LEARNINGS]
    assert boundary.text in result.files[LEARNINGS]


def test_read_packet_is_only_rules_recent_and_cited_logs():
    snap = snapshot("\n## 2026-10-01 — cited\nCited.\n## 2026-09-01 — not cited\nHidden.\n"
                    "## 2026-11-01 — new\nRecent.\n")
    packet = build_packet(snap, since=datetime(2026, 11, 1, tzinfo=timezone.utc))
    text = json.dumps(packet.model_input())
    assert "Cited." in text and "Recent." in text and "Hidden." not in text
    assert "Do not send" not in text and "Outside text" not in text and "Never publish" not in text
    # Same-day entries remain included even after the pass timestamp.
    assert len(packet.entries) == 10


@pytest.mark.parametrize("heading", ["## Working rules", "## Standing rules (read before any work)"])
def test_layouts_classify_dated_notes_inside_rules_and_can_move_them(heading):
    snap = snapshot(heading=heading)
    snap[LEARNINGS] = snap[LEARNINGS].replace("- Rule 0.", "## 2026-10-30 — note\nDated note.\n\n- Rule 0.")
    snap[LEARNINGS] += "\n## Log\n\n## 2026-10-31 — log\nLog text.\n"
    packet = build_packet(snap, since=None)
    assert len(packet.rules) == 8 and len(packet.all_logs) == 2
    old = packet.all_logs[0]
    result = run(snap, {"edits": [{"number": 1, "operation": "move_log",
            "targets": [{"entry_id": old.entry_id, "old_text": old.text}],
            "replacement": "", "sources": [old.entry_id]}], "diary": "Moved a note."})
    assert result.status == "offer" and result.files[LEARNINGS].count(old.text) == 1
    assert len(parse(result.files[LEARNINGS]).rules) == 8


def test_imported_rules_heading_is_not_a_working_rules_section():
    snap = snapshot("\n# Learning log\n## Working rules from another memory\n- Imported rule.\n")
    assert len(parse(snap[LEARNINGS]).rules) == 8
    snap[LEARNINGS] = "## Working rules from another memory\n- Imported rule.\n"
    result = run(snap, {"edits": [], "diary": ""})
    assert result.status == "flagged" and any(f.startswith("18:") for f in result.flags)


def test_no_edits_has_no_card_artifact_and_no_change_log():
    result = run(snapshot(), {"edits": [], "diary": "Nothing to do."})
    assert result.status == "no_change" and "no change" in result.diary
    assert not result.files and not result.diff and not result.edits_json


def test_conflict_check_is_required_flags_conflict_in_diary_and_cannot_edit_agent():
    snap = snapshot()
    for checker in [None, lambda agent, rules: ["publishing"], lambda agent, rules: None]:
        result = run(snap, proposal(snap), conflict_check=checker)
        assert result.status == "flagged" and "9:" in result.diary
        assert snap["AGENT.md"] == "Never publish without approval.\n"


@pytest.mark.parametrize("secret", ["sk-" + "X" * 30, "ghp_" + "Y" * 30, "API_KEY=AbC123" * 6])
def test_new_secret_is_withheld_even_in_diary(secret):
    snap = snapshot()
    obj = proposal(snap)
    obj["diary"] = secret
    result = run(snap, obj)
    assert result.status == "flagged" and any(f.startswith("19:") for f in result.flags)
    assert secret not in str(result)


def test_existing_memory_secret_is_not_new_but_knowledge_secret_is():
    secret = "sk-" + "Z" * 30
    snap = snapshot()
    obj = proposal(snap)
    obj["diary"] = secret
    snap["memory/old.md"] = secret
    assert run(snap, obj).status == "offer"
    snap["knowledge/old.md"] = snap.pop("memory/old.md")
    assert run(snap, obj).status == "flagged"


def test_malformed_json_and_long_diary_do_not_leak_or_offer():
    snap = snapshot()
    assert run(snap, {"edits": [], "diary": "a\nb\nc\nd"}).status == "flagged"
    result = evaluate(snap, '{"edits": [], "edits": [], "diary": ""}', since=None, today=TODAY,
                      checkout_before={}, checkout_after={}, conflict_check=lambda a, r: [])
    assert result.status == "flagged"


def test_undated_imported_log_needs_previous_snapshot_to_select_new_text_only():
    old = snapshot("\n# Learning log\n## Working rules from another memory\n- Imported rule.\n")
    snap = dict(old)
    snap[LEARNINGS] += "\n## New undated note\nNew material.\n"
    since = datetime(2026, 11, 1, tzinfo=timezone.utc)
    packet = build_packet(snap, since=since, previous_learnings=old[LEARNINGS])
    assert packet.scope_complete
    text = json.dumps(packet.model_input())
    assert "New material" in text and "Imported rule" not in text
    assert not build_packet(snap, since=since).scope_complete
    initial = build_packet(snap, since=None)
    assert "Imported rule" in json.dumps(initial.model_input())
