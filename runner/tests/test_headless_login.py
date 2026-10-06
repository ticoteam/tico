"""Readiness accepts logins made on a box with no browser.

Each CLI is a stub script on PATH, so this checks what the runner does with the answers real
CLIs give, not the CLIs themselves.
"""
import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runner.tests.test_profiles import service

CODEX = """#!/bin/sh
# Signed in only when CODEX_HOME holds auth.json, as `codex login --device-auth` leaves it.
[ "$1" = login ] && [ "$2" = status ] || { echo "codex-cli 9.9.9"; exit 0; }
if [ -f "${CODEX_HOME:-$HOME/.codex}/auth.json" ]; then echo "Logged in using $(cat "${CODEX_HOME:-$HOME/.codex}/auth.json")"; exit 0; fi
echo "Not logged in" >&2; exit 1
"""
# Signs in from a key on stdin, like `codex login --with-api-key`, and records every argument it was given.
CODEX_KEY = """#!/bin/sh
echo "$*" >> "$STUB_ARGS"
home="${CODEX_HOME:-$HOME/.codex}"
if [ "$1 $2" = "login --with-api-key" ]; then
  read key
  [ "$key" = "sk-good" ] || { echo "Error: invalid key $key" >&2; exit 1; }
  mkdir -p "$home" && echo "API key sk-..." > "$home/auth.json"; exit 0
fi
[ "$1 $2" = "login status" ] || { echo "codex-cli 9.9.9"; exit 0; }
if [ -f "$home/auth.json" ]; then echo "Logged in using an API key"; exit 0; fi
echo "Not logged in" >&2; exit 1
"""
CLAUDE = """#!/bin/sh
[ "$1" = auth ] || { echo "claude 9.9.9"; exit 0; }
echo '{"loggedIn": false}'
"""
CLAUDE_CRASH = """#!/bin/sh
[ "$1" = auth ] || { echo "claude 9.9.9"; exit 0; }
echo "not json"
"""


class HeadlessLogin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "secrets").mkdir()
        keep = {k: v for k, v in os.environ.items()
                if k not in ("CODEX_HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
        patcher = mock.patch.dict(os.environ, {**keep, "PATH": f"{self.bin}:/usr/bin:/bin",
                                               "HOME": str(self.root / "home")}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = service(self.root)

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def readiness(self, runtime, bots=()):
        assignments = [{"bot": bot, "config": {"runtime": runtime}} for bot in bots]
        return self.runner.runtime_readiness(runtime, assignments)

    def test_claude_needs_a_login_or_a_token(self):
        self.stub("claude", CLAUDE)
        self.assertEqual(self.readiness("claude")["authenticated"], "missing")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}):
            row = self.readiness("claude")
        self.assertEqual((row["authenticated"], row["detail"]), ("ready", "Signed in with CLAUDE_CODE_OAUTH_TOKEN"))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x"}):
            self.assertEqual(self.readiness("claude")["authenticated"], "ready")

    def codex_key(self, key, bots=("ana",)):
        """Readiness with `key` as OPENAI_API_KEY in secrets/_shared.env; (row, what the runner printed, argv log)."""
        args = self.root / "args.log"
        args.write_text("")
        self.stub("codex", CODEX_KEY)
        (self.root / "secrets" / "_shared.env").write_text(f"OPENAI_API_KEY={key}\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"STUB_ARGS": str(args)}), contextlib.redirect_stdout(out):
            row = self.readiness("codex", bots)
            again = self.readiness("codex", bots)
        return row, again, out.getvalue(), args.read_text()

    def test_codex_signs_in_by_itself_with_an_api_key_that_never_reaches_a_command_line_or_a_log(self):
        row, again, printed, argv = self.codex_key("sk-good")
        self.assertEqual((row["authenticated"], row["detail"]), ("ready", "Signed in with an API key"))
        self.assertEqual(again["authenticated"], "ready")
        self.assertEqual(argv.count("login --with-api-key"), 1)              # once, not on every heartbeat
        self.assertNotIn("sk-good", argv + printed)
        self.assertTrue((self.root / "home" / ".codex" / "auth.json").exists())

    def test_a_key_codex_refuses_is_tried_once_and_leaves_the_key_out_of_the_log(self):
        row, again, printed, argv = self.codex_key("sk-bad")
        self.assertEqual((row["authenticated"], row["detail"]), ("missing", "Codex login required"))
        self.assertEqual(argv.count("login --with-api-key"), 1)
        self.assertIn("failed", printed)
        self.assertNotIn("sk-bad", argv + printed)

    def team_key(self, reply, bots=()):
        """Readiness on a computer with no key of its own, where the server answers `reply`; (row, printed, asks)."""
        asks = []

        class Server:
            def get(self, path, **query):
                asks.append((path, query))
                if isinstance(reply, Exception):
                    raise reply
                return reply

        self.runner.client = Server()
        args = self.root / "args.log"
        args.write_text("")
        self.stub("codex", CODEX_KEY)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"STUB_ARGS": str(args)}), contextlib.redirect_stdout(out):
            row = self.readiness("codex", bots)
            self.readiness("codex", bots)
        return row, out.getvalue() + args.read_text(), asks

    def test_a_new_computer_signs_in_with_the_key_the_team_gave_every_computer(self):
        row, seen, asks = self.team_key({"credentials": [{"env": "OPENAI_API_KEY", "value": "sk-good"}]})
        self.assertEqual((row["authenticated"], row["detail"]), ("ready", "Signed in with an API key"))
        self.assertEqual(asks, [("runner-model-credentials", {"runtime": "codex"})])       # once, and only for the model
        self.assertNotIn("sk-good", seen)
        team = self.root / "secrets" / "_team_model.env"
        self.assertEqual(team.read_text(), "OPENAI_API_KEY=sk-good\n")
        self.assertEqual(team.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / "secrets" / "_shared.env").exists())
        # Codex signs in once and its turns never see the key.
        self.assertNotIn("OPENAI_API_KEY", self.runner.credential_environment("ana"))

    def test_a_key_put_on_the_computer_by_hand_is_never_replaced_by_the_team_key(self):
        (self.root / "secrets" / "_shared.env").write_text("OPENAI_API_KEY=sk-good\n")
        row, _, asks = self.team_key({"credentials": [{"env": "OPENAI_API_KEY", "value": "sk-other"}]}, bots=("ana",))
        self.assertEqual(row["authenticated"], "ready")
        self.assertEqual(asks, [])
        self.assertFalse((self.root / "secrets" / "_team_model.env").exists())

    def test_claude_takes_the_team_token_but_turns_need_their_own_grant(self):
        self.stub("claude", CLAUDE)
        self.runner.client = type("S", (), {"get": lambda self, path, **q: {"credentials": [
            {"env": "ANTHROPIC_API_KEY", "value": "sk-ant-team"}, {"env": "OPENAI_API_KEY", "value": "sk-not-asked"}]}})()
        row = self.readiness("claude")
        self.assertEqual((row["authenticated"], row["detail"]), ("ready", "Signed in with ANTHROPIC_API_KEY"))
        self.assertEqual((self.root / "secrets" / "_team_model.env").read_text(), "ANTHROPIC_API_KEY=sk-ant-team\n")
        self.assertNotIn("ANTHROPIC_API_KEY", self.runner.credential_environment("ana"))
        self.assertNotIn("shared_env", self.runner.readiness([]))


if __name__ == "__main__":
    unittest.main()
