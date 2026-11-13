"""Helpers that other tests share, moved out of test_conformance.py so they import no test file."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest
from backend.tests.helpers.api import assign, get, post, runner, settled
from runner_support import engine
from backend import bot_settings as BS


ROOT = Path(__file__).resolve().parents[3]


FAKES = Path(__file__).resolve().parents[1] / "conformance"


MODELS = {
    "claude": "claude-sonnet-5",
    "codex": "gpt-5.5",
    "gemini": "gemini-3.8-flash",
    "antigravity": "gemini-3.8-flash",
    "musecode": "muse-spark-1.3",
    "pi": "kimi-k3",
    "cursor": "cursor-auto",
    "grok": "grok-4.6",
}


# Each fake executable on PATH and the script behind it (runner_support/tests/conformance/fake_<name>.py).
FAKE_PROGRAMS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "agy": "agy",
    "muse": "muse",
    "pi": "pi",
    "cursor-agent": "cursor",
    "grok": "grok",
}


# The model key each harness signs in with, granted to the bot as the server does (readiness wants the grant).
GRANTED_KEYS = {
    "gemini": ("Gemini", "GEMINI_API_KEY", "gemini-synthetic-fixture-key-5531"),
    "pi": ("OpenRouter", "OPENROUTER_API_KEY", "openrouter-synthetic-fixture-key-7702"),
}


RUNTIMES = {"antigravity": "gemini"}


@pytest.fixture(scope="session")
def rust_binary(tmp_path_factory):
    """The release binary, built once per test run: xdist workers share the base temp directory's lock, and the
    first to take it builds and copies the binary. A copy, so no worker ever runs a file cargo is relinking. A
    release binary already built from these sources (the release gate's, which ships) is used as it is."""
    import fcntl

    built = ROOT / "runner-rs" / "target" / "release" / "tico-runner"
    if engine.current(built, ROOT) and not shutil.which("cargo"):
        pytest.skip("cargo is not installed")
    shared = tmp_path_factory.getbasetemp().parent
    # Inside the checkout, where the binary finds its root (runner-rs/src/env.rs root).
    binary = ROOT / "runner-rs" / "target" / "conformance" / shared.name / "tico-runner"
    with open(shared / "tico-runner.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists():
            if engine.current(built, ROOT):
                subprocess.run(["cargo", "build", "--release", "-q"], cwd=ROOT / "runner-rs", check=True, timeout=900)
            shutil.rmtree(binary.parent.parent, ignore_errors=True)  # earlier runs' copies
            binary.parent.mkdir(parents=True)
            staged = binary.parent / "tico-runner.part"
            shutil.copy2(built, staged)
            staged.rename(binary)
    return binary


def until(check, what, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class Computer:
    """One registration, its workspace, and the runner process running it (`python -m runner_support run` with `shim`)."""

    def __init__(
        self, api, live, tmp_path, harness, binary, bot=None, registration=None, git=False, env=None, shim=False
    ):
        self.api, self.harness, self.binary, self.shim = api, harness, binary, shim
        self.registration = runner(api)
        assign(api, self.registration, "ops")
        self.runtime = RUNTIMES.get(harness, harness)
        with api.app.state.store.transaction() as c:
            BS.write(
                c,
                'ops',
                {
                    'name': 'ops',
                    'runtime': self.runtime,
                    'harness': harness,
                    'model': MODELS[harness],
                    'status': 'active',
                    **(bot or {}),
                },
            )
        if harness in GRANTED_KEYS:
            # The Gemini CLI and Pi sign in with the bot's own key; readiness wants it granted.
            from backend.tests.helpers.credentials import create, setup

            setup(api)
            name, variable, secret = GRANTED_KEYS[harness]
            key = create(api, name=name, env=variable, secret=secret)
            post(api, f"credentials/{key['id']}/grants", {"subject": "bot:ops"})
        repo = tmp_path / "projects" / "bot-ops"
        repo.mkdir(parents=True)
        (repo / "AGENTS.md").write_text("# Ops\n\nAnswer the Acme team.\n")
        (repo / "bot.yaml").write_text(f"name: ops\nruntime: {self.runtime}\nmodel: {MODELS[harness]}\n")
        if git:
            # A checkout with an upstream, so a task can have a worktree of its own (runner-rs/src/sessions.rs).
            origin = tmp_path / "origin.git"
            git_env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Acme",
                "GIT_AUTHOR_EMAIL": "ops@acme.invalid",
                "GIT_COMMITTER_NAME": "Acme",
                "GIT_COMMITTER_EMAIL": "ops@acme.invalid",
            }
            for args in (
                ["init", "-q", "--bare", "-b", "main", str(origin)],
                ["-C", str(repo), "init", "-q", "-b", "main"],
                ["-C", str(repo), "add", "-A"],
                ["-C", str(repo), "commit", "-q", "-m", "Start"],
                ["-C", str(repo), "remote", "add", "origin", str(origin)],
                ["-C", str(repo), "push", "-q", "-u", "origin", "main"],
            ):
                subprocess.run(["git", *args], check=True, env=git_env, capture_output=True)
        home, bin_dir = tmp_path / "home", tmp_path / "bin"
        (home / ".codex").mkdir(parents=True)
        # Grok now checks its scoped local login before accepting a turn.
        (home / ".grok").mkdir()
        (home / ".grok/auth.json").write_text(
            json.dumps({"https://accounts.x.ai/sign-in": {"auth_mode": "oidc", "key": "synthetic-grok-login"}})
        )
        bin_dir.mkdir()
        self.log_path = tmp_path / "harness.jsonl"
        for name, script in FAKE_PROGRAMS.items():
            wrapper = bin_dir / name
            # A turn's environment keeps only process settings, so the fakes' own settings ride in the wrapper.
            fake = "".join(f"{k}='{v}' " for k, v in (env or {}).items() if k.startswith("FAKE_"))
            wrapper.write_text(
                f"#!/bin/sh\n{fake}FAKE_LOG='{self.log_path}' exec '{sys.executable}' '{FAKES / f'fake_{script}.py'}' \"$@\"\n"
            )
            wrapper.chmod(0o755)
        self.config = tmp_path / "config" / "runner.json"
        self.config.parent.mkdir()
        # A Meta-account login, as signing in from Tico leaves it: in the runner's own Muse folder, never ~/.config.
        muse = self.config.parent / f"state-{self.registration['runner_id']}" / "musecode" / "config" / "muse"
        muse.mkdir(parents=True)
        (muse / "auth.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "providers": {"meta": {"api_key": "acme-synthetic-meta-login"}},
                }
            )
        )
        self.config.write_text(
            json.dumps(
                {
                    "url": live,
                    "token": self.registration["token"],
                    "runner_id": self.registration["runner_id"],
                    "projects_dir": str(tmp_path / "projects"),
                    **(registration or {}),
                }
            )
        )
        keep = {k: v for k, v in os.environ.items() if k in ("LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")}
        self.env = {
            **keep,
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "TICO_UPDATE_CHECK": "off",
            "PATH": os.pathsep.join(
                [str(bin_dir), str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
            ),
            **(env or {}),
        }
        self.output = tmp_path / "runner.log"
        self.process = None

    def start(self):
        command = (
            [sys.executable, "-m", "runner", "--config", str(self.config), "run"]
            if self.shim
            else [str(self.binary), "--config", str(self.config), "run"]
        )
        with self.output.open("a") as out:
            self.process = subprocess.Popen(
                command, cwd=ROOT, env=self.env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
            )
        until(lambda: self.readiness().get("ops", {}).get("ready") or self.died(), "the runner's first heartbeat")
        assert not self.died(), self.output.read_text()[-3000:]
        assert self.readiness()["ops"]["ready"], self.readiness()["ops"]
        settled(self.api, self.registration)  # past the server's wake-up settle, so it is given work

    def died(self):
        return self.process.poll() is not None

    def kill(self):
        os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=10)

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.kill()

    def readiness(self):
        with self.api.app.state.store.read() as c:
            row = c.execute(
                "SELECT readiness FROM runners_with_state WHERE id=?", (self.registration["runner_id"],)
            ).fetchone()
        return (json.loads(row[0] or "{}") if row else {}).get("bots", {})

    def profiles(self):
        """The subscription profiles this computer's last heartbeat listed: {name: {runtime: state}}."""
        with self.api.app.state.store.read() as c:
            return {
                r[0]: json.loads(r[1] or "{}")
                for r in c.execute(
                    "SELECT profile, runtimes FROM computer_profiles WHERE runner_id=?",
                    (self.registration["runner_id"],),
                )
            }

    def calls(self):
        if not self.log_path.exists():
            return []
        # A running fake can be midway through appending its last JSONL record.
        return [json.loads(line) for line in self.log_path.read_text().split("\n")[:-1] if line.strip()]

    def prompts(self):
        return [call for call in self.calls() if "prompt" in call]

    def attempts(self):
        with self.api.app.state.store.read() as c:
            rows = c.execute(
                "SELECT id, state, result, final_text FROM attempts WHERE runner_id=? ORDER BY created_at",
                (self.registration["runner_id"],),
            ).fetchall()
        return [{"id": r[0], "state": r[1], "result": terminal_result(r[2]), "text": r[3]} for r in rows]

    def events(self, aid):
        with self.api.app.state.store.read() as c:
            return [
                {"seq": r[0], "kind": r[1], "payload": json.loads(r[2])}
                for r in c.execute(
                    "SELECT seq, kind, payload FROM attempt_events WHERE attempt_id=? ORDER BY seq", (aid,)
                )
            ]

    def keys(self):
        with self.api.app.state.store.read() as c:
            return {r[0] for r in c.execute("SELECT key FROM idempotency")}


@pytest.fixture(params=["claude", "codex", "gemini", "antigravity", "musecode", "pi", "cursor", "grok"])
def harness(request):
    return request.param


@pytest.fixture
def setup():
    """What a scenario changes about its computer (Computer's keyword arguments); override with parametrize."""
    return {}


@pytest.fixture
def computer(api, live, tmp_path, harness, setup, rust_binary):
    machine = Computer(api, live, tmp_path, harness, rust_binary, **setup)
    yield machine
    machine.stop()


def reply_to(api, conversation, token="ana-test", bot="ops"):
    messages = get(api, f"conversations/{conversation}/messages", token)
    return next((m for m in reversed(messages) if m["sender_id"] == "bot:" + bot), None)


def terminal_result(raw):
    # Running fallback custody is durable before completion. It is not a terminal result to poll for.
    result = json.loads(raw or "null")
    return result if result and result.get("outcome") else None
