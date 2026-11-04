"""Pure synthetic snapshots: no live repository, credentials or database."""
import json
from datetime import date, datetime, timezone

import pytest

from backend.memory_tidy import ARCHIVE, LEARNINGS, build_packet, evaluate, parse, preservation_flags, resolve_source

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
    return evaluate(snap, json.dumps(obj), since=kw.pop("since", None), today=kw.pop("today", TODAY),
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
    assert "AGENT.md" not in result.files
    assert "memory/evidence/private.txt" not in result.files
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
    assert result.status == "offer" and "retired 2026-11-01 (edit 1)" in result.files[ARCHIVE]


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


def test_semantic_checker_failure_withholds_all_offer_artifacts():
    def unavailable(agent, rules):
        raise OSError("private diagnostic text")

    result = run(snapshot(), proposal(snapshot()), conflict_check=unavailable)
    assert result.status == "flagged" and "instruction-conflict check failed" in result.diary
    assert "private diagnostic" not in str(result)
    assert not result.files and not result.edits_json and not result.diff



def test_dated_bullet_logs_are_individual_scoped_entries_and_archive_alone():
    snap = snapshot(heading="## Working rules")
    snap[LEARNINGS] += ("\n## Log\n- 2026-11-01 New note.\n"
                        "- 2026-10-31 Recent note.\n- 2026-09-01 Old note.\n")
    entries = parse(snap[LEARNINGS]).all_logs
    assert len(entries) == 3
    packet = build_packet(snap, since=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert [e.text for e in packet.entries if e.kind == "log"] == [entries[0].text]
    old = entries[2]
    result = run(snap, {"edits": [{"number": 1, "operation": "archive_log",
                 "targets": [{"entry_id": old.entry_id, "old_text": old.text}],
                 "replacement": "", "sources": [old.entry_id]}], "diary": "Archive one."})
    assert result.status == "offer"
    assert old.text in result.files[ARCHIVE] and old.text not in result.files[LEARNINGS]
    assert all(e.text in result.files[LEARNINGS] for e in entries[:2])


def test_oldest_first_bullet_log_does_not_hide_appended_new_entries():
    snap = snapshot("\n## Log\n- 2026-09-01 Old.\n- 2026-11-01 New.\n")
    packet = build_packet(snap, since=datetime(2026, 11, 1, tzinfo=timezone.utc))
    logs = [e.text for e in packet.entries if e.kind == "log"]
    assert logs == ["- 2026-11-01 New.\n"]


def test_old_note_move_to_log_is_flagged_even_when_archive_also_contains_it():
    snap = snapshot("\n## 2026-09-01 Old.\nAn old dated note.\n")
    old = parse(snap[LEARNINGS]).all_logs[0]
    result = run(snap, {"edits": [{"number": 1, "operation": "move_log",
                 "targets": [{"entry_id": old.entry_id, "old_text": old.text}],
                 "replacement": "", "sources": [old.entry_id]}], "diary": "Move."})
    assert result.status == "flagged" and any(f.startswith("10:") for f in result.flags)
    assert not result.files


def test_nothing_lost_is_independent_of_archive_implementation():
    before = {LEARNINGS: "- Keep exact wording.\n"}
    after = {LEARNINGS: "- Changed wording.\n"}
    assert preservation_flags(before, after) == ["7: removed line not preserved verbatim"]
    assert preservation_flags(before, {**after, "knowledge/facts.md": before[LEARNINGS]}) == []
    assert preservation_flags(before, {**after, "state.md": before[LEARNINGS]})


def test_indented_note_bullets_stay_log_and_undated_top_level_bullets_resume_rules():
    snap = snapshot(heading="## Standing rules")
    snap[LEARNINGS] = snap[LEARNINGS].replace("- Rule 0.",
        "### 2026-10-31 A note\nText.\n  - Its detail.\n\n- Rule 0.")
    packet = parse(snap[LEARNINGS])
    assert len(packet.rules) == 8
    assert len(packet.all_logs) == 1 and "  - Its detail." in packet.all_logs[0].text
    assert "Rule 0" not in packet.all_logs[0].text


def test_attachment_uses_only_server_diary_and_scratch_contains_changed_paths_only():
    snap = snapshot()
    obj = proposal(snap)
    obj["diary"] = "Untrusted narrative."
    result = run(snap, obj)
    attachment = json.loads(result.edits_json)
    assert attachment["diary"] == result.diary and "Untrusted narrative" not in result.edits_json
    assert set(result.files) == {LEARNINGS, ARCHIVE}


def test_later_pass_archives_aged_out_history_without_exposing_it_to_model():
    snap = snapshot("\n## Log\n- 2026-10-15 A note that will age out.\n")
    previous = snap[LEARNINGS]
    first = run(snap, {"edits": [], "diary": ""}, today=date(2026, 11, 13))
    assert first.status == "no_change"
    since = datetime(2026, 11, 13, tzinfo=timezone.utc)
    packet = build_packet(snap, since=since, previous_learnings=previous)
    old = packet.all_logs[0]
    assert old.text not in json.dumps(packet.model_input())
    result = run(snap, proposal(snap), today=date(2026, 11, 15), since=since, previous_learnings=previous)
    assert result.status == "offer"
    edits = json.loads(result.edits_json)["edits"]
    assert [e["number"] for e in edits] == [1, 2]
    assert edits[1] == {"number": 2, "operation": "archive_log",
                         "targets": [{"entry_id": old.entry_id, "old_text": old.text}],
                         "replacement": "", "sources": [old.entry_id]}
    assert old.text not in result.files[LEARNINGS]
    assert resolve_source({**snap, **result.files}, old.entry_id) == old.text
    assert "1 archive_log" in result.diary


def test_no_model_edits_stays_no_change_even_when_hidden_history_ages_out():
    snap = snapshot("\n## Log\n- 2026-09-15 Old hidden note.\n")
    result = run(snap, {"edits": [], "diary": ""}, since=datetime(2026, 11, 1, tzinfo=timezone.utc),
                 previous_learnings=snap[LEARNINGS])
    assert result.status == "no_change" and "no change" in result.diary
    assert not result.files and not result.edits_json


def test_model_cannot_target_hidden_history_even_if_code_archives_it():
    snap = snapshot("\n## Log\n- 2026-09-15 Old hidden note.\n")
    old = parse(snap[LEARNINGS]).all_logs[0]
    edit = {"number": 1, "operation": "archive_log", "replacement": "", "sources": [old.entry_id],
            "targets": [{"entry_id": old.entry_id, "old_text": old.text}]}
    result = run(snap, {"edits": [edit], "diary": ""}, since=datetime(2026, 11, 1, tzinfo=timezone.utc),
                 previous_learnings=snap[LEARNINGS])
    assert result.status == "flagged" and "unknown or repeated target" in result.diary


def test_sources_resolve_after_earlier_text_moves_and_only_cited_archive_is_read():
    snap = snapshot()
    original, later = parse(snap[LEARNINGS]).rules[:2]
    result = run(snap, proposal(snap))
    assert result.status == "offer"
    kept = {**snap, **result.files}
    assert next(e for e in parse(kept[LEARNINGS]).rules if e.text == later.text).entry_id == later.entry_id
    assert resolve_source(kept, original.entry_id) == original.text
    assert original.entry_id in kept[ARCHIVE]
    # Uncited archive content never enters the packet.
    kept[ARCHIVE] += "\nAn unrelated archived note.\n"
    packet = build_packet(kept, since=datetime(2026, 11, 1, tzinfo=timezone.utc))
    sources = [e for e in packet.entries if e.kind == "source"]
    assert len(sources) == 1 and sources[0].text == original.text
    assert "unrelated archived note" not in json.dumps(packet.model_input())
    obj = {"edits": [{"number": 1, "operation": "retire", "replacement": "", "sources": [original.entry_id],
                     "targets": [{"entry_id": original.entry_id, "old_text": original.text}]}], "diary": ""}
    assert run(kept, obj).status == "flagged"
    # A forged or damaged archive record cannot satisfy the reference.
    kept[ARCHIVE] = kept[ARCHIVE].replace(original.text, original.text.replace("Rule 0", "Rule X"))
    assert resolve_source(kept, original.entry_id) is None


def test_identical_entries_have_distinct_content_occurrences_not_offsets():
    text = "# Working rules\n- Same.\n- Other.\n- Same.\n"
    before = parse(text).rules
    after = parse(text.replace("- Other.\n", "")).rules
    assert before[0].entry_id != before[2].entry_id
    assert [e.entry_id for e in after] == [before[0].entry_id, before[2].entry_id]


@pytest.mark.parametrize("heading", ["# Working rules", "## Working rules", "## Standing rules"])
def test_explicit_log_heading_ends_rules_and_moves_reuse_it(heading):
    snap = snapshot(heading=heading)
    snap[LEARNINGS] = snap[LEARNINGS].replace("- Rule 0.", "## 2026-10-31 Note\nText.\n\n- Rule 0.")
    # The dated heading ends top-level PR Reviewer rules, but remains mixed for level two.
    if heading == "# Working rules":
        snap = snapshot(heading=heading)
    snap[LEARNINGS] += "\n## Log\n- An undated log detail.\n## 2026-11-01 Existing\nKept.\n"
    packet = parse(snap[LEARNINGS])
    assert len(packet.rules) == 8
    if heading != "# Working rules":
        old = packet.all_logs[0]
        obj = {"edits": [{"number": 1, "operation": "move_log", "replacement": "", "sources": [old.entry_id],
                         "targets": [{"entry_id": old.entry_id, "old_text": old.text}]}], "diary": ""}
        result = run(snap, obj)
        assert result.status == "offer" and result.files[LEARNINGS].count("## Log\n") == 1
        assert result.files[LEARNINGS].index(old.text) > result.files[LEARNINGS].index("## Log\n")
        assert len(parse(result.files[LEARNINGS]).rules) == 8
