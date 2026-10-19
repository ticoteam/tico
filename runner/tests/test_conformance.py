"""The runner's contract: the Rust `tico-runner run` (runner-rs/), run as a real process against a real listening API,
with fake `claude`, `codex`, `gemini`, `agy`, `muse`, `pi`, `cursor-agent` and `grok` executables first on PATH
(runner/tests/conformance/). Every scenario runs on every harness, and checks the result through the server: the claim, the streamed events, the completion and its Idempotency-Keys, granted
credentials and their redaction, session resume, and recovery after the runner is killed mid-turn.

Slow: it builds the release binary once per session (cargo) and starts runner processes."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.store import encode
from backend.tests.test_api import api, assign, get, headers, post, put, runner, settled  # noqa: F401
from backend.tests.live import live  # noqa: F401

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
FAKES = Path(__file__).resolve().parent / "conformance"
MODELS = {"claude": "claude-sonnet-5", "codex": "gpt-5.5", "gemini": "gemini-3.8-flash", "antigravity": "gemini-3.8-flash",
          "musecode": "muse-spark-1.3", "pi": "kimi-k3", "cursor": "cursor-auto", "grok": "grok-4.6"}
# Each fake executable on PATH and the script behind it (runner/tests/conformance/fake_<name>.py).
FAKE_PROGRAMS = {"claude": "claude", "codex": "codex", "gemini": "gemini", "agy": "agy", "muse": "muse", "pi": "pi",
                 "cursor-agent": "cursor", "grok": "grok"}
# The model key each harness signs in with, granted to the bot as the server does (readiness wants the grant).
GRANTED_KEYS = {"gemini": ("Gemini", "GEMINI_API_KEY", "gemini-synthetic-fixture-key-5531"),
                "pi": ("OpenRouter", "OPENROUTER_API_KEY", "openrouter-synthetic-fixture-key-7702")}
RUNTIMES = {"antigravity": "gemini"}
SECRET = "acme-synthetic-fixture-4821"


@pytest.fixture(scope="session")
def rust_binary(tmp_path_factory):
    """The release binary, built once per test run: xdist workers share the base temp directory's lock, and the
    first to take it builds and copies the binary. A copy, so no worker ever runs a file cargo is relinking."""
    if not shutil.which("cargo"):
        pytest.skip("cargo is not installed")
    import fcntl
    shared = tmp_path_factory.getbasetemp().parent
    # Inside the checkout, where the binary finds its root (runner-rs/src/env.rs root).
    binary = ROOT / "runner-rs" / "target" / "conformance" / shared.name / "tico-runner"
    with open(shared / "tico-runner.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not binary.exists():
            subprocess.run(["cargo", "build", "--release", "-q"], cwd=ROOT / "runner-rs", check=True, timeout=900)
            shutil.rmtree(binary.parent.parent, ignore_errors=True)  # earlier runs' copies
            binary.parent.mkdir(parents=True)
            staged = binary.parent / "tico-runner.part"
            shutil.copy2(ROOT / "runner-rs" / "target" / "release" / "tico-runner", staged)
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
    """One registration, its workspace, and the runner process running it (`python -m runner run` with `shim`)."""

    def __init__(self, api, live, tmp_path, harness, binary, bot=None, registration=None, git=False, env=None, shim=False):
        self.api, self.harness, self.binary, self.shim = api, harness, binary, shim
        self.registration = runner(api)
        assign(api, self.registration, "ops")
        self.runtime = RUNTIMES.get(harness, harness)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                      (encode({"name": "ops", "runtime": self.runtime, "harness": harness, "model": MODELS[harness],
                               "status": "active", **(bot or {})}),))
        if harness in GRANTED_KEYS:
            # The Gemini CLI and Pi sign in with the bot's own key; readiness wants it granted.
            from backend.tests.test_credentials import create, setup
            setup(api)
            name, variable, secret = GRANTED_KEYS[harness]
            key = create(api, name=name, env=variable, secret=secret)
            post(api, f"credentials/{key['id']}/grants", {"subject": "bot:ops"})
        repo = tmp_path / "projects" / "bot-ops"
        repo.mkdir(parents=True)
        (repo / "AGENT.md").write_text("# Ops\n\nAnswer the Acme team.\n")
        (repo / "bot.yaml").write_text(f"name: ops\nruntime: {self.runtime}\nmodel: {MODELS[harness]}\n")
        if git:
            # A checkout with an upstream, so a task can have a worktree of its own (runner-rs/src/sessions.rs).
            origin = tmp_path / "origin.git"
            git_env = {**os.environ, "GIT_AUTHOR_NAME": "Acme", "GIT_AUTHOR_EMAIL": "ops@acme.invalid",
                       "GIT_COMMITTER_NAME": "Acme", "GIT_COMMITTER_EMAIL": "ops@acme.invalid"}
            for args in (["init", "-q", "--bare", "-b", "main", str(origin)], ["-C", str(repo), "init", "-q", "-b", "main"],
                         ["-C", str(repo), "add", "-A"], ["-C", str(repo), "commit", "-q", "-m", "Start"],
                         ["-C", str(repo), "remote", "add", "origin", str(origin)],
                         ["-C", str(repo), "push", "-q", "-u", "origin", "main"]):
                subprocess.run(["git", *args], check=True, env=git_env, capture_output=True)
        home, bin_dir = tmp_path / "home", tmp_path / "bin"
        (home / ".codex").mkdir(parents=True)
        bin_dir.mkdir()
        self.log_path = tmp_path / "harness.jsonl"
        for name, script in FAKE_PROGRAMS.items():
            wrapper = bin_dir / name
            # A turn's environment keeps only process settings, so the fakes' own settings ride in the wrapper.
            fake = "".join(f"{k}='{v}' " for k, v in (env or {}).items() if k.startswith("FAKE_"))
            wrapper.write_text(f"#!/bin/sh\n{fake}FAKE_LOG='{self.log_path}' exec '{sys.executable}' '{FAKES / f'fake_{script}.py'}' \"$@\"\n")
            wrapper.chmod(0o755)
        self.config = tmp_path / "config" / "runner.json"
        self.config.parent.mkdir()
        # A Meta-account login, as signing in from Tico leaves it: in the runner's own Muse folder, never ~/.config.
        muse = self.config.parent / f"state-{self.registration['runner_id']}" / "musecode" / "config" / "muse"
        muse.mkdir(parents=True)
        (muse / "auth.json").write_text("{}")
        self.config.write_text(json.dumps({"url": live, "token": self.registration["token"],
                                           "runner_id": self.registration["runner_id"],
                                           "projects_dir": str(tmp_path / "projects"), **(registration or {})}))
        keep = {k: v for k, v in os.environ.items() if k in ("LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")}
        self.env = {**keep, "HOME": str(home), "CODEX_HOME": str(home / ".codex"), "TICO_UPDATE_CHECK": "off",
                    "PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), "/usr/bin", "/bin",
                                             "/usr/sbin", "/sbin"]),
                    **(env or {})}
        self.output = tmp_path / "runner.log"
        self.process = None

    def start(self):
        command = ([sys.executable, "-m", "runner", "--config", str(self.config), "run"] if self.shim
                   else [str(self.binary), "--config", str(self.config), "run"])
        with self.output.open("a") as out:
            self.process = subprocess.Popen(command, cwd=ROOT, env=self.env,
                                            stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        until(lambda: self.readiness().get("ops", {}).get("ready") or self.died(), "the runner's first heartbeat")
        assert not self.died(), self.output.read_text()[-3000:]
        assert self.readiness()["ops"]["ready"], self.readiness()["ops"]
        settled(self.api, self.registration)        # past the server's wake-up settle, so it is given work

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
            row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (self.registration["runner_id"],)).fetchone()
        return (json.loads(row[0] or "{}") if row else {}).get("bots", {})

    def profiles(self):
        """The subscription profiles this computer's last heartbeat listed: {name: {runtime: state}}."""
        with self.api.app.state.store.read() as c:
            return {r[0]: json.loads(r[1] or "{}") for r in c.execute(
                "SELECT profile, runtimes_json FROM computer_profiles WHERE runner_id=?", (self.registration["runner_id"],))}

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    def prompts(self):
        return [call for call in self.calls() if "prompt" in call]

    def attempts(self):
        with self.api.app.state.store.read() as c:
            rows = c.execute("SELECT id, state, result_json, final_text FROM attempts WHERE runner_id=? ORDER BY created",
                             (self.registration["runner_id"],)).fetchall()
        return [{"id": r[0], "state": r[1], "result": json.loads(r[2] or "null"), "text": r[3]} for r in rows]

    def events(self, aid):
        with self.api.app.state.store.read() as c:
            return [{"seq": r[0], "kind": r[1], "payload": json.loads(r[2])}
                    for r in c.execute("SELECT seq, kind, payload_json FROM attempt_events WHERE attempt_id=? ORDER BY seq", (aid,))]

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
    return next((m for m in reversed(messages) if m["from_actor"] == "bot:" + bot), None)


