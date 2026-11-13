# pyright: strict
"""The UI's TypeScript files, compiled to JavaScript before anything serves them.

A file under ui/ may be TypeScript (`ui/app/a.ts`); index.html then lists it by that name. `npm run build:ui`
(scripts/build-ui.cjs) compiles each one to `build/ui/app/a.js`, stripping the types and nothing else, with its source
map beside it (`a.js.map`, served next to the compiled file so a stack trace names a.ts), and writes
build/ui/manifest.json with the sha256 of every source, output and map. The bundle (backend/ui_bundle.py) and the `.ts`
route (backend/app.py) serve that output in the source's place, and only while the manifest matches both files: a
missing or stale output is an error that names the command to run, never old JavaScript. The image build compiles
the UI in its own stage (Dockerfile `ui-build`), since the server image has no Node; a checkout compiles it with
its own Node, and `python -m backend.server` does that itself when it can (`prepare`), finding Node in the usual
install folders when PATH has none (a launchd service's PATH has only the system folders).

`python -m backend.ui_build --check` exits 1, naming the files, when any output is missing or stale.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    # A checkout path as a string or a path object (callers pass both).
    type PathArg = str | os.PathLike[str]

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "manifest.json"
# The folders scripts/build-ui.cjs leaves out, the same in both.
SKIP = {"node_modules", "vendor"}
COMMAND = "npm run build:ui"
# Where Node usually is when PATH lacks it: Homebrew (Apple silicon, Intel), the runner image's own Node, Volta, nvm.
NODE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/node/bin", "~/.volta/bin", "~/.nvm/current/bin")
# The output's last line, naming its source map; the bundle drops it, since the map is of the file served alone.
MAP_LINE = re.compile(rb"\n//# sourceMappingURL=[^\n]*\s*\Z")


class StaleBuild(Exception):
    pass


def out_dir(ui_dir: "PathArg") -> Path:
    """Where the compiled files of `ui_dir` live: build/ui beside it."""
    ui_dir = Path(ui_dir)
    return ui_dir.parent / "build" / ui_dir.name


def sources(ui_dir: "PathArg") -> list[str]:
    """Every .ts file under ui_dir (declaration files hold types only), relative to it, sorted."""
    ui_dir = Path(ui_dir)
    found: list[str] = []
    for base, dirs, files in os.walk(ui_dir):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in files:
            if name.endswith(".ts") and not name.endswith(".d.ts"):
                found.append((Path(base) / name).relative_to(ui_dir).as_posix())
    return sorted(found)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_path(ui_dir: "PathArg") -> Path:
    return out_dir(ui_dir) / MANIFEST


def _manifest(ui_dir: "PathArg") -> dict[str, dict[str, Any]]:
    try:
        return json.loads(manifest_path(ui_dir).read_text()).get("files", {})
    except (OSError, ValueError, AttributeError):
        return {}


def compiled(ui_dir: "PathArg", name: str, manifest: dict[str, Any] | None = None) -> Path:
    """The compiled JavaScript of ui/<name> (a .ts path relative to ui_dir), or StaleBuild when it is missing or was
    built from other source."""
    ui_dir = Path(ui_dir)
    entry: dict[str, Any] = (_manifest(ui_dir) if manifest is None else manifest).get(name) or {}
    output = out_dir(ui_dir) / (name[:-3] + ".js")
    try:
        fresh = entry.get("source") == _sha(ui_dir / name) and output.is_file() and entry.get("output") == _sha(output)
    except OSError:
        fresh = False
    if not fresh:
        raise StaleBuild(
            f"ui/{name} has no up-to-date compiled JavaScript in build/ui: run `{COMMAND}` "
            "(or `npm run watch:ui` while editing)"
        )
    return output


def source_map(ui_dir: "PathArg", name: str) -> Path:
    """The source map of ui/<name>'s compiled JavaScript, while that output is current and the map is the one built
    with it; StaleBuild otherwise."""
    ui_dir = Path(ui_dir)
    manifest = _manifest(ui_dir)
    output = compiled(ui_dir, name, manifest)
    path = output.with_name(output.name + ".map")
    try:
        fresh = (manifest.get(name) or {}).get("map") == _sha(path)
    except OSError:
        fresh = False
    if not fresh:
        raise StaleBuild(f"ui/{name} has no up-to-date source map in build/ui: run `{COMMAND}`")
    return path


def minified_entries(ui_dir: "PathArg") -> dict[str, dict[str, Any]]:
    """The manifest's "min": each bundled file's minified copy and map, by the file's path under ui/."""
    try:
        return json.loads(manifest_path(ui_dir).read_text()).get("min", {})
    except (OSError, ValueError, AttributeError):
        return {}


def minified(ui_dir: "PathArg", name: str, entries: dict[str, Any] | None = None) -> tuple[Path, Path] | None:
    """The minified copy of ui/<name> (a file index.html bundles) and its source map, both under build/ui/min, while
    the manifest's "min" matches the source, the copy and the map; None otherwise (built before an edit, or never).
    `entries` is the manifest's "min", read once by a caller asking for many files."""
    ui_dir = Path(ui_dir)
    entry: dict[str, Any] = (minified_entries(ui_dir) if entries is None else entries).get(name) or {}
    if not entry:
        return None
    output = out_dir(ui_dir) / "min" / (name[:-3] + ".js" if name.endswith(".ts") else name)
    map_path = output.with_name(output.name + ".map")
    try:
        fresh = (
            entry.get("source") == _sha(ui_dir / name)
            and entry.get("output") == _sha(output)
            and entry.get("map") == _sha(map_path)
        )
    except OSError:
        fresh = False
    return (output, map_path) if fresh else None


