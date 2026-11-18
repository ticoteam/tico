"""scripts/landmines.py: each rule finds its bad case and not its good one, and the counts only go down."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import landmines  # noqa: E402

PY = "backend/tests/test_thing.py"
UI = "ui/tests/thing.cts"


def hits(path, text):
    return {rule: len(lines) for rule, lines in landmines.hits_in(path, text, landmines.RULES).items()}


@pytest.mark.parametrize(
    ("rule", "path", "bad", "good"),
    [
        ("no-retry", PY, "@pytest.mark.flaky(reruns=3)\ndef test_x(): pass\n", "def test_x(): pass\n"),
        ("no-retry", "pytest.ini", "[pytest]\naddopts = --reruns 2\n", "[pytest]\naddopts = -q\n"),
        (
            "no-retry",
            "ui/playwright.config.ts",
            "export default {\n  retries: 2,\n};\n",
            "export default {\n  workers: 2,\n};\n",
        ),
        ("no-fixed-sleep-py", PY, "import time\ntime.sleep(0.5)\n", "import asyncio\nawait asyncio.sleep(0)\n"),
        ("no-fixed-sleep-py", PY, "await asyncio.sleep(1)\n", "assert done.wait(10), 'the worker never finished'\n"),
        ("no-fixed-wait-ui", UI, "await page.waitForTimeout(200);\n", "await until(() => drawn(), 'the row');\n"),
        ("no-fixed-wait-ui", UI, "await new Promise(r => setTimeout(r, 50));\n", "await page.waitForResponse(url);\n"),
        ("no-raised-timeout", UI, "await x.waitFor({timeout: 10000});\n", "await x.waitFor({timeout: t(5000)});\n"),
        ("no-raised-timeout", UI, "await x.waitFor({timeout: t(60000)});\n", "await x.waitFor({timeout: 500});\n"),
        ("no-unittest-mock", PY, "from unittest import mock\n", "from backend.tests.helpers import fake_github\n"),
        (
            "no-unittest-mock",
            PY,
            "with mock.patch.object(H, 'which', which):\n    pass\n",
            "monkeypatch.setattr(H, 'now', at)\n",
        ),
        ("no-unittest-mock", PY, "m = MagicMock()\n", "m = FakeGitHub()\n"),
        (
            "no-unittest-mock",
            PY,
            "with mock.patch.dict(os.environ, {}), mock.patch.object(H, 'run'):\n    pass\n",
            "with mock.patch.dict(os.environ, {'PATH': ''}):\n    pass\n",
        ),
        (
            "no-global-time-patch",
            PY,
            "monkeypatch.setattr(apps.time, 'sleep', lambda s: None)\n",
            "monkeypatch.setattr(store, '_sleep', lambda s: None)\n",
        ),
        ("no-global-time-patch", PY, "monkeypatch.setattr('clients.tico.time.sleep', f)\n", "x = 1\n"),
        (
            "no-global-time-patch",
            PY,
            "with mock.patch.object(H.time, 'time', lambda: 1):\n    pass\n",
            "with mock.patch.object(H, '_now', lambda: 1):\n    pass\n",
        ),
        (
            "no-global-time-patch",
            PY,
            "with mock.patch('clients.tico.time.monotonic'):\n    pass\n",
            "with time_machine.travel(0, tick=False):\n    pass\n",
        ),
    ],
)
def test_each_rule_finds_its_bad_case_and_not_its_good_one(rule, path, bad, good):
    assert hits(path, bad).get(rule) == 1
    assert rule not in hits(path, good)


def test_a_pattern_inside_a_string_or_a_comment_is_not_a_hit():
    text = 'WORKER = "import time\\ntime.sleep(60)\\n"\n# time.sleep(1) would race here\nx = f"{time}"\n'
    assert hits(PY, text) == {}


def test_a_rule_only_reads_the_files_in_its_scope():
    assert hits("backend/store.py", "time.sleep(1)\n") == {}
    assert hits("ui/app/chat.ts", "await page.waitForTimeout(5);\n") == {}


def test_a_count_above_its_baseline_fails_and_prints_every_hit():
    found = {PY: {"no-fixed-sleep-py": [(3, "time.sleep(1)"), (9, "time.sleep(2)")]}}
    problems = landmines.compare(found, {PY: {"no-fixed-sleep-py": 1}})
    assert len(problems) == 1
    assert "has 2, its baseline allows 1" in problems[0]
    assert f"{PY}:3: time.sleep(1)" in problems[0] and f"{PY}:9: time.sleep(2)" in problems[0]
    assert "Stream.until" in problems[0]


def test_a_count_below_its_baseline_fails_as_stale():
    problems = landmines.compare({}, {PY: {"no-fixed-sleep-py": 2}})
    assert problems == [
        f"no-fixed-sleep-py: {PY} now has 0; lower its baseline to 0 with "
        "`python3 scripts/landmines.py --write` in this commit"
    ]


def test_an_equal_count_passes():
    found = {PY: {"no-unittest-mock": [(1, "from unittest import mock")]}}
    assert landmines.compare(found, {PY: {"no-unittest-mock": 1}}) == []


def test_a_ban_fails_on_one_hit_whatever_the_baseline_says():
    found = {"pytest.ini": {"no-retry": [(2, "addopts = --reruns 2")]}}
    problems = landmines.compare(found, {"pytest.ini": {"no-retry": 1}})
    assert any("is a ban and has no baseline" in p for p in problems)
    assert any("has 1, its baseline allows 0" in p for p in problems)


def test_write_never_records_a_ban():
    found = {"pytest.ini": {"no-retry": [(2, "x")]}, PY: {"no-fixed-sleep-py": [(1, "y")]}}
    assert landmines.counts_of(found) == {"pytest.ini": {}, PY: {"no-fixed-sleep-py": 1}}


def test_a_baseline_that_grew_against_the_base_fails():
    problems = landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {PY: {"no-fixed-sleep-py": 2}}, {"no-fixed-sleep-py"})
    assert len(problems) == 1 and "grew from 2 to 3" in problems[0]


def test_a_rule_the_base_does_not_have_brings_its_own_baseline():
    assert landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {}, {"no-retry"}) == []
    assert landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {}, None) == []


def test_rules_at_reads_the_rule_ids_from_a_copy_of_this_script():
    source = Path(landmines.__file__).read_text(encoding="utf-8")
    ids = set(__import__("re").findall(r'^\s*Rule\(\s*\n\s*"([a-z0-9-]+)"', source, __import__("re").M))
    assert ids == set(landmines.BY_ID)


ROW = "| {id} | Do the thing. | {incidents} | {cls} | {seen} | {status} | {where} |\n"


def lessons(**row):
    fields = {
        "id": "L01",
        "incidents": "2026-11-03 #2294",
        "cls": "script",
        "seen": "1",
        "status": "open",
        "where": "-",
    }
    fields.update(row)
    return "| id | lesson | incidents | class | seen | status | where |\n|---|---|---|---|---|---|---|\n" + ROW.format(
        **fields
    )


def lesson_problems(text, exists=lambda path: True):
    return landmines.lesson_problems(text, set(landmines.BY_ID), exists)


def test_a_true_lessons_table_passes():
    text = lessons(
        incidents="2026-11-03 #2294; 2026-11-12 e9dece9583 (an empty commit; a rerun)",
        seen="2",
        status="encoded no-retry",
    )
    cited_elsewhere = ("L02", "L26", "L31")  # the real rules cite rows this one-row table does not have
    assert [p for p in lesson_problems(text) if not any(i in p for i in cited_elsewhere)] == []


def test_seen_must_match_the_incidents():
    assert any("seen is 3 but it lists 1" in p for p in lesson_problems(lessons(seen="3")))


def test_an_incident_needs_a_pr_or_a_sha():
    assert any("needs a date and a PR number" in p for p in lesson_problems(lessons(incidents="2026-11-03 a rerun")))


def test_a_script_lesson_seen_twice_cannot_stay_open():
    text = lessons(incidents="2026-11-03 #1; 2026-11-04 #2", seen="2")
    assert any("still open" in p for p in lesson_problems(text))


def test_an_encoded_row_must_name_a_real_rule_or_script():
    assert any("is not in scripts/landmines.py" in p for p in lesson_problems(lessons(status="encoded no-such-rule")))
    missing = lessons(status="encoded `scripts/gone.py`")
    assert any("does not exist" in p for p in lesson_problems(missing, exists=lambda path: False))


def test_a_rule_must_cite_a_lesson_that_exists():
    assert any("cites L02, which is not in" in p for p in lesson_problems(lessons()))


def test_the_real_lessons_file_passes():
    assert landmines.check_lessons() == []
