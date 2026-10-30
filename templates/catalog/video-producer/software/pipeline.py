#!/usr/bin/env python3
"""The Video Producer's pipeline: where each project's files stand, what to do next, and whether a file may be used.

    python3 software/pipeline.py status projects/<slug>            each stage, its mover, its files and approvals
    python3 software/pipeline.py next projects/<slug> [--task ID]  the one step to take now: a hub command or "self: proceed"
    python3 software/pipeline.py gate projects/<slug>/<stage>/<file>   exit 0 when the next stage may use the file

Stages, who moves each one and the budget come from pipeline.yaml at the repository root. A project is a folder per
stage (projects/<slug>/<stage>/<asset>). An asset may have a provenance sidecar: the same file name with `.json` added,
holding `cost_usd` among its fields. Approval is `hub file approved <path>`: exit 0 when approved, 1 when not.
Standard library only.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "pipeline.yaml"
PROJECTS = REPO / "projects"
MOVER = re.compile(r"owner|self|person:.+|bot:[a-z0-9][a-z0-9-]*")
STAGE = re.compile(r"[a-z][a-z0-9-]*")


class Problem(Exception):
    """Something the pipeline cannot answer for: a bad pipeline.yaml, a file outside a project, or hub failing."""


# ----------------------------------------------------------------------------- the stages
def parse_pipeline(text):
    """The stages in order, from the small YAML subset pipeline.yaml uses: `stages:`, then `- id:` items with
    `mover:` and optional `budget_usd:` lines. Comments and blank lines are skipped."""
    items, item = [], None
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#") or line == "stages:":
            continue
        if line.startswith("- "):
            item = {}
            items.append(item)
            line = line[2:].strip()
        key, sep, value = line.partition(":")
        if item is None or not sep:
            raise Problem(f"pipeline.yaml: cannot read {raw.strip()!r}")
        item[key.strip()] = value.strip().strip('"')
    return items


def load(path=None):
    """The stages in order, checked: {id, mover, budget} with budget a float or None."""
    stages = []
    for item in parse_pipeline(Path(path or PIPELINE).read_text()):
        stage_id, mover = item.get("id", ""), item.get("mover", "")
        if not STAGE.fullmatch(stage_id):
            raise Problem(f"pipeline.yaml: stage id {stage_id!r} must be lowercase words joined by dashes")
        if not MOVER.fullmatch(mover):
            raise Problem(f"pipeline.yaml: stage {stage_id} mover {mover!r} is not owner, self, person:<name> or bot:<slug>")
        try:
            budget = float(item["budget_usd"]) if "budget_usd" in item else None
        except ValueError:
            raise Problem(f"pipeline.yaml: stage {stage_id} budget_usd {item['budget_usd']!r} is not a number")
        stages.append({"id": stage_id, "mover": mover, "budget": budget})
    ids = [s["id"] for s in stages]
    if not ids or len(set(ids)) != len(ids):
        raise Problem("pipeline.yaml: needs at least one stage and no repeated stage id")
    return stages


def stage_named(stages, stage_id):
    return next((s for s in stages if s["id"] == stage_id), None)


# ----------------------------------------------------------------------------- the files
def assets(stage_dir):
    """The files in one stage folder, sorted. A provenance sidecar (`x.json` beside `x`) is not an asset; a `.json`
    with no file beside it, such as a cut list, is."""
    if not stage_dir.is_dir():
        return []
    names = {p.name for p in stage_dir.iterdir() if p.is_file() and not p.name.startswith(".")}
    return [stage_dir / n for n in sorted(names) if not (n.endswith(".json") and n[:-5] in names)]


def sidecar(asset):
    path = asset.with_name(asset.name + ".json")
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Problem(f"{path.name}: not readable JSON")
    return data if isinstance(data, dict) else {}


def cost(asset):
    try:
        return float(sidecar(asset).get("cost_usd") or 0)
    except (TypeError, ValueError):
        raise Problem(f"{asset.name}.json: cost_usd is not a number")


def spent(project, stages):
    """What the self-moved stages of one project have cost so far: the budget a self stage is checked against."""
    return sum(cost(f) for s in stages if s["mover"] == "self" for f in assets(project / s["id"]))


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(REPO.resolve()))
    except ValueError:
        raise Problem(f"{path} is outside this repository")


def approval(asset):
    """(approved, detail) from `hub file approved`: exit 0 approved, 1 not approved, anything else is a failure."""
    try:
        done = subprocess.run(["hub", "file", "approved", rel(asset)], cwd=REPO, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Problem(f"hub file approved could not run: {exc}")
    if done.returncode == 0:
        try:
            detail = json.loads(done.stdout)
        except ValueError:
            detail = {}
        return True, detail if isinstance(detail, dict) else {}
    if done.returncode == 1:
        return False, {}
    raise Problem(f"hub file approved exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")


# ----------------------------------------------------------------------------- what to do next
def ask(task, stage, files, question=None):
    """The hub command that puts a stage's files to its mover for review."""
    who = stage["mover"]
    target = who.split(":", 1)[1] if who.startswith("person:") else who
    text = question or f"Approve the {stage['id']} files?"
    command = ["hub", "task", "question", task, "--on", target, "--ask", text]
    for path in files:
        command += ["--review", rel(path)]
    return shlex.join(command)


