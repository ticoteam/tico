#!/usr/bin/env python3
"""Unit tests for connectors/slack.py. No network, no token.

    python3 -m unittest discover -s connectors
"""

import contextlib, io, json, os, stat, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slack                                            # noqa: E402

TZ = "America/Los_Angeles"
NOW = datetime(2026, 9, 2, 10, 30, tzinfo=slack.zone(TZ))   # a Wednesday morning in LA


class PostPolicy(unittest.TestCase):
    channels = [
        {"id": "C0000000001", "name": "marketing", "post": True},
        {"id": "C0000000002", "name": "release_notes", "post": False},
        {"id": None, "name": "agents", "post": True},
        {"id": "C0000000003", "name": "random"},
    ]

    def test_employee_must_declare_slack_post(self):
        allowed = {"access": [{"service": "slack", "can": ["read", "post"]}]}
        self.assertEqual(slack.check_can_post(allowed, "doc-updater")["service"], "slack")
        with self.assertRaises(slack.Refused):
            slack.check_can_post({"access": [{"service": "slack", "can": ["read"]}]}, "doc-updater")
        with self.assertRaises(slack.Refused):
            slack.check_can_post({"access": [{"service": "gmail", "can": ["post"]}]}, "seo")
        with self.assertRaises(slack.Refused):
            slack.check_can_post({}, "seo")

    def test_channel_must_be_registered_and_postable(self):
        entry = slack.find_registry_channel(self.channels, "#marketing")
        self.assertEqual(slack.check_channel_postable(entry, "#marketing"), "C0000000001")
        self.assertIs(slack.find_registry_channel(self.channels, "C0000000001"), entry)
        with self.assertRaises(slack.Refused):
            slack.find_registry_channel(self.channels, "#general")
        with self.assertRaises(slack.Refused):
            slack.check_channel_postable(
                slack.find_registry_channel(self.channels, "release_notes"), "release_notes")
        with self.assertRaises(slack.Refused):
            slack.check_channel_postable(
                slack.find_registry_channel(self.channels, "#agents"), "#agents")

    def test_a_channel_that_says_nothing_about_posting_is_postable(self):
        entry = slack.find_registry_channel(self.channels, "#random")
        self.assertEqual(slack.check_channel_postable(entry, "#random"), "C0000000003")

    def test_externally_shared_channels_are_refused(self):
        slack.check_not_external({"channel": {"name": "marketing"}}, "#marketing")
        for flag in ("is_ext_shared", "is_shared", "is_pending_ext_shared", "is_org_shared"):
            with self.assertRaises(slack.Refused):
                slack.check_not_external({"channel": {"name": "partner", flag: True}}, "#partner")

class DmPolicy(unittest.TestCase):
    allowed = {"ana@acme.example", "cara@acme.example"}

    def person(self, **kw):
        row = {"id": "U9", "name": "cara", "display": "Cara", "real_name": "Cara",
               "email": "cara@acme.example", "is_bot": False, "deleted": False}
        row.update(kw)
        return row

    def test_bots_and_deactivated_accounts_are_refused(self):
        with self.assertRaises(slack.Refused):
            slack.check_recipient(self.person(is_bot=True), self.allowed, "@companyhub")
        with self.assertRaises(slack.Refused):
            slack.check_recipient(self.person(id="USLACKBOT"), self.allowed, "@slackbot")
        with self.assertRaises(slack.Refused):
            slack.check_recipient(self.person(deleted=True), self.allowed, "@gone")

