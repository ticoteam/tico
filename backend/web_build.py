# pyright: strict
"""The web app's build for a server run from a checkout (`python -m backend.server`): build/web, which Vite makes
from web/ and backend/web_routes.py serves at /.

The Docker image builds it in its own stage (Dockerfile `web-build`). A checkout builds it here at start when it is
missing or out of date with web/ (`stale`): `npm ci` and `npm run build` in web/, with the npm on PATH or one in the
usual install folders. A build records the digest of what it read in build/web.inputs, beside the build and never
served, so the next start can tell. Without npm a build that is there is served as it is (the image ships none). With
no build and no npm, or when the build fails, the server still starts (the API, the CLI and MCP need no page) and says
what to run; / answers 404 until the build is there.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "npm ci && npm run build, in web/"
# What a build reads: the app's own files. Generated files (the route tree, the API types) and installs are left out.
INPUTS = ("package.json", "package-lock.json", "index.html", "vite.config.ts", "tsconfig.json", "src", "public")
SKIP = {"node_modules", "routeTree.gen.ts", "schema.d.ts"}
BUILD_SECONDS = 900


def inputs_digest(web: Path) -> str:
    """One digest over every input file's path and contents, in path order."""
    whole = hashlib.sha256()
    for name in INPUTS:
        base = web / name
        files = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        for path in (f for f in files if f.is_file()):
            relative = path.relative_to(web)
            if SKIP.intersection(relative.parts):
                continue
            whole.update(relative.as_posix().encode() + b"\0")
            whole.update(hashlib.sha256(path.read_bytes()).digest())
    return whole.hexdigest()


def stamp_file(out: Path) -> Path:
    return out.with_name(out.name + ".inputs")


def stale(web: Path, out: Path) -> bool:
    """Whether the build in `out` is missing or was made from other inputs than web/ holds now."""
    try:
        recorded = stamp_file(out).read_text().strip()
    except OSError:
        return True
    return not (out / "index.html").is_file() or recorded != inputs_digest(web)


def npm() -> str | None:
    """The npm to build with: PATH's, else one in the usual install folders (a launchd or systemd job's PATH may lack
    it)."""
    found = shutil.which("npm")
    if found:
        return found
    home = Path.home()
    candidates = [Path("/opt/homebrew/bin/npm"), Path("/usr/local/bin/npm"), home / ".volta" / "bin" / "npm"]
    candidates += sorted((home / ".nvm" / "versions" / "node").glob("*/bin/npm"), reverse=True)
    return next((str(path) for path in candidates if path.is_file()), None)


def prepare(out: Path, web: Path = ROOT / "web") -> str | None:
    """Build the web app into `out` when it is stale and this is a checkout that can. None when the build is current
    (or was just made), else a line saying why there is none."""
    if not (web / "package.json").is_file() or Path(out).resolve() != (ROOT / "build" / "web").resolve():
        return None  # not this checkout's own build (an image, or a build put elsewhere): nothing to make here
    if not stale(web, out):
        return None
    runner = npm()
    if runner is None:
        if (out / "index.html").is_file():
            return None  # the image's build (it ships no npm), or one made by hand: served as it is
        return f"the web app is not built and there is no npm to build it: run `{COMMAND}`"
    env = {**os.environ, "PATH": str(Path(runner).parent) + os.pathsep + os.environ.get("PATH", "")}
    print(f"tico server: building the web app ({COMMAND})", file=sys.stderr, flush=True)
    try:
        for args in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
            subprocess.run([runner, *args], cwd=web, env=env, check=True, timeout=BUILD_SECONDS)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return f"the web app did not build ({type(exc).__name__}): run `{COMMAND}`"
    stamp_file(out).write_text(inputs_digest(web) + "\n")
    return None
