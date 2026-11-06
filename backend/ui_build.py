"""The UI's TypeScript files, compiled to JavaScript before anything serves them.

A file under ui/ may be TypeScript (`ui/app/a.ts`); index.html then lists it by that name. `npm run build:ui`
(scripts/build-ui.cjs) compiles each one to `build/ui/app/a.js`, stripping the types and nothing else, and writes
build/ui/manifest.json with the sha256 of every source and output. The bundle (backend/ui_bundle.py) and the `.ts`
route (backend/app.py) serve that output in the source's place, and only while the manifest matches both files: a
missing or stale output is an error that names the command to run, never old JavaScript. The image build compiles
the UI in its own stage (Dockerfile `ui-build`), since the server image has no Node; a checkout compiles it with
its own Node, and `python -m backend.server` does that itself when it can (`prepare`).

`python -m backend.ui_build --check` exits 1, naming the files, when any output is missing or stale.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "manifest.json"
# The folders scripts/build-ui.cjs leaves out, the same in both.
SKIP = {"node_modules", "vendor"}
COMMAND = "npm run build:ui"


class StaleBuild(Exception):
    pass


def out_dir(ui_dir):
    """Where the compiled files of `ui_dir` live: build/ui beside it."""
    ui_dir = Path(ui_dir)
    return ui_dir.parent / "build" / ui_dir.name


def sources(ui_dir):
    """Every .ts file under ui_dir (declaration files hold types only), relative to it, sorted."""
    ui_dir = Path(ui_dir)
    found = []
    for base, dirs, files in os.walk(ui_dir):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if name.endswith(".ts") and not name.endswith(".d.ts"):
                found.append((Path(base) / name).relative_to(ui_dir).as_posix())
    return sorted(found)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_path(ui_dir):
    return out_dir(ui_dir) / MANIFEST


def _manifest(ui_dir):
    try:
        return json.loads(manifest_path(ui_dir).read_text()).get("files", {})
    except (OSError, ValueError, AttributeError):
        return {}


def compiled(ui_dir, name, manifest=None):
    """The compiled JavaScript of ui/<name> (a .ts path relative to ui_dir), or StaleBuild when it is missing or was
    built from other source."""
    ui_dir = Path(ui_dir)
    entry = (_manifest(ui_dir) if manifest is None else manifest).get(name) or {}
    output = out_dir(ui_dir) / (name[:-3] + ".js")
    try:
        fresh = (entry.get("source") == _sha(ui_dir / name) and output.is_file()
                 and entry.get("output") == _sha(output))
    except OSError:
        fresh = False
    if not fresh:
        raise StaleBuild(f"ui/{name} has no up-to-date compiled JavaScript in build/ui: run `{COMMAND}` "
                         "(or `npm run watch:ui` while editing)")
    return output


def stale(ui_dir):
    """The .ts files under ui_dir whose compiled output is missing or stale."""
    manifest = _manifest(ui_dir)
    bad = []
    for name in sources(ui_dir):
        try:
            compiled(ui_dir, name, manifest)
        except StaleBuild:
            bad.append(name)
    return bad


def check(ui_dir):
    bad = stale(ui_dir)
    if bad:
        raise StaleBuild(f"{len(bad)} UI TypeScript file{'s' if len(bad) > 1 else ''} not compiled or out of date "
                         f"({', '.join('ui/' + n for n in bad)}): run `{COMMAND}` in {Path(ui_dir).parent}")


def prepare(ui_dir=ROOT / "ui", run=subprocess.run, which=shutil.which):
    """Before a server starts: the compiled UI, built here first when it is stale and this checkout can build it
    (Node and `npm ci`'s esbuild), as screen_binary.prepare builds the relay. StaleBuild when it still is not."""
    ui_dir = Path(ui_dir)
    if not stale(ui_dir):
        return
    root, node = ui_dir.parent, which("node")
    script = root / "scripts" / "build-ui.cjs"
    if node and script.is_file() and (root / "node_modules" / "esbuild").is_dir():
        run([node, str(script)], cwd=str(root), check=False, timeout=300, stdin=subprocess.DEVNULL)
    check(ui_dir)


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        raise SystemExit("usage: python -m backend.ui_build --check")
    try:
        check(ROOT / "ui")
    except StaleBuild as exc:
        raise SystemExit(f"ui build: {exc}") from None
    print(f"ui build: every TypeScript file under ui/ is compiled and current ({len(sources(ROOT / 'ui'))})")
