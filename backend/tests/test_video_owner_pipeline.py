"""The Video Owner's pipeline script (templates/catalog/video-owner/software/pipeline.py) with `hub` stubbed: a
stage waits for its mover's approval of each file, the owner is found from pipeline.yaml or the command, a self-moved
stage runs until its project budget (plus any raise the owner approved) and is done on its `.done` file, a file is
used only when it is approved or its stage is self-moved, and a failed `tico file approved` stops the pipeline instead of
reading as pending."""

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "templates/catalog/video-owner"
loader = importlib.machinery.SourceFileLoader("pipeline", str(TEMPLATE / "software/pipeline.py"))
pipeline = importlib.util.module_from_spec(importlib.util.spec_from_loader("pipeline", loader))
loader.exec_module(pipeline)
REAL_APPROVAL = pipeline.approval  # the fixture swaps it for a fake; the --by test calls the real one


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(pipeline, "REPO", root)
    monkeypatch.setattr(pipeline, "PROJECTS", root / "projects")
    monkeypatch.setattr(pipeline, "PIPELINE", root / "pipeline.yaml")
    (root / "pipeline.yaml").write_text((TEMPLATE / "pipeline.yaml").read_text())
    approved = set()

    def hub_approved(asset, mover="owner"):  # stands in for `tico file approved --by`: (approved, detail)
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
        (folder / (name + ".json")).write_text(
            json.dumps({"stage": stage, "cost_usd": cost, "tool": "test", "at": "now"})
        )
    if approve:
        repo.approved.add(path.relative_to(repo.root).as_posix())
    return path


def finished_up_to_shots(repo):
    for stage in ("brief", "script", "storyboard"):
        put(repo, stage, f"{stage}.md", approve=True)


def test_the_shipped_pipeline_has_the_default_owner_budget_and_movers():
    pipe = cfg()
    assert pipe["owner"] == "" and pipe["budget"] == 10.0
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
    publish, ask = step.splitlines()
    assert publish == "1. tico file publish projects/launch/script/intro.md"
    assert ask.startswith("2. tico task ask T-1 --to 'Ana Lee' 'OK the script files for launch?' --kind ok")
    assert "--show file:<FILE@N> --version N" in ask
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.next_step(repo.project, stages, owner="Ana Lee").startswith("waiting: make the storyboard files")
    put(repo, "storyboard", "sheet.html")  # now the storyboard waits for the owner
    (repo.root / "pipeline.yaml").write_text(
        (repo.root / "pipeline.yaml").read_text().replace('owner: ""', "owner: Ana Lee")
    )
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1")  # the owner comes from pipeline.yaml
    assert "--to 'Ana Lee'" in step and "tico file publish projects/launch/storyboard/sheet.html" in step


def test_a_bot_mover_gets_a_hand_off_task_not_a_question(repo):
    cfg(repo)
    (repo.root / "pipeline.yaml").write_text(
        (repo.root / "pipeline.yaml")
        .read_text()
        .replace("  - id: script\n    mover: owner", "  - id: script\n    mover: bot:reviewer")
    )
    put(repo, "brief", "brief.md", approve=True)
    put(repo, "script", "intro.md")
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1")
    assert "tico task create --owner bot:reviewer --parent T-1" in step and "--show" not in step
    assert "projects/launch/script/intro.md" in step


def test_a_self_stage_is_done_on_its_done_file_and_the_budget_gates_it(repo):
    stages = cfg(repo)
    finished_up_to_shots(repo)
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with shots (0.00 of 10.00 USD used)")
    put(repo, "shots", "clip.mp4", cost=4.0)
    assert [p.name for p in pipeline.assets(repo.project / "shots")] == ["clip.mp4"]  # its sidecar is not an asset
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with shots")  # one shot is not done
    (repo.project / "shots" / ".done").write_text("")
    assert pipeline.next_step(repo.project, stages).startswith("self: proceed with cut (4.00 of 10.00 USD used)")
    put(repo, "cut", "edit.json", cost=5.0)
    assert pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee").startswith("self: proceed with cut")
    (repo.project / "cut" / ".done").write_text("")
    assert pipeline.next_step(repo.project, stages).startswith("waiting: make the final files")


