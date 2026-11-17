#!/usr/bin/env python3
"""Unit tests for connectors/slack.py, a thin client of Tico's Slack routes. No network, no token: Tico's answers are
faked at `slack.hub` and `slack.client`.

python3 -m unittest discover -s connectors
"""

import contextlib, email.message, io, json, os, stat, sys, tempfile, unittest, urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import slack  # noqa: E402
from clients.tico import APIError  # noqa: E402

TZ = "America/Los_Angeles"
NOW = datetime(2026, 9, 2, 10, 30, tzinfo=slack.zone(TZ))  # a Wednesday morning in LA


def run(*argv):
    """(exit code, stdout, stderr) of one command."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = slack.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Tico:
    """Tico's answers by (method, path); every call is kept."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, path, body=None, query=None, timeout=30):
        self.calls.append((method, path, body, query))
        answer = self.answers[(method, path)]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Times(unittest.TestCase):
    def test_yesterday_is_the_whole_previous_day(self):
        start, end = slack.time_range("yesterday", None, TZ, NOW)
        self.assertEqual((start.day, start.hour, end.day, end.hour), (1, 0, 2, 0))
        start, end = slack.time_range("2026-08-30", "yesterday", TZ, NOW)
        self.assertEqual((start.day, end.day), (30, 2))

    def test_relative_iso_and_today(self):
        start, end = slack.time_range("90m", None, TZ, NOW)
        self.assertEqual((end - start).total_seconds(), 5400)
        self.assertEqual(slack.time_range("today", "now", TZ, NOW)[0].hour, 0)
        start, _ = slack.time_range("2026-09-01T09:00Z", None, TZ, NOW)
        self.assertEqual(start.utcoffset().total_seconds(), 0)

    def test_an_empty_range_a_bad_time_or_zone_is_a_failure(self):
        for since, until, tz in (
            ("now", "24h", TZ),
            ("someday", None, TZ),
            ("24h", None, "Mars/Base"),
            (" ", None, TZ),
        ):
            with self.assertRaises(slack.Failure):
                slack.time_range(since, until, tz, NOW)


class Text(unittest.TestCase):
    def test_markdown_shows_the_warning_threads_and_an_empty_channel(self):
        md = slack.render_md(
            [
                {
                    "id": "C1",
                    "name": "general",
                    "start": "a",
                    "end": "b",
                    "warning": "Tico screening: careful",
                    "messages": [
                        {
                            "time": "t1",
                            "author": "Ana",
                            "text": "hello\n\nworld",
                            "permalink": "p1",
                            "replies": [{"time": "t2", "author": "Ben", "text": "", "permalink": "p2"}],
                        }
                    ],
                },
                {"id": "C2", "messages": []},
            ]
        )
        self.assertIn("## #general (C1)", md)
        self.assertIn("1 messages, 1 thread replies", md)
        self.assertIn("Tico screening: careful", md)
        self.assertIn("  - t2 Ben (reply) - p2\n    (no text)", md)
        self.assertIn("## C2", md)
        self.assertIn("Nothing in this range.", md)

    def test_one_line_drops_control_and_bidi_characters(self):
        self.assertEqual(slack.one_line("a\x00b‮ c\n d"), "ab c d")

    def test_a_file_name_is_prefixed_with_its_id_and_trimmed(self):
        self.assertEqual(slack.file_name("F01", "../../etc/passwd"), "F01-passwd")
        self.assertEqual(slack.file_name("F0/1", ".."), "F01")
        long = slack.file_name("F01", "x" * 400 + ".png")
        self.assertTrue(long.endswith(".png") and len(long.encode()) <= slack.MAX_NAME_BYTES)
        odd = slack.file_name("F01", "y" * 300 + "." + "z" * 40)
        self.assertLessEqual(len(odd.encode()), slack.MAX_NAME_BYTES)