def add_bot(api, computer, tmp_path, slug, bot=None):
    """Another bot on this computer, on the same harness and the computer's own logins (call before start)."""
    assign(api, computer.registration, slug)
    config = {"name": slug, "runtime": computer.runtime, "harness": computer.harness, "model": MODELS[computer.harness],
              "status": "active", **(bot or {})}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode(config), slug))
    repo = tmp_path / "projects" / f"bot-{slug}"
    repo.mkdir(parents=True)
    (repo / "AGENT.md").write_text(f"# {slug.title()}\n\nAnswer the Acme team.\n")
    (repo / "bot.yaml").write_text(f"name: {slug}\nruntime: {config['runtime']}\nmodel: {config['model']}\n")


def codex_turns(computer):
    """Each Codex turn's (start, end) from the fake's log, earliest start first."""
    calls = computer.calls()
    ends = {c["turn"]: c["at"] for c in calls if c.get("method") == "turn/completed"}
    return sorted((c["at"], ends.get(c["turn"])) for c in calls if c.get("method") == "turn/start")


def bot_attempts(api, computer, bot):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT id, result_json FROM attempts WHERE runner_id=? AND bot=? ORDER BY created",
                         (computer.registration["runner_id"], bot)).fetchall()
    return [{"id": r[0], "result": json.loads(r[1] or "null")} for r in rows]


def test_a_chat_turn_streams_completes_and_resumes_the_bot_thread(api, computer):
    computer.start()
    first = post(api, "chat/ops", {"text": "Please answer locally"})
    answer = until(lambda: reply_to(api, first["conversation_id"]), "the bot's reply")
    assert answer["body"] == "Acme reply."
    attempt = computer.attempts()[-1]
    assert attempt["state"] == "completed" and attempt["result"]["outcome"] == "completed"
    kinds = [e["kind"] for e in computer.events(attempt["id"])]
    # cursor-agent's stream-json has whole messages only; Grok Build reports no token usage.
    assert "message" in kinds and ("delta" in kinds or computer.harness == "cursor")
    assert {f"started:{attempt['id']}", f"complete:{attempt['id']}"} <= computer.keys()
    if computer.harness != "grok":
        usage = attempt["result"].get("usage") or {}
        assert usage.get("output_tokens") == 2 and usage.get("runtime") == computer.runtime

    # Another person's conversation continues the bot's one thread, told only what is new.
    second = post(api, "chat/ops", {"text": "A separate question from Ben"}, token="ben-test")
    until(lambda: reply_to(api, second["conversation_id"], "ben-test"), "the second reply")
    # Only this harness's own calls: the runner's background Codex usage read logs to the same file at any moment.
    calls = computer.prompts() if computer.harness in ("claude", "pi", "cursor", "grok") else \
        [c for c in computer.calls() if c["harness"] == computer.harness]
    if computer.harness in ("claude", "gemini"):
        session = calls[0]["argv"][calls[0]["argv"].index("--session-id") + 1]
        assert calls[-1]["argv"][calls[-1]["argv"].index("--resume") + 1] == session
    elif computer.harness == "antigravity":
        # Another conversation is another warm scope: its own process and home, so a provider conversation of its own.
        assert calls[-1]["pid"] != calls[0]["pid"] and all("--conversation" not in c["argv"] for c in calls)
        assert calls[-1]["argv"][calls[-1]["argv"].index("--model") + 1] == "gemini-3.8-flash-low"
    elif computer.harness in ("pi", "cursor"):
        # Pi's session id and Cursor's chat id are the thread: every turn names the first one.
        option = "--session-id" if computer.harness == "pi" else "--resume"
        assert len(calls) == 2 and len({call["argv"][call["argv"].index(option) + 1] for call in calls}) == 1
        if computer.harness == "pi":
            assert calls[0]["env"]["OPENROUTER_API_KEY"] == GRANTED_KEYS["pi"][2]
            assert calls[0]["argv"][calls[0]["argv"].index("--model") + 1] == "moonshotai/kimi-k3"
    elif computer.harness == "grok":
        # The next turn loads the ACP session the first made; a tool permission Grok asks for is always allowed.
        assert len(calls) == 2 and calls[0]["session"] == calls[1]["session"]
        assert any(c.get("method") == "session/load" and c["session"] == calls[0]["session"] for c in computer.calls())
        assert calls[0]["permission"] == {"outcome": "selected", "optionId": "always"}
    elif computer.harness == "musecode":
        # Every turn names the one Muse session Tico made for the thread; the launcher never updates itself.
        sessions = {call["argv"][call["argv"].index("--session-id") + 1] for call in calls}
        assert len(calls) == 2 and len(sessions) == 1
        assert all(call["env"]["MUSE_NO_AUTO_UPDATE"] == "1" for call in calls)
    else:
        starts = [c for c in calls if c.get("method") in ("thread/start", "thread/resume")]
        assert starts[-1] == {"harness": "codex", "method": "thread/resume", "thread": starts[0]["thread"]}
    assert "Please answer locally" not in computer.prompts()[-1]["prompt"]


def started_threads(api, computer):
    """The provider thread each of this computer's turns reported when it started, oldest first."""
    with api.app.state.store.read() as c:
        return [r[0] for r in c.execute("SELECT thread_id FROM attempts WHERE runner_id=? ORDER BY created",
                                        (computer.registration["runner_id"],))]


def from_an_old_hub(monkeypatch):
    """Claims as a Hub from before `session_epoch` and `bot_session_epoch` hands them out. Returns the real claim."""
    from backend.execution import Execution
    claim = Execution.claim

    def without_epochs(self, *args, **kwargs):
        out = claim(self, *args, **kwargs)
        if isinstance(out, dict) and isinstance(out.get("attempt"), dict):
            out["attempt"].pop("session_epoch", None)
            out["attempt"].pop("bot_session_epoch", None)
        return out
    monkeypatch.setattr(Execution, "claim", without_epochs)
    return claim