def self_step(project, stage, stages, task):
    """A self-moved stage proceeds within its budget; over it, the owner is asked to approve more spend."""
    used = spent(project, stages)
    if stage["budget"] is None or used < stage["budget"]:
        note = "" if stage["budget"] is None else f" ({used:.2f} of {stage['budget']:.2f} USD used)"
        return f"self: proceed with {stage['id']}{note}"
    owner = dict(stage, mover="owner")
    return (f"over budget ({used:.2f} of {stage['budget']:.2f} USD): ask the owner: "
            + ask(task, owner, [], f"Spend more on {stage['id']}? {used:.2f} of {stage['budget']:.2f} USD used."))


def next_step(project, stages, task="TASK-ID"):
    """The one step to take now. Stages run in order: a self stage with files is done (it needs no approval), any
    other stage waits until each of its files is approved by its mover."""
    for stage in stages:
        folder, files = project / stage["id"], assets(project / stage["id"])
        if stage["mover"] == "self":
            if not files:
                return self_step(project, stage, stages, task)
            continue
        if not files:
            return f"waiting: make the {stage['id']} files in {rel(folder)}, then run next again"
        pending = [f for f in files if not approval(f)[0]]
        if pending:
            return ask(task, stage, pending)
    return "nothing to do: every stage has its files approved"


def gate(asset):
    """Exit status for a file the next stage wants to use: 0 when its stage is self-moved or the file is approved."""
    try:
        parts = Path(asset).resolve().relative_to(PROJECTS.resolve()).parts
    except ValueError:
        parts = ()
    if len(parts) < 3:
        raise Problem(f"{asset} is not a file in projects/<slug>/<stage>/")
    stage = stage_named(load(), parts[1])
    if stage is None:
        raise Problem(f"{asset}: no stage {parts[1]!r} in pipeline.yaml")
    if stage["mover"] == "self":
        print(f"self: {stage['id']} needs no review")
        return 0
    ok, detail = approval(Path(asset).resolve())
    if ok:
        print(f"approved: {rel(asset)}" + (f" by {detail['by']}" if detail.get("by") else ""))
        return 0
    print(f"not approved: {rel(asset)}; ask {stage['mover']}")
    return 1


def status(project, stages):
    lines = []
    for stage in stages:
        files = assets(project / stage["id"])
        if not files:
            lines.append(f"{stage['id']:<11} {stage['mover']:<16} (no files yet)")
        for f in files:
            if stage["mover"] == "self":
                state = "self-moved"
            else:
                state = "approved" if approval(f)[0] else "not approved"
            lines.append(f"{stage['id']:<11} {stage['mover']:<16} {f.name:<28} {state:<12} {cost(f):.2f} USD")
        if stage["budget"] is not None and stage["mover"] == "self":
            lines.append(f"{'':<11} budget {stage['budget']:.2f} USD for this stage, "
                         f"{spent(project, stages):.2f} USD spent by self-moved stages")
    lines.append("next: " + next_step(project, stages))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="The Video Producer's pipeline (pipeline.yaml).")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "next"):
        sub = commands.add_parser(name)
        sub.add_argument("project", help="projects/<slug>")
        if name == "next":
            sub.add_argument("--task", default="TASK-ID", help="the Tico task the review question goes on")
    gate_cmd = commands.add_parser("gate")
    gate_cmd.add_argument("file", help="projects/<slug>/<stage>/<file>")
    args = parser.parse_args(argv)
    try:
        stages = load()
        if args.command == "gate":
            return gate(args.file)
        project = Path(args.project).resolve()
        if args.command == "status":
            print(status(project, stages))
        else:
            print(next_step(project, stages, args.task))
        return 0
    except Problem as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
