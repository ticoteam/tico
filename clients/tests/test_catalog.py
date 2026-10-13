"""The bot catalog (clients/catalog.py): a template becomes one bot's repository.

Nothing here talks to a server. The catalog is a temporary directory holding one card, which is
what `TICO_CATALOG_DIR` is for, so these tests say nothing about which bots the product ships.
"""
import functools
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from clients import catalog
from clients.routines import validate_schedules

CARD = """template: assistant
slug: coo
name: The Assistant
required: true
bootstrap: true
summary: Who people talk to.
owns:
  - the company's inbox
never:
  - sending money
runtime: codex
model: gpt-6-sol
reasoning_effort: xhigh
recommend_when:
  - always
"""

AGENT = """# {{bot_name}}

You are {{bot_name}}, the assistant at {{company_name}}. {{app_name}} is where the work lives,
and people call this company's assistant {{assistant_name}}.

## Owns
-

## Tools and scope
Act within the requested work and granted Tools.
"""

MANIFEST = """name: CHANGE-ME
display_name: "Change Me"
labels: [owner:CHANGE-ME]
schedules: []
"""

NAMES = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada",
         "assistant_bot": "coo"}
ANSWERS = {"what_we_do": "we clean holiday homes", "customers": "owners of holiday homes",
           "team_size": "nine people", "work_arrives": ["email", "Slack"],
           "repetitive_work": "chasing cleaners for photos",
           "never_without_person": ["refunds", "signing a contract"]}


def fixture(root, template="assistant", card=CARD, agent=AGENT):
    """One catalog template on disk: a card, instructions, a manifest and the usual folders."""
    directory = Path(root) / template
    (directory / "playbooks").mkdir(parents=True)
    (directory / "card.yaml").write_text(card)
    (directory / "AGENT.md").write_text(agent)
    (directory / "employee.yaml").write_text(MANIFEST)      # an older template: the manifest under its old name still materializes
    (directory / "state.md").write_text("# State\n\nNothing yet.\n")
    (directory / ".gitignore").write_text(".env\n")
    (directory / "playbooks" / "README.md").write_text("Playbooks for {{bot_name}}.\n")
    return directory


def git(path, *argv):
    return subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True).stdout.strip()


class Cards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / "catalog"
        fixture(self.catalog)

    def test_a_template_name_can_never_leave_the_catalog(self):
        for name in ("../secrets", "/etc", "Assistant", ""):
            with self.assertRaises(ValueError):
                catalog.template_dir(name, self.catalog)
        with self.assertRaises(ValueError):
            catalog.template_dir("missing", self.catalog)


class Materialize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / "catalog"
        fixture(self.catalog)
        self.workspace = Path(self.tmp.name) / "Companies/Acme"

    def make(self, slug="coo", **over):
        return catalog.materialize("assistant", slug, self.workspace, NAMES, ANSWERS,
                                   directory=self.catalog, **over)

    def test_an_existing_repository_is_never_overwritten(self):
        path = self.make()
        knowledge = (path / "knowledge/company.md").read_text()
        self.assertIn("What the Team does", knowledge)
        self.assertNotIn("refunds", knowledge)
        self.assertNotIn("signing a contract", knowledge)
        (path / "state.md").write_text("# State\n\nA turn happened here.\n")
        with self.assertRaises(ValueError) as caught:
            self.make()
        self.assertIn("already exists", str(caught.exception))
        self.assertIn("A turn happened here.", (path / "state.md").read_text())