@pytest.mark.parametrize("old_hub", [False, True])
def test_a_person_clearing_the_chat_starts_a_new_provider_thread_and_without_one_it_resumes(api, computer, old_hub,
                                                                                            monkeypatch):
    if old_hub:
        # A Hub from before the epochs: the runner keeps resuming, as before, and nothing breaks.
        from_an_old_hub(monkeypatch)
    computer.start()
    conversation = None
    for n, text in enumerate(["Remember the Acme code word", "What was it?"], start=1):
        conversation = post(api, "chat/ops", {"text": text})["conversation_id"]
        until(lambda: len([a for a in computer.attempts() if a["result"]]) == n, f"reply {n}")
    cleared = computer.api.post("/api/employees/ops/session/clear", json={}, headers=headers())
    assert cleared.status_code == 200 and cleared.json()["conversation_id"] == conversation, cleared.text
    post(api, "chat/ops", {"text": "Start over: a new question"})
    until(lambda: len([a for a in computer.attempts() if a["result"]]) == 3, "the reply after the clear")
    assert [a["result"]["outcome"] for a in computer.attempts()] == ["completed"] * 3
    first, second, third = started_threads(api, computer)
    # No clear: the same thread, warm. After the clear: a new one, not a resume of the old.
    assert first and second == first, (first, second)
    if old_hub:
        assert third == first, "with no epoch from the Hub the runner resumes as it always did"
        return
    assert third and third != first, (first, third)
    calls = computer.prompts()
    assert "Remember the Acme code word" not in calls[-1]["prompt"], "the cleared history stays out of the new thread"
    if computer.harness in ("claude", "gemini"):
        argv = calls[-1]["argv"]
        assert "--resume" not in argv and argv[argv.index("--session-id") + 1] == third
    elif computer.harness == "codex":
        starts = [c for c in computer.calls() if c.get("harness") == "codex" and c.get("method") in ("thread/start", "thread/resume")]
        assert [c["method"] for c in starts] == ["thread/start", "thread/resume", "thread/start"]
    elif computer.harness == "antigravity":
        # A new epoch is a new warm scope: its own process, so a provider conversation of its own.
        assert calls[-1]["pid"] != calls[0]["pid"] == calls[1]["pid"]


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_clear_made_after_an_upgrade_before_the_chats_next_turn_still_starts_a_new_thread(api, computer, monkeypatch):
    from backend.execution import Execution
    # Before the upgrade: a thread saved with no epoch recorded beside it.
    claim = from_an_old_hub(monkeypatch)
    computer.start()
    post(api, "chat/ops", {"text": "Remember the Acme code word"})
    until(lambda: len([a for a in computer.attempts() if a["result"]]) == 1, "the first reply")
    assert computer.api.post("/api/employees/ops/session/clear", json={}, headers=headers()).status_code == 200
    # Upgraded: the next turn carries the moved epoch, seen here for the first time.
    monkeypatch.setattr(Execution, "claim", claim)
    post(api, "chat/ops", {"text": "Start over: a new question"})
    until(lambda: len([a for a in computer.attempts() if a["result"]]) == 2, "the reply after the clear")
    assert [a["result"]["outcome"] for a in computer.attempts()] == ["completed"] * 2
    first, second = started_threads(api, computer)
    assert first and second and second != first, (first, second)
    assert "Remember the Acme code word" not in computer.prompts()[-1]["prompt"]
    if computer.harness == "claude":
        assert "--resume" not in computer.prompts()[-1]["argv"]
    else:
        starts = [c for c in computer.calls() if c.get("harness") == "codex" and c.get("method") in ("thread/start", "thread/resume")]
        assert [c["method"] for c in starts] == ["thread/start", "thread/start"]


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_after_one_persons_clear_another_persons_chat_on_the_shared_thread_starts_a_new_one_too(api, computer):
    computer.start()
    done = lambda n: len([a for a in computer.attempts() if a["result"]]) == n  # noqa: E731
    ana = post(api, "chat/ops", {"text": "Remember the Acme code word"})["conversation_id"]
    until(lambda: done(1), "Ana's reply")
    ben = post(api, "chat/ops", {"text": "Ben's question"}, token="ben-test")["conversation_id"]
    until(lambda: done(2), "Ben's reply")
    assert computer.api.post("/api/employees/ops/session/clear", json={}, headers=headers()).status_code == 200
    # Ben's chat runs first after Ana's Clear: the thread they shared is not resumed, and his own history stays.
    post(api, "chat/ops", {"text": "Ben again"}, token="ben-test")
    until(lambda: done(3), "Ben's reply after Ana's clear")
    # Ana's next turn continues that new thread, not a third.
    post(api, "chat/ops", {"text": "Ana starts over"})
    until(lambda: done(4), "Ana's reply after her clear")
    assert [a["result"]["outcome"] for a in computer.attempts()] == ["completed"] * 4
    first, second, third, fourth = started_threads(api, computer)
    assert second == first and third and third != first and fourth == third, (first, second, third, fourth)
    assert ana != ben
    if computer.harness == "codex":
        starts = [c for c in computer.calls() if c.get("harness") == "codex" and c.get("method") in ("thread/start", "thread/resume")]
        assert [c["method"] for c in starts] == ["thread/start", "thread/resume", "thread/start", "thread/resume"]


@pytest.mark.parametrize("harness", ["gemini", "antigravity"])
def test_a_second_message_reuses_the_warm_process_or_resumes_the_session(api, computer):
    computer.start()
    first = post(api, "chat/ops", {"text": "reply with the word ok"})
    until(lambda: reply_to(api, first["conversation_id"]), "the first reply")
    second = post(api, "chat/ops", {"text": "and once more"})
    assert second["conversation_id"] == first["conversation_id"]
    until(lambda: len([m for m in get(api, f"conversations/{first['conversation_id']}/messages")
                       if m["from_actor"] == "bot:ops"]) == 2, "the second reply")
    calls = computer.prompts()
    tokens = {a["id"]: a for a in computer.attempts()}
    assert [a["result"]["outcome"] for a in tokens.values()] == ["completed", "completed"]
    if computer.harness == "antigravity":
        # One process for the conversation; each turn read its own attempt token from the lease file, which is blank
        # between turns, and each turn's usage is its own share of the process's running total.
        assert len({c["pid"] for c in calls}) == 1 and calls[0]["env"]["HUB_TOKEN"].startswith("tico-file:")
        assert calls[0]["token"] and calls[1]["token"] and calls[0]["token"] != calls[1]["token"]
        until(lambda: Path(calls[0]["env"]["HUB_TOKEN"][len("tico-file:"):]).read_text() == "", "the blanked token file")
        assert [a["result"]["usage"]["output_tokens"] for a in tokens.values()] == [2, 2]
        assert "reply with the word ok" not in calls[1]["prompt"] and "and once more" in calls[1]["prompt"]
    else:
        session = calls[0]["argv"][calls[0]["argv"].index("--session-id") + 1]
        assert calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == session
        assert calls[0]["env"]["GEMINI_API_KEY"] == "gemini-synthetic-fixture-key-5531"
        settings = json.loads(Path(calls[0]["env"]["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
        assert settings["security"]["auth"]["selectedType"] == "gemini-api-key"


def test_a_granted_credential_reaches_the_turn_and_never_the_transcript(api, computer):
    from backend.tests.test_credentials import create, setup
    if computer.harness not in GRANTED_KEYS:
        setup(api)                      # the gemini and pi Computers set the vault up for their keys
    secret = create(api, name="Acme API", env="ACME_API_KEY", secret=SECRET)
    post(api, f"credentials/{secret['id']}/grants", {"subject": "bot:ops"})
    computer.start()
    msg = post(api, "chat/ops", {"text": "Use the Acme key"})
    answer = until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")
    assert computer.prompts()[-1]["env"]["ACME_API_KEY"] == SECRET
    assert SECRET not in json.dumps(get(api, f"conversations/{msg['conversation_id']}/messages"))
    assert "••••" in answer["body"]
    aid = computer.attempts()[-1]["id"]
    assert SECRET not in json.dumps(computer.events(aid))


def test_a_runner_killed_mid_turn_files_the_turn_as_interrupted_when_it_restarts(api, computer):
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please HANG for a while"})
    # The first text reaches the server while the turn is still running.
    attempt = until(lambda: running_with_text(computer), "the streamed first text")
    computer.kill()
    computer.start()
    done = until(lambda: next((a for a in computer.attempts() if a["id"] == attempt["id"] and a["result"]), None),
                 "the recovered result")
    assert done["result"]["outcome"] == "interrupted"
    assert done["result"]["text"] == "Runner restarted during execution"
    assert msg["conversation_id"]


def running_with_text(computer):
    # A delta, or for cursor-agent (whole messages only) a message.
    return next((a for a in computer.attempts() if a["state"] == "running"
                 and any(e["kind"] in ("delta", "message") for e in computer.events(a["id"]))), None)


@pytest.mark.parametrize("harness", ["codex"])
def test_a_native_goal_is_set_runs_its_turn_and_a_persons_clear_reaches_codex(api, computer):
    computer.start()
    until(lambda: computer.readiness()["ops"].get("goals") is True, "native goals in readiness")
    conversation = post(api, "conversations", {"participants": ["bot:ops"]})["id"]
    post(api, f"conversations/{conversation}/goal", {"action": "set", "objective": "Keep the Acme summary current"})
    until(lambda: any(c.get("method") == "turn/completed" for c in computer.calls()), "the goal's first turn")
    post(api, f"conversations/{conversation}/goal", {"action": "clear"})
    until(lambda: any(c.get("method") == "thread/goal/clear" for c in computer.calls()), "the native clear")
    calls = computer.calls()
    goal_set = next(c for c in calls if c.get("method") == "thread/goal/set")
    assert goal_set["goal"]["status"] == "active" and goal_set["goal"]["objective"].startswith("Keep the Acme summary current")
    assert next(c for c in calls if c.get("method") == "thread/goal/clear")["thread"] == goal_set["thread"]
    assert get(api, f"conversations/{conversation}/goal")["goal"]["status"] == "cleared"
    done = until(lambda: (lambda rows: rows if len(rows) == 2 and all(a["result"] for a in rows) else None)(computer.attempts()),
                 "both attempts settled")
    assert [a["result"]["outcome"] for a in done] == ["completed", "completed"]
    assert sum(c.get("method") == "turn/start" for c in calls) == 1     # the clear is a control, not a turn


@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"bot": {"fallback": {"harness": "claude", "model": "claude-sonnet-5"}}}])
def test_a_usage_limit_hops_the_turn_to_the_fallback_harness(api, computer):
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please LIMIT yourself"})
    answer = until(lambda: reply_to(api, msg["conversation_id"]), "the fallback's reply")
    assert answer["body"] == "Acme reply."
    result = until(lambda: computer.attempts()[-1]["result"], "the completion")
    assert result["outcome"] == "completed" and result["fallback"] == "claude" and not result.get("limited")
    assert [p["harness"] for p in computer.prompts()] == ["codex", "claude"]
    # A segment per harness that used tokens: the refused Codex turn used none.
    assert [part["runtime"] for part in result["usage"]["segments"]][-1:] == ["claude"]
    diagnostics = [e["payload"].get("text", "") for e in computer.events(computer.attempts()[-1]["id"]) if e["kind"] == "diagnostic"]
    assert "codex hit its usage limit; running this turn on claude" in diagnostics
    assert not result.get("primary_limited")          # the bot's own fallback: an older server never sees the field
    # The fallback turn is told which model is doing the work, so its own records do not name the configured one.
    note = "This turn runs on the fallback claude with model claude-sonnet-5 because codex hit its usage limit"
    assert [note in str(p["prompt"]) for p in computer.prompts()] == [False, True]
    made = next(r["ran_on"] for r in get(api, "bots/ops/turns") if r["id"] == computer.attempts()[-1]["id"])
    assert made["fallback"] == "claude" and [s["runtime"] for s in made["segments"]][-1:] == ["claude"]