class Saving(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_file_is_never_replaced_or_followed(self):
        target = self.dir / "F1-a.txt"
        target.write_bytes(b"old")
        saved = slack.save_new(target, b"new", folder=True)
        self.assertEqual((saved.name, target.read_bytes(), saved.read_bytes()), ("F1-a-2.txt", b"old", b"new"))
        with self.assertRaises(slack.Refused):
            slack.save_new(target, b"new", folder=False)
        with self.assertRaises(slack.Failure):
            slack.save_new(self.dir / "missing" / "x", b"", folder=False)

    def test_the_default_folder_is_made_private_and_refused_unless_it_is_a_real_folder(self):
        folder = self.dir / "files"
        self.assertEqual(slack.files_dir(folder), folder)
        os.chmod(folder, 0o755)
        slack.files_dir(folder)
        self.assertEqual(stat.S_IMODE(os.lstat(folder).st_mode), 0o700)
        link = self.dir / "link"
        link.symlink_to(self.dir)
        with self.assertRaises(slack.Refused):
            slack.files_dir(link)
        with self.assertRaises(slack.Failure):
            slack.files_dir(self.dir / "no" / "such")


class Calls(unittest.TestCase):
    def test_without_a_tico_credential_nothing_is_asked(self):
        with mock.patch.dict(os.environ, {"HUB_API_URL": "", "HUB_TOKEN": ""}):
            code, _, err = run("channels")
        self.assertEqual(code, 1)
        self.assertIn("no Tico credential", err)

    def test_ticos_refusal_exits_2_and_anything_else_exits_1(self):
        api = mock.Mock()
        with mock.patch.object(slack, "client", return_value=api):
            api.call.side_effect = APIError("slack_scope", "may not read #x", 403)
            with self.assertRaises(slack.Refused):
                slack.hub("GET", "slack/history")
            api.call.side_effect = APIError("slack_gateway_off", "Start it", 503)
            with self.assertRaises(slack.Failure) as caught:
                slack.hub("GET", "slack/history")
            self.assertNotIsInstance(caught.exception, slack.Refused)
            self.assertEqual(slack.failed("slack_timeout", "", 504).hint, "Try again shortly.")

    def test_doctor_says_when_the_gateway_is_off(self):
        tico = Tico(
            {("GET", "me"): {"actor": "bot:ops"}, ("GET", "slack/channels"): {"channels": [{}], "gateway": "off"}}
        )
        with mock.patch.object(slack, "hub", tico):
            code, out, err = run("doctor")
        self.assertEqual(code, 1)
        self.assertIn("gateway   off", out)
        self.assertIn("TICO_SLACK_GATEWAY_ENABLED", err)
        tico.answers[("GET", "slack/channels")] = {"channels": [], "gateway": "connected"}
        with mock.patch.object(slack, "hub", tico):
            self.assertEqual(run("doctor", "--json")[0], 0)

    def test_channels_lists_the_list(self):
        rows = [{"channel": "#general", "post": True, "readers": ["ops"], "note": "Team\nnews"}, {"channel": "#q"}]
        tico = Tico({("GET", "slack/channels"): {"channels": rows}})
        with mock.patch.object(slack, "hub", tico):
            code, out, _ = run("channels")
            self.assertEqual(json.loads(run("channels", "--json")[1]), rows)
        self.assertEqual(code, 0)
        self.assertIn("#general", out)
        self.assertIn("  Team news", out)
        self.assertIn("2 channels on the list", out)

    def test_history_asks_tico_for_each_channel_and_renders_it(self):
        answer = {
            "id": "C1",
            "name": "general",
            "warning": "Tico screening: careful",
            "messages": [{"ts": "1788000000.0001", "author": "Ana", "text": "hi", "replies": [{"ts": "1788000001.0"}]}],
        }
        tico = Tico({("GET", "slack/history"): answer})
        with mock.patch.object(slack, "hub", tico):
            code, out, _ = run("history", "--channel", "#general,C2", "--since", "7d", "--threads")
        self.assertEqual(code, 0)
        self.assertEqual([q["channel"] for _, _, _, q in tico.calls], ["#general", "C2"])
        self.assertEqual(tico.calls[0][3]["threads"], "true")
        self.assertIn("Tico screening: careful", out)
        with mock.patch.object(slack, "hub", tico):
            code, out, _ = run("history", "--channel", "C1", "--format", "json")
        self.assertEqual(json.loads(out)["channels"][0]["messages"][0]["time"][:4], "2026")

    def test_a_post_is_followed_until_slack_has_it(self):
        tico = Tico(
            {
                ("POST", "slack/posts"): {"message_id": "m1", "state": "ready", "channel": "C1", "name": "general"},
                ("GET", "slack/posts/m1"): [{"state": "ready"}, {"state": "sent", "permalink": "https://x/p1"}],
            }
        )
        with mock.patch.object(slack, "hub", tico), mock.patch.object(slack.time, "sleep"):
            code, out, _ = run("post", "--channel", "#general", "--text", "Shipped", "--thread", "1.2")
        self.assertEqual((code, out.strip()), (0, "posted to #general: https://x/p1"))
        self.assertEqual(tico.calls[0][2], {"channel": "#general", "text": "Shipped", "thread_ts": "1.2"})

    def test_a_canceled_post_is_refused_and_a_failed_one_fails(self):
        for state, code in (("canceled", 2), ("failed", 1)):
            tico = Tico(
                {
                    ("POST", "slack/posts"): {"message_id": "m1", "channel": "C1"},
                    ("GET", "slack/posts/m1"): {"state": state, "error": "Slack Connect channel"},
                }
            )
            with mock.patch.object(slack, "hub", tico):
                got, _, err = run("post", "--channel", "#partners", "--text", "hi")
            self.assertEqual(got, code)
            self.assertIn("Slack Connect channel", err)

    def test_a_held_post_says_why_and_sends_nothing(self):
        tico = Tico({("POST", "slack/posts"): {"held": {"reason": "No prices in public channels"}}})
        with mock.patch.object(slack, "hub", tico):
            code, out, _ = run("post", "--channel", "#general", "--text", "$5", "--json")
        self.assertEqual(code, 2)
        self.assertIn("No prices in public channels", json.loads(out)["error"])

    def test_a_dm_goes_to_one_person_and_text_can_come_from_stdin(self):
        tico = Tico(
            {
                ("POST", "slack/dms"): {"message_id": "m2", "to": "ana@acme.example"},
                ("GET", "slack/posts/m2"): {"state": "queued"},
            }
        )
        with (
            mock.patch.object(slack, "hub", tico),
            mock.patch.object(slack.time, "sleep"),
            mock.patch.object(sys, "stdin", io.StringIO("from stdin")),
        ):
            code, out, _ = run("dm", "--to", "ana@acme.example", "--text", "-")
        self.assertEqual((code, out.strip()), (0, "queued"))
        self.assertEqual(tico.calls[0][2], {"to": "ana@acme.example", "text": "from stdin"})
        tico.answers[("GET", "slack/posts/m2")] = {"state": "sent", "permalink": "https://x/d"}
        with mock.patch.object(slack, "hub", tico):
            self.assertIn("https://x/d", run("dm", "--to", "ana@acme.example", "--text", "hi")[1])
            self.assertEqual(run("dm", "--to", "a@x,b@x", "--text", "hi")[0], 2)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def api(self, opened):
        api = mock.Mock(url="https://tico.example")
        api.bearer.return_value = "token"
        api.opener.open.side_effect = opened
        return api

    def test_a_file_from_tico_is_saved_beside_others_with_the_screening_line(self):
        headers = email.message.Message()
        for k, v in (
            ("X-Slack-File-Name", "a%20b.txt"),
            ("X-Slack-File-Type", "text/plain"),
            ("X-Tico-Screening", "Careful"),
        ):
            headers[k] = v
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value, response.headers = b"data", headers
        with mock.patch.object(slack, "client", return_value=self.api(lambda req, timeout: response)):
            code, out, _ = run("file", "--id", "F0FILE0001", "--out", str(self.dir), "--json")
        row = json.loads(out)
        self.assertEqual((code, row["name"], row["warning"], row["bytes"]), (0, "a b.txt", "Careful", 4))
        self.assertEqual(Path(row["path"]).read_bytes(), b"data")
        self.assertEqual(Path(row["path"]).name, "F0FILE0001-a b.txt")
        with (
            mock.patch.object(slack, "client", return_value=self.api(lambda req, timeout: response)),
            mock.patch.object(slack, "FILES_DIR", self.dir / "default"),
        ):
            code, out, _ = run("file", "--id", "F0FILE0001")
        self.assertEqual(code, 0)
        self.assertIn("Careful", out)
        response.read.return_value = b"x" * (slack.MAX_FILE_BYTES + 1)
        with mock.patch.object(slack, "client", return_value=self.api(lambda req, timeout: response)):
            self.assertEqual(run("file", "--id", "F0FILE0001", "--out", str(self.dir / "big"))[0], 2)

    def test_ticos_refusal_or_no_answer_saves_nothing(self):
        def refused(req, timeout):
            body = io.BytesIO(json.dumps({"error": {"code": "slack_scope", "detail": "not your channel"}}).encode())
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", email.message.Message(), body)

        with mock.patch.object(slack, "client", return_value=self.api(refused)):
            code, _, err = run("file", "--id", "F0FILE0001", "--out", str(self.dir))
        self.assertEqual(code, 2)
        self.assertIn("not your channel", err)

        def down(req, timeout):
            raise urllib.error.URLError("refused")

        with mock.patch.object(slack, "client", return_value=self.api(down)):
            self.assertEqual(run("file", "--id", "F0FILE0001", "--out", str(self.dir))[0], 1)
        self.assertEqual(list(self.dir.iterdir()), [])


class Workspace(unittest.TestCase):
    """browser.py finds a bot's bot.yaml under TICO_PROJECTS_DIR when the runner sets it, else beside the checkout. These
    lived beside Slack's when slack.py read bot.yaml too; it no longer does (Tico holds each bot's Slack scope)."""

    def projects(self, value):
        import subprocess

        env = {k: v for k, v in os.environ.items() if k != "TICO_PROJECTS_DIR"}
        if value:
            env["TICO_PROJECTS_DIR"] = value
        out = subprocess.run(
            [sys.executable, "-c", "import browser; print(browser.PROJECTS)"],
            cwd=Path(__file__).resolve().parent,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.split()

    def test_the_workspace_follows_tico_projects_dir_and_defaults_to_the_folder_around_the_checkout(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(self.projects(workspace), [workspace])
        self.assertEqual(self.projects(None), [str(Path(__file__).resolve().parents[2])])

    def test_a_slug_cannot_name_a_folder_outside_the_workspace(self):
        import browser

        with tempfile.TemporaryDirectory() as root:
            workspace, outside = Path(root, "work"), Path(root, "outside")
            (workspace / "bot-real").mkdir(parents=True)
            outside.mkdir()
            (workspace / "bot-real" / "bot.yaml").write_text("tools: []\n")
            (outside / "bot.yaml").write_text("tools: []\n")
            with mock.patch.object(browser, "PROJECTS", workspace):
                self.assertEqual(browser.load_manifest("real"), {"tools": []})
                for slug in ("real/../../outside", "../outside", "/tmp", "", "real/.."):
                    with self.assertRaises(browser.Refused, msg=slug):
                        browser.load_manifest(slug)

    def test_a_runs_own_bot_is_read_from_the_folder_the_runner_names(self):
        # A bot made with a UUID id runs as HUB_BOT=<id> in the folder named from its first slug, which a rename keeps:
        # bot-<HUB_BOT> is not its folder, HUB_BOT_FOLDER is.
        import browser

        bot = "01a19806-0460-77a7-860b-b797cea3f270"
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "bot-sales-helper").mkdir()
            (workspace / "bot-sales-helper" / "bot.yaml").write_text("tools: [own]\n")
            with mock.patch.object(browser, "PROJECTS", workspace):
                with mock.patch.dict(os.environ, {"HUB_BOT": bot, "HUB_BOT_FOLDER": "bot-sales-helper"}):
                    self.assertEqual(browser.load_manifest(bot), {"tools": ["own"]})


class Standalone(unittest.TestCase):
    """The script as a bot's turn runs it: from another folder, with nothing of the checkout on sys.path (these tests
    put it there themselves, which hid a missing import path)."""

    def test_it_reaches_tico_from_outside_the_checkout(self):
        import subprocess

        script = str(Path(__file__).resolve().parent / "slack.py")
        env = {"PATH": os.environ.get("PATH", ""), "HUB_API_URL": "http://127.0.0.1:9", "HUB_TOKEN": "t"}
        with tempfile.TemporaryDirectory() as elsewhere:
            for argv in (["channels"], ["file", "--id", "F0FILE0001"]):
                out = subprocess.run(
                    [sys.executable, "-I", script, *argv], cwd=elsewhere, env=env, capture_output=True, text=True
                )
                self.assertNotIn("ModuleNotFoundError", out.stderr, argv)
                self.assertNotIn("Traceback", out.stderr, argv)
                self.assertEqual(out.returncode, 1, (argv, out.stdout, out.stderr))  # Tico unreachable: a failure


if __name__ == "__main__":
    unittest.main()
