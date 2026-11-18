"""A workflow job that checks out a sparse subset must carry every module its scripts import at load time.

#4135: release jobs sparse-checked-out scripts/pr_checks.py without the flake module it imports (now scripts/flake_detect.py), and
failed on a clean runner while every test (run in a full checkout) passed. This reads the workflows and the scripts'
source only: no network, no checkout, no execution.
"""

import ast
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
SCRIPT = re.compile(r"(?<![\w./-])((?:[\w-]+/)*scripts/\w+\.py)\b")
BARE_IMPORT = re.compile(r"^\s*(?:import|from)\s+(\w+)", re.M)


def sparse_jobs():
    """(workflow, job, checkout path, files in the sparse checkout, run text) for every job with a sparse checkout."""
    found = []
    for workflow in WORKFLOWS:
        for name, job in (yaml.safe_load(workflow.read_text()).get("jobs") or {}).items():
            steps = job.get("steps") or []
            runs = "\n".join(step["run"] for step in steps if "run" in step)
            for step in steps:
                with_ = step.get("with") or {}
                if "sparse-checkout" in with_:
                    files = {line.strip() for line in with_["sparse-checkout"].splitlines() if line.strip()}
                    found.append((workflow.name, name, with_.get("path", ""), files, runs))
    return found


def module_level_nodes(tree):
    """The statements that run when a module is imported: not the bodies of functions or classes."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        for field in ("body", "orelse", "finalbody", "handlers"):
            pending.extend(getattr(node, field, []))


def repo_module(dotted, here):
    """The repo file a module name stands for, looking beside `here` first (scripts run with their folder on sys.path)."""
    parts = dotted.split(".")
    for base in (here.parent, ROOT):
        for candidate in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
            if candidate.is_file():
                return candidate
    return None


def load_time_dependencies(script):
    """Repo files `script` needs once imported: its module-level imports and the repo .py paths it names at module level
    (importlib loads). An import inside a function is a lazy one and is not required to start."""
    needed = set()
    for node in module_level_nodes(ast.parse(script.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.ImportFrom) and node.level == 1:  # `from . import x` beside the script
            names = [alias.name for alias in node.names]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            # A path built as `ROOT / "scripts/x.py"`: what release_train loads with importlib.
            for part in (n for n in ast.walk(node.value) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)):
                text = part.right.value if isinstance(part.right, ast.Constant) else None
                if isinstance(text, str) and text.endswith(".py") and (ROOT / text).is_file():
                    needed.add(ROOT / text)
        for name in names:
            found = repo_module(name, script)
            if found:
                needed.add(found)
    return needed


def closure(script):
    seen, pending = set(), [script]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(load_time_dependencies(current))
    return seen


def invoked_scripts(path, runs):
    """The repo scripts a job's run steps start, as paths from the repo root (the checkout's `path` is stripped)."""
    prefix = f"{path.rstrip('/')}/" if path else ""
    scripts = set()
    for match in SCRIPT.finditer(runs):
        text = match.group(1)
        if text.startswith(prefix) and text[len(prefix) :].startswith("scripts/"):
            scripts.add(text[len(prefix) :])
    for name in BARE_IMPORT.findall(runs):  # python heredocs that put scripts/ on sys.path and import a script
        if (ROOT / "scripts" / f"{name}.py").is_file():
            scripts.add(f"scripts/{name}.py")
    return scripts


def test_there_are_sparse_checkout_jobs_to_guard():
    assert len(sparse_jobs()) >= 4  # a rename of the key must not turn the guard below into a pass over nothing


@pytest.mark.parametrize(
    "workflow,job,path,files,runs", sparse_jobs(), ids=[f"{found[0]}-{found[1]}" for found in sparse_jobs()]
)
def test_a_sparse_checkout_has_every_module_its_scripts_import(workflow, job, path, files, runs):
    scripts = invoked_scripts(path, runs)
    missing = {
        f"{dependency.relative_to(ROOT)} (imported by {script})"
        for script in scripts
        for dependency in closure(ROOT / script)
        if str(dependency.relative_to(ROOT)) not in files
    }
    assert not missing, f"{workflow} job {job}: add to its sparse-checkout: {sorted(missing)}"


def test_the_guard_sees_a_missing_module(tmp_path):
    # pr_checks.py imports flake_detect at load time; its module is what #4135 left out of the release jobs.
    assert ROOT / "scripts/flake_detect.py" in closure(ROOT / "scripts/pr_checks.py")
    assert invoked_scripts("", "python3 scripts/pr_checks.py durations") == {"scripts/pr_checks.py"}