class FakeOks:
    """Stands in for `tico ok use` against Tico's OK grants (backend/task_asks.py use): exit 0 only for an OK that was
    given, for the same subject and exactly the same content, and not used before; it is used then."""

    def __init__(self):
        self.grants, self.calls = {}, []

    def ask(self, rid, subject, content, given=True):
        self.grants[rid] = {"subject": subject, "content": content, "given": given, "used": False}

    def run(self, command, **kwargs):
        self.calls.append(command)
        assert command[0] in ("tico", "hub") and command[1:3] == ["ok", "use"]
        rid, subject = command[3], command[command.index("--for") + 1]
        content = json.loads(command[command.index("--content") + 1])
        grant = self.grants.get(rid)
        refusal = (
            "No such OK"
            if not grant
            else "This OK was already used"
            if grant["used"]
            else "This OK is for something else"
            if grant["subject"] != subject
            else "It changed after it was asked about"
            if grant["content"] != content
            else "It waits for an OK"
            if not grant["given"]
            else None
        )
        if refusal:
            return subprocess.CompletedProcess(command, 1, "", json.dumps({"error": refusal}))
        grant["used"] = True
        return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True}), "")


def over_budget(repo):
    finished_up_to_shots(repo)
    put(repo, "shots", "clip.mp4", cost=10.0)
    (repo.project / "shots" / ".done").write_text("")


def test_going_over_budget_asks_the_owner_for_an_ok_bound_to_the_project_and_amount(repo):
    stages = cfg(repo)
    over_budget(repo)
    ask = pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee")
    assert ask.startswith("over budget (10.00 of 10.00 USD used): ask the owner for an OK: tico task ask T-1")
    assert "--to 'Ana Lee'" in ask and "--for budget_raise:launch" in ask
    assert """--content '{"project":"launch","usd":"10.00"}'""" in ask and "--choices" not in ask
    assert "Spend 10 USD more on launch?" in ask
    assert "software/pipeline.py raise projects/launch --usd 10.00 --ok <the requirement id it printed>" in ask
    assert pipeline.next_step(repo.project, stages, owner="").startswith("waiting: no owner")


def test_a_raise_is_recorded_only_after_tico_takes_the_owners_ok_once(repo, monkeypatch, capsys):
    stages = cfg(repo)
    over_budget(repo)
    oks = FakeOks()
    monkeypatch.setattr(pipeline.subprocess, "run", oks.run)
    oks.ask("req-1", "budget_raise:launch", {"project": "launch", "usd": "10.00"})
    oks.ask("req-2", "budget_raise:launch", {"project": "launch", "usd": "10.00"}, given=False)

    def raise_with(ok, usd="10"):
        return pipeline.main(["raise", str(repo.project), "--usd", usd, "--ok", ok])

    assert raise_with("made-up-OK") == 2  # never asked
    assert raise_with("req-2") == 2  # asked, not given yet
    assert raise_with("req-1", usd="1000000") == 2  # given for 10 USD, not for a million
    assert not (repo.project / "budget.json").exists()
    assert "did not take OK" in capsys.readouterr().err
    assert raise_with("req-1") == 0
    assert raise_with("req-1") == 0  # recorded once; Tico is not asked again
    assert len([c for c in oks.calls if c[3] == "req-1"]) == 2
    assert json.loads((repo.project / "budget.json").read_text())["extra_usd"] == 10.0
    assert pipeline.next_step(repo.project, stages, task="T-1", owner="Ana Lee").startswith("self: proceed with cut")
    (repo.project / "budget.json").unlink()  # a lost record does not let the used OK raise it again
    assert raise_with("req-1") == 2
    assert not (repo.project / "budget.json").exists()


def test_a_raise_is_refused_when_hub_cannot_run(repo, monkeypatch):
    def broken(command, **kwargs):
        raise OSError("hub: not found")

    monkeypatch.setattr(pipeline.subprocess, "run", broken)
    assert pipeline.main(["raise", str(repo.project), "--usd", "10", "--ok", "req-1"]) == 2
    assert not (repo.project / "budget.json").exists()


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
    assert "tico task create --owner bot:reviewer --parent T-1" in hand_off
    assert hand_off.splitlines()[0] == "1. tico file publish projects/launch/script/intro.md"
    assert "<FILE@N>" in hand_off and "tico file review <FILE@N> --approve|--changes --task <this task>" in hand_off
    assert "projects/launch/script/intro.md" not in hand_off.split("\n", 1)[1].replace("intro.md: <FILE@N>", "")


