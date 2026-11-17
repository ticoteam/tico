# pyright: strict
"""The web UI as `npm run build:ui` (scripts/build-ui.cjs) builds it into build/ui/: the server serves that folder and
nothing it builds itself (backend/ui_bundle_routes.py).

build/ui/index.html is the page, naming the bundle files in build/ui/bundle/ (`app.bundle.<hash>.js`, each named by its
content, with its source map and a gzip copy beside it); build/ui/<path>.js is each TypeScript file compiled on its own
(the overview's modules, the service worker). build/ui/manifest.json holds the build's id (`build`, which the config
carries as `ui_build` so an open page can tell it is out of date), the files a cold open runs (`shell`) and the sha256 of
every file under ui/ the build read (`inputs`): a build whose inputs differ from ui/ is stale. A server refuses to start
on a stale build (`prepare` builds it first when this checkout has Node (`node()`: PATH, then the usual install folders) and `npm ci`'s esbuild); the image
builds the UI in its own stage (Dockerfile `ui-build`) and checks it with `python -m backend.ui_build --check`.

`python -m backend.ui_build --shell-size` prints what a cold open of Home downloads; `--where app.bundle.<hash>.js:1:4567`
reads a Sentry frame (file, line, column) back to its source with the build's maps (build the release's commit first).
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
COMMAND = "npm run build:ui"
# The folders scripts/build-ui.cjs leaves out when it looks for TypeScript files, the same in both.
SKIP = {"node_modules", "vendor"}
# The bundle's files, served from build/ui/bundle under this URL prefix and kept for a year (named by content).
BUNDLE_PREFIX = "/tico/ui/app.bundle."
BUNDLE_NAME = re.compile(r"app\.bundle\.(?:[a-z][a-z0-9-]*\.)?[0-9a-f]{16}\.(?:js|css)(?:\.map)?")
IMMUTABLE = "public, max-age=31536000, immutable"
# The most JavaScript, in bytes, a cold open of Home may download before the app runs: the shell's scripts as gzip
# (`cold_js`). The performance budget is 300 KB (P05b); lower this whenever the shell shrinks, never raise it to let a
# change in: move code a click runs into a page part (ui/parts.json) instead.
SHELL_JS_CEILING = 300 * 1024


class StaleBuild(Exception):
    pass


def out_dir(ui_dir: "PathArg") -> Path:
    """Where the build of `ui_dir` lives: build/ui beside it."""
    ui_dir = Path(ui_dir)
    return ui_dir.parent / "build" / ui_dir.name


def bundle_dir(ui_dir: "PathArg") -> Path:
    return out_dir(ui_dir) / "bundle"


def sources(ui_dir: "PathArg") -> list[str]:
    """Every .ts file under ui_dir (declaration files hold types only), relative to it, sorted."""
    ui_dir = Path(ui_dir)
    found: list[str] = []
    for base, dirs, files in os.walk(ui_dir):
        dirs[:] = [d for d in dirs if d not in SKIP]
        found += [(Path(base) / n).relative_to(ui_dir).as_posix() for n in files if n.endswith(".ts")]
    return sorted(n for n in found if not n.endswith(".d.ts"))


def manifest(ui_dir: "PathArg") -> dict[str, Any]:
    try:
        found = json.loads((out_dir(ui_dir) / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def shell(ui_dir: "PathArg") -> list[str]:
    """The bundle files a cold open runs, as the manifest names them (the page parts are fetched as a page opens)."""
    names: list[Any] = manifest(ui_dir).get("shell") or []
    return [str(name) for name in names]


def stale(ui_dir: "PathArg") -> list[str]:
    """The files under ui_dir the build is not current with: an input that changed or is gone, or a TypeScript file it
    never compiled. Every input when there is no build."""
    ui_dir = Path(ui_dir)
    inputs: dict[str, str] = manifest(ui_dir).get("inputs") or {}
    if not inputs or not (out_dir(ui_dir) / "index.html").is_file():
        return sources(ui_dir) or ["index.html"]
    bad: list[str] = []
    for name, digest in inputs.items():
        try:
            same = hashlib.sha256((ui_dir / name).read_bytes()).hexdigest() == digest
        except OSError:
            same = False
        if not same:
            bad.append(name)
    return sorted(bad + [n for n in sources(ui_dir) if n not in inputs])


def check(ui_dir: "PathArg") -> None:
    bad = stale(ui_dir)
    if bad:
        names = ", ".join("ui/" + n for n in bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
        raise StaleBuild(
            f"the UI build in build/ui is missing or out of date ({names}): run `{COMMAND}` in {Path(ui_dir).parent} "
            "(`npm run watch:ui` while editing)"
        )


# Where Node usually is when PATH lacks it: a launchd or systemd job installed before Node was on its PATH keeps the PATH
# it was installed with. Homebrew (Apple silicon, Intel), the runner image's own Node, Volta, nvm's `current` link.
NODE_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/node/bin", "~/.volta/bin", "~/.nvm/current/bin")


def node(which: "Callable[[str], str | None]" = shutil.which, home: "PathArg | None" = None) -> str | None:
    """The Node to build with: PATH's, else the first in the usual install folders (NODE_DIRS, VOLTA_HOME, NVM_BIN, then
    nvm's newest installed version)."""
    found = which("node")
    if found:
        return found
    home = Path(home or Path.home())
    places = [Path(d.replace("~", str(home), 1)) for d in NODE_DIRS]
    places += [
        Path(os.environ[k]) / ("bin" if k == "VOLTA_HOME" else "")
        for k in ("VOLTA_HOME", "NVM_BIN")
        if os.environ.get(k)
    ]
    versions = home / ".nvm" / "versions" / "node"
    if versions.is_dir():

        def number(p: Path) -> tuple[int, ...]:
            return tuple(int(n) if n.isdigit() else 0 for n in p.name.lstrip("v").split("."))

        places += [p / "bin" for p in sorted(versions.iterdir(), key=number, reverse=True)]
    return next((str(p / "node") for p in places if (p / "node").is_file() and os.access(p / "node", os.X_OK)), None)