# The fallback's fresh thread is given the conversation so far, so it can answer what only the earlier turns said.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"bot": {"fallback": {"harness": "claude", "model": "claude-sonnet-5"}}}])
def test_a_fallback_turn_answers_from_the_earlier_conversation(api, computer):
    computer.start()
    first = post(api, "chat/ops", {"text": "Note this: the Acme code word is PELICAN."})
    until(lambda: reply_to(api, first["conversation_id"]), "the first reply")
    post(api, "chat/ops", {"text": "Please LIMIT yourself and RECALL the code word."})
    answer = until(lambda: (lambda r: r if r and "code word" in r["body"] else None)(reply_to(api, first["conversation_id"])),
                   "the fallback's reply")
    assert answer["body"] == "The code word is PELICAN."
    assert computer.attempts()[-1]["result"]["fallback"] == "claude"
    fallback = computer.prompts()[-1]
    assert fallback["harness"] == "claude" and "[Historical message " in fallback["prompt"]
    assert "from bot:ops (you)" in fallback["prompt"]           # its own earlier reply, which the new thread lacks


# A bot out of quota that names no fallback runs the turn once on another provider signed in here (#504).
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("auto", [True, False])
def test_a_usage_limit_with_no_fallback_set_runs_on_another_signed_in_provider(api, computer, auto):
    from backend.store import H
    view = get(api, "providers")
    saved = api.put("/api/v2/providers", json={"enabled": ["openai", "anthropic"], "model": view["default"]["model"] or "",
                                                "expected_revision": view["revision"], "auto_fallback": auto},
                    headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})
    assert saved.status_code == 200, saved.text
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please LIMIT yourself"})
    result = until(lambda: (computer.attempts() or [{}])[-1].get("result"), "the completion")
    diagnostics = [e["payload"].get("text", "") for e in computer.events(computer.attempts()[-1]["id"]) if e["kind"] == "diagnostic"]
    if not auto:            # the owner turned it off: limited, as before
        assert result.get("limited") and not result.get("fallback")
        assert [p["harness"] for p in computer.prompts()] == ["codex"]
        return
    assert until(lambda: reply_to(api, msg["conversation_id"]), "the fallback's reply")["body"] == "Acme reply."
    assert result["outcome"] == "completed" and result["fallback"] == "claude" and result["primary_limited"] is True
    assert not result.get("limited")
    assert [p["harness"] for p in computer.prompts()] == ["codex", "claude"]
    assert any(d.startswith("codex hit its usage limit; running this turn on claude (no fallback set; another provider signed in here)")
               for d in diagnostics), diagnostics


# TK-EKWAFWPD: the first of the company's providers is out of quota on this computer too. The hop that hits its limit
# moves the turn on to the next provider in the same turn, and the next turn skips that login altogether.
@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize("setup", [{"env": {"FAKE_CLAUDE_LIMIT": "1"}}])
def test_a_fallback_out_of_quota_too_moves_the_turn_on_and_is_skipped_next_time(api, computer):
    from backend.store import H
    view = get(api, "providers")
    saved = api.put("/api/v2/providers", json={"enabled": ["anthropic", "openai", "meta"], "model": view["default"]["model"] or "",
                                                "expected_revision": view["revision"], "auto_fallback": True},
                    headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})
    assert saved.status_code == 200, saved.text
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please LIMIT yourself"})       # Codex fails this on its usage limit
    assert until(lambda: reply_to(api, msg["conversation_id"]), "the second fallback's reply")["body"] == "Acme reply."
    first = until(lambda: (computer.attempts() or [{}])[-1].get("result"), "the completion")
    assert first["outcome"] == "completed" and first["fallback"] == "musecode" and first["primary_limited"] is True
    assert not first.get("limited")
    assert [p["harness"] for p in computer.prompts()] == ["claude", "codex", "musecode"]
    diagnostics = [e["payload"].get("text", "") for e in computer.events(computer.attempts()[-1]["id"]) if e["kind"] == "diagnostic"]
    assert any(d.startswith("claude hit its usage limit; running this turn on codex") for d in diagnostics), diagnostics
    assert any(d.startswith("codex hit its usage limit; running this turn on musecode") for d in diagnostics), diagnostics
    # The next turn: Claude is still out of quota, and so is this computer's Codex login, so Muse Code takes it at once.
    # Both logins are held (backend/login_holds.py, Dev 241): the turn never prompts either of them again.
    post(api, "chat/ops", {"text": "Please LIMIT yourself again"})
    second = until(lambda: (lambda rows: rows[-1]["result"] if len(rows) == 2 and rows[-1]["result"] else None)(computer.attempts()),
                   "the second completion")
    assert second["outcome"] == "completed" and second["fallback"] == "musecode"
    assert [p["harness"] for p in computer.prompts()] == ["claude", "codex", "musecode", "musecode"]
    diagnostics = [e["payload"].get("text", "") for e in computer.events(computer.attempts()[-1]["id"]) if e["kind"] == "diagnostic"]
    assert any(d.startswith("claude login held (usage limit)") and d.endswith("; running this turn on musecode") for d in diagnostics), diagnostics


# A completion reports at most two usage segments (backend/models.py RunUsage): once the primary and one fallback have
# both spent tokens, a further hop would make a third, so the turn ends limited there. Spent-nothing hops do not count.
@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize("setup", [{"env": {"FAKE_CLAUDE_LIMIT": "1", "FAKE_LIMIT_SPENDS": spends}} for spends in ("claude", "claude,codex")])
def test_a_fallback_chain_stops_before_a_third_usage_segment(api, computer):
    from backend.store import H
    spends = computer.env["FAKE_LIMIT_SPENDS"]
    view = get(api, "providers")
    saved = api.put("/api/v2/providers", json={"enabled": ["anthropic", "openai", "meta"], "model": view["default"]["model"] or "",
                                                "expected_revision": view["revision"], "auto_fallback": True},
                    headers={"Authorization": "Bearer ana-test", "Idempotency-Key": H.new_id()})
    assert saved.status_code == 200, saved.text
    computer.start()
    post(api, "chat/ops", {"text": "Please LIMIT yourself"})
    result = until(lambda: (computer.attempts() or [{}])[-1].get("result"), "the completion")
    segments = [s["runtime"] for s in result["usage"]["segments"]]
    if spends == "claude":      # Codex refused with nothing spent: Muse Code still takes the turn
        assert result["outcome"] == "completed" and result["fallback"] == "musecode" and not result.get("limited")
        assert [p["harness"] for p in computer.prompts()] == ["claude", "codex", "musecode"]
        assert segments == ["claude", "musecode"]
    else:                       # both spent tokens: Muse Code never starts, and the server takes the two segments
        assert result.get("limited") and result["fallback"] == "codex"
        assert [p["harness"] for p in computer.prompts()] == ["claude", "codex"]
        assert segments == ["claude", "codex"]
    made = next(r["ran_on"] for r in get(api, "bots/ops/turns") if r["id"] == computer.attempts()[-1]["id"])
    assert [s["runtime"] for s in made["segments"]] == segments


