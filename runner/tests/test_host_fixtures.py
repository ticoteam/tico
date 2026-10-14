"""Harness wire formats both runner engines replay (runner/tests/fixtures/<harness>/*.jsonl).

Each fixture is what the harness writes on stdout for one turn; `<name>.expected.json` is the events the
Python host makes of it, with the per-run thread and turn ids left out for Claude, Gemini, Antigravity and Muse Code. The Rust runner's host
tests (runner-rs/src/hosts) replay the same files against the same expected events, so the two engines
read a harness the same way. `python -m runner.tests.test_host_fixtures --write` refreshes the expected files.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from runner.hosts.antigravity import AntigravityHost
from runner.hosts.claude import ClaudeHost
from runner.hosts.codex import CodexHost
from runner.hosts.gemini import GeminiHost

try:
    from runner.hosts.musecode import MuseCodeHost   # PR #508; without it the Muse Code fixtures are the Rust host's only
except ImportError:
    MuseCodeHost = None

FIXTURES = Path(__file__).parent / "fixtures"


class _Claude:
    """`claude -p` for one turn: the fixture's lines on stdout once the prompt is written, exit 0 on stdin close."""

    def __init__(self, lines):
        import queue
        self.lines, self.out, self.pid, self.rc = lines, queue.Queue(), 4243, None
        self.stdin, self.stdout = self, iter(self.out.get, None)

    def write(self, text):
        for line in self.lines:
            self.out.put(line + "\n")
        self.lines = []

    def flush(self):
        pass

    def close(self):
        if self.rc is None:
            self.rc = 0
            self.out.put(None)

    def poll(self):
        return self.rc

    def terminate(self):
        self.close()

    kill = terminate

    def wait(self, timeout=None):
        return self.rc if self.rc is not None else 0


def claude_events(path):
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    host = ClaudeHost(bot="acme", spawn=lambda argv, **kw: _Claude(lines),
                      version=lambda: "2.1.296 (Claude Code)", help=lambda: "--autocompact")
    host.start()
    thread = host.start_thread("acme", {"cwd": "/tmp", "env": {}})
    host.start_turn(thread, "hello")
    host._reader.join(timeout=5)
    return [{k: v for k, v in ev.items() if k not in ("thread_id", "turn_id")} for ev in host.drain()]


def codex_events(path):
    host = CodexHost(config={}, spawn=lambda *a, **k: None)
    for line in path.read_text().splitlines():
        if line.strip():
            host._on_message(json.loads(line))
    return host.drain()


def _ids_out(events):
    return [{k: v for k, v in ev.items() if k not in ("thread_id", "turn_id")} for ev in events]


def gemini_events(path):
    """`gemini -p ""` for one turn: the fixture once the prompt is in, then exit 0."""
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    with tempfile.TemporaryDirectory() as home:
        host = GeminiHost(bot="acme", home=home, spawn=lambda argv, **kw: _Claude(lines))
        host.start()
        thread = host.start_thread("acme", {"cwd": "/tmp", "model": "gemini-3.8-flash", "effort": "low",
                                            "env": {"GEMINI_API_KEY": "test-key", "PATH": "/bin"}})
        host.start_turn(thread, "hello")
        host._reader.join(timeout=5)
        return _ids_out(host.drain())


def antigravity_events(path):
    """One `agy` turn: the fixture on stdout once the user line is written."""
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    with tempfile.TemporaryDirectory() as home:
        proc = _Claude(lines)
        host = AntigravityHost(home, spawn=lambda argv, **kw: proc)
        host.start()
        thread = host.start_thread("acme", {"cwd": "/tmp", "model": "gemini-3.8-flash", "env": {}})
        host.start_turn(thread, "hello")
        proc.close()
        host._reader.join(timeout=5)
        return _ids_out(host.drain())


READERS = {"claude": claude_events, "codex": codex_events, "gemini": gemini_events, "antigravity": antigravity_events}


class _Muse:
    """`muse exec --json` for one turn: the fixture's lines on stdout, then exit 0."""

    def __init__(self, lines):
        self.stdout, self.pid = iter([line + "\n" for line in lines]), 4244

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    kill = terminate


def musecode_events(path):
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    with tempfile.TemporaryDirectory() as state:
        host = MuseCodeHost(bot="acme", spawn=lambda argv, **kw: _Muse(lines), state_dir=state)
        host.start()
        thread = host.start_thread("acme", {"cwd": "/tmp", "env": {"PATH": "/usr/bin:/bin"}})
        host.start_turn(thread, "hello")
        host._reader.join(timeout=5)
    return [{k: v for k, v in ev.items() if k not in ("thread_id", "turn_id")} for ev in host.drain()]


if MuseCodeHost is not None:
    READERS["musecode"] = musecode_events


def cases():
    for harness, read in READERS.items():
        for path in sorted((FIXTURES / harness).glob("*.jsonl")):
            yield harness, path, read


class HostFixtures(unittest.TestCase):
    def test_every_fixture_gives_its_expected_events(self):
        for harness, path, read in cases():
            with self.subTest(fixture=f"{harness}/{path.name}"):
                expected = json.loads(path.with_suffix(".expected.json").read_text())
                self.assertEqual(read(path), expected)


if __name__ == "__main__" and "--write" in sys.argv:
    for harness, path, read in cases():
        path.with_suffix(".expected.json").write_text(json.dumps(read(path), indent=1) + "\n")
