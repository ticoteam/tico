#!/usr/bin/env python3
"""The Video Producer's pipeline: where each project's files stand, what to do next, and whether a file may be used.

    python3 software/pipeline.py status projects/<slug> [--task ID] [--owner NAME]
    python3 software/pipeline.py next projects/<slug> [--task ID] [--owner NAME]   the one step to take now
    python3 software/pipeline.py gate projects/<slug>/<stage>/<file>               exit 0 when the next stage may use it
    python3 software/pipeline.py raise projects/<slug> --usd N --approval ID       record a budget raise the owner approved

Stages, their movers, the owner and the budget come from pipeline.yaml at the repository root. A project is a folder per
stage (projects/<slug>/<stage>/<asset>). An asset may have a provenance sidecar: the same file name with `.json` added,
holding `cost_usd`. A self-moved stage is done when its folder holds a `.done` file. The budget raised by the owner is in
projects/<slug>/budget.json. Approval is `hub file approved <path>`: exit 0 when approved, 1 when not.
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
NO_OWNER = ("waiting: no owner is named (pipeline.yaml `owner`, or --owner NAME): use the person who opened the project "
            "task, then run next again with --owner NAME")


class Problem(Exception):
    """Something the pipeline cannot answer for: a bad pipeline.yaml, a file outside a project, or hub failing."""


# ----------------------------------------------------------------------------- the pipeline file
def parse_pipeline(text):
    """(top-level values, stage items) from the small YAML subset pipeline.yaml uses: `key: value` lines before
    `stages:`, then `- id:` items with `key: value` lines. Comments and blank lines are skipped."""
    top, items, item, in_stages = {}, [], None, False
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        if line == "stages:":
            in_stages = True
            continue
        if line.startswith("- "):
            if not in_stages:
                raise Problem(f"pipeline.yaml: a list item before stages: {raw.strip()!r}")
            item = {}
            items.append(item)
            line = line[2:].strip()
        key, sep, value = line.partition(":")
        if not sep:
            raise Problem(f"pipeline.yaml: cannot read {raw.strip()!r}")
        value = value.strip().strip('"')
        if item is not None:
            item[key.strip()] = value
        elif not in_stages:
            top[key.strip()] = value
        else:
            raise Problem(f"pipeline.yaml: {raw.strip()!r} is not in a stage")
    return top, items


def read(path=None):
    """The pipeline, checked: {owner, budget (USD per project), stages: [{id, mover}]} in order."""
    top, items = parse_pipeline(Path(path or PIPELINE).read_text())
    try:
        budget = float(top["budget_usd"])
    except (KeyError, ValueError):
        raise Problem("pipeline.yaml: budget_usd must be a number of USD per project")
    stages = []
    for item in items:
        stage_id, mover = item.get("id", ""), item.get("mover", "")
        if not STAGE.fullmatch(stage_id):
            raise Problem(f"pipeline.yaml: stage id {stage_id!r} must be lowercase words joined by dashes")
        if not MOVER.fullmatch(mover):
            raise Problem(f"pipeline.yaml: stage {stage_id} mover {mover!r} is not owner, self, person:<name> or bot:<slug>")
        stages.append({"id": stage_id, "mover": mover})
    ids = [s["id"] for s in stages]
    if not ids or len(set(ids)) != len(ids):
        raise Problem("pipeline.yaml: needs at least one stage and no repeated stage id")
    return {"owner": top.get("owner", ""), "budget": budget, "stages": stages}


def stage_named(cfg, stage_id):
    return next((s for s in cfg["stages"] if s["id"] == stage_id), None)


# ----------------------------------------------------------------------------- the files
def assets(stage_dir):
    """The files in one stage folder, sorted. Hidden files (such as `.done`) and provenance sidecars (`x.json` beside
    `x`) are not assets; a `.json` with no file beside it, such as a cut list, is."""
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


def spent(project, cfg):
    """What the self-moved stages of one project have cost: the sum of their sidecars' cost_usd."""
    return sum(cost(f) for s in cfg["stages"] if s["mover"] == "self" for f in assets(project / s["id"]))