# Steering and subscription profiles are Claude Code and Codex features (runner-rs/src/profiles.rs, steer in drive).
@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("setup", [{"env": {"FAKE_SLOW": "8"}}])
def test_a_message_sent_mid_turn_is_steered_into_it_once(api, computer):
    computer.start()
    first = post(api, "chat/ops", {"text": "Please go SLOW"})
    attempt = until(lambda: running_with_text(computer), "the streamed first text")
    post(api, "chat/ops", {"text": "Also count the Acme invoices"})
    until(lambda: (computer.attempts()[-1]["result"] or {}).get("outcome"), "the steered turn's completion", timeout=40)
    delivered = [c for c in computer.calls() if "Acme invoices" in (c.get("steer") or c.get("prompt") or "")]
    assert len(delivered) == 1
    assert [a["id"] for a in computer.attempts()] == [attempt["id"]]      # no turn of its own
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempt_inputs WHERE attempt_id=? AND acked_at IS NOT NULL",
                         (attempt["id"],)).fetchone()[0] == 1
    assert reply_to(api, first["conversation_id"])


@pytest.fixture
def profiled(tmp_path):
    directory = tmp_path / "profiles" / "acme"
    for runtime in ("claude", "codex"):
        (directory / runtime).mkdir(parents=True)
    return directory


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_bot_on_a_subscription_profile_runs_in_that_profiles_home(api, live, tmp_path, harness, profiled, rust_binary):
    machine = Computer(api, live, tmp_path, harness, rust_binary,
                       registration={"profiles": {"acme": {"dir": str(profiled)}}, "bot_profiles": {"ops": "acme"}})
    try:
        machine.start()
        assert machine.readiness()["ops"]["profile"] == "acme"
        msg = post(api, "chat/ops", {"text": "Please answer on the Acme subscription"})
        until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")
        env = machine.prompts()[-1]["env"]
        assert env["HOME" if harness == "claude" else "CODEX_HOME"] == str(profiled / harness)
        result = until(lambda: machine.attempts()[-1]["result"], "the completion")
        assert result["profile_used"] == "acme"
    finally:
        machine.stop()


# A named subscription signed in from Settings on a running computer: the sign-in writes it into runner.json, and the
# heartbeat lists it, shows its sign-in and a turn on it uses it, with no restart. A failed sign-in keeps the profile,
# listed as not signed in; one taken out of runner.json goes from the next heartbeat.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"env": {"FAKE_CODEX_LOGIN": "file"}}])
def test_a_subscription_signed_in_from_the_browser_counts_without_a_restart(api, computer):
    (Path(computer.env["CODEX_HOME"]) / "auth.json").write_text("{}")      # the computer's own login
    computer.start()
    rid = computer.registration["runner_id"]
    signed = lambda name: computer.profiles().get(name, {}).get("codex", {}).get("signed_in")  # noqa: E731
    state = lambda login: get(api, f"runners/{rid}/logins/{login['id']}")["state"]  # noqa: E731
    assert computer.profiles() == {}
    login = post(api, f"runners/{rid}/logins", {"runtime": "codex", "profile": "chris-chatgpt"})
    home = computer.config.parent / "profiles" / "chris-chatgpt" / "codex"
    until(lambda: state(login) == "waiting", "the sign-in's link and code")
    # Listed while the person is still on the provider's page: there, not signed in yet.
    until(lambda: signed("chris-chatgpt") is False, "the new profile in the heartbeat")
    (home / "approve").write_text("")
    until(lambda: state(login) == "signed_in", "the sign-in's verdict")
    until(lambda: signed("chris-chatgpt") is True, "the profile signed in, in the heartbeat")
    put(api, "subscriptions", {"scope": "bot", "target": "ops", "profile": "chris-chatgpt"})
    msg = post(api, "chat/ops", {"text": "Please answer on Chris's subscription"})
    until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply on the new subscription")
    assert computer.prompts()[-1]["env"]["CODEX_HOME"] == str(home)
    assert until(lambda: computer.attempts()[-1]["result"], "the completion")["profile_used"] == "chris-chatgpt"

    failed = post(api, f"runners/{rid}/logins", {"runtime": "codex", "profile": "spare"})
    until(lambda: state(failed) == "waiting", "the second sign-in's link and code")
    (computer.config.parent / "profiles" / "spare" / "codex" / "deny").write_text("")
    until(lambda: state(failed) == "failed", "the failed sign-in's verdict")
    until(lambda: signed("spare") is False, "the failed profile, not signed in")

    registration = json.loads(computer.config.read_text())
    del registration["profiles"]["spare"]
    computer.config.write_text(json.dumps(registration))
    until(lambda: "spare" not in computer.profiles(), "the profile taken out, gone from the heartbeat", timeout=40)
    assert signed("chris-chatgpt") is True
    # The subscription left in place still takes turns after another one is taken out.
    again = post(api, "chat/ops", {"text": "Please answer again on Chris's subscription"})
    assert until(lambda: len(computer.attempts()) == 2 and computer.attempts()[-1]["result"],
                 "the second turn's completion")["profile_used"] == "chris-chatgpt"
    assert "answer again" in computer.prompts()[-1]["prompt"]
    assert computer.prompts()[-1]["env"]["CODEX_HOME"] == str(home)
    assert reply_to(api, again["conversation_id"])
    assert not computer.died()


# The signed-in account a computer reports per tool (#610): allow-listed fields only, Codex's without a token refresh.
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_the_signed_in_account_reaches_readiness_without_anything_token_like(api, computer):
    computer.start()

    def account():
        with api.app.state.store.read() as c:
            row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (computer.registration["runner_id"],)).fetchone()
        return json.loads(row[0] or "{}").get("runtimes", {}).get(computer.runtime, {}).get("account")

    got = until(account, "the account in readiness", timeout=40)
    got.pop("checked_at")
    if computer.runtime == "codex":
        # The fake answers account/read only when asked without a token refresh.
        assert got == {"email": "dev@acme.example", "plan": "pro", "kind": "subscription"}
    else:
        assert got == {"email": "ops@acme.example", "plan": "max", "org": "Acme Ltd"}
    with api.app.state.store.read() as c:
        stored = json.dumps([list(r) for r in c.execute("SELECT readiness_json FROM runners")])
    assert "sk-ant-oat01" not in stored and "acme-org-0001" not in stored


def runtime_row(api, computer):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (computer.registration["runner_id"],)).fetchone()
    return json.loads(row[0] or "{}").get("runtimes", {}).get(computer.runtime, {})


# The computer's own login's usage windows reach the heartbeat with their percentages: Claude's from its turns'
# rate_limit_event (`unifiedWindows`), Codex's from the idle app-server read, with no turn spent to read them.
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_the_own_logins_usage_windows_reach_the_heartbeat_with_percentages(api, computer):
    computer.start()

    def windows():
        row = runtime_row(api, computer)
        got = tuple((row.get(w) or {}).get("used_percent") for w in ("five_hour", "weekly"))
        return got if None not in got else None

    if computer.runtime == "claude":
        assert windows() is None, "Claude reads its usage only from a turn"
        first = post(api, "chat/ops", {"text": "Please answer locally"})
        until(lambda: reply_to(api, first["conversation_id"]), "the bot's reply")
        assert until(windows, "Claude's windows in readiness", timeout=40) == (53, 17)
        assert runtime_row(api, computer)["five_hour"]["status"] == "allowed"
    else:
        assert until(windows, "Codex's windows in readiness", timeout=40) == (40, 12)
        assert not computer.prompts() and not any(c.get("method") == "turn/start" for c in computer.calls())
        assert any(c.get("method") == "account/rateLimits/read" for c in computer.calls())
    row = runtime_row(api, computer)
    assert {"resets_at", "reported_at"} <= set(row["weekly"]) and row["account"]["plan"] in ("max", "pro")


# An API-key Codex login has no plan usage: the server refuses to read it, so it is never asked.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"env": {"FAKE_CODEX_LOGIN": "apikey"}}])
def test_an_api_key_codex_login_is_never_asked_for_plan_usage(api, computer):
    computer.start()
    until(lambda: (runtime_row(api, computer).get("account") or {}).get("kind") == "api_key", "the API-key account", timeout=40)
    time.sleep(2)  # a later heartbeat
    row = runtime_row(api, computer)
    assert "weekly" not in row and "five_hour" not in row
    assert not any(c.get("method") == "account/rateLimits/read" for c in computer.calls())


