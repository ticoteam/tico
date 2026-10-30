"""The Video Producer's pipeline script (templates/catalog/video-producer/software/pipeline.py) with `hub` stubbed: a stage
waits for its mover's approval of each file, a self-moved stage runs until its budget, and a file is used only when it
is approved or its stage is self-moved."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "templates/catalog/video-producer"
loader = importlib.machinery.SourceFileLoader("pipeline", str(TEMPLATE / "software/pipeline.py"))
pipeline = importlib.util.module_from_spec(importlib.util.spec_from_loader("pipeline", loader))
loader.exec_module(pipeline)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(pipeline, "REPO", root)
    monkeypatch.setattr(pipeline, "PROJECTS", root / "projects")
    monkeypatch.setattr(pipeline, "PIPELINE", TEMPLATE / "pipeline.yaml")
    approved = set()

    def hub_approved(asset):              # stands in for `hub file approved`: (approved, detail)
        rel = asset.relative_to(root).as_posix()
        return rel in approved, ({"by": "Ana"} if rel in approved else {})

    monkeypatch.setattr(pipeline, "approval", hub_approved)
    return SimpleNamespace(root=root, project=root / "projects" / "launch", approved=approved)


def put(repo, stage, name, cost=None, approve=False):
    folder = repo.project / stage
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("x")
    if cost is not None:
        (folder / (name + ".json")).write_text(json.dumps({"stage": stage, "cost_usd": cost, "tool": "test", "at": "now"}))
    if approve:
        repo.approved.add(path.relative_to(repo.root).as_posix())
    return path


def test_the_shipped_pipeline_has_the_default_movers_and_budgets():
    stages = {s["id"]: s for s in pipeline.load(TEMPLATE / "pipeline.yaml")}
    assert list(stages) == ["brief", "script", "storyboard", "shots", "cut", "final", "publish"]
    assert [stages[k]["mover"] for k in ("script", "storyboard", "final", "publish")] == ["owner"] * 4
    assert (stages["shots"]["mover"], stages["shots"]["budget"]) == ("self", 10.0)
    assert (stages["cut"]["mover"], stages["cut"]["budget"]) == ("self", 10.0)


def test_a_mover_that_is_not_a_known_form_is_refused(tmp_path):
    bad = tmp_path / "pipeline.yaml"
    bad.write_text("stages:\n  - id: script\n    mover: friend\n")
    with pytest.raises(pipeline.Problem):
        pipeline.load(bad)


def test_a_stage_waits_for_its_mover_and_the_next_one_starts_once_approved(repo):
    stages = pipeline.load()
    put(repo, "brief", "brief.md", approve=True)
    script = put(repo, "script", "intro.md")
    step = pipeline.next_step(repo.project, stages, task="T-1")
    assert step.startswith("hub task question T-1 --on owner --ask ")
    assert "--review projects/launch/script/intro.md" in step
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.next_step(repo.project, stages).startswith("waiting: make the storyboard files")


def test_a_self_stage_runs_until_its_budget_is_spent_and_sidecars_are_not_files(repo):
    stages = pipeline.load()
    for stage in ("brief", "script", "storyboard"):
        put(repo, stage, f"{stage}.md", approve=True)
    assert pipeline.next_step(repo.project, stages) == "self: proceed with shots (0.00 of 10.00 USD used)"
    put(repo, "shots", "clip.mp4", cost=9.0)
    assert [p.name for p in pipeline.assets(repo.project / "shots")] == ["clip.mp4"]     # clip.mp4.json is its sidecar
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with cut")
    put(repo, "shots", "clip2.mp4", cost=1.0)
    assert pipeline.next_step(repo.project, stages).startswith("over budget (10.00 of 10.00 USD): ask the owner: hub task question TASK-ID --on owner")


def test_gate_lets_a_file_through_only_when_approved_or_self_moved(repo):
    script = put(repo, "script", "intro.md")
    shots = put(repo, "shots", "clip.mp4")
    assert pipeline.gate(script) == 1
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.gate(script) == 0
    assert pipeline.gate(shots) == 0                      # self-moved: no approval asked
    with pytest.raises(pipeline.Problem):                 # a stage the pipeline does not have
        pipeline.gate(repo.project / "unknown-stage" / "x.md")