class Refresh(unittest.TestCase):
    """A built-in bot follows the release: the product's instructions and playbooks are brought up when the template
    changed, and whatever the bot improved and wrote itself stays."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / "catalog"
        self.template = fixture(self.catalog)
        (self.template / "playbooks" / "fleet.md").write_text("Check the fleet for {{company_name}}.\n")
        self.path = catalog.materialize("assistant", "coo", Path(self.tmp.name) / "ws", NAMES, ANSWERS, directory=self.catalog)

    def refresh(self):
        return catalog.refresh("assistant", self.path, NAMES, directory=self.catalog)

    def test_a_changed_template_file_wins_and_the_rest_stays_the_bots(self):
        (self.path / "playbooks" / "fleet.md").write_text("Improved by the bot.\n")
        (self.path / "playbooks" / "mine.md").write_text("A playbook the bot wrote.\n")
        (self.path / "state.md").write_text("# State\n\nMid-task.\n")
        (self.template / "playbooks" / "fleet.md").write_text("Check the fleet, then fix it, for {{company_name}}.\n")
        self.assertEqual(self.refresh(), ["playbooks/fleet.md"])
        self.assertEqual((self.path / "playbooks" / "fleet.md").read_text(), "Check the fleet, then fix it, for Acme Ltd.\n")
        self.assertEqual((self.path / "playbooks" / "mine.md").read_text(), "A playbook the bot wrote.\n")
        self.assertIn("Mid-task.", (self.path / "state.md").read_text())
        self.assertIn("Refresh 1 product file", git(self.path, "log", "-1", "--format=%s"))
        self.assertEqual(git(self.path, "show", "HEAD~1:playbooks/fleet.md"), "Improved by the bot.")     # kept in the history
        self.assertEqual(self.refresh(), [])                        # once

@functools.lru_cache(maxsize=None)
def read_catalog(directory):
    """Every card once, by template name: the checks below look cards up a hundred times."""
    return {card["template"]: card for card in catalog.cards(directory)}


def starters(directory):
    """Every template a company can pick: all but the built-ins the platform always creates."""
    return sorted(name for name, card in read_catalog(directory).items() if not card.get("required") and not card.get("bootstrap"))


class StarterBots(unittest.TestCase):
    """Every catalog template starts safe: draft-first sending, routines off until setup, read-only Tools."""

    directory = catalog.ROOT / "templates/catalog"

    def test_every_template_starts_draft_first_and_read_only(self):
        names = starters(self.directory)
        self.assertGreaterEqual(len(names), 90)
        for name in names:
            folder, where = self.directory / name, f"template {name}"
            card = read_catalog(self.directory)[name]
            self.assertIs(card["first_routine"]["draft_only"], True, where)
            self.assertNotIn("approval_required", card, f"{where}: sending uses outbound_send")
            manifest = yaml.safe_load((folder / "bot.yaml").read_text())
            self.assertIs(manifest["outbound_send"], False, where)
            routines = validate_schedules(manifest["routines"], lambda rel: (folder / rel).read_text())
            self.assertTrue(routines and all(r["enabled"] is False for r in routines), where)
            for access in manifest["tools"]:
                self.assertFalse({"send", "write", "modify", "delete"} & set(access.get("can", [])), where)


class BotOpsStopsOnARefusal(unittest.TestCase):
    """Building a bot for a company whose GitHub App may not create repositories: BotOps asks once for the exact fix
    and ends the run. No retry loop, no `sleep`, no repository copied onto a computer by hand; a Tico fault is reported."""

    folder = catalog.ROOT / "templates/catalog/botops"

    def text(self, name):
        return " ".join((self.folder / name).read_text().split())

    def test_the_instructions_stop_ask_once_and_report_tico_bugs(self):
        agent = self.text("AGENT.md")
        for rule in ("`retryable: false` means stop", "never `sleep` and call again", "ask once", "then end the run",
                     "Never hand-edit a computer", "remove or change a git remote", "hub support file",
                     "operation_id", "Blocked on a Tico bug, reported as"):
            self.assertIn(rule, agent)

    def test_build_checks_create_names_the_refusal_and_waits_without_copying(self):
        build = self.text("playbooks/build-me-a-bot.md")
        for rule in ("`hub bot create`'s exit status", "`github_permission_missing`", "ask the person once",
                     "waiting for a computer to get the repository is not a failure", "do not copy the repository"):
            self.assertIn(rule, build)

    def test_no_playbook_suggests_a_sleep_retry_or_a_hand_copy(self):
        for path in [self.folder / "AGENT.md", *sorted((self.folder / "playbooks").glob("*.md"))]:
            for line in path.read_text().splitlines():
                self.assertNotRegex(line, r"sleep \d|retry until|git clone \S*/projects|git remote (remove|rm)\b",
                                    f"{path.name}: {line}")
                if "sleep" in line.lower():
                    self.assertRegex(line.lower(), r"\b(never|no)\b", f"{path.name}: {line}")


if __name__ == "__main__":
    unittest.main()