# On an API-key Codex login the lanes run at once; on a ChatGPT login they run in turn, the second shown as waiting
# (a status, not a failure), because OpenAI's rule is one job per login, not per bot.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"bot": {"session": "task"}, "git": True, "registration": {"capacity": 4},
                                    "env": {"FAKE_SLOW": "4", "FAKE_CODEX_LOGIN": login}} for login in ("apikey", "chatgpt")],
                         ids=["api-key", "chatgpt"])
def test_two_tasks_of_one_bot_run_side_by_side_in_their_own_lanes(api, computer, setup):
    computer.start()
    for title in ("SLOW first", "SLOW second"):
        post(api, "tasks", {"owner": "ops", "title": title, "body": "Please do " + title + "."})
    until(lambda: len([c for c in computer.calls() if c.get("method") == "turn/completed"]) == 2, "both task turns", timeout=60)
    turns = codex_turns(computer)
    waits = [e for a in computer.attempts() for e in computer.events(a["id"]) if e["kind"] == "status" and e["payload"].get("waiting")]
    if setup["env"]["FAKE_CODEX_LOGIN"] == "apikey":
        assert len(turns) == 2 and turns[1][0] < turns[0][1] and not waits, (turns, waits)
    else:
        assert len(turns) == 2 and turns[1][0] >= turns[0][1], turns
        assert [e["payload"]["waiting"] for e in waits] == [
            "Waiting for this computer's ChatGPT login to be free (busy with another turn of this bot)"]
        done = until(lambda: (lambda rows: rows if len(rows) == 2 and all(a["result"] for a in rows) else None)(computer.attempts()),
                     "both results")
        assert [a["result"]["outcome"] for a in done] == ["completed", "completed"]


# Brief test 16 (Dev 215): OpenAI's Codex terms allow a ChatGPT sign-in one auth.json per serialized job stream. Two bots
# on this computer's one ChatGPT login start at once: one Codex turn runs, the other shows it is waiting (not failed),
# then runs once the first is done.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"registration": {"capacity": 4}, "env": {"FAKE_SLOW": "3"}}])
def test_two_bots_on_one_chatgpt_login_take_turns_and_the_one_waiting_says_so(api, computer, tmp_path):
    add_bot(api, computer, tmp_path, "finance")
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("ready"), "the second bot's readiness")
    chats = {bot: post(api, f"chat/{bot}", {"text": "Please be SLOW"})["conversation_id"] for bot in ("ops", "finance")}

    def waiting():
        for bot, cid in chats.items():
            line = (get(api, f"conversations/{cid}/snapshot")["execution"] or {}).get("waiting")
            if line:
                return bot, line
    bot, line = until(waiting, "a turn waiting for the login")
    other = next(b for b in chats if b != bot)
    assert line == f"Waiting for this computer's ChatGPT login to be free (busy with {other})"
    for b, cid in chats.items():
        until(lambda b=b, cid=cid: reply_to(api, cid, bot=b), f"{b}'s reply", timeout=40)
    turns = codex_turns(computer)
    assert len(turns) == 2 and turns[1][0] >= turns[0][1], turns
    for b in chats:
        result = until(lambda b=b: bot_attempts(api, computer, b)[-1]["result"], f"{b}'s completion")
        assert result["outcome"] == "completed" and not result.get("fallback")
    # The wait is a status, set while it waited and cleared once the login was its own; the first turn never waited.
    said = lambda b: [e["payload"]["waiting"] for e in computer.events(bot_attempts(api, computer, b)[-1]["id"])
                      if e["kind"] == "status" and "waiting" in e["payload"]]
    assert said(bot)[0].startswith("Waiting for this computer's ChatGPT login") and said(bot)[-1] == "" and said(other) == []
    assert (get(api, f"conversations/{chats[bot]}/snapshot")["execution"] or {}).get("waiting") is None


# The second bot has its own fallback ready: it does not wait, it runs there, and the run says which and why.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"registration": {"capacity": 4}, "env": {"FAKE_SLOW": "3"}}])
def test_a_bot_whose_chatgpt_login_is_busy_runs_on_its_own_fallback_and_says_why(api, computer, tmp_path):
    add_bot(api, computer, tmp_path, "finance", bot={"fallback": {"harness": "claude", "model": "claude-sonnet-5"}})
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("ready"), "the second bot's readiness")
    ops = post(api, "chat/ops", {"text": "Please be SLOW"})
    until(lambda: codex_turns(computer), "ops's Codex turn")
    finance = post(api, "chat/finance", {"text": "Please answer locally"})
    assert until(lambda: reply_to(api, finance["conversation_id"], bot="finance"), "the fallback's reply")["body"] == "Acme reply."
    attempt = until(lambda: (lambda rows: rows[-1] if rows and rows[-1]["result"] else None)(bot_attempts(api, computer, "finance")),
                    "finance's completion")
    assert attempt["result"]["outcome"] == "completed" and attempt["result"]["fallback"] == "claude"
    notes = [e["payload"] for e in computer.events(attempt["id"]) if e["kind"] == "diagnostic"]
    assert {"text": "ChatGPT login busy with ops; running this turn on claude", "fallback": "claude",
            "busy": {"login": "this computer's ChatGPT login", "holder": "ops"}} in notes, notes
    assert not any(e["kind"] == "status" and "waiting" in e["payload"] for e in computer.events(attempt["id"]))
    prompt = next(p for p in computer.prompts() if p["harness"] == "claude")["prompt"]
    assert "because codex's ChatGPT login was busy with another turn" in prompt
    until(lambda: reply_to(api, ops["conversation_id"]), "ops's reply", timeout=40)
    assert len(codex_turns(computer)) == 1 and bot_attempts(api, computer, "ops")[-1]["result"].get("fallback") is None


def waits_of(computer, aid):
    return [e["payload"]["waiting"] for e in computer.events(aid) if e["kind"] == "status" and "waiting" in e["payload"]]


# A fallback onto a ChatGPT login takes that login too: a Claude bot out of quota hops to Codex while another bot's
# Codex turn holds the login, so its already-running turn waits (and says so), then runs, never beside the other.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"registration": {"capacity": 4}, "env": {"FAKE_SLOW": "3", "FAKE_CLAUDE_LIMIT": "1"}}])
def test_a_fallback_onto_a_busy_chatgpt_login_waits_for_it(api, computer, tmp_path):
    add_bot(api, computer, tmp_path, "finance", bot={"runtime": "claude", "harness": "claude", "model": MODELS["claude"],
                                                     "fallback": {"harness": "codex", "model": MODELS["codex"]}})
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("ready"), "the second bot's readiness")
    ops = post(api, "chat/ops", {"text": "Please be SLOW"})
    until(lambda: codex_turns(computer), "ops's Codex turn")
    finance = post(api, "chat/finance", {"text": "Please answer"})
    line = "Waiting for this computer's ChatGPT login to be free (busy with ops)"
    # Waiting after the run has started: the chat shows it, not a failure.
    until(lambda: (get(api, f"conversations/{finance['conversation_id']}/snapshot")["execution"] or {}).get("waiting") == line,
          "the hop's waiting line")
    until(lambda: reply_to(api, finance["conversation_id"], bot="finance"), "the fallback's reply", timeout=40)
    until(lambda: reply_to(api, ops["conversation_id"]), "ops's reply", timeout=40)
    turns = codex_turns(computer)
    assert len(turns) == 2 and turns[1][0] >= turns[0][1], turns
    attempt = until(lambda: (lambda rows: rows[-1] if rows and rows[-1]["result"] else None)(bot_attempts(api, computer, "finance")),
                    "finance's completion")
    assert attempt["result"]["outcome"] == "completed" and attempt["result"]["fallback"] == "codex"
    assert waits_of(computer, attempt["id"]) == [line, ""]


