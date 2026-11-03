"""The Assistant evals without a model (evals/assistant/): every case file loads, every regex compiles, and every hub command a
case expects parses with the repo's own clients/hubcli.py. The real-model run (evals/assistant/run.py) is on demand only and
never runs here.

A command is read from the start of a must or must_not regex: `^task (bulk close|close)\\b` expects `task bulk close` and
`task close`. A command counts as parsed when argparse accepts its words, or stops on a missing argument; it fails on an
unknown word or flag, which is how the real CLI rejects a command that does not exist.
"""
import contextlib
import io
import re
import sys
from pathlib import Path

import pytest

from clients import hubcli

EVALS = Path(__file__).resolve().parents[2] / "evals" / "assistant"
sys.path.insert(0, str(EVALS))
import run as live  # noqa: E402

# The Assistant's new commands land on the chris/assistant-tools branch, not on main. A case may expect them; on main the
# parser must still reject them. Delete an entry here when its command lands, so the case is checked against the real parser.
NEW_COMMANDS = [("needs-you", "show"), ("origin", "show"), ("assistant", "change")]
# Every case the Assistant suite must keep: the real Nov 3 chat split into its asks, undo, injection, decline, approval
# without the setting, and the everyday jobs.
REQUIRED = {"queue-clear-old-tasks", "queue-stop-release-notices", "queue-why-sent", "queue-needs-you-breakdown",
            "queue-undo-close", "queue-morning-summary", "approvals-injection-bot-message",
            "approvals-decline-replaced-release", "approvals-no-setting-no-approve", "approvals-read-back-named",
            "approvals-vendor-text-task", "approvals-clear-stale-direct", "background-daily-sweep", "jobs-launch-status", "jobs-route-paid-trials",
            "jobs-new-bot-botops", "jobs-discount-to-cfo", "jobs-change-bot-routine", "jobs-what-did-bot-do",
            "jobs-find-doc", "jobs-task-for-person"}


@pytest.fixture(scope="module")
def cases():
    return live.load_cases()


@pytest.fixture(scope="module")
def parser():
    return hubcli.parser()


def expectations(case):
    """Every must and must_not regex of a case, with the reply rubrics left out."""
    for key in ("must", "must_not"):
        for rx in case.get(key) or []:
            if not rx.startswith("reply:"):
                yield key, rx


def expand(rx):
    """Expand each (a|b) group into its alternatives, one regex per combination; (?...) groups are lookarounds and stay."""
    for m in re.finditer(r"\(([^()]*)\)", rx):
        if m.group(0).startswith("(?"):
            continue
        out = []
        for alt in m.group(1).split("|"):
            out.extend(expand(rx[:m.start()] + alt + rx[m.end():]))
        return out
    return [rx]


def command_words(rx):
    """The literal words at the start of a regex: `^task create .*--owner x` gives ['task', 'create']."""
    words = []
    for tok in rx.lstrip("^").split():
        found = re.match(r"--?[a-z][a-z0-9-]*|[a-z][a-z0-9-]*", tok)
        if not found:
            break
        words.append(found.group(0))
        if found.group(0) != tok:
            break
    return words


def parses(parser, words):
    """True when argparse accepts the words, or stops only on a missing argument (not on an unknown word or flag)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            parser.parse_args(words)
    except SystemExit:
        pass
    text = buf.getvalue()
    return "invalid choice" not in text and "unrecognized arguments" not in text


def test_every_case_file_loads_with_its_fields(cases):
    assert cases, "no case files under evals/assistant/cases"
    for case in cases:
        assert isinstance(case["id"], str) and case["id"], case
        assert case["prompt"].strip(), case["id"]
        assert case["reply"].strip(), f"{case['id']} has no rubric for the judge"
        assert case.get("must") or case.get("must_not"), f"{case['id']} checks nothing"
        for row in case.get("hub") or []:
            assert "match" in row and ("out" in row or "exit" in row), case["id"]


def test_ids_are_unique_and_the_required_cases_are_there(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert len(ids) >= 18, f"{len(ids)} cases; the suite needs at least 18"
    assert REQUIRED <= set(ids), f"missing: {sorted(REQUIRED - set(ids))}"


def test_every_regex_compiles(cases):
    for case in cases:
        for _key, rx in expectations(case):
            re.compile(rx)
        for row in case.get("hub") or []:
            re.compile(row["match"])


def test_expected_hub_commands_parse_with_the_real_cli(cases, parser):
    """Only `must` names commands the case expects; a must_not naming a command that does not exist cannot fail a run."""
    checked = 0
    for case in cases:
        for rx in case.get("must") or []:
            if rx.startswith("reply:"):
                continue
            key = "must"
            for alt in expand(rx):
                words = command_words(alt)
                if len(words) < 2:
                    continue
                checked += 1
                if any(tuple(words[:len(p)]) == p for p in NEW_COMMANDS):
                    assert not parses(parser, words), (
                        f"{case['id']}: `hub {' '.join(words)}` parses now; remove its entry from NEW_COMMANDS")
                    continue
                assert parses(parser, words), f"{case['id']} ({key}): `hub {' '.join(words)}` is not a command"
    assert checked >= 40, f"only {checked} commands were checked; the heuristic has stopped reading the cases"
