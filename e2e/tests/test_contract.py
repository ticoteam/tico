"""Golden API contract (e2e/README.md "contract"): the main v2 responses to a fixed scenario, compared with the
goldens recorded from main. The goldens are not generated from the code under test in a normal run; they are
re-recorded only on purpose (`python -m e2e.run --kind contract --record`), and the diff is reviewed."""
import difflib
import json
import os
from pathlib import Path

import pytest

from backend.tests.test_api import api  # noqa: F401  (the fixture: a fresh database and the fictional company)
from backend.tests.test_events import live  # noqa: F401  (a listening server for the events catch-up)
from e2e import renames, scenario
from e2e.normalize import normalize

GOLDENS = Path(__file__).resolve().parents[1] / "goldens"
RECORD = os.environ.get("TICO_E2E_RECORD") == "1"


def fresh_capture(api, live):
    ids = scenario.populate(api)
    out = scenario.capture(api, ids)
    stream = live(token=scenario.OWNER, query="?after=0")
    out["events"] = {"request": "GET /api/v2/events?after=0", "status": 200,
                     "body": scenario.events(stream)}
    names = {k: v for k, v in ids.items() if not k.startswith("_")}
    return {name: normalize(entry, names) for name, entry in out.items()}


def write(captured):
    GOLDENS.mkdir(parents=True, exist_ok=True)
    for stale in GOLDENS.glob("*.json"):
        if stale.stem not in captured:
            stale.unlink()
    for name, entry in captured.items():
        (GOLDENS / f"{name}.json").write_text(json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


@pytest.mark.slow
def test_v2_responses_match_the_goldens(api, live):  # noqa: F811
    captured = fresh_capture(api, live)
    if RECORD:
        write(captured)           # the runner reports it; review `git diff e2e/goldens` before committing
        return
    rename_map = renames.active()
    expected = {p.stem for p in GOLDENS.glob("*.json")}
    assert expected == set(captured), "goldens and captures differ: %s" % sorted(expected ^ set(captured))
    differences = []
    for name in sorted(captured):
        golden = json.loads((GOLDENS / f"{name}.json").read_text())
        golden = {**golden, "body": renames.apply_fields(golden["body"], renames.golden_fields(rename_map, name))}
        fresh = {**captured[name], "body": renames.drop_added(captured[name]["body"], rename_map["golden_added"].get(name))}
        if golden != fresh:
            old = json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
            new = json.dumps(fresh, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
            diff = "\n".join(list(difflib.unified_diff(old, new, "golden " + name, "fresh " + name, lineterm=""))[:60])
            differences.append(f"--- {name}\n{diff}")
    assert not differences, "\n\n".join(differences)
