"""Browser sign-in on the runner: the relay, the pasted code, cancel and timeout, and no leaks.

Each CLI is a stub script on PATH that prints what the real one prints (checked against
codex 0.157 and Claude Code 2.1.284), so this exercises the runner's handling, not the CLIs.
"""
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from runner.login import Logins, parse
from runner.tests.test_profiles import service

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
CREDENTIAL = "credential-body-" + "Zq9" * 12

CODEX = f"""#!/bin/sh
home="${{CODEX_HOME:-$HOME/.codex}}"
if [ "$1 $2" = "login status" ]; then
  [ -f "$home/auth.json" ] && {{ echo "Logged in using ChatGPT"; exit 0; }}
  echo "Not logged in" >&2; exit 1
fi
[ "$1 $2" = "login --device-auth" ] || {{ echo "codex-cli 9.9.9"; exit 0; }}
echo $$ > "$TICO_PIDFILE"
printf '\\nWelcome to Codex [v\\033[90m9.9.9\\033[0m]\\n\\n1. Open this link in your browser\\n   \\033[94mhttps://auth.example/codex/device\\033[0m\\n\\n'
printf '2. Enter this one-time code \\033[90m(expires in 15 minutes)\\033[0m\\n   \\033[94mAB12-CD345\\033[0m\\n\\n'
echo "debug token: {JWT}"
sleep "${{TICO_DELAY:-1}}"
mkdir -p "$home"; echo '{CREDENTIAL}' > "$home/auth.json"
echo "Successfully logged in"
"""

# Claude Code draws with a terminal: hyperlinks are OSC 8 and the prompt has no newline.
CLAUDE = f"""#!/bin/sh
dir="${{CLAUDE_CONFIG_DIR:-$HOME/.claude}}"
if [ "$1" = auth ] && [ "$2" = status ]; then
  [ -f "$dir/creds" ] && echo '{{"loggedIn": true, "authMethod": "claude.ai"}}' || echo '{{"loggedIn": false}}'
  exit 0
fi
[ "$1 $2" = "auth login" ] || {{ echo "claude 9.9.9"; exit 0; }}
echo $$ > "$TICO_PIDFILE"
printf 'Opening browser to sign in\\342\\200\\246\\nIf the browser did not open, visit: '
printf '\\033]8;id=1;https://claude.example/authorize?state=abc\\007https://claude.example/auth\\033]8;;\\007\\n'
printf 'Paste\\033[8Gcode\\033[13Ghere > '
read -r code
if [ "$code" = "good-code#state-1234" ]; then mkdir -p "$dir"; echo '{CREDENTIAL}' > "$dir/creds"; echo "Login successful"; exit 0; fi
echo "Login failed"; exit 1
"""


class Client:
    def __init__(self):
        self.posts, self.wanted = [], []

    def get(self, path):
        return {"logins": list(self.wanted)}

    def post(self, path, body=None):
        self.posts.append((path, body))
        return {}


class Login(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "secrets").mkdir()
        self.pidfile = self.root / "pid"
        keep = {k: v for k, v in os.environ.items()
                if k not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")}
        patcher = mock.patch.dict(os.environ, {**keep, "PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.root / "home"),
                                               "CODEX_HOME": str(self.root / "codex-home"),
                                               "CLAUDE_CONFIG_DIR": str(self.root / "claude-home"),
                                               "TICO_PIDFILE": str(self.pidfile), "TICO_DELAY": "0.2"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        quiet = mock.patch("runner.login.log")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.runner = service(self.root)
        self.runner.client, self.runner.last_heartbeat = Client(), 99
        self.logins = Logins(self.runner)
        self.addCleanup(self.logins.stop)

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def drive(self, until, timeout=20):
        """Poll the way the runner's loop does until `until()` holds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.logins.poll(force=True)
            if until():
                return
            time.sleep(0.1)
        self.fail("gave up waiting; reports: " + json.dumps([b for _, b in self.runner.client.posts])[-800:])

    def states(self, lid=None):
        return [b["state"] for path, b in self.runner.client.posts if lid is None or lid in path]

    def alive(self):
        try:
            os.kill(int(self.pidfile.read_text()), 0)
            return True
        except (OSError, ValueError):
            return False

    def test_nothing_secret_leaves_the_machine(self):
        self.stub("codex", CODEX)
        self.runner.client.wanted = [{"id": "l1", "runtime": "codex", "profile": ""}]
        self.drive(lambda: "signed_in" in self.states())
        sent = json.dumps(self.runner.client.posts)
        for secret in (JWT, "eyJ", CREDENTIAL, "auth.json"):
            self.assertNotIn(secret, sent)
        self.assertIn("[redacted]", sent)

    def test_a_pasted_code_is_typed_into_the_cli_and_never_reported(self):
        self.stub("claude", CLAUDE)
        client = self.runner.client
        client.wanted = [{"id": "c1", "runtime": "claude", "profile": ""}]
        self.drive(lambda: any(b["state"] == "waiting" and b["url"] for _, b in client.posts))
        first = next(b for _, b in client.posts if b["state"] == "waiting")
        self.assertEqual(first["url"], "https://claude.example/authorize?state=abc")
        self.assertTrue(any("Paste code here" in line for line in first["lines"]))
        client.wanted = [{"id": "c1", "runtime": "claude", "profile": "", "code": "good-code#state-1234"}]
        self.drive(lambda: "signed_in" in self.states())
        self.assertTrue(any(b.get("code_taken") for _, b in client.posts))
        sent = json.dumps(client.posts)
        for secret in ("good-code", CREDENTIAL, "state-1234"):
            self.assertNotIn(secret, sent)

class Parse(unittest.TestCase):

    def test_tokens_are_scrubbed_from_relayed_lines(self):
        got = parse(f"Your token: sk-ant-oat01-{'Ab1' * 12}\nid {JWT}\nblob {'QUJD' * 12}\nfine line here\n")
        text = " ".join(got["lines"])
        for secret in ("sk-ant", "eyJ", "QUJD"):
            self.assertNotIn(secret, text)
        self.assertIn("fine line here", text)

    def test_an_echoed_pasted_code_is_hidden(self):
        got = parse("Paste code > good-code#state-1234\nLogin successful\n", hide="good-code#state-1234")
        self.assertEqual(got["lines"], ["Login successful"])


if __name__ == "__main__":
    unittest.main()
