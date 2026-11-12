"""scripts/ui_baseline.py: per-file baselines, the old single-file layout, the migration and the growth check."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ui_baseline  # noqa: E402


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@acme.example",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@acme.example",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def finding(file, line=1):
    return ui_baseline.Finding(file, line, 1, "TS2304", "Cannot find name 'x'.")


def single_file(root, tool, files):
    path = root / ui_baseline.SINGLE_FILE[tool]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"files": files}, indent=2) + "\n")


def test_each_file_has_its_own_baseline_file_and_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    findings = [finding("ui/app/a.js"), finding("ui/app/a.js", 2), finding("ui/app/b/c.js")]
    ui_baseline.write_layout("tsc", ui_baseline.counts_of(findings))
    assert (tmp_path / "ui/baselines/tsc/app/a.js.json").read_text() == '{"count": 2}\n'
    assert json.loads((tmp_path / "ui/baselines/tsc/app/b/c.js.json").read_text()) == {"count": 1}
    assert ui_baseline.load_layout("tsc") == {"ui/app/a.js": 2, "ui/app/b/c.js": 1}


def test_a_file_that_reaches_zero_loses_its_baseline_and_empty_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("biome", {"ui/app/gone/a.js": 3, "ui/app/b.js": 2})
    ui_baseline.write_layout("biome", {"ui/app/b.js": 1})
    assert ui_baseline.load_layout("biome") == {"ui/app/b.js": 1}
    assert not (tmp_path / "ui/baselines/biome/app/gone").exists()
    assert (tmp_path / "ui/baselines/biome/.gitkeep").exists()


def test_write_never_raises_a_count(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("biome", {"ui/app/a.js": 1})
    with pytest.raises(SystemExit):
        ui_baseline.write([finding("ui/app/a.js"), finding("ui/app/a.js", 2)], "biome")
    assert ui_baseline.load_layout("biome") == {"ui/app/a.js": 1}


def test_a_baseline_file_at_zero_is_stale_and_fails_the_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    path = tmp_path / "ui/baselines/tsc/app/a.js.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"count": 0}\n')
    assert ui_baseline.check([], "tsc", None) == 1
    assert "STALE ui/app/a.js" in capsys.readouterr().out


def test_migrate_splits_the_single_files_and_removes_them(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    single_file(tmp_path, "tsc", {"ui/app/a.js": 4, "ui/app/b.js": 1})
    single_file(tmp_path, "biome", {})
    (tmp_path / "ui/index.html").write_text('<head>\n<script src="/tico/ui/app/a.js"></script>\n</head>\n')
    assert ui_baseline.main(["migrate"]) == 0
    assert ui_baseline.load_layout("tsc") == {"ui/app/a.js": 4, "ui/app/b.js": 1}
    assert ui_baseline.load_layout("biome") == {}
    assert not (tmp_path / "ui/types-baseline.json").exists()
    assert not (tmp_path / "ui/lint-baseline.json").exists()


def test_migrate_again_drops_baselines_the_old_file_no_longer_has(tmp_path, monkeypatch):
    # A rebase: this branch wrote its layout earlier, then main's old file (one entry gone) is taken back.
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("tsc", {"ui/app/a.js": 4, "ui/app/converted.js": 2})
    single_file(tmp_path, "tsc", {"ui/app/a.js": 4})
    (tmp_path / "ui/index.html").write_text("<head>\n</head>\n")
    ui_baseline.main(["migrate"])
    assert ui_baseline.load_layout("tsc") == {"ui/app/a.js": 4}


def test_the_growth_check_reads_the_single_file_layout_at_a_ref(tmp_path, monkeypatch, capsys):
    git(tmp_path, "init", "-q", "-b", "main")
    single_file(tmp_path, "tsc", {"ui/app/a.js": 2})
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("tsc", {"ui/app/a.js": 3})
    assert ui_baseline.check([finding("ui/app/a.js", n) for n in range(3)], "tsc", "HEAD") == 1
    assert "GREW ui/app/a.js: 2 at HEAD, now 3" in capsys.readouterr().out


def test_the_growth_check_reads_the_per_file_layout_at_a_ref(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("tsc", {"ui/app/a.js": 2, "ui/app/b.js": 1})
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    ui_baseline.write_layout("tsc", {"ui/app/a.js": 2, "ui/app/b.js": 2})  # b.js grew in this change
    findings = [finding("ui/app/a.js"), finding("ui/app/a.js", 2), finding("ui/app/b.js"), finding("ui/app/b.js", 2)]
    assert ui_baseline.check(findings, "tsc", "HEAD") == 1
    assert "GREW ui/app/b.js: 1 at HEAD, now 2" in capsys.readouterr().out
    ui_baseline.write_layout(
        "tsc", {"ui/app/a.js": 2}
    )  # b.js is converted: its baseline file goes, and it is not grown
    assert ui_baseline.check(findings[:2], "tsc", "HEAD") == 0


def test_a_ref_with_only_the_empty_folder_marker_has_an_empty_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_baseline, "ROOT", tmp_path)
    ui_baseline.write_layout("biome", {})  # every file is clean: the folder holds only .gitkeep
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "clean")
    assert ui_baseline.layout_at("HEAD", "biome") == {}
    assert ui_baseline.layout_at("HEAD", "tsc") is None


def test_run_tsc_drops_typescript_findings_but_counts_them_as_diagnostics(monkeypatch):
    out = (
        "ui/app/a.ts(1,1): error TS2304: Cannot find name 'x'.\nui/app/b.js(2,3): error TS2304: Cannot find name 'y'.\n"
    )
    done = subprocess.CompletedProcess(["tsc"], 2, stdout=out, stderr="")
    monkeypatch.setattr(ui_baseline.subprocess, "run", lambda *args, **kwargs: done)
    findings, _ = ui_baseline.run_tsc()
    assert [f.file for f in findings] == ["ui/app/b.js"]


def test_run_tsc_with_only_typescript_findings_is_not_a_crash(monkeypatch):
    out = "ui/app/a.ts(1,1): error TS2304: Cannot find name 'x'.\n"
    done = subprocess.CompletedProcess(["tsc"], 2, stdout=out, stderr="")
    monkeypatch.setattr(ui_baseline.subprocess, "run", lambda *args, **kwargs: done)
    assert ui_baseline.run_tsc()[0] == []


def test_run_tsc_that_exits_with_no_diagnostics_fails(monkeypatch):
    done = subprocess.CompletedProcess(["tsc"], 2, stdout="", stderr="crash")
    monkeypatch.setattr(ui_baseline.subprocess, "run", lambda *args, **kwargs: done)
    with pytest.raises(SystemExit):
        ui_baseline.run_tsc()
