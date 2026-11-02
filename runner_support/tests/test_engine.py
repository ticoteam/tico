"""The `python -m runner run` shim (runner/engine.py): exec a current Rust runner, download one, or exit 75."""

import gzip
import hashlib
import json

import pytest

from runner import engine
from runner.version import RUNNER_VERSION


def fake_binary(path, version, build_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\ncase \"$1\" in\n"
                    f"  --version) echo 'tico-runner {version}' ;;\n  --build-id) echo '{build_id}' ;;\n  *) exit 2 ;;\nesac\n")
    path.chmod(0o755)
    return path


def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "runner-rs" / "src").mkdir(parents=True)
    (root / "runner-rs" / "src" / "main.rs").write_text("fn main() {}\n")
    return root


def test_only_a_binary_of_this_version_and_these_sources_runs(tmp_path):
    root = checkout(tmp_path)
    stale = fake_binary(tmp_path / "old" / "tico-runner", "0.0.1", engine.source_id(root))
    env = {"TICO_RUNNER_BIN": str(stale)}
    assert engine.ready(env, root) is None and "is version 0.0.1" in engine.current(stale, root)
    own = fake_binary(root / "runner-rs" / "target" / "release" / "tico-runner", RUNNER_VERSION, engine.source_id(root))
    assert engine.ready(env, root) == own
    (root / "runner-rs" / "src" / "main.rs").write_text("fn main() { run() }\n")   # the checkout moved on
    assert engine.ready(env, root) is None and "other runner-rs sources" in engine.current(own, root)


def test_the_shim_execs_the_binary_and_ignores_a_python_setting(tmp_path, capsys):
    root = checkout(tmp_path)
    given = fake_binary(tmp_path / "bin" / "tico-runner", RUNNER_VERSION, engine.source_id(root))
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"engine": "python"}))
    calls = []
    argv = ["--config", str(config), "run"]
    with pytest.raises(SystemExit):
        engine.exec_binary(argv, config_path=config, root=root, execv=lambda *a: calls.append(a),
                           env={"TICO_RUNNER_BIN": str(given), "TICO_RUNNER_ENGINE": "python", "PATH": ""})
    program, args, env = calls.pop()
    assert (program, args) == (str(given), [str(given), *argv])
    assert env["TICO_ROOT"] == str(root) and env["TICO_RUNNER_PYTHON"]
    err = capsys.readouterr().err
    assert "TICO_RUNNER_ENGINE=python is ignored" in err and '"engine": "python" in runner.json is ignored' in err


def test_no_binary_and_no_way_to_get_one_exits_75_saying_why(tmp_path, capsys):
    root = checkout(tmp_path)                       # not a git checkout, so on no release tag; no cargo on PATH
    with pytest.raises(SystemExit) as stopped:
        engine.exec_binary(["run"], root=root, execv=lambda *a: pytest.fail("nothing to exec"), env={"PATH": ""})
    assert stopped.value.code == engine.RETRY_EXIT == 75
    err = capsys.readouterr().err
    assert "not on a release tag" in err and "cargo is not on PATH" in err


def test_a_release_checkout_downloads_its_binary_only_when_the_checksum_matches(tmp_path):
    root = checkout(tmp_path)
    name = engine.asset()
    if not name:
        pytest.skip("no release binary for this platform")
    script = fake_binary(tmp_path / "built" / "tico-runner", RUNNER_VERSION, engine.source_id(root)).read_bytes()
    release = tmp_path / "releases" / "download" / "v0.3.47"
    release.mkdir(parents=True)
    (release / name).write_bytes(gzip.compress(script))
    env = {"TICO_RUNNER_RELEASES": (tmp_path / "releases").as_uri()}
    (release / engine.SUMS).write_text(f"{'0' * 64}  {name}\n")
    assert "does not match its checksum" in engine.fetch(root, env, release="0.3.47")
    assert not engine.dist(root).exists()
    (release / engine.SUMS).write_text(f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n")
    assert engine.fetch(root, env, release="0.3.47") == ""
    assert engine.ready(env, root) == engine.dist(root)
    assert engine.fetch(root, env, release="")                         # a checkout on main has nothing to download