# A command belongs to the bot's own harness: with the login busy, setting a native goal (a command that carries the
# active goal) waits for the login even with a fallback ready, then drives the goal there.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"registration": {"capacity": 4}, "env": {"FAKE_SLOW": "3"}}])
def test_a_goal_command_waits_for_a_busy_chatgpt_login_even_with_a_fallback_ready(api, computer, tmp_path):
    add_bot(api, computer, tmp_path, "finance", bot={"fallback": {"harness": "claude", "model": MODELS["claude"]}})
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("goals") is True, "the second bot's readiness")
    room = post(api, "conversations", {"participants": ["bot:finance"]})["id"]
    post(api, "chat/ops", {"text": "Please be SLOW"})
    until(lambda: codex_turns(computer), "ops's Codex turn")
    post(api, f"conversations/{room}/goal", {"action": "set", "objective": "Keep the Acme summary current"})
    until(lambda: len(codex_turns(computer)) == 2, "the goal's turn", timeout=40)
    command = bot_attempts(api, computer, "finance")[0]
    assert waits_of(computer, command["id"]) == ["Waiting for this computer's ChatGPT login to be free (busy with ops)", ""]
    assert not [p for p in computer.prompts() if p.get("harness") == "claude"]
    turns = codex_turns(computer)
    assert turns[0][1] is not None and turns[1][0] >= turns[0][1], turns
    post(api, f"conversations/{room}/goal", {"action": "clear"})
    rows = until(lambda: (lambda rows: rows if len(rows) == 2 and all(r["result"] for r in rows) else None)(
        bot_attempts(api, computer, "finance")), "the goal's attempts settled")
    assert [r["result"]["outcome"] for r in rows] == ["completed", "completed"]
    assert not any(r["result"].get("fallback") for r in rows)


# An API-key Codex login is not limited: two bots on it run at once, and neither waits.
@pytest.mark.parametrize("harness", ["codex"])
@pytest.mark.parametrize("setup", [{"registration": {"capacity": 4}, "env": {"FAKE_SLOW": "3", "FAKE_CODEX_LOGIN": "apikey"}}])
def test_two_bots_on_an_api_key_codex_login_run_at_once(api, computer, tmp_path):
    add_bot(api, computer, tmp_path, "finance")
    computer.start()
    until(lambda: computer.readiness().get("finance", {}).get("ready"), "the second bot's readiness")
    chats = {bot: post(api, f"chat/{bot}", {"text": "Please be SLOW"})["conversation_id"] for bot in ("ops", "finance")}
    for b, cid in chats.items():
        until(lambda b=b, cid=cid: reply_to(api, cid, bot=b), f"{b}'s reply", timeout=40)
    turns = codex_turns(computer)
    assert len(turns) == 2 and turns[1][0] < turns[0][1], turns
    for b in chats:
        events = computer.events(bot_attempts(api, computer, b)[-1]["id"])
        assert not any(e["kind"] == "status" and "waiting" in e["payload"] for e in events)


@pytest.mark.parametrize("harness", ["claude"])
@pytest.mark.parametrize("setup", [{"bot": {"template": "botops"}, "git": True}])
def test_a_built_in_bots_product_files_follow_the_release_before_its_turn(api, computer, tmp_path):
    computer.start()
    first = post(api, "chat/ops", {"text": "Please answer locally"})
    until(lambda: reply_to(api, first["conversation_id"]), "the bot's reply")
    repo = tmp_path / "projects" / "bot-ops"
    stamp = json.loads((repo / ".tico-template.json").read_text())
    assert stamp["template"] == "botops" and "AGENT.md" in stamp["files"]
    agent = (repo / "AGENT.md").read_text()
    assert agent != "# Ops\n\nAnswer the Acme team.\n" and "{{" not in agent.splitlines()[0]
    # Committed in the checkout (the turn's own push carries it; this fixture's file remote takes no push).
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s", "main"], capture_output=True, text=True, check=True).stdout
    assert log.startswith("Refresh ") and "from the botops template" in log, log


def git(cwd, *args):
    done = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "init.defaultBranch=main", *args], cwd=cwd, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd),
                               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_task_turn_works_in_its_own_worktree_with_the_tasks_repo_and_pushes_its_run(api, computer, tmp_path):
    projects = tmp_path / "projects"
    # The bot's repository tracks a local remote; the task's product checkout is a worktree of a base clone.
    bot_remote = tmp_path / "remotes" / "bot-ops.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bot_remote))
    repo = projects / "bot-ops"
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Ops starts")
    git(repo, "remote", "add", "origin", str(bot_remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    product_remote = tmp_path / "remotes" / "product.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(product_remote))
    seed = tmp_path / "product-seed"
    git(tmp_path, "clone", "-q", str(product_remote), str(seed))
    (seed / "AGENTS.md").write_text("Run the product tests before every commit.\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "Product rules")
    git(seed, "push", "-q", "origin", "main")
    base = projects / "repos" / "org__product"
    git(tmp_path, "clone", "-q", str(product_remote), str(base))
    git(base, "remote", "set-url", "origin", "https://github.com/org/product.git")
    git(base, "worktree", "add", "-q", "-b", "tico/feature", str(projects / "work" / "product"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (encode({"name": "ops", "runtime": computer.harness, "model": MODELS[computer.harness],
                           "status": "active", "session": "task"}),))
    computer.start()

    task = post(api, "tasks", {"owner": "ops", "title": "Build product", "body": "Please build the product."})
    until(lambda: any(a["state"] == "completed" for a in computer.attempts()), "the task's first turn")
    link = post(api, f"tasks/{task['id']}/worktrees/attach",
                {"path": "work/product", "repo": "org/product", "branch": "tico/feature"})

    def certified():
        # Each config change is a full readiness pass, which inspects the computer's worktrees.
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.touched',?) WHERE bot='ops'",
                      (time.monotonic(),))
            row = c.execute("SELECT state, detail_json FROM task_links WHERE id=?", (link["link_id"],)).fetchone()
        time.sleep(0.5)
        return row["state"] == "present" and json.loads(row["detail_json"])["checkout_state"] == "ready"
    until(certified, "the attached worktree reported ready", timeout=60)

    post(api, f"tasks/{task['id']}/chat", {"text": "REMEMBER what the build taught you"})
    second = until(lambda: next((a for a in computer.attempts()[1:] if a["state"] == "completed"), None),
                   "the turn with the worktree")
    call = computer.prompts()[-1]
    session = projects / "sessions" / "bot-ops" / task["id"]
    assert Path(call["cwd"]).resolve() == session.resolve()
    assert "Repo rules for org/product: how to write code there" in call["prompt"]
    assert "Run the product tests before every commit." in call["prompt"]
    if computer.harness == "claude":
        assert call["argv"][call["argv"].index("--add-dir") + 1] == str(projects / "work" / "product")
    # The lesson the turn left is committed with its run and lands on the bot's remote.
    trailer = f"Tico-Run: {second['id']}"
    until(lambda: trailer in git(tmp_path, "--git-dir", str(bot_remote), "log", "--format=%B", "main"),
          "the turn's memory pushed")
@pytest.mark.parametrize("harness", ["claude"])
def test_a_claude_turn_gets_only_hub_declared_and_project_mcp_servers(api, computer):
    # --strict-mcp-config on every turn keeps out the operator's own servers; the repo's .mcp.json still counts.
    repo = Path(computer.config).parent.parent / "projects" / "bot-ops"
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"acme-docs": {"command": "acme-docs"}}}))
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please answer locally"})
    until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")
    argv = computer.prompts()[-1]["argv"]
    assert "--strict-mcp-config" in argv
    servers = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert {"acme-docs", "hub"} <= set(servers)


def test_a_declared_watcher_runs_without_a_model_and_its_event_opens_a_task(api, computer):
    repo = Path(computer.config).parent.parent / "projects" / "bot-ops"
    (repo / "software").mkdir()
    (repo / "software" / "watch.py").write_text(
        "import os\n"
        "print('tico-event {\"op\": \"task\", \"key\": \"acme:1\", \"title\": \"Acme ticket\", \"body\": \"From HQ\"}')\n"
        "print('hub token: ' + ('yes' if os.environ.get('HUB_TOKEN') else 'no'))\n")
    with (repo / "bot.yaml").open("a") as manifest:
        manifest.write("watchers:\n  - name: acme-hq\n    run: software/watch.py\n    every: 1m\n")
    computer.start()

    def ran():
        with api.app.state.store.read() as c:
            return c.execute("SELECT exit_code, events, output FROM watcher_runs WHERE bot='ops' AND name='acme-hq'").fetchone()
    exit_code, events, output = until(ran, "the watcher's report", timeout=60)
    assert (exit_code, events, output) == (0, 1, "hub token: no")
    with api.app.state.store.read() as c:
        task = c.execute("SELECT t.title FROM watcher_items w JOIN tasks t ON t.id=w.task_id WHERE w.bot='ops' AND w.key='acme:1'").fetchone()
    assert task[0] == "Acme ticket"


