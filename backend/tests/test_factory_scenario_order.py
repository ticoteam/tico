"""The factory scenario list is complete whichever module a process imports first.

The case modules import their helpers from evals.factory.scenarios, and that module joins the case list at its end. A
test that imports one case module before the rest used to see a short list, so a timings check failed only in the
merge queue, where the test process happened to import a case first. Each entry point runs in its own interpreter here,
so the order is the one that matters."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRINT_NAMES = """
import importlib, json, sys
importlib.import_module(sys.argv[1])
from evals.factory.scenarios import SCENARIOS
print(json.dumps(sorted(s.name for s in SCENARIOS)))
"""


def names_when_first_imported(module):
    done = subprocess.run(
        [sys.executable, "-c", PRINT_NAMES, module],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize(
    "module",
    ["evals.factory.cases.freezes", "evals.factory.cases.source_outages", "evals.factory.cases.recovery"],
)
def test_a_case_module_imported_first_sees_every_scenario(module):
    assert names_when_first_imported(module) == names_when_first_imported("evals.factory.scenarios")
