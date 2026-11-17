"""Frozen behaviour of the `tico` command line, one file per slice in clients/tests/cli_fixtures/: each case is a command
line and what it does, end to end through clients/hubcli.py and clients/remotecli.py: the tools it runs with their
arguments, the HTTP calls they make (method, path, body), what it prints on stdout and stderr, and its exit code.

The slices are the ones the typed command spec (step 4 of the CLI reorganisation) is piloted on, so a parser built
from the spec has to give the same answers. argparse's usage lines are left out of stderr: they list flags in the
parser's own order, which a generated parser need not keep. The error line after them stays, as Python 3.12 words it
(Python 3.14 quotes the choices and adds its own guess at a typo).

Each file lists its cases; a new case is a line `{"argv": [...]}` added to its file, then recorded. A deliberate change
regenerates the files, to be read in the diff before it is committed:
    TICO_CLI_FIXTURES_RECORD=1 python3 -m pytest -q clients/tests/test_cli_fixtures.py
backend/tests/test_bot_instruction_commands.py fails when a command line a bot is taught is not a case in its noun's
file.
"""

import json

import pytest

from clients.tests.helpers.cli_fixtures import HERE, RECORD, run_line


def slices():
    """{noun: [case, ...]} from the files in cli_fixtures/."""
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(HERE.glob("*.json"))}


@pytest.mark.parametrize("slice_name", sorted(slices()))
def test_the_command_line_does_what_its_fixtures_froze(slice_name, monkeypatch):
    path = HERE / f"{slice_name}.json"
    frozen = slices()[slice_name]
    argvs = [case["argv"] for case in frozen]
    assert len({tuple(argv) for argv in argvs}) == len(argvs), f"{path.name}: a case is listed twice"
    observed = []
    for argv in argvs:
        with monkeypatch.context() as m:
            observed.append(run_line(argv, m))
    if RECORD:
        path.write_text(json.dumps(observed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    for case, seen in zip(frozen, observed, strict=True):
        assert case == seen, f"{path.name}: tico {' '.join(case['argv'])}" + (
            "" if "exit" in case else " is not recorded"
        )