def test_a_completed_turn_publishes_the_bots_report_as_a_tico_file(api, computer):
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please PUBLISH the report"})
    until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")

    def published():
        with api.app.state.store.read() as c:
            return [tuple(r) for r in c.execute("SELECT title, kind FROM bot_files WHERE bot='ops'")]
    assert until(published, "the published file") == [("acme.md", "document")]


def test_python_m_runner_run_is_the_rust_runner_and_says_so_in_readiness(api, live, tmp_path, rust_binary):
    """What launchd, systemd and the image start: `python -m runner run` with no engine setting execs the Rust runner,
    which reports its engine and build, and follows the server's release (it reports one)."""
    machine = Computer(api, live, tmp_path, "claude", rust_binary, shim=True, env={"TICO_RUNNER_BIN": str(rust_binary)})
    try:
        machine.start()
        with api.app.state.store.read() as c:
            row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (machine.registration["runner_id"],)).fetchone()
        from runner.engine import source_id
        reported = json.loads(row[0])["engine"]
        assert (reported["name"], reported["build"]) == ("rust", source_id(ROOT))
        assert "Tico runner 0" in machine.output.read_text() and "(rust) started" in machine.output.read_text()
    finally:
        machine.stop()


@pytest.mark.parametrize("harness", ["grok"])
def test_a_grok_turn_gets_only_its_declared_mcp_servers_never_the_operators(api, computer):
    # Grok would load the operator's servers from ~/.claude.json, ~/.cursor/mcp.json and the xAI account: all off.
    computer.start()
    msg = post(api, "chat/ops", {"text": "Please answer locally"})
    until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")
    env = computer.prompts()[-1]["env"]
    assert {k: env[k] for k in ("GROK_CLAUDE_MCPS_ENABLED", "GROK_CURSOR_MCPS_ENABLED", "GROK_MANAGED_MCPS_ENABLED")} == \
        {"GROK_CLAUDE_MCPS_ENABLED": "false", "GROK_CURSOR_MCPS_ENABLED": "false", "GROK_MANAGED_MCPS_ENABLED": "false"}
    assert [c["mcpServers"] for c in computer.calls() if c.get("method") == "session/new"] == [[]]


# What Cloudflare's bot protection refuses before a request reaches Tico: no user agent, or a library's default one.
# Matched on every product token, since a default can lead with another (aiohttp's is `Python/3.12 aiohttp/3.10.11`).
DEFAULT_AGENTS = ("python-requests/", "python-urllib/", "python-httpx/", "aiohttp/", "go-http-client/", "curl/",
                  "wget/", "okhttp/", "java/", "node", "undici", "axios/", "libwww-perl/")


def refused_agent(agent):
    return not agent or any(token.lower().startswith(DEFAULT_AGENTS) for token in agent.split())


def status_of(response):
    parts = response.split(b"\r\n", 1)[0].split(b" ")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


class RefusingProxy:
    """A proxy in front of the test API that answers 403 to a default user agent, as a real install behind Cloudflare
    does; CI talks to Tico directly and never sees it. One request per connection (it forwards `Connection: close`),
    so every request is judged: `refused` keeps (method, path, user agent) and `passed` adds the server's status."""

    def __init__(self, upstream):
        host, port = upstream.removeprefix("http://").rsplit(":", 1)
        self.upstream = (host, int(port))
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.1)       # accept wakes to see `stopping`: closing the socket does not interrupt it
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        self.refused, self.passed, self.workers = [], [], []
        self.stopping = threading.Event()
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()

    def close(self):
        self.stopping.set()
        for thread in [self.acceptor, *self.workers]:
            thread.join(timeout=10)
        self.listener.close()
        assert not any(t.is_alive() for t in [self.acceptor, *self.workers]), "the proxy's threads did not stop"

    def accept(self):
        while not self.stopping.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            client.settimeout(None)
            worker = threading.Thread(target=self.serve, args=(client,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def serve(self, client):
        with client:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(65536)
                if not chunk:
                    return
                head += chunk
            head, rest = head.split(b"\r\n\r\n", 1)
            start, *fields = head.decode("latin-1").split("\r\n")
            method, target = start.split(" ")[:2]
            fields = [f for f in fields if f.split(":", 1)[0].strip().lower() not in ("connection", "keep-alive")]
            agent = next((f.split(":", 1)[1].strip() for f in fields if f.lower().startswith("user-agent:")), "")
            path = target.split("?", 1)[0]
            if refused_agent(agent):
                self.refused.append((method, path, agent))
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            with socket.create_connection(self.upstream) as upstream:
                forwarded = "\r\n".join([start, *fields, "Connection: close"]).encode("latin-1")
                upstream.sendall(forwarded + b"\r\n\r\n" + rest)
                # The rest of a request body, and the client hanging up.
                threading.Thread(target=self.pump, args=(client, upstream), daemon=True).start()
                try:
                    response = b""
                    while b"\r\n" not in response:        # the whole status line, however the reads split it
                        chunk = upstream.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                    self.passed.append((method, path, agent, status_of(response)))
                    while response:
                        client.sendall(response)
                        response = upstream.recv(65536)
                    # A shutdown, not just a close: the pump blocked reading the client would hold the connection open.
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    @staticmethod
    def pump(client, upstream):
        try:
            while chunk := client.recv(65536):
                upstream.sendall(chunk)
        except OSError:
            pass
        try:
            upstream.shutdown(socket.SHUT_RDWR)   # the client hung up: so does the server's side of a held stream
        except OSError:
            pass


def test_the_refusing_proxy_refuses_each_default_agent_and_reads_a_split_status_line():
    """The proxy itself, against an upstream that sends its status line in two writes."""
    upstream = socket.create_server(("127.0.0.1", 0))

    def answer():
        while True:
            try:
                conn, _ = upstream.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                conn.sendall(b"HTTP/1.1 2")
                time.sleep(0.2)
                conn.sendall(b"00 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    threading.Thread(target=answer, daemon=True).start()
    proxy = RefusingProxy(f"http://127.0.0.1:{upstream.getsockname()[1]}")

    def status(agent):
        with socket.create_connection(proxy.listener.getsockname()) as conn:
            conn.sendall(f"GET /api/v2/me HTTP/1.1\r\nHost: acme.example\r\n{agent}\r\n".encode())
            reply = b""
            while chunk := conn.recv(65536):
                reply += chunk
        return status_of(reply)

    agents = ["", "python-requests/2.32.3", "Python-urllib/3.12", "python-httpx/0.27.0", "Python/3.12 aiohttp/3.10.11",
              "Go-http-client/1.1", "Tico-Client/0.2.1"]
    try:
        assert [status(f"User-Agent: {a}\r\n" if a else "") for a in agents] == [403] * 6 + [200]
        assert proxy.passed == [("GET", "/api/v2/me", "Tico-Client/0.2.1", 200)]
    finally:
        proxy.close()
        upstream.close()


def test_behind_a_proxy_that_refuses_default_user_agents_the_runner_and_hub_get_through(api, live, tmp_path,
                                                                                        rust_binary):
    """A real install sits behind Cloudflare, which refuses a library's default user agent: v0.3.28's runners silently
    stopped receiving events when the stream request went out without Tico's own. Here the runner and the `hub` CLI
    reach the server only through such a proxy: the event stream opens, a turn runs, and no request is refused."""
    proxy = RefusingProxy(live)
    machine = Computer(api, live, tmp_path, "claude", rust_binary, registration={"url": proxy.url})
    try:
        machine.start()
        msg = post(api, "chat/ops", {"text": "Please answer locally"})
        assert until(lambda: reply_to(api, msg["conversation_id"]), "the bot's reply")["body"] == "Acme reply."
        until(lambda: machine.attempts() and machine.attempts()[-1]["state"] == "completed", "the completed attempt")
        events = "/api/v2/runners/me/events"
        until(lambda: any(r[1] == events for r in proxy.passed + proxy.refused), "the event stream request")
        hub = subprocess.run([sys.executable, str(ROOT / "scripts" / "hub"), "whoami"], capture_output=True, text=True,
                             timeout=60, env={**machine.env, "HUB_API_URL": proxy.url, "HUB_TOKEN": "ana-test"})
        assert proxy.refused == [], f"refused for its user agent: {proxy.refused}"
        assert [r[3] for r in proxy.passed if r[1] == events][:1] == [200], proxy.passed
        assert hub.returncode == 0, hub.stderr[-2000:]
    finally:
        machine.stop()
        proxy.close()
