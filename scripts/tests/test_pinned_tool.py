"""scripts/pinned_tool.sh: the pinned linters come from a kept, verified archive, and a failed download says so.

The Nov 8 merge queue lost a static-checks run to GitHub answering 504 four times: the script then reported the empty
download as a SHA-256 mismatch. CI now keeps the verified archives (actions/cache, pr-checks.yml static checks) and the
script checks a kept archive again before unpacking it. These run a copy of the script whose shellcheck SHA-256 is
that of a small fake archive, with a `curl` on PATH that the test controls; nothing is downloaded.
"""

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSION = re.search(r"^SHELLCHECK_VERSION=(\S+)", (ROOT / "scripts" / "pinned_tool.sh").read_text(), re.M)[1]

# The fake curl: `FAKE_CURL_SERVE` is copied to the -o path, or with nothing to serve it fails as GitHub's 504 did.
# Each call is logged, so a test can tell whether the network was asked.
CURL = """#!/usr/bin/env bash
echo "$@" >> "$FAKE_CURL_LOG"
out=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then out=$2; shift; fi
  shift
done
if [ -z "${FAKE_CURL_SERVE:-}" ]; then echo "curl: (22) The requested URL returned error: 504" >&2; exit 22; fi
cp "$FAKE_CURL_SERVE" "$out"
"""


def fake_archive(body):
    """A shellcheck release archive (tar.xz) whose binary is a script printing `body`."""
    data = f"#!/bin/sh\necho {body}\n".encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as tar:
        info = tarfile.TarInfo(f"shellcheck-v{VERSION}/shellcheck")
        info.size, info.mode = len(data), 0o755
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def tool(tmp_path):
    """Runs a copy of pinned_tool.sh pinned to the good fake archive; returns (run, paths)."""
    good = fake_archive("good")
    pinned = hashlib.sha256(good).hexdigest()
    (tmp_path / "repo" / "scripts").mkdir(parents=True)
    script = (ROOT / "scripts" / "pinned_tool.sh").read_text()
    script = re.sub(r"(shellcheck-\w+-\w+\)) echo [0-9a-f]{64}", rf"\1 echo {pinned}", script)
    assert script.count(pinned) == 4
    (tmp_path / "repo" / "scripts" / "pinned_tool.sh").write_text(script)
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "curl").write_text(CURL)
    (tmp_path / "bin" / "curl").chmod(0o755)
    (tmp_path / "good.tar.xz").write_bytes(good)
    (tmp_path / "bad.tar.xz").write_bytes(fake_archive("tampered"))
    tools, log = tmp_path / "tools", tmp_path / "curl.log"
    log.write_text("")

    def run(*args, serve=None):
        env = {
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "TICO_TOOLS_DIR": str(tools),
            "FAKE_CURL_LOG": str(log),
        }
        env.pop("FAKE_CURL_SERVE", None)
        if serve:
            env["FAKE_CURL_SERVE"] = str(tmp_path / serve)
        return subprocess.run(
            ["bash", str(tmp_path / "repo" / "scripts" / "pinned_tool.sh"), *args],
            env=env,
            capture_output=True,
            text=True,
        )

    return run, {"tools": tools, "log": log, "good": good, "pinned": pinned}


def kept(tools):
    return sorted((tools / "downloads").glob("shellcheck-v*.tar.xz"))


def test_a_failed_download_says_it_failed_not_that_the_sha_differs(tool):
    run, paths = tool
    result = run("path", "shellcheck")
    assert result.returncode != 0
    assert "pinned_tool: download of " in result.stderr
    assert "failed after retries: https://github.com/koalaman/shellcheck/releases/download/" in result.stderr
    assert "SHA-256" not in result.stderr
    assert kept(paths["tools"]) == []


def test_a_kept_verified_archive_is_used_without_the_network(tool):
    run, paths = tool
    first = run("fetch", "shellcheck", serve="good.tar.xz")
    assert first.returncode == 0, first.stderr
    [archive] = kept(paths["tools"])
    assert archive.read_bytes() == paths["good"]
    calls = paths["log"].read_text().count("\n")
    assert calls == 1
    # A fresh runner: the cache restored downloads/, nothing else. GitHub is down (the fake curl answers 504).
    shutil.rmtree(paths["tools"] / f"shellcheck-{VERSION}", ignore_errors=True)
    result = run("shellcheck", "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "good"
    assert paths["log"].read_text().count("\n") == calls


def test_a_kept_archive_that_does_not_match_is_downloaded_again(tool):
    run, paths = tool
    first = run("fetch", "shellcheck", serve="good.tar.xz")
    [archive] = kept(paths["tools"])
    archive.write_bytes(fake_archive("tampered"))
    result = run("shellcheck", "--version", serve="good.tar.xz")
    assert first.returncode == 0 and result.returncode == 0, result.stderr
    assert "the kept shellcheck-v" in result.stderr and "downloading it again" in result.stderr
    assert result.stdout.strip() == "good"
    assert archive.read_bytes() == paths["good"]


def test_a_download_that_does_not_match_is_refused_and_not_kept(tool):
    run, paths = tool
    result = run("shellcheck", "--version", serve="bad.tar.xz")
    assert result.returncode != 0
    assert re.search(rf"has SHA-256 [0-9a-f]{{64}}, not the pinned {paths['pinned']}", result.stderr)
    assert kept(paths["tools"]) == []
    assert not (paths["tools"] / f"shellcheck-{VERSION}" / "shellcheck").exists()
