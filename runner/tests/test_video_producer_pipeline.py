"""The Video Producer's pipeline script (templates/catalog/video-producer/software/pipeline.py) with `hub` stubbed: a
stage waits for its mover's approval of each file, the owner is found from pipeline.yaml or the command, a self-moved
stage runs until its project budget (plus any raise the owner approved) and is done on its `.done` file, a file is
used only when it is approved or its stage is self-moved, and a failed `hub file approved` stops the pipeline instead of
reading as pending."""
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
REAL_APPROVAL = pipeline.approval           # the fixture swaps it for a fake; the --by test calls the real one


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(pipeline, "REPO", root)
    monkeypatch.setattr(pipeline, "PROJECTS", root / "projects")
    monkeypatch.setattr(pipeline, "PIPELINE", root / "pipeline.yaml")
    (root / "pipeline.yaml").write_text((TEMPLATE / "pipeline.yaml").read_text())
    approved = set()

    def hub_approved(asset, mover="owner"):   # stands in for `hub file approved --by`: (approved, detail)
        rel = asset.relative_to(root).as_posix()
        return rel in approved, ({"by": "Ana"} if rel in approved else {})

    monkeypatch.setattr(pipeline, "approval", hub_approved)
    return SimpleNamespace(root=root, project=root / "projects" / "launch", approved=approved)


def cfg(repo=None):
    return pipeline.read(repo.root / "pipeline.yaml" if repo else TEMPLATE / "pipeline.yaml")


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


def finished_up_to_shots(repo):
    for stage in ("brief", "script", "storyboard"):
        put(repo, stage, f"{stage}.md", approve=True)


def test_the_shipped_pipeline_has_the_default_owner_budget_and_movers():
    pipe = cfg()
    assert pipe["owner_id"] == "" and pipe["budget"] == 10.0
    assert [s["id"] for s in pipe["stages"]] == ["brief", "script", "storyboard", "shots", "cut", "final", "publish"]
    movers = {s["id"]: s["mover"] for s in pipe["stages"]}
    assert movers["shots"] == movers["cut"] == "self"
    assert all(movers[k] == "owner" for k in ("script", "storyboard", "final", "publish"))


def test_a_mover_that_is_not_a_known_form_is_refused(tmp_path):
    bad = tmp_path / "pipeline.yaml"
    bad.write_text("budget_usd: 10\nstages:\n  - id: script\n    mover: friend\n")
    with pytest.raises(pipeline.Problem):
        pipeline.read(bad)


def test_the_owner_comes_from_the_pipeline_or_the_command_and_is_asked_for_when_missing(repo):
    stages = cfg(repo)
    put(repo, "brief", "brief.md", approve=True)
    script = put(repo, "script", "intro.md")
    waiting = pipeline.next_step(repo.project, stages, task="T-1")
    assert waiting.startswith("waiting: no owner is named") and "--owner" in waiting
    step = pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee")
    assert step.startswith("hub task question 'Approve the script files for launch?' --on 'Ana Lee' --parent T-1")
    assert "--review projects/launch/script/intro.md" in step
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.next_step(repo.project, stages, owner="Ana Lee").startswith("waiting: make the storyboard files")
    put(repo, "storyboard", "sheet.html")                 # now the storyboard waits for the owner
    (repo.root / "pipeline.yaml").write_text((repo.root / "pipeline.yaml").read_text().replace('owner: ""', "owner: Ana Lee"))
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1")                 # the owner comes from pipeline.yaml
    assert "--on 'Ana Lee' --parent T-1" in step and "--review projects/launch/storyboard/sheet.html" in step


def test_a_bot_mover_gets_a_hand_off_task_not_a_question(repo):
    cfg(repo)
    (repo.root / "pipeline.yaml").write_text((repo.root / "pipeline.yaml").read_text().replace(
        "  - id: script\n    mover: owner", "  - id: script\n    mover: bot:reviewer"))
    put(repo, "brief", "brief.md", approve=True)
    put(repo, "script", "intro.md")
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1")
    assert "hub task create --owner bot:reviewer --parent T-1" in step and "--review" not in step
    assert "projects/launch/script/intro.md" in step


def test_a_self_stage_is_done_on_its_done_file_and_the_budget_gates_it(repo):
    stages = cfg(repo)
    finished_up_to_shots(repo)
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with shots (0.00 of 10.00 USD used)")
    put(repo, "shots", "clip.mp4", cost=4.0)
    assert [p.name for p in pipeline.assets(repo.project / "shots")] == ["clip.mp4"]      # its sidecar is not an asset
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with shots")  # one shot is not done
    (repo.project / "shots" / ".done").write_text("")
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with cut (4.00 of 10.00 USD used)")
    put(repo, "cut", "edit.json", cost=5.0)
    assert pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee").startswith("self: proceed with cut")
    (repo.project / "cut" / ".done").write_text("")
    assert pipeline.next_step(repo.project, stages).startswith("waiting: make the final files")


def test_going_over_budget_asks_the_owner_for_a_raise_and_an_approved_raise_lets_self_stages_go_on(repo, tmp_path):
    stages = cfg(repo)
    finished_up_to_shots(repo)
    put(repo, "shots", "clip.mp4", cost=10.0)
    (repo.project / "shots" / ".done").write_text("")
    ask = pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee")
    assert ask.startswith("over budget (10.00 of 10.00 USD used): ask the owner: hub task question")
    assert "--on 'Ana Lee' --parent T-1" in ask and "--spend" in ask and "--option" in ask
    assert "Raise budget=10:launch self-moved stages" in ask
    assert pipeline.next_step(repo.project, stages, owner="").startswith("waiting: no owner")
    pipeline.main(["raise", str(repo.project), "--usd", "10", "--approval", "spend-42"])
    pipeline.main(["raise", str(repo.project), "--usd", "10", "--approval", "spend-42"])   # the same approval counts once
    assert json.loads((repo.project / "budget.json").read_text())["extra_usd"] == 10.0
    assert pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee").startswith("self: proceed with cut")