APPROVED = (0, json.dumps({"approved": True, "by": "Ana"}), "")
NOT_APPROVED = (1, json.dumps({"approved": False, "by": None, "verdict": None}), "")
TRACEBACK = (
    "Traceback (most recent call last):\n  File \"clients/hubcli.py\", line 3383, in <module>\n"
    "    sys.exit(main(sys.argv[1:]))\n"
    "TypeError: Client.get() got multiple values for argument 'path'\n"
)


def tico_file_approved(monkeypatch, answers):
    """The real approval() against a fake `tico file approved`: answers maps a file's repo path to (exit, stdout, stderr)."""
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
    tico_file_approved(
        monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": APPROVED}
    )
    assert REAL_APPROVAL(brief) == (True, {"approved": True, "by": "Ana"})
    assert pipeline.next_step(repo.project, cfg(repo), owner="Ana Lee").startswith("waiting: make the storyboard files")
    assert pipeline.gate(script) == 0


def test_the_hubs_not_approved_answer_is_pending_and_asks_for_review(repo, monkeypatch):
    put(repo, "brief", "brief.md")
    script = put(repo, "script", "intro.md")
    tico_file_approved(
        monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": NOT_APPROVED}
    )
    assert REAL_APPROVAL(script) == (False, {})
    step = pipeline.next_step(repo.project, cfg(repo), task="T-1", owner="Ana Lee")
    assert step.startswith("1. tico file publish projects/launch/script/intro.md")
    assert "tico task ask T-1 --to 'Ana Lee' 'OK the script files for launch?' --kind ok --show file:<FILE@N>" in step
    assert pipeline.gate(script) == 1


@pytest.mark.parametrize(
    "code, stdout, stderr",
    [
        (1, "", TRACEBACK),  # a crash: traceback only
        (
            1,
            json.dumps(
                {
                    "error": "unavailable",
                    "retryable": True,
                    "operation_id": "op-1",  # Tico unreachable
                    "detail": "Tico could not confirm this request",
                }
            ),
            "",
        ),
        (2, "", "usage: tico file approved TARGET"),
    ],
)
def test_a_failed_hub_call_is_an_error_not_a_pending_file(repo, monkeypatch, code, stdout, stderr):
    put(repo, "brief", "brief.md")
    script = put(repo, "script", "intro.md")
    tico_file_approved(
        monkeypatch,
        {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": (code, stdout, stderr)},
    )
    with pytest.raises(pipeline.Problem) as caught:
        pipeline.next_step(repo.project, cfg(repo), task="T-1", owner="Ana Lee")
    assert f"tico file approved exited {code}" in str(caught.value)
    assert (stderr or stdout).strip()[-40:] in str(caught.value)
    with pytest.raises(pipeline.Problem):
        pipeline.gate(script)


def test_a_crash_shows_the_stderr_tail_in_the_error_and_stops_the_command(repo, monkeypatch, capsys):
    put(repo, "brief", "brief.md")
    put(repo, "script", "intro.md")
    tico_file_approved(
        monkeypatch, {"projects/launch/brief/brief.md": APPROVED, "projects/launch/script/intro.md": (1, "", TRACEBACK)}
    )
    assert pipeline.main(["next", str(repo.project), "--task", "T-1", "--owner", "Ana Lee"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: tico file approved exited 1 for projects/launch/script/intro.md")
    assert "TypeError: Client.get() got multiple values for argument 'path'" in captured.err


def test_gate_lets_a_file_through_only_when_approved_or_self_moved(repo):
    script = put(repo, "script", "intro.md")
    shots = put(repo, "shots", "clip.mp4")
    assert pipeline.gate(script) == 1
    repo.approved.add(script.relative_to(repo.root).as_posix())
    assert pipeline.gate(script) == 0
    assert pipeline.gate(shots) == 0  # self-moved: no approval asked
    with pytest.raises(pipeline.Problem):  # a stage the pipeline does not have
        pipeline.gate(repo.project / "unknown-stage" / "x.md")
