"""The Release Owner's batch mover (templates/catalog/release-owner/software/move_release.py) with `tico` stubbed: every
open task with the batch's label goes to the named stage at the version it was listed with, the release task named by
--except stays put, a task is counted as moved only when a fresh read shows it there, and a refused or stuck move is
reported with why while the rest still move."""

import importlib.machinery
import json
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "templates/catalog/release-owner/software/move_release.py"
loader = importlib.machinery.SourceFileLoader("move_release", str(SCRIPT))
move_release = importlib.util.module_from_spec(importlib.util.spec_from_loader("move_release", loader))
loader.exec_module(move_release)

LABEL = "release:2026-11-13.1"


def task(n, stage="Ready", status="open", version=3):
    return {
        "id": f"t{n}",
        "number": n,
        "title": f"Task {n}",
        "status": status,
        "version": version,
        "stage": {"id": f"s-{stage.lower()}", "name": stage},
    }


@pytest.fixture
def board(monkeypatch):
    """A fake `tico`: list pages of 2, moves that land unless told to refuse or stick, and every call recorded."""
    state = {"tasks": {}, "refuse": set(), "stick": set(), "calls": []}

    def tico(*args):
        state["calls"].append(args)
        if args[:2] == ("task", "list"):
            assert args[args.index("--label") + 1] == LABEL
            offset = int(args[args.index("--offset") + 1])
            rows = [dict(t) for t in state["tasks"].values() if t["status"] == "open"]
            more = offset + 2 < len(rows)
            return {"tasks": rows[offset : offset + 2], "next_offset": offset + 2 if more else None}
        if args[:2] == ("task", "move"):
            tid, to = args[2], args[3]
            if tid in state["refuse"]:
                raise move_release.Problem("No room in Deploy.")
            if tid not in state["stick"]:
                t = state["tasks"][tid]
                if to in ("done", "closed"):
                    t["status"] = to
                else:
                    t["stage"] = {"id": f"s-{to.lower()}", "name": to}
                t["version"] += 1
            return {"ok": True}
        if args[:2] == ("task", "show"):
            return {"task": dict(state["tasks"][args[2]])}
        raise AssertionError(args)

    monkeypatch.setattr(move_release, "tico", tico)
    return state


def add(board, *tasks):
    for t in tasks:
        board["tasks"][t["id"]] = t


def test_every_labelled_task_moves_at_its_version_and_the_release_task_stays(board):
    add(board, task(1), task(2, version=7), task(3, stage="Deploy"), task(4, stage="Waiting for OK"), task(5))
    report = move_release.move_all(LABEL, "Deploy", "In release 2026-11-13.1", skip={"t4"})
    assert [m["number"] for m in report["moved"]] == [1, 2, 5]  # every page of the list was read
    assert [a["number"] for a in report["already"]] == [3]
    assert report["not_moved"] == []
    assert board["tasks"]["t4"]["stage"]["name"] == "Waiting for OK"
    moves = [c for c in board["calls"] if c[:2] == ("task", "move")]
    assert ("task", "move", "t2", "Deploy", "-m", "In release 2026-11-13.1", "--version", "7") in moves
    assert not [c for c in moves if c[2] in ("t3", "t4")]


def test_a_refused_or_stuck_move_is_reported_with_why_and_the_rest_still_move(board, capsys):
    add(board, task(1), task(2), task(3))
    board["refuse"].add("t1")
    board["stick"].add("t2")
    assert move_release.main([LABEL, "done", "-m", "Shipped"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [m["number"] for m in report["moved"]] == [3]
    assert board["tasks"]["t3"]["status"] == "done"
    why = {n["number"]: n for n in report["not_moved"]}
    assert why[1]["why"] == "No room in Deploy." and why[1]["stage"] == "Ready"
    assert why[2]["why"] == "still in Ready after the move"


def test_a_batch_that_cannot_be_read_moves_nothing(monkeypatch, capsys):
    def tico(*args):
        raise move_release.Problem("not signed in")

    monkeypatch.setattr(move_release, "tico", tico)
    assert move_release.main([LABEL, "Deploy", "-m", "In release"]) == 2
    assert "not signed in" in capsys.readouterr().out