class FakeHTTP:
    """Replaces slack._request. Each response is (status, headers, payload)."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": json.loads(data) if data else None,
                           "auth": headers.get("Authorization")})
        status, hdrs, payload = self.responses.pop(0)
        return status, hdrs, json.dumps(payload).encode()


DANILLO = {"id": "U9", "name": "cara", "display": "Cara", "real_name": "Cara",
           "email": "cara@acme.example", "is_bot": False, "deleted": False}
ANA = {"id": "U2", "name": "anah", "display": "anah", "real_name": "Ana",
         "email": "ana@acme.example", "is_bot": False, "deleted": False}
A_BOT = {"id": "UB1", "name": "someapp", "display": "Some App", "real_name": "",
         "email": "", "is_bot": True, "deleted": False}


def slack_user(uid, email, name="Cara", **kw):
    """A users.lookupByEmail / users.info payload."""
    row = {"id": uid, "name": name.lower(),
           "profile": {"display_name": name, "real_name": name, "email": email}}
    row.update(kw)
    return row


class FakeSlack(unittest.TestCase):
    """Base for command tests: a token, a fake employee, no disk writes, no network."""

    slug = "doc-updater"

    def setUp(self):
        self.real_request, self.real_projects = slack._request, slack.PROJECTS
        self.real_audit, self.audited = slack.audit, []
        self.old_token = os.environ.get("SLACK_BOT_TOKEN")
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
        slack._CACHE.clear()
        slack._CACHE["directory"] = [DANILLO, ANA, A_BOT]      # no users.list call
        self.tmp = tempfile.TemporaryDirectory()
        slack.PROJECTS = Path(self.tmp.name)
        self.employee("doc-updater", ["read", "post"])
        self.employee("reader", ["read"])
        slack.audit = lambda *a, **k: self.audited.append((a, k))
        access = Path(self.tmp.name) / "hub-access.yaml"        # the company list, never the checkout's own
        access.write_text("owner: ana@acme.example\nallowed: [cara@acme.example]\n")
        self.real_hub_access = slack.load_hub_access
        slack.load_hub_access = lambda path=access: self.real_hub_access(path)
        self.real_hub_get, self.stage = slack.HUB_GET, "active"   # Tico says the bot is past onboarding
        slack.HUB_GET = lambda path: {"stage": self.stage} if path.startswith("bots/") and self.stage else None

    def tearDown(self):
        slack._request, slack.PROJECTS, slack.audit = (self.real_request, self.real_projects,
                                                       self.real_audit)
        slack.HUB_GET = self.real_hub_get
        slack.load_hub_access = self.real_hub_access
        os.environ.pop("SLACK_BOT_TOKEN", None)
        if self.old_token is not None:
            os.environ["SLACK_BOT_TOKEN"] = self.old_token
        slack._CACHE.clear()
        self.tmp.cleanup()

    def employee(self, slug, can):
        d = Path(self.tmp.name) / f"emp-{slug}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "employee.yaml").write_text(
            "slug: %s\naccess:\n  - service: slack\n    can: [%s]\n" % (slug, ", ".join(can)))

    def run_cli(self, argv, responses=()):
        slack._request = FakeHTTP(*responses)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = slack.main(argv)
        return rc, out.getvalue(), slack._request.calls


OK_OPEN = (200, {}, {"ok": True, "channel": {"id": "D0123"}})
OK_POST = (200, {}, {"ok": True, "ts": "1756742400.000100"})
OK_AUTH = (200, {}, {"ok": True, "user_id": "U0BOT", "url": "https://acme.slack.com"})


class DmGates(FakeSlack):
    def test_hub_access_is_a_file_people_write_so_its_owner_key_stays_owner(self):
        self.assertIn("ana@acme.example", slack.load_hub_access())

    def test_someone_outside_hub_access_is_refused_and_nothing_is_posted(self):
        rc, out, calls = self.run_cli(
            ["dm", "--as", self.slug, "--to", "lead@acme.com", "--text", "hi", "--json"],
            [(200, {}, {"ok": True, "user": slack_user("U77", "lead@acme.com", "Lead")})])
        body = json.loads(out)
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)                  # the lookup, then it stopped
        self.assertIn("lead@acme.com", body["error"])
        self.assertIn("hub-access.yaml", body["hint"])
        self.assertEqual(self.audited, [])

    def test_an_onboarding_bot_or_one_whose_stage_tico_cannot_confirm_sends_nothing(self):
        for stage in ("onboarding", None):                 # None: no hub credential, or Tico unreachable
            self.stage = stage
            for argv in (["post", "--as", self.slug, "--channel", "#agents", "--text", "hi", "--json"],
                         ["dm", "--as", self.slug, "--to", "cara@acme.example", "--text", "hi", "--json"]):
                rc, out, calls = self.run_cli(argv, [OK_OPEN, OK_POST, OK_AUTH])
                self.assertEqual((rc, calls), (2, []), (stage, argv[0], out))
                self.assertIn("onboarding", json.loads(out)["error"])
        self.assertEqual(self.audited, [])


class Workspace(unittest.TestCase):
    """A bot's bot.yaml is found under TICO_PROJECTS_DIR when the runner sets it, else beside the checkout."""

    def projects(self, value):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "TICO_PROJECTS_DIR"}
        if value:
            env["TICO_PROJECTS_DIR"] = value
        code = "import browser, slack; print(browser.PROJECTS); print(slack.PROJECTS)"
        out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent, env=env,
                             capture_output=True, text=True, check=True)
        return out.stdout.split()

    def test_the_workspace_follows_tico_projects_dir_and_defaults_to_the_folder_around_the_checkout(self):
        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(self.projects(workspace), [workspace] * 2)
        self.assertEqual(self.projects(None), [str(Path(__file__).resolve().parents[2])] * 2)

    def test_a_slug_cannot_name_a_folder_outside_the_workspace(self):
        import browser
        with tempfile.TemporaryDirectory() as root:
            workspace, outside = Path(root, "work"), Path(root, "outside")
            (workspace / "bot-real").mkdir(parents=True)
            outside.mkdir()
            (workspace / "bot-real" / "bot.yaml").write_text("tools: []\n")
            (outside / "bot.yaml").write_text("tools: []\n")
            saved = browser.PROJECTS, slack.PROJECTS
            browser.PROJECTS = slack.PROJECTS = workspace
            try:
                self.assertEqual(slack.manifest_of("real")[0], workspace / "bot-real" / "bot.yaml")
                self.assertEqual(browser.load_manifest("real"), {"tools": []})
                for slug in ("real/../../outside", "../outside", "/tmp", "", "real/.."):
                    with self.assertRaises(slack.Refused, msg=slug):
                        slack.manifest_of(slug)
                    with self.assertRaises(browser.Refused, msg=slug):
                        browser.load_manifest(slug)
            finally:
                browser.PROJECTS, slack.PROJECTS = saved

    def test_a_runs_own_bot_is_read_from_the_folder_the_runner_names(self):
        # A bot made with a UUID id runs as HUB_BOT=<id> in the folder named from its first slug, which a rename keeps:
        # bot-<HUB_BOT> is not its folder, HUB_BOT_FOLDER is. Another bot named by slug is still found by its slug.
        import browser
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from connectors import mail
        bot = "01a19806-0460-77a7-860b-b797cea3f270"
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            for folder, text in (("bot-sales-helper", "tools: [own]\n"), ("bot-ops", "tools: [ops]\n")):
                (workspace / folder).mkdir()
                (workspace / folder / "bot.yaml").write_text(text)
            saved = browser.PROJECTS, slack.PROJECTS
            browser.PROJECTS = slack.PROJECTS = workspace
            env = {"HUB_BOT": bot, "HUB_BOT_FOLDER": "bot-sales-helper"}
            try:
                with mock.patch.dict(os.environ, env):
                    self.assertEqual(slack.manifest_of(bot)[0], workspace / "bot-sales-helper" / "bot.yaml")
                    self.assertEqual(browser.load_manifest(bot), {"tools": ["own"]})
                    self.assertEqual(mail.repo_dir(workspace, bot), workspace / "bot-sales-helper")
                    self.assertEqual(slack.manifest_of("ops")[0], workspace / "bot-ops" / "bot.yaml")
                    self.assertEqual(mail.repo_dir(workspace, "ops"), workspace / "bot-ops")
                with mock.patch.dict(os.environ, {**env, "HUB_BOT_FOLDER": "../outside"}):
                    self.assertEqual(slack.manifest_of(bot)[0], workspace / f"bot-{bot}" / "bot.yaml")
            finally:
                browser.PROJECTS, slack.PROJECTS = saved