def test_a_bot_mover_is_approved_by_that_bot_and_the_hand_off_says_how_to_review(repo, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    intro = put(repo, "script", "intro.md")
    assert REAL_APPROVAL(intro, "bot:reviewer") == (True, {})
    assert calls[-1][-2:] == ["--by", "bot:reviewer"]
    assert REAL_APPROVAL(intro, "owner")[0] is True and calls[-1][-2:] == ["--by", "human"]
    hand_off = pipeline.request(repo.project, cfg(repo), {"id": "script", "mover": "bot:reviewer"}, [intro], "T-1", "")
    assert "hub task create --owner bot:reviewer --parent T-1" in hand_off
    assert hand_off.splitlines()[0] == "1. hub file publish projects/launch/script/intro.md"
    assert "<FILE@N>" in hand_off and "hub file review <FILE@N> --approve|--changes --task <this task>" in hand_off
    assert "projects/launch/script/intro.md" not in hand_off.split("\n", 1)[1].replace("intro.md: <FILE@N>", "")


APPROVED = (0, json.dumps({"approved": True, "by": "Ana"}), "")
NOT_APPROVED = (1, json.dumps({"approved": False, "by": None, "verdict": None}), "")
TRACEBACK = ("Traceback (most recent call last):\n  File \"clients/hubcli.py\", line 3383, in <module>\n"
             "    sys.exit(main(sys.argv[1:]))\n"
             "TypeError: Client.get() got multiple values for argument 'path'\n")


def hub_file_approved(monkeypatch, answers):
    """The real approval() against a fake `hub file approved`: answers maps a file's repo path to (exit, stdout, stderr)."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        code, out, err = answers[command[3]]
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(pipeline, "approval", REAL_APPROVAL)
    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    return calls


def test_exit_0_is_approved_and_the_pipeline_moves_on(repo, monkeypatch):
    brief = put(repo, "brief", "brief.md")
    script = put(repo, "script", "intro.md")
    hub_file_approved(monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": APPROVED})
    assert REAL_APPROVAL(brief) == (True, {"approved": True, "by": "Ana"})
    assert pipeline.next_step(repo.project, cfg(repo), owner="Ana Lee").startswith("waiting: make the storyboard files")
    assert pipeline.gate(script) == 0


def test_the_hubs_not_approved_answer_is_pending_and_asks_for_review(repo, monkeypatch):
    put(repo, "brief", "brief.md")
    script = put(repo, "script", "intro.md")
    hub_file_approved(monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": NOT_APPROVED})
    assert REAL_APPROVAL(script) == (False, {})
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1", owner="Ana Lee")
    assert step.startswith("hub task question 'Approve the script files for launch?' --on 'Ana Lee' --parent T-1")
    assert "--review projects/launch/script/intro.md" in step
    assert pipeline.gate(script) == 1


@pytest.mark.parametrize("code, stdout, stderr", [
    (1, "", TRACEBACK),                                                                        # a crash: traceback only
    (1, json.dumps({"error": "TypeError: Client.get() got multiple values"}), ""),            # an unexpected exception
    (1, json.dumps({"error": "unavailable", "retryable": True, "operation_id": "op-1",         # Tico unreachable
                    "detail": "Tico could not confirm this request"}), ""),
    (2, json.dumps({"error": "validation", "retryable": False, "detail": "--by is human"}), ""),  # bad input
    (2, "", "usage: hub file approved TARGET"),
    (7, "", "something unexpected"),
])
def test_a_failed_hub_call_is_an_error_not_a_pending_file(repo, monkeypatch, code, stdout, stderr):
    put(repo, "brief", "brief.md")
    script = put(repo, "script", "intro.md")
    hub_file_approved(monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": (code, stdout, stderr)})
    with pytest.raises(pipeline.Problem) as caught:
        pipeline.next_step(repo.project, cfg(repo), task="T-1", owner="Ana Lee")
    assert f"hub file approved exited {code}" in str(caught.value)
    assert (stderr or stdout).strip()[-40:] in str(caught.value)
    with pytest.raises(pipeline.Problem):
        pipeline.gate(script)


def test_a_crash_shows_the_stderr_tail_in_the_error_and_stops_the_command(repo, monkeypatch, capsys):
    put(repo, "brief", "brief.md")
    put(repo, "script", "intro.md")
    hub_file_approved(monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": (1, "", TRACEBACK)})
    assert pipeline.main(["next", str(repo.project), "--task", "T-1", "--owner", "Ana Lee"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: hub file approved exited 1 for projects/launch/script/intro.md")
    assert "TypeError: Client.get() got multiple values for argument 'path'" in captured.err


def test_gate_lets_a_file_through_only_when_approved_or_self_moved(repo):
    script = put(repo, "script", "intro.md")
    shots = put(repo, "shots", "clip.mp4")
    assert pipeline.gate(script) == 1
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.gate(script) == 0
    assert pipeline.gate(shots) == 0                      # self-moved: no approval asked
    with pytest.raises(pipeline.Problem):                 # a stage the pipeline does not have
        pipeline.gate(repo.project / "unknown-stage" / "x.md")