def without_map_line(data: bytes) -> bytes:
    """Compiled JavaScript without the line naming its source map."""
    return MAP_LINE.sub(b"\n", data)


def stale(ui_dir: "PathArg") -> list[str]:
    """The .ts files under ui_dir whose compiled output is missing or stale."""
    manifest = _manifest(ui_dir)
    bad: list[str] = []
    for name in sources(ui_dir):
        try:
            compiled(ui_dir, name, manifest)
        except StaleBuild:
            bad.append(name)
    return bad


def check(ui_dir: "PathArg") -> None:
    bad = stale(ui_dir)
    if bad:
        raise StaleBuild(
            f"{len(bad)} UI TypeScript file{'s' if len(bad) > 1 else ''} not compiled or out of date "
            f"({', '.join('ui/' + n for n in bad)}): run `{COMMAND}` in {Path(ui_dir).parent}"
        )


def _nvm_default(home: Path) -> list[Path]:
    """nvm's default Node without its `current` link: the newest installed version its default alias names (a
    version prefix such as `22`), or the newest installed at all."""
    versions = home / ".nvm" / "versions" / "node"
    installed: list[Path]
    try:
        alias = (home / ".nvm" / "alias" / "default").read_text().strip().lstrip("v")
        installed = [p for p in versions.iterdir() if (p / "bin" / "node").is_file()]
    except OSError:
        alias, installed = "", []
    if not installed:
        return []

    def number(p: Path) -> tuple[int, ...]:
        return tuple(int(n) if n.isdigit() else 0 for n in p.name.lstrip("v").split("."))

    named = [
        p for p in installed if re.fullmatch(r"[\d.]+", alias) and f"{p.name.lstrip('v')}.".startswith(f"{alias}.")
    ]
    return [max(named or installed, key=number) / "bin"]


def nodes(
    which: "Callable[[str], str | None]" = shutil.which,
    dirs: tuple[str, ...] = NODE_DIRS,
    home: "PathArg | None" = None,
) -> list[str]:
    """Every Node this computer has, PATH's first, then the usual install folders (NODE_DIRS, NVM_BIN, VOLTA_HOME,
    nvm's default version): a service started by launchd or systemd has only the system folders on PATH."""
    home = Path(home or Path.home())
    found: list[str]
    seen: set[str]
    found, seen = [], set()
    places = [Path(d.replace("~", str(home), 1)) for d in dirs]
    places += [Path(os.environ[k]) for k in ("NVM_BIN",) if os.environ.get(k)]
    places += [Path(os.environ["VOLTA_HOME"]) / "bin"] if os.environ.get("VOLTA_HOME") else []
    places += _nvm_default(home)
    for node in [which("node")] + [str(p / "node") for p in places]:
        if not node or not os.access(node, os.X_OK) or not Path(node).is_file():
            continue
        real = os.path.realpath(node)
        if real not in seen:
            seen.add(real)
            found.append(node)
    return found


def prepare(
    ui_dir: "PathArg" = ROOT / "ui",
    run: "Callable[..., Any]" = subprocess.run,
    which: "Callable[[str], str | None]" = shutil.which,
    dirs: tuple[str, ...] = NODE_DIRS,
    home: "PathArg | None" = None,
) -> None:
    """Before a server starts: the compiled UI, built here first when it is stale and this checkout can build it
    (Node and `npm ci`'s esbuild), as screen_binary.prepare builds the relay. Each Node found (`nodes`) is tried in turn
    until one builds it. StaleBuild, saying what was missing, when it still is not."""
    ui_dir = Path(ui_dir)
    if not stale(ui_dir):
        return
    root = ui_dir.parent
    script = root / "scripts" / "build-ui.cjs"
    found = nodes(which, dirs, home)
    if not script.is_file():
        reason = f"{script} is missing"
    elif not found:
        reason = f"no Node on PATH or in {', '.join(dirs)}: install Node 22, then `npm ci`"
    elif not (root / "node_modules" / "esbuild").is_dir():
        reason = f"node_modules/esbuild is missing: run `npm ci` in {root}"
    else:
        for node in found:
            try:
                run([node, str(script)], cwd=str(root), check=False, timeout=300, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                continue
            if not stale(ui_dir):
                return
        reason = f"building with {', '.join(found)} failed"
    try:
        check(ui_dir)
    except StaleBuild as exc:
        raise StaleBuild(f"{exc} ({reason})") from None


if __name__ == "__main__":
    if sys.argv[1:] != ["--check"]:
        raise SystemExit("usage: python -m backend.ui_build --check")
    try:
        check(ROOT / "ui")
    except StaleBuild as exc:
        raise SystemExit(f"ui build: {exc}") from None
    print(f"ui build: every TypeScript file under ui/ is compiled and current ({len(sources(ROOT / 'ui'))})")