def budget_record(project):
    """projects/<slug>/budget.json: {"extra_usd": the raises so far, "approval": the latest, "approvals": all of them}."""
    path = project / "budget.json"
    if not path.is_file():
        return {"extra_usd": 0.0, "approval": "", "approvals": []}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise Problem(f"{path}: not readable JSON")
    return {"extra_usd": float(data.get("extra_usd") or 0), "approval": str(data.get("approval") or ""),
            "approvals": [str(a) for a in data.get("approvals") or []]}


def record_raise(project, usd, approval_id):
    """Add an approved raise to the project's budget. The same approval id is recorded once, however often this runs."""
    record = budget_record(project)
    if approval_id in record["approvals"]:
        return record
    record = {"extra_usd": record["extra_usd"] + float(usd), "approval": approval_id,
              "approvals": record["approvals"] + [approval_id]}
    project.mkdir(parents=True, exist_ok=True)
    (project / "budget.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(REPO.resolve()))
    except ValueError:
        raise Problem(f"{path} is outside this repository")


def approved_by(mover):
    """Whose approval counts for a mover: a person's for owner and person:<name>, that bot's for bot:<slug>."""
    return mover if mover.startswith("bot:") else "human"


def approval(asset, mover="owner"):
    """(approved, detail) from `hub file approved --by`: exit 0 approved, 1 not approved, anything else is a failure."""
    command = ["hub", "file", "approved", rel(asset), "--by", approved_by(mover)]
    try:
        done = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=60)
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
def question(task, person, text, files, extra=()):
    """`hub task question`: one decision for a person, as its own task under the project task."""
    command = ["hub", "task", "question", text, "--on", person, "--parent", task]
    for path in files:
        command += ["--review", rel(path)]
    return shlex.join(command + list(extra))


def request(project, cfg, stage, files, task, owner):
    """The step that puts a stage's unapproved files to its mover."""
    mover = stage["mover"]
    if mover == "owner" and not owner:
        return NO_OWNER
    if mover.startswith("bot:"):
        # The reviewer runs in its own repository, so it reviews by file id and version: this bot publishes each file
        # first (step 1), then fills the <FILE@N> placeholders from that output into the hand-off (step 2).
        steps = [f"1. {shlex.join(['hub', 'file', 'publish', rel(f)])}" for f in files]
        lines = [f"Review each file for the {stage['id']} stage of {project.name}. Files, as <FILE@N> from step 1:"]
        lines += [f"- {rel(f)}: <FILE@N>" for f in files]
        lines.append("Run for each one: hub file review <FILE@N> --approve|--changes --task <this task> [--note TEXT]")
        create = ["hub", "task", "create", "--owner", mover, "--parent", task,
                  "--title", f"Review {stage['id']} files for {project.name}", "--body", "\n".join(lines)]
        steps.append(f"2. {shlex.join(create)}")
        return "\n".join(steps)
    person = owner if mover == "owner" else mover.split(":", 1)[1]
    return question(task, person, f"Approve the {stage['id']} files for {project.name}?", files)


def self_step(project, cfg, stage, task, owner):
    """A self-moved stage proceeds while the project's spend is under its budget plus any raise the owner approved."""
    used, limit = spent(project, cfg), cfg["budget"] + budget_record(project)["extra_usd"]
    if used < limit:
        return f"self: proceed with {stage['id']} ({used:.2f} of {limit:.2f} USD used)"
    if not owner:
        return NO_OWNER
    raise_by = f"{cfg['budget']:g}"
    options = [f"Raise budget -> the self-moved stages of {project.name} may spend {raise_by} USD more",
               "Stop -> no more self-moved work on this project"]
    extra = [item for option in options for item in ("--option", option)]
    extra += ["--spend", f"Raise budget={raise_by}:{project.name} self-moved stages"]
    return (f"over budget ({used:.2f} of {limit:.2f} USD used): ask the owner: "
            + question(task, owner, f"Spend {raise_by} USD more on {project.name}? {used:.2f} of {limit:.2f} USD used.",
                       [], extra))


