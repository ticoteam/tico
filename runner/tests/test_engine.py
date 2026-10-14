"""The runner engine switch (runner/engine.py): exec the Rust runner only when it is asked for and current."""

import json

from runner import engine
from runner.service import RUNNER_VERSION


def fake_binary(path, version, build_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\ncase \"$1\" in\n"
                    f"  --version) echo 'tico-runner {version}' ;;\n  --build-id) echo '{build_id}' ;;\n  *) exit 2 ;;\nesac\n")
    path.chmod(0o755)
    return path


def test_a_current_rust_binary_takes_the_process_and_anything_else_stays_in_python(tmp_path):
    calls = []
    execv = lambda program, argv, env: calls.append((program, argv, env))  # noqa: E731
    argv = ["--config", "/x/runner.json", "run"]
    given = fake_binary(tmp_path / "bin" / "tico-runner", RUNNER_VERSION, engine.source_id(engine.ROOT))

    engine.maybe_exec({}, argv, env={"TICO_RUNNER_BIN": str(given)}, execv=execv)
    engine.maybe_exec({"engine": "rust"}, argv, env={"TICO_RUNNER_ENGINE": "python", "TICO_RUNNER_BIN": str(given)}, execv=execv)
    assert calls == []                                   # not asked for, or turned off for this start

    engine.maybe_exec({"engine": "rust"}, argv, env={"TICO_RUNNER_BIN": str(given), "PATH": ""}, execv=execv)
    program, args, env = calls.pop()
    assert (program, args[1:]) == (str(given), argv)
    assert json.loads(env["TICO_RUNNER_PYTHON_ARGV"])[-3:] == argv  # a refusal comes back here

    # A checkout of its own: the binary must carry this tree's fingerprint, not just its version number.
    root = tmp_path / "checkout"
    (root / "runner-rs" / "src").mkdir(parents=True)
    (root / "runner-rs" / "src" / "main.rs").write_text("fn main() {}\n")
    before = engine.source_id(root)
    own = fake_binary(root / "runner-rs" / "target" / "release" / "tico-runner", RUNNER_VERSION, before)
    assert engine.choose({"engine": "rust"}, {}, root=root, building=lambda *a: False) == own
    (root / "runner-rs" / "src" / "main.rs").write_text("fn main() { run() }\n")   # the checkout moved on
    assert engine.source_id(root) != before
    assert engine.choose({"engine": "rust"}, {}, root=root, building=lambda *a: False) is None  # stale, no cargo: Python
    built = []

    def rebuild(at, env):
        built.append(at)
        fake_binary(own, RUNNER_VERSION, engine.source_id(at))
        return True
    stale = fake_binary(tmp_path / "old" / "tico-runner", "0.0.1", before)
    assert engine.choose({"engine": "rust"}, {"TICO_RUNNER_BIN": str(stale)}, root=root, building=rebuild) == own
    assert built == [root]
