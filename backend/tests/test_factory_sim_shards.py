"""The factory sim's groups (evals/factory/run.py): each VM of a remote run, or each shard of a CI matrix, gets its
share, and together they run every (scenario, seed) pair exactly once."""

import subprocess
import sys
from pathlib import Path

from evals.factory import run

ROOT = Path(__file__).resolve().parents[2]


def test_shards_run_every_pair_once_and_stay_near_the_target():
    seconds = {"long": 170, "mid": 90, **{f"s{i}": 30 + i for i in range(20)}}
    pairs = [(name, seed) for seed in (1, 2, 3) for name in seconds]
    count = run.groups_for(pairs, seconds)
    groups = run.pack(pairs, count, seconds)
    assert sorted(p for g in groups for p in g) == sorted(pairs)
    loads = [sum(seconds[name] for name, _ in g) / run.REMOTE_CPUS for g in groups]
    assert max(loads) <= run.TARGET_SECONDS and max(loads) - min(loads) < 60
    # The same pairs and timings pack the same way, so each matrix shard can work out its own group.
    assert run.pack(list(reversed(pairs)), count, seconds) == groups


def test_shard_flags_cover_the_whole_run(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(run, "run_here", lambda pairs, *a: seen.extend(pairs) or [])
    for i in (1, 2, 3):
        assert run.main(["happy_path", "skip_qa", "architect", "--seeds", "1-2", "--shard", f"{i}/3"]) == 0
    assert sorted(seen) == sorted((n, s) for n in ("happy_path", "skip_qa", "architect") for s in (1, 2))


def test_a_scenario_with_no_timing_still_runs():
    assert run.pack([("new", 1), ("old", 1)], 2, {"old": 50}) == [[("new", 1)], [("old", 1)]]


def test_every_scenario_is_listed_whichever_module_is_imported_first():
    # A test that imported a case module before evals.factory.scenarios used to leave SCENARIOS with 21 of 41, so
    # timings.json "lacked" scenarios on some CI shards and not others. Fresh interpreters, so the order is real.
    def names(first):
        code = f"import {first}; from evals.factory.scenarios import SCENARIOS; print(*[s.name for s in SCENARIOS])"
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return out.split()

    expected = names("evals.factory.scenarios")
    assert len(expected) == len(set(expected)) and set(expected) == set(run.timings())
    for first in ("evals.factory.cases", "evals.factory.cases.freezes", "evals.factory.cases.source_outages"):
        assert names(first) == expected, first