class ChannelList(FakeSlack):
    """The channel list is the hub's (Settings > Tools > Slack); the old registry file counts only until it is imported."""

    rows = [{"id": "C0000000010", "name": "customer_success", "post": True, "readers": ["reader"], "note": ""},
            {"id": "", "name": "agents", "post": True, "readers": [], "note": ""},
            {"id": "C0000000011", "name": "release_notes", "post": False, "readers": [], "note": ""}]

    def setUp(self):
        super().setUp()
        self.asked = []
        self.file = Path(self.tmp.name) / "slack-channels.yaml"
        self.answer = {"channels": self.rows, "registry_file": {"present": False, "channels": 0, "imported": True}}
        slack.HUB_GET = lambda path: ({"stage": "active"} if path.startswith("bots/")
                                      else (self.asked.append(path), self.answer)[1])

    def tearDown(self):
        super().tearDown()

    def test_a_run_reads_the_list_from_the_hub_and_the_file_only_until_it_is_imported(self):
        self.file.write_text("channels:\n  - {id: C0000000012, name: old_one, readers: reader}\n")
        self.assertEqual([c["name"] for c in slack.load_channels(self.file)], ["customer_success", "agents", "release_notes"])
        self.assertEqual(self.asked, ["slack/channels"])
        slack._CACHE.clear()
        self.answer["registry_file"]["imported"] = False
        self.assertEqual([c["name"] for c in slack.load_channels(self.file)],
                         ["customer_success", "agents", "release_notes", "old_one"])

    def test_without_the_hub_the_file_is_used_and_with_neither_the_refusal_says_where_the_list_is(self):
        self.answer = None
        with self.assertRaises(slack.Refused) as refused:
            slack.load_channels(self.file)
        self.assertIn("Settings > Tools > Slack", refused.exception.hint)
        slack._CACHE.clear()
        self.file.write_text("channels:\n  - {id: C0000000012, name: old_one}\n")
        self.assertEqual([c["name"] for c in slack.load_channels(self.file)], ["old_one"])

    def test_a_reader_on_the_list_may_read_a_channel_outside_its_declared_scope(self):
        manifest = {"tools": [{"service": "slack", "can": ["read"], "channels": ["agents"]}]}
        rows = slack.load_channels(self.file)
        slack.check_history_scope(manifest, "reader", ["#agents", "C0000000010"], rows)
        with self.assertRaises(slack.Refused):                       # listed, but its reader is someone else
            slack.check_history_scope(manifest, "other-bot", ["#customer_success"], rows)
        with self.assertRaises(slack.Refused):                       # not on the list at all
            slack.check_history_scope(manifest, "reader", ["#general"], rows)
        # A declared channel the list does not have no longer refuses every read.
        stale = {"tools": [{"service": "slack", "can": ["read"], "channels": ["gone", "agents"]}]}
        slack.check_history_scope(stale, "reader", ["#agents"], rows)

    def test_a_post_follows_the_list_and_externally_shared_channels_stay_refused(self):
        listing = (200, {}, {"ok": True, "channels": [{"id": "C0000000020", "name": "agents"}]})
        rc, out, calls = self.run_cli(["post", "--as", self.slug, "--channel", "#agents", "--text", "hi", "--json"],
                                      [listing, (200, {}, {"ok": True, "channel": {"id": "C0000000020", "name": "agents"}}),
                                       OK_POST, OK_AUTH])
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls[-2]["data"]["channel"], "C0000000020")      # listed by name: Slack gave the id
        self.assertEqual(len(self.audited), 1)
        self.audited.clear()
        slack._CACHE.clear()
        rc, out, calls = self.run_cli(["post", "--as", self.slug, "--channel", "#release_notes", "--text", "hi", "--json"])
        self.assertEqual((rc, calls), (2, []))                        # the list says bots may not post there
        self.assertIn("Settings > Tools > Slack", json.loads(out)["hint"])
        rc, out, calls = self.run_cli(["post", "--as", self.slug, "--channel", "#general", "--text", "hi", "--json"])
        self.assertEqual((rc, calls), (2, []))                        # not on the list
        slack._CACHE.clear()
        rc, out, calls = self.run_cli(["post", "--as", self.slug, "--channel", "C0000000010", "--text", "hi", "--json"],
                                      [(200, {}, {"ok": True, "channel": {"name": "customer_success", "is_ext_shared": True}})])
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)                               # conversations.info, then it stopped
        self.assertEqual(self.audited, [])


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SHOT = {"id": "F0SHOT1", "name": "checkout-error.png", "mimetype": "image/png", "size": len(PNG),
        "url_private": "https://files.slack.com/files-pri/T1-F0SHOT1/checkout-error.png",
        "url_private_download": "https://files.slack.com/files-pri/T1-F0SHOT1/download/checkout-error.png",
        "channels": [], "groups": [], "ims": ["D0123"]}


