#!/usr/bin/env python3
"""Move every open task in a release batch to one stage, and check that each one got there.

A batch is the open tasks carrying its label (`release:YYYY-MM-DD.N`, playbooks/write-the-release-report.md). The
release task carries the same label: leave it out with --except.

    python3 software/move_release.py release:2026-11-13.1 "Deploy" --except <release task id> -m "In release 2026-11-13.1"
    python3 software/move_release.py release:2026-11-13.1 done --except <release task id> -m "Shipped in 2026-11-13.1"

The stage is a stage's name or id in each task's pipeline, `done` or `closed`. Each task is moved with
`tico task move` at the version it was listed with, then read again: it counts as moved only when it is in that stage
(or done or closed). Prints one JSON report, {label, to, moved, already, not_moved}, each entry with the task's id,
number and title, and for not_moved the stage it is in and why. Exits 1 when any task did not move, 2 when the batch
could not be read. Standard library only.
"""

import argparse
import json
import subprocess
import sys

ENDED = ("done", "closed")
PAGE = 500


class Problem(Exception):
    pass


def tico(*args):
    """Run `tico` and return its JSON. A failed command, or an answer that is an error, raises Problem with why."""
    try:
        done = subprocess.run(["tico", *args], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Problem(f"tico {args[0]} {args[1]}: {exc}") from None
    try:
        answer = json.loads(done.stdout or "null")
    except ValueError:
        answer = None
    if isinstance(answer, dict) and answer.get("error"):
        raise Problem(str(answer.get("detail") or answer["error"])[:400])
    if done.returncode or answer is None:
        raise Problem((done.stderr or done.stdout or f"tico exited {done.returncode}").strip()[:400])
    return answer


def batch(label):
    """The open tasks with this label, every page of them."""
    rows, offset = [], 0
    while True:
        page = tico(
            "task",
            "list",
            "--label",
            label,
            "--status",
            "open",
            "--brief",
            "--limit",
            str(PAGE),
            "--offset",
            str(offset),
        )
        if not isinstance(page, dict):
            return rows + page
        rows += page.get("tasks") or []
        offset = page.get("next_offset")
        if not offset:
            return rows


def task_of(answer):
    return answer.get("task") if isinstance(answer.get("task"), dict) else answer


def stage_of(task):
    stage = task.get("stage")
    return stage if isinstance(stage, dict) else {"id": task.get("stage_id"), "name": stage}


def there(task, to):
    """Whether the task is where `to` names: done or closed by its status, a stage by its id or name."""
    want = to.strip().lower()
    if want in ENDED:
        return task.get("status") == want
    stage = stage_of(task)
    return task.get("status") == "open" and want in (
        str(stage.get("id") or "").lower(),
        str(stage.get("name") or "").lower(),
    )


def entry(task, **more):
    return {"id": task.get("id"), "number": task.get("number"), "title": task.get("title"), **more}


def move_all(label, to, why, skip=()):
    report = {"label": label, "to": to, "moved": [], "already": [], "not_moved": []}
    for task in batch(label):
        if task.get("id") in skip:
            continue
        if there(task, to):
            report["already"].append(entry(task))
            continue
        try:
            tico("task", "move", str(task["id"]), to, "-m", why, "--version", str(task.get("version")))
            now = task_of(tico("task", "show", str(task["id"]), "--json"))
        except Problem as exc:
            report["not_moved"].append(entry(task, stage=stage_of(task).get("name"), why=str(exc)))
            continue
        if there(now, to):
            report["moved"].append(entry(now))
        else:
            where = stage_of(now).get("name") if now.get("status") == "open" else now.get("status")
            report["not_moved"].append(entry(now, stage=where, why=f"still in {where} after the move"))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("label", help="the batch's label, release:YYYY-MM-DD.N")
    parser.add_argument("to", help="a stage's name or id, done or closed")
    parser.add_argument("-m", "--why", required=True, help="why, said on each task")
    parser.add_argument("--except", dest="skip", action="append", default=[], help="a task to leave where it is")
    args = parser.parse_args(argv)
    if args.to.strip().lower() in ("next", "reopen"):
        parser.error("name the stage: every task in the batch goes to the same one")
    try:
        report = move_all(args.label, args.to, args.why, set(args.skip))
    except Problem as exc:
        print(json.dumps({"label": args.label, "to": args.to, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 1 if report["not_moved"] else 0


if __name__ == "__main__":
    sys.exit(main())
