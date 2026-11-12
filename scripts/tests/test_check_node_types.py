"""scripts/check_node_types.py: the Node scripts' TypeScript gate (effective options, coverage, no declare global, tsc
with no baseline). tsc itself never runs here: its --showConfig output and its diagnostics are given."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_node_types as node  # noqa: E402

GOOD_OPTIONS = {
    "strict": True,
    "noEmit": True,
    "erasableSyntaxOnly": True,
    "verbatimModuleSyntax": True,
    "moduleDetection": "force",
    "strictNullChecks": True,
    # What the page project (recorder/ui) needs too; the Node projects' lib and types are not checked.
    "lib": ["es2022", "dom"],
    "types": [],
}
LEAF = {"extends": "../tsconfig.node.json", "include": ["./**/*.cts"]}


def project_root(tmp_path, monkeypatch, files, options=None):
    """A repository with tsconfig.node.json and one leaf config per project; `files` maps a path to its text. Each
    project's effective config holds GOOD_OPTIONS (or `options`) and every file under its folder."""
    (tmp_path / node.BASE).write_text("{}")
    for config in node.PROJECTS:
        (tmp_path / config).parent.mkdir(parents=True, exist_ok=True)
        up = "../" * len(Path(config).parent.parts)  # recorder/ui/tsconfig.json sits two folders down
        (tmp_path / config).write_text(json.dumps({**LEAF, "extends": f"{up}{node.BASE}"}))
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    def effective(config, root=None):
        folder = str(Path(config).parent)
        mine = [f"./{Path(rel).relative_to(folder).as_posix()}" for rel in files if rel.startswith(folder + "/")]
        return {"compilerOptions": dict(options or GOOD_OPTIONS), "files": mine}

    monkeypatch.setattr(node, "ROOT", tmp_path)
    monkeypatch.setattr(node, "effective_config", effective)
    monkeypatch.setattr(node, "ts_files", lambda: sorted(rel for rel in files if not rel.startswith("ui/")))
    return tmp_path


def no_tsc(config):
    pytest.fail(f"tsc must not run on {config} while a rule before it fails")


def test_the_real_projects_extend_the_shared_base_and_ui_tests_does_too():
    for config in node.PROJECTS:
        assert node.extends_problems(config) == [], config
    ui_tests = json.loads((node.ROOT / "ui/tests/tsconfig.json").read_text())
    assert (node.ROOT / "ui/tests" / ui_tests["extends"]).resolve() == (node.ROOT / node.BASE).resolve()
    assert "include" not in json.loads((node.ROOT / node.BASE).read_text())


def test_clean_projects_pass_and_tsc_runs_on_each(tmp_path, monkeypatch):
    files = {
        "scripts/a.cts": "export {};\n",
        "recorder/tests/b.cts": "export {};\n",
        "recorder/ui/app/c.ts": "export {};\n",
    }
    project_root(tmp_path, monkeypatch, files)
    ran = []
    monkeypatch.setattr(node, "run_project", lambda config: ran.append(config) or (0, ""))
    assert node.check() == []
    assert ran == list(node.PROJECTS)


def test_a_diagnostic_fails_with_no_baseline(tmp_path, monkeypatch):
    project_root(tmp_path, monkeypatch, {"scripts/a.cts": "export {};\n"})
    output = "scripts/a.cts(1,7): error TS2322: Type 'number' is not assignable to type 'string'.\n"
    monkeypatch.setattr(
        node, "run_project", lambda config: (2, output) if config == "scripts/tsconfig.json" else (0, "")
    )
    problems = node.check()
    assert problems[0] == "scripts/tsconfig.json: tsc exited 2; fix every diagnostic (no baseline)"
    assert "TS2322" in problems[1] and len(problems) == 2
    assert node.main([]) == 1


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda o: o.update(strict=False), "strict must be true (in effect, extends followed)"),
        (lambda o: o.pop("noEmit"), "noEmit must be true"),
        (lambda o: o.pop("erasableSyntaxOnly"), "erasableSyntaxOnly must be true"),
        (lambda o: o.update(verbatimModuleSyntax=False), "verbatimModuleSyntax must be true"),
        (lambda o: o.update(moduleDetection="auto"), 'moduleDetection must be "force"'),
        (lambda o: o.update(strictNullChecks=False), "strictNullChecks may not be false"),
        (lambda o: o.update(noCheck=True), "noCheck is not allowed"),
    ],
)
def test_a_weakened_effective_option_fails_without_running_tsc(tmp_path, monkeypatch, change, expected):
    options = dict(GOOD_OPTIONS)
    change(options)
    project_root(tmp_path, monkeypatch, {"scripts/a.cts": "export {};\n"}, options)
    monkeypatch.setattr(node, "run_project", no_tsc)
    problems = node.check()
    assert any(problem.endswith(expected) or expected in problem for problem in problems), problems


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda o: o.update(lib=["es2022"]), "recorder/ui/tsconfig.json: lib must include DOM"),
        (lambda o: o.pop("lib"), "recorder/ui/tsconfig.json: lib must include DOM"),
        (lambda o: o.update(types=["node"]), "recorder/ui/tsconfig.json: types must be []"),
        (lambda o: o.pop("types"), "recorder/ui/tsconfig.json: types must be []"),
    ],
)
def test_the_page_project_needs_the_dom_and_no_node_types(tmp_path, monkeypatch, change, expected):
    page = dict(GOOD_OPTIONS)
    change(page)
    project_root(tmp_path, monkeypatch, {"recorder/ui/app/a.ts": "export {};\n"})
    node_options = {**GOOD_OPTIONS, "lib": ["es2022"], "types": ["node"]}

    def effective(config, root=None):
        if config == "recorder/ui/tsconfig.json":
            return {"compilerOptions": page, "files": ["./app/a.ts"]}
        return {"compilerOptions": node_options, "files": []}

    monkeypatch.setattr(node, "effective_config", effective)
    monkeypatch.setattr(node, "run_project", no_tsc)
    # Only the page project is held to it: the Node projects here have Node's types and no DOM, and pass.
    [problem] = node.check()
    assert problem.startswith(expected)