class FileDownload(FakeSlack):
    """`file --id` reads files.info, then fetches url_private with the bot token, as files:read allows."""

    def setUp(self):
        super().setUp()
        self.real_download, self.fetched = slack._download, []
        self.served = (200, {"Content-Type": "image/png"}, PNG)
        slack._download = lambda url, headers, timeout, limit: (self.fetched.append((url, headers)), self.served)[1]
        self.out = Path(self.tmp.name) / "slack-files"

    def tearDown(self):
        slack._download = self.real_download
        super().tearDown()

    def fetch(self, info=SHOT, slug="reader"):
        return self.run_cli(["file", "--as", slug, "--id", info["id"], "--out", str(self.out) + "/", "--json"],
                            [(200, {}, {"ok": True, "file": info})])

    def test_a_screenshot_shared_in_a_dm_is_saved_with_the_bot_token(self):
        rc, out, calls = self.fetch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls[0]["url"], slack.API + "files.info?file=F0SHOT1")
        url, headers = self.fetched[0]
        self.assertEqual(url, SHOT["url_private_download"])
        self.assertEqual(headers["Authorization"], "Bearer xoxb-test")
        saved = Path(json.loads(out)["path"])
        self.assertEqual((saved.name, saved.read_bytes()), ("F0SHOT1-checkout-error.png", PNG))

    def test_a_shared_name_never_replaces_or_follows_a_file_that_is_there(self):
        self.out.mkdir()
        agent = self.out / "AGENT.md"
        agent.write_text("the bot's instructions")
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("untouched")
        (self.out / "F0SHOT1-pic.png").symlink_to(outside)
        for name in ("../AGENT.md", "AGENT.md", "pic.png"):
            rc, out, _ = self.fetch({**SHOT, "name": name})
            self.assertEqual(rc, 0, out)
            self.assertNotEqual(Path(json.loads(out)["path"]).resolve(), agent.resolve())
        self.assertEqual(agent.read_text(), "the bot's instructions")
        self.assertEqual(outside.read_text(), "untouched")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["AGENT.md", "F0SHOT1-AGENT-2.md", "F0SHOT1-AGENT.md", "F0SHOT1-pic-2.png", "F0SHOT1-pic.png"])
        # An exact --out path that exists is refused, a symlink included.
        for existing in (agent, self.out / "F0SHOT1-pic.png"):
            rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--out", str(existing), "--json"],
                                      [(200, {}, {"ok": True, "file": SHOT})])
            self.assertEqual(rc, 2, out)
        self.assertEqual((agent.read_text(), outside.read_text()), ("the bot's instructions", "untouched"))

    def test_by_default_the_file_lands_outside_the_repository(self):
        real, slack.FILES_DIR = slack.FILES_DIR, self.out
        try:
            rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--json"],
                                      [(200, {}, {"ok": True, "file": {**SHOT, "size": "big"}})])
        finally:
            slack.FILES_DIR = real
        self.assertEqual(rc, 0, out)
        self.assertEqual(Path(json.loads(out)["path"]).parent, self.out)
        self.assertNotIn(str(slack.HUB), str(real))
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        if hasattr(os, "getuid"):
            self.assertTrue(real.name.endswith(f"-{os.getuid()}"), real)    # one folder per local user

    def test_the_default_folder_is_refused_unless_it_is_this_users_own_folder(self):
        real = slack.FILES_DIR
        try:
            elsewhere = Path(self.tmp.name) / "elsewhere"
            elsewhere.mkdir()
            slack.FILES_DIR = self.out
            self.out.symlink_to(elsewhere)                                  # planted link: refused
            rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--json"],
                                      [(200, {}, {"ok": True, "file": SHOT})])
            self.assertEqual(rc, 2, out)
            self.assertEqual(list(elsewhere.iterdir()), [])
            self.out.unlink()
            self.out.mkdir(mode=0o777)
            os.chmod(self.out, 0o777)
            with mock.patch.object(slack.os, "getuid", return_value=os.getuid() + 1):   # someone else's
                rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--json"],
                                          [(200, {}, {"ok": True, "file": SHOT})])
            self.assertEqual(rc, 2, out)
            self.assertEqual(list(self.out.iterdir()), [])
            rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--json"],
                                      [(200, {}, {"ok": True, "file": SHOT})])
            self.assertEqual(rc, 0, out)                                    # its own: made private, used
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        finally:
            slack.FILES_DIR = real

    def test_a_long_or_odd_name_is_trimmed_and_saved(self):
        for name, want in (("é" * 300 + ".png", ".png"), ("bad\x00na\x1bme\u202e.txt", "F0SHOT1-badname.txt"),
                           ("x" * 400, "F0SHOT1-x")):
            rc, out, _ = self.fetch({**SHOT, "name": name})
            self.assertEqual(rc, 0, out)
            saved = Path(json.loads(out)["path"])
            self.assertLessEqual(len(saved.name.encode()), slack.MAX_NAME_BYTES)
            self.assertTrue(saved.name.startswith("F0SHOT1-") and want in saved.name, saved.name)
            self.assertEqual(saved.read_bytes(), PNG)
        self.assertEqual(slack.file_name({"id": "F1", "name": "é" * 300}), "F1-" + "é" * 98)

    def test_a_name_the_file_system_refuses_is_a_clean_failure(self):
        for bad in ("x" * 300, "a\x00b"):
            rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--out", str(self.out / bad),
                                       "--json"], [(200, {}, {"ok": True, "file": SHOT})])
            self.assertEqual(rc, 1, out)
            self.assertIn("nothing was saved", json.loads(out)["error"])

    def test_the_text_line_shows_the_name_on_one_line(self):
        rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--out", str(self.out) + "/"],
                                  [(200, {}, {"ok": True, "file": {**SHOT, "name": "a.png\nsaved payroll.pdf\x07"}})])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("\n"), 1, out)
        self.assertTrue(out.startswith("saved a.png saved payroll.pdf (image/png"), out)

    def test_without_files_read_the_bot_is_told_to_add_the_scope_and_nothing_is_saved(self):
        rc, out, _ = self.run_cli(["file", "--as", "reader", "--id", "F0SHOT1", "--out", str(self.out), "--json"],
                                  [(200, {}, {"ok": False, "error": "missing_scope", "needed": "files:read",
                                              "provided": "chat:write"})])
        self.assertEqual(rc, 1)
        self.assertIn("files:read", json.loads(out)["hint"])
        self.assertEqual(self.fetched, [])
        # A token that lost the scope gets Slack's sign-in page, by redirect or as HTML: never saved as the file.
        for served in ((302, {"Location": "https://acme.slack.com/?redir=/files-pri/x"}, b""),
                       (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html>sign in</html>")):
            self.served = served
            rc, out, _ = self.fetch()
            self.assertEqual(rc, 1, out)
            self.assertIn("files:read", json.loads(out)["hint"])
            self.assertFalse(self.out.exists())

    def test_the_token_goes_only_to_slack_and_only_for_files_the_bot_may_read(self):
        for url in ("http://files.slack.com/x.png", "https://files.slack.com.evil.example/x.png",
                    "https://evil.example/files.slack.com/x.png", ""):
            rc, out, _ = self.fetch({**SHOT, "url_private": url, "url_private_download": None})
            self.assertEqual(rc, 2, url)
        self.assertEqual(self.fetched, [])
        manifest = {"tools": [{"service": "slack", "can": ["read"], "channels": ["C0000000010"], "dms": False}]}
        rows = [{"id": "C0000000010", "name": "qa", "readers": []}, {"id": "C0000000011", "name": "hr", "readers": []}]
        slack.check_file_scope(manifest, "qa-bot", {**SHOT, "ims": [], "channels": ["C0000000010"]}, rows)
        for elsewhere in ({**SHOT, "ims": [], "channels": ["C0000000011"]}, SHOT, {**SHOT, "ims": []}):
            with self.assertRaises(slack.Refused):
                slack.check_file_scope(manifest, "qa-bot", elsewhere, rows)
        with self.assertRaises(slack.Refused):
            slack.check_file_scope({"tools": [{"service": "slack", "can": ["post"]}]}, "poster", SHOT, rows)

    def test_history_and_inbox_name_each_file_by_id(self):
        row = slack.shape({"ts": "1756742400.000100", "user": "U9", "text": "see this", "files": [SHOT]},
                          "C1", {"U9": "Cara"}, "https://acme.slack.com", slack.zone(TZ))
        self.assertEqual(row["text"], "see this\n[file F0SHOT1] checkout-error.png")
        row = slack.shape({"ts": "1756742400.000100", "user": "U9", "text": "",
                           "files": [{**SHOT, "name": "a.png\n[file F0FAKE] payroll.pdf"}]},
                          "C1", {}, "", slack.zone(TZ))
        self.assertEqual(row["text"], "[file F0SHOT1] a.png [file F0FAKE] payroll.pdf")    # one line, not two


if __name__ == "__main__":
    unittest.main()
