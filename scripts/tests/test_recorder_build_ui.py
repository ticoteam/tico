"""recorder/build-ui.cjs, the Recorder window's build: run on a copy in a temporary tree (esbuild from this checkout's
node_modules). Its manifest is what recorder/build.rs checks (recorder/build_window.rs, with its own Rust tests): the
hashes here are recomputed the way that check computes them."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None or not (ROOT / "node_modules/esbuild").is_dir(), reason="needs node and the repository's npm ci"
)


def fnv(data: bytes) -> str:
    """FNV-1a 64, as build-ui.cjs and build_window.rs write it."""
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


def tree(tmp_path):
    files = {
        "package.json": "{}\n",
        "package-lock.json": "{}\n",
        "recorder/ui/app/index.html": '<script src="app.js"></script>\n',
        "recorder/ui/app/app.ts": "const n: number = 1;\nconsole.log(n as number);\n",
        "recorder/ui/app/app.css": "body {}\n",
        "recorder/ui/app/fonts/a.woff2": "font",
        "recorder/ui/types/bridge.d.ts": "declare const x: number;\n",
        "recorder/ui/preview/index.html": "<p>preview</p>\n",
        "recorder/ui/preview/mock.ts": "const m: string = 'm';\n",
        "recorder/ui/preview/shoot.mts": "export {};\n",
        "recorder/ui/preview/types.d.ts": "export {};\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    shutil.copy(ROOT / "recorder/build-ui.cjs", tmp_path / "recorder/build-ui.cjs")
    return tmp_path / "recorder"


def env():
    return {**os.environ, "NODE_PATH": str(ROOT / "node_modules")}


def build(recorder, *args):
    return subprocess.run(
        [NODE, str(recorder / "build-ui.cjs"), *args], capture_output=True, text=True, env=env(), check=False
    )


def manifest(recorder):
    # Missing while a build runs (it is removed first, written last).
    try:
        lines = (recorder / "dist/build-manifest.txt").read_text().splitlines()
    except FileNotFoundError:
        return []
    return [line.split("\t") for line in lines if not line.startswith("#")]


def test_types_are_stripped_other_files_copied_and_the_manifest_lists_both_with_their_hashes(tmp_path):
    recorder = tree(tmp_path)
    done = build(recorder)
    assert done.returncode == 0, done.stderr
    dist = recorder / "dist"
    assert sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file()) == [
        "app/app.css",
        "app/app.js",
        "app/fonts/a.woff2",
        "app/index.html",
        "build-manifest.txt",
        "preview/index.html",
        "preview/mock.js",
    ]
    assert (dist / "app/app.js").read_text() == "const n = 1;\nconsole.log(n);\n"
    rows = manifest(recorder)
    assert rows[0][0] == "esbuild"
    assert rows[1:4] == [
        ["input", "build-ui.cjs", fnv((recorder / "build-ui.cjs").read_bytes())],
        ["input", "../package.json", fnv(b"{}\n")],
        ["input", "../package-lock.json", fnv(b"{}\n")],
    ]
    for _, source, source_hash, output, output_hash in rows[4:]:
        assert fnv((recorder / source).read_bytes()) == source_hash, source
        assert fnv((recorder / output).read_bytes()) == output_hash, output
    assert ["ui/app/app.ts", "dist/app/app.js"] == [rows[5][1], rows[5][3]]


def test_a_rebuild_writes_only_what_changed_and_removes_what_lost_its_source(tmp_path):
    recorder = tree(tmp_path)
    build(recorder)
    css = recorder / "dist/app/app.css"
    os.utime(css, (1, 1))
    (recorder / "ui/app/fonts/a.woff2").unlink()
    done = build(recorder)
    assert ", 1 changed ->" in done.stdout  # the removed font; app.css, its bytes the same, is not rewritten
    assert css.stat().st_mtime == 1
    assert not (recorder / "dist/app/fonts").exists()
    assert all("fonts" not in row[1] for row in manifest(recorder)[4:])


def test_a_failed_build_leaves_no_manifest_so_the_cargo_check_stops(tmp_path):
    recorder = tree(tmp_path)
    build(recorder)
    (recorder / "ui/app/app.ts").write_text("const n: = ;\n")
    done = build(recorder)
    assert done.returncode == 1 and "recorder ui build failed" in done.stderr
    assert not (recorder / "dist/build-manifest.txt").exists()