def test_the_effective_options_are_read_not_the_raw_json(tmp_path, monkeypatch):
    # The leaf names no flag at all (they come from the base): it passes. A leaf whose raw JSON looks strict but whose
    # options in effect are not fails.
    project_root(tmp_path, monkeypatch, {"scripts/a.cts": "export {};\n"})
    monkeypatch.setattr(node, "run_project", lambda config: (0, ""))
    assert node.check() == []
    raw_strict = {**LEAF, "compilerOptions": GOOD_OPTIONS}
    (tmp_path / "scripts/tsconfig.json").write_text(json.dumps(raw_strict))
    monkeypatch.setattr(node, "effective_config", lambda config, root=None: {"compilerOptions": {}, "files": []})
    monkeypatch.setattr(node, "run_project", no_tsc)
    assert any("strict must be true" in problem for problem in node.check())


def test_effective_config_runs_show_config_and_reports_a_config_tsc_cannot_read(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        if "bad/tsconfig.json" in command:
            return subprocess.CompletedProcess(command, 1, "error TS5083: Cannot read file 'missing.json'.\n", "")
        return subprocess.CompletedProcess(command, 0, json.dumps({"compilerOptions": GOOD_OPTIONS}), "")

    monkeypatch.setattr(node.subprocess, "run", run)
    assert node.effective_config("scripts/tsconfig.json")["compilerOptions"] == GOOD_OPTIONS
    assert calls[0] == ([*node.TSC, "-p", "scripts/tsconfig.json", "--showConfig"], node.ROOT)
    node.effective_config("ui/tests/tsconfig.json", root=tmp_path)
    assert calls[1][1] == tmp_path
    with pytest.raises(node.ConfigError, match="bad/tsconfig.json: tsc --showConfig exited 1: error TS5083"):
        node.effective_config("bad/tsconfig.json")


def test_a_leaf_that_does_not_extend_the_base_fails(tmp_path, monkeypatch):
    root = project_root(tmp_path, monkeypatch, {"scripts/a.cts": "export {};\n"})
    monkeypatch.setattr(node, "run_project", no_tsc)
    (root / "scripts/tsconfig.json").write_text(json.dumps({"extends": "./other.json", "include": ["./**/*.cts"]}))
    assert node.check() == [
        "scripts/tsconfig.json: extends must name tsconfig.node.json (its options are shared with ui/tests/tsconfig.json)"
    ]
    (root / "scripts/tsconfig.json").write_text("{not json")
    assert "cannot be read as JSON" in node.check()[0]


def test_typescript_outside_every_project_fails(tmp_path, monkeypatch):
    project_root(tmp_path, monkeypatch, {"scripts/a.cts": "export {};\n", "tools/new.mts": "export {};\n"})
    monkeypatch.setattr(node, "run_project", no_tsc)
    [problem] = node.check()
    assert problem.startswith("tools/new.mts: in no TypeScript project")


def test_typescript_files_are_found_outside_ui_only(tmp_path, monkeypatch):
    for rel in ("scripts/a.cts", "recorder/b.mts", "recorder/c.d.ts", "ui/app/d.ts", "scripts/e.py", "x/f.tsx"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr(node, "ROOT", tmp_path)
    assert node.ts_files() == ["recorder/b.mts", "recorder/c.d.ts", "scripts/a.cts", "x/f.tsx"]


def test_a_declare_global_in_a_script_fails_and_a_declaration_file_may_hold_one(tmp_path, monkeypatch):
    files = {
        "scripts/a.cts": "declare global {\n  interface Window { x: number }\n}\nexport {};\n",
        "recorder/tests/globals.d.ts": "declare global {\n  var helper: number;\n}\nexport {};\n",
        "recorder/ui/b.mts": "declare const window: Window & typeof globalThis & {x: number};\nexport {};\n",
        "recorder/ui/app/c.ts": "declare global {\n  interface Window { y: number }\n}\nexport {};\n",
    }
    project_root(tmp_path, monkeypatch, files)
    monkeypatch.setattr(node, "run_project", no_tsc)
    problems = node.check()
    assert [problem.split(":")[0] for problem in problems] == ["recorder/ui/app/c.ts", "scripts/a.cts"]
    assert all("`declare global` is not allowed in a script" in problem for problem in problems)