def next_step(project, cfg, task="TASK-ID", owner=None):
    """The one step to take now. Stages run in order. A self-moved stage holds until its `.done` file exists. Any
    other stage waits until each of its files is approved by its mover."""
    owner = owner or cfg["owner"]
    for stage in cfg["stages"]:
        folder = project / stage["id"]
        if stage["mover"] == "self":
            if (folder / ".done").exists():
                continue
            return self_step(project, cfg, stage, task, owner)
        files = assets(folder)
        if not files:
            return f"waiting: make the {stage['id']} files in {rel(folder)}, then run next again"
        pending = [f for f in files if not approval(f, stage["mover"])[0]]
        if pending:
            return request(project, cfg, stage, pending, task, owner)
    return "nothing to do: every stage is done or approved"


def gate(asset):
    """Exit status for a file the next stage wants to use: 0 when its stage is self-moved or the file is approved."""
    cfg = read()
    try:
        parts = Path(asset).resolve().relative_to(PROJECTS.resolve()).parts
    except ValueError:
        parts = ()
    if len(parts) < 3:
        raise Problem(f"{asset} is not a file in projects/<slug>/<stage>/")
    stage = stage_named(cfg, parts[1])
    if stage is None:
        raise Problem(f"{asset}: no stage {parts[1]!r} in pipeline.yaml")
    if stage["mover"] == "self":
        print(f"self: {stage['id']} needs no review")
        return 0
    ok, detail = approval(Path(asset).resolve(), stage["mover"])
    if ok:
        print(f"approved: {rel(asset)}" + (f" by {detail['by']}" if detail.get("by") else ""))
        return 0
    print(f"not approved: {rel(asset)}; ask {stage['mover']}")
    return 1


def status(project, cfg, task="TASK-ID", owner=None):
    owner = owner or cfg["owner"]
    lines = []
    for stage in cfg["stages"]:
        folder = project / stage["id"]
        files = assets(folder)
        if stage["mover"] == "self":
            lines.append(f"{stage['id']:<11} {'self':<16} {'done' if (folder / '.done').exists() else 'in progress'}")
            for f in files:
                lines.append(f"{'':<11} {f.name:<28} {cost(f):.2f} USD")
            continue
        if not files:
            lines.append(f"{stage['id']:<11} {stage['mover']:<16} (no files yet)")
        for f in files:
            state = "approved" if approval(f, stage["mover"])[0] else "not approved"
            lines.append(f"{stage['id']:<11} {stage['mover']:<16} {f.name:<28} {state:<12} {cost(f):.2f} USD")
    record = budget_record(project)
    lines.append(f"budget {cfg['budget']:.2f} USD + {record['extra_usd']:.2f} raised, {spent(project, cfg):.2f} USD spent "
                 "by self-moved stages")
    lines.append("next: " + next_step(project, cfg, task, owner))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="The Video Producer's pipeline (pipeline.yaml).")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "next"):
        sub = commands.add_parser(name)
        sub.add_argument("project", help="projects/<slug>")
        sub.add_argument("--task", default="TASK-ID", help="the project's Tico task: review questions go under it")
        sub.add_argument("--owner", default="", help="the person who approves owner stages (default: pipeline.yaml)")
    gate_cmd = commands.add_parser("gate")
    gate_cmd.add_argument("file", help="projects/<slug>/<stage>/<file>")
    raise_cmd = commands.add_parser("raise")
    raise_cmd.add_argument("project", help="projects/<slug>")
    raise_cmd.add_argument("--usd", type=float, required=True, help="the amount the owner approved")
    raise_cmd.add_argument("--approval", required=True, help="the spend approval id the owner's answer created")
    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            return gate(args.file)
        if args.command == "raise":
            record = record_raise(Path(args.project).resolve(), args.usd, args.approval)
            print(f"budget raised: {record['extra_usd']:.2f} USD raised in total")
            return 0
        cfg = read()
        project = Path(args.project).resolve()
        print(status(project, cfg, args.task, args.owner) if args.command == "status"
              else next_step(project, cfg, args.task, args.owner))
        return 0
    except Problem as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
