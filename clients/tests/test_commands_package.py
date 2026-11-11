"""The tool table as a package (clients/commands/, re-exported by clients/hubtools.py) and the script that moves it there.

These hold before and after the move: scripts/cleanup/split_commands.py runs this file on its moved copy (check g).
"""

import ast
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from clients import hubtools

ROOT = Path(__file__).absolute().parents[2]  # not resolve(): split_commands runs this file in a linked copy
PACKAGE = ROOT / "clients" / "commands"
MAX_LINES = 1200
MOVED = (PACKAGE / "__init__.py").is_file()

# Each entry point, imported first in a fresh process, must load the whole table: BY_NAME is built once every tool
# module has registered, whichever module a process happens to import first.
FIRST_IMPORTS = [
    "clients.hubtools",
    "clients.hubmcp",
    "clients.hubcli",
    "clients.remotecli",
    "backend.mcp",
    "backend.counsel",
]
FIRST_IMPORTS += [f"clients.{p.stem}" for p in sorted((ROOT / "clients").glob("hubtools_*.py"))]  # until they move
if MOVED:
    FIRST_IMPORTS += ["clients.commands.org"] if (PACKAGE / "org.py").exists() else []
    FIRST_IMPORTS += [
        "clients.commands",
        "clients.commands._mcp",
        "clients.commands._registry",
        "clients.commands.tasks",
    ]
PROBE = """
import importlib, json, sys
importlib.import_module(sys.argv[1])
from clients import hubtools
names = [t["name"] for t in hubtools.TOOLS]
print(json.dumps({"tools": len(names), "by_name": sorted(hubtools.BY_NAME) == sorted(names),
                  "bound": all(hubtools.BY_NAME[n] is t for n, t in zip(names, hubtools.TOOLS))}))
"""


def _fresh(*args, env=None):
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
        timeout=120,
        check=False,
    )


def test_every_module_stays_under_the_cap():
    """No clients/commands module grows past MAX_LINES: a noun that does gets split by verb (tasks -> task_work)."""
    over = {p.name: n for p in sorted(PACKAGE.glob("*.py")) if (n := p.read_text().count("\n")) > MAX_LINES}
    assert not over, f"over {MAX_LINES} lines: {over}; split the noun by verb (scripts/cleanup/split_commands.py)"


def test_every_entry_point_loads_the_whole_table():
    with ThreadPoolExecutor(len(FIRST_IMPORTS)) as pool:
        runs = dict(zip(FIRST_IMPORTS, pool.map(lambda m: _fresh("-c", PROBE, m), FIRST_IMPORTS), strict=True))
    seen = {}
    for module, run in runs.items():
        assert run.returncode == 0, f"importing {module} first: {run.stderr[-2000:]}"
        seen[module] = json.loads(run.stdout.splitlines()[-1])
    assert all(s == {"tools": len(hubtools.TOOLS), "by_name": True, "bound": True} for s in seen.values()), seen


def test_the_mcp_server_launched_as_a_file_starts():
    """`python clients/hubmcp.py` (how a runner may launch it) imports the table; without a turn's URL it says so."""
    env = {k: v for k, v in os.environ.items() if k not in ("HUB_API_URL", "PYTHONPATH")}
    run = subprocess.run(
        [sys.executable, str(ROOT / "clients" / "hubmcp.py")],
        capture_output=True,
        text=True,
        cwd=ROOT.parent,
        env={**env, "PYTHONDONTWRITEBYTECODE": "1"},
        input="",
        timeout=120,
        check=False,
    )
    assert run.returncode == 1 and "HUB_API_URL is not set" in run.stderr, run.stderr[-2000:]
    assert "Traceback" not in run.stderr


class _Computer:
    """A Computer's api: streams files as multipart; records what it was sent."""

    def __init__(self, multipart=True):
        self.calls, self.multipart = [], multipart

    def features(self):
        return {"task_files_multipart": self.multipart}

    def post_multipart(self, path, files, fields, key=None):
        self.calls.append(("multipart", path, {k: Path(v).name for k, v in files.items()}, fields))
        return {"file_id": "f1", "version": 1}

    def post(self, path, body, key=None):
        self.calls.append(("post", path, body))
        return {"ok": True}


def test_local_files_reach_the_api_through_the_lazily_imported_helpers(tmp_path, monkeypatch):
    """The three tools that import clients.bot_files (and clients.visual_embed) only when a local path is given."""
    monkeypatch.chdir(tmp_path)  # not a git checkout: the files are read from here
    (tmp_path / "notes.txt").write_text("hello\n")
    (tmp_path / "chart.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>')

    api = _Computer()
    hubtools.task_attach(api, {"id": "t1", "name": "notes.txt", "path": "notes.txt"})
    hubtools.thread_upload(api, {"thread": "th 1", "path": "notes.txt", "text": "see"})
    uploads = []

    def upload(index, name, data):
        uploads.append((index, name, data[:4]))
        return f"f{index}@1"

    attachments, local = hubtools._visuals(api, [{"path": "chart.svg", "title": "Chart"}], upload)
    assert api.calls == [
        ("multipart", "tasks/t1/files", {"file": "notes.txt"}, {"name": "notes.txt"}),
        ("multipart", "uploads/threads/th%201/messages", {"file": "notes.txt"}, {"text": "see"}),
    ]
    assert attachments == [{"ref": "f0@1", "title": "Chart"}] and uploads == [("0", "chart.svg", b"<svg")]
    assert local == [{"path": "chart.svg", "unembedded": []}]

    old = _Computer(multipart=False)
    hubtools.task_attach(old, {"id": "t1", "name": "notes.txt", "path": "notes.txt"})
    assert old.calls == [("post", "tasks/t1/files", {"name": "notes.txt", "content_base64": "aGVsbG8K"})]


@pytest.mark.skipif(not MOVED, reason="clients/commands/ does not exist yet")
def test_the_facade_hands_out_the_package_objects():
    """`hubtools.X` is the package's own X: a change through the facade (TOOLS, AUDIENCE, BY_NAME) is a change to
    the table, and every name in `__all__` is there."""
    source = {}  # name -> the module the facade imports it from (a clients.commands module, or a library's)
    for node in ast.parse(Path(hubtools.__file__).read_text()).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            source.update({a.asname or a.name: node.module for a in node.names})
    for name in hubtools.__all__:
        assert name in source, f"hubtools.{name} is in __all__ but the facade does not import it"
        assert getattr(hubtools, name) is getattr(importlib.import_module(source[name]), name), name
    for name in ("TOOLS", "BY_NAME", "AUDIENCE", "SHELL_ONLY", "UNLISTED", "CLI_TOOL_ALIASES"):
        assert name in hubtools.__all__, f"hubtools.{name} is not re-exported"


@pytest.mark.skipif(MOVED, reason="the table already lives in clients/commands/")
def test_the_move_still_verifies_on_this_tree():
    """scripts/cleanup/split_commands.py builds the package in a scratch copy and checks the moved table serves the
    same tools, listings, commands and versions. Not strict: a noun it has no module for gets its own."""
    spec = importlib.util.spec_from_file_location("split_commands", ROOT / "scripts" / "cleanup" / "split_commands.py")
    split = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(split)
    try:
        report = split.run(ROOT, strict=False, tests=False)
    except split.Stop as exc:
        pytest.fail(f"scripts/cleanup/split_commands.py: {exc}")
    assert report["identity"]["tools"] == len(hubtools.TOOLS)