def prepare(
    ui_dir: "PathArg" = ROOT / "ui",
    run: "Callable[..., Any]" = subprocess.run,
    which: "Callable[[str], str | None]" = shutil.which,
    home: "PathArg | None" = None,
) -> None:
    """Before a server starts: the UI build, built here first when it is stale and this checkout can build it (a Node,
    `node()`, and `npm ci`'s esbuild). StaleBuild, saying what was missing, when it still is not current."""
    ui_dir = Path(ui_dir)
    if not stale(ui_dir):
        return
    root, found = ui_dir.parent, node(which, home)
    script = root / "scripts" / "build-ui.cjs"
    if not script.is_file():
        reason = f"{script} is missing"
    elif not found:
        reason = f"no Node on PATH or in {', '.join(NODE_DIRS)}: install Node 22, then `npm ci`"
    elif not (root / "node_modules" / "esbuild").is_dir():
        reason = f"node_modules/esbuild is missing: run `npm ci` in {root}"
    else:
        try:
            run([found, str(script)], cwd=str(root), check=False, timeout=300, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        reason = f"building with {found} failed"
    try:
        check(ui_dir)
    except StaleBuild as exc:
        raise StaleBuild(f"{exc} ({reason})") from None


_BUILD_ID: dict[str, tuple[tuple[int, int], str]] = {}


def build_id(ui_dir: "PathArg") -> str:
    """The id of the build the server serves now (empty with no build), read again only when the manifest changes."""
    path = out_dir(ui_dir) / "manifest.json"
    try:
        stat = path.stat()
    except OSError:
        return ""
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _BUILD_ID.get(str(path))
    if cached is None or cached[0] != stamp:
        cached = _BUILD_ID[str(path)] = (stamp, str(manifest(ui_dir).get("build") or ""))
    return cached[1]


def cold_js(ui_dir: "PathArg") -> dict[str, int]:
    """{file name: bytes on the wire} of the JavaScript a cold open of Home downloads before the app runs: the scripts
    the page names (manifest `shell`), as the gzip copies the server sends. Page parts are fetched only as a page opens."""
    folder = bundle_dir(ui_dir)
    return {name: (folder / f"{name}.gz").stat().st_size for name in shell(ui_dir)}


def shell_over(ui_dir: "PathArg", ceiling: int | None = None) -> str | None:
    """None when a cold open's JavaScript (`cold_js`) is within `ceiling` bytes; otherwise what to tell the change's
    author: the size, the ceiling and the five largest files it runs."""
    ceiling = SHELL_JS_CEILING if ceiling is None else ceiling
    sizes = cold_js(ui_dir)
    total = sum(sizes.values())
    if total <= ceiling:
        return None
    largest = _shell_files(ui_dir)[:5]
    files = ", ".join(f"{name} {size / 1024:.1f} KB" for size, name in largest)
    return (
        f"A cold open of Home downloads {total / 1024:.1f} KB of JavaScript (gzip), over its ceiling of "
        f"{ceiling / 1024:.1f} KB (backend/ui_build.py SHELL_JS_CEILING). The largest shell files: {files}. Move code "
        "that runs on a click or on another page into a page part (ui/parts.json) instead of raising the ceiling."
    )


def _shell_files(ui_dir: "PathArg") -> list[tuple[int, str]]:
    """The source files in the shell's scripts, each with its size minified and compressed alone, largest first."""
    import gzip

    found: list[tuple[int, str]] = []
    for name in shell(ui_dir):
        text = (bundle_dir(ui_dir) / name).read_text()
        for m in re.finditer(r"^// file: (\S+)\n(.*?)(?=^// file: |\Z)", text, re.M | re.S):
            found.append((len(gzip.compress(m.group(2).encode(), 6, mtime=0)), m.group(1)))
    return sorted(found, reverse=True)


_B64 = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _segments(mappings: str) -> list[list[tuple[int, ...]]]:
    """A source map's mappings, decoded: per generated line, [(column, source, line, column)] (0-based, absolute)."""
    lines: list[list[tuple[int, ...]]] = []
    state = [0, 0, 0, 0, 0]
    for text in mappings.split(";"):
        row: list[tuple[int, ...]] = []
        state[0] = 0
        for seg in filter(None, text.split(",")):
            values: list[int] = []
            value = shift = 0
            for ch in seg:
                digit = _B64[ch]
                value += (digit & 31) << shift
                if digit & 32:
                    shift += 5
                else:
                    values.append(-(value >> 1) if value & 1 else value >> 1)
                    value = shift = 0
            for i, v in enumerate(values):
                state[i] += v
            if len(values) >= 4:
                row.append(tuple(state[:4]))
        lines.append(row)
    return lines


def where(ui_dir: "PathArg", frame: str) -> str | None:
    """A frame of a bundle file (`app.bundle.<hash>.js:1:4567`, 1-based line and column as Sentry and browsers give
    them) as `source:line:column`, or None when no source is there. ValueError for a frame of no file of this build."""
    m = re.fullmatch(r"(?:.*/)?(app\.bundle\.[\w.-]+\.(?:js|css)):(\d+):(\d+)", frame)
    path = bundle_dir(ui_dir) / f"{m.group(1)}.map" if m else None
    if not m or not path or not path.is_file():
        raise ValueError(f"{frame}: not a file of this build (app.bundle.<hash>.js:line:column)")
    line, column = int(m.group(2)) - 1, int(m.group(3)) - 1
    found: list[dict[str, Any]] = json.loads(path.read_text())["sections"]
    sections = [s for s in found if s["offset"]["line"] <= line]
    if not sections:
        return None
    start, source_map = int(sections[-1]["offset"]["line"]), dict[str, Any](sections[-1]["map"])
    rows = _segments(str(source_map["mappings"]))
    row = rows[line - start] if line - start < len(rows) else list[tuple[int, ...]]()
    hits = [s for s in row if s[0] <= column]
    if not hits:
        return None
    _, source, src_line, src_column = hits[-1]
    return f"{source_map['sources'][source]}:{src_line + 1}:{src_column + 1}"


def main(argv: list[str]) -> int:
    ui = ROOT / "ui"
    if argv == ["--check"]:
        try:
            check(ui)
        except StaleBuild as exc:
            raise SystemExit(f"ui build: {exc}") from None
        print(f"ui build: build/ui is current with ui/ ({len(manifest(ui).get('inputs') or {})} files)")
        return 0
    if argv == ["--shell-size"]:
        sizes = cold_js(ui)
        for name, size in sizes.items():
            print(f"{size / 1024:8.1f} KB  {name}")
        print(
            f"{sum(sizes.values()) / 1024:8.1f} KB  cold open of Home, gzip (ceiling {SHELL_JS_CEILING / 1024:.1f} KB)"
        )
        return 1 if shell_over(ui) else 0
    if argv[:1] == ["--where"] and argv[1:]:
        for arg in argv[1:]:
            print(f"{arg} -> {where(ui, arg) or 'no source there'}")
        return 0
    raise SystemExit("usage: python -m backend.ui_build --check | --shell-size | --where FILE:LINE:COLUMN ...")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
