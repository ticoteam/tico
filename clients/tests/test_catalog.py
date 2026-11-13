"""The bot catalog (clients/catalog.py): a template becomes one bot's repository.

Nothing here talks to a server. The catalog is a temporary directory holding one card, which is
what `TICO_CATALOG_DIR` is for, so these tests say nothing about which bots the product ships.
"""

import functools
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml
import pytest

from clients import catalog
from clients import manifest, repo_safety
from clients.routines import validate_schedules

CARD = """template: assistant
slug: assistant
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

NAMES = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada", "assistant_bot": "assistant"}
ANSWERS = {
    "what_we_do": "we clean holiday homes",
    "customers": "owners of holiday homes",
    "team_size": "nine people",
    "work_arrives": ["email", "Slack"],
    "repetitive_work": "chasing cleaners for photos",
    "never_without_person": ["refunds", "signing a contract"],
}


def fixture(root, template="assistant", card=CARD, agent=AGENT):
    """One catalog template on disk: a card, instructions, a manifest and the usual folders."""
    directory = Path(root) / template
    (directory / "playbooks").mkdir(parents=True)
    (directory / "card.yaml").write_text(card)
    (directory / "AGENTS.md").write_text(agent)
    (directory / "employee.yaml").write_text(
        MANIFEST
    )  # an older template: the manifest under its old name still materializes
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

    def make(self, slug="assistant", **over):
        return catalog.materialize("assistant", slug, self.workspace, NAMES, ANSWERS, directory=self.catalog, **over)

    def test_an_existing_repository_is_never_overwritten(self):
        path = self.make()
        knowledge = (path / "knowledge/company.md").read_text()
        self.assertIn("What the Team does", knowledge)
        self.assertNotIn("refunds", knowledge)
        self.assertNotIn("signing a contract", knowledge)
        (path / "notes.md").write_text("A turn happened here.\n")
        with self.assertRaises(ValueError) as caught:
            self.make()
        self.assertIn("already exists", str(caught.exception))
        self.assertIn("A turn happened here.", (path / "notes.md").read_text())

    def test_a_workspace_ancestor_cannot_point_into_the_install(self):
        install = Path(self.tmp.name) / "install"
        install.mkdir()
        self.workspace.parent.mkdir(parents=True)
        self.workspace.symlink_to(install, target_is_directory=True)
        with patch.object(repo_safety, "ROOT", install):
            with self.assertRaisesRegex(ValueError, "inside the Tico install"):
                self.make()
            other_case = install.with_name("INSTALL")
            if other_case.exists():
                with self.assertRaisesRegex(ValueError, "inside the Tico install"):
                    repo_safety.outside_installation(other_case / "new")
        self.assertEqual(list(install.iterdir()), [])

    def test_a_bot_given_another_bots_old_slug_is_built_in_the_folder_the_server_recorded(self):
        # A renamed bot still works in bot-assistant; the new bot named assistant was given bot-assistant-<part of its id>.
        first = self.make()
        (first / "notes.md").write_text("The renamed bot's memory.\n")
        second = self.make(folder="bot-coo-0a1b2c3d")
        # macOS gives tmp as /var, a link to /private/var: the bot folder comes back resolved.
        self.assertEqual(second, (self.workspace / "bot-coo-0a1b2c3d").resolve())
        self.assertTrue((second / "AGENTS.md").is_file())
        self.assertIn("The renamed bot's memory.", (first / "notes.md").read_text())
        # A recorded value that is not one plain folder name is never a path.
        with self.assertRaises(ValueError):
            self.make(folder="../assistant")


NOTHING = {"added": [], "updated": [], "removed": [], "kept": []}
SEAT_CARD = CARD.replace("template: assistant", "template: sales").replace("bootstrap: true", "bootstrap: false")
SETTINGS = '{"permissions": {"allow": ["Read"], "deny": ["Bash(gh repo delete *)"]}}\n'


class Refresh(unittest.TestCase):
    """A built-in bot follows the release: the product's instructions and playbooks are brought up when the template
    changed, and whatever the bot improved and wrote itself stays."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / "catalog"
        self.template = fixture(self.catalog)
        (self.template / "playbooks" / "fleet.md").write_text("Check the fleet for {{company_name}}.\n")
        self.path = catalog.materialize(
            "assistant", "assistant", Path(self.tmp.name) / "ws", NAMES, ANSWERS, directory=self.catalog
        )

    def refresh(self):
        return catalog.refresh("assistant", self.path, NAMES, directory=self.catalog)

    def test_a_bot_still_on_agent_md_is_refreshed_once_the_runner_moved_it_to_agents_md(self):
        import json

        # A bot made before AGENTS.md: its instructions and its stamp name AGENT.md.
        (self.path / "AGENTS.md").rename(self.path / "AGENT.md")
        stamp = json.loads((self.path / catalog.STAMP).read_text())
        stamp["files"]["AGENT.md"] = stamp["files"].pop("AGENTS.md")
        (self.path / catalog.STAMP).write_text(json.dumps(stamp))
        git(self.path, "add", "-A")
        git(self.path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Before AGENTS.md")
        (self.template / "AGENTS.md").write_text("Updated instructions.\n")
        self.assertEqual(self.refresh(), NOTHING)  # it waits for the runner's move
        self.assertFalse((self.path / "AGENTS.md").exists())
        (self.path / "AGENT.md").rename(self.path / "AGENTS.md")
        self.assertEqual(self.refresh(), {**NOTHING, "updated": ["AGENTS.md"]})
        self.assertEqual((self.path / "AGENTS.md").read_text(), "Updated instructions.\n")

    @pytest.mark.slow
    def test_a_bot_root_or_named_folder_cannot_resolve_into_the_install(self):
        alias = self.path.parent / "bot-alias"
        alias.symlink_to(self.path, target_is_directory=True)
        with patch.object(repo_safety, "ROOT", self.path):
            for path in (self.path, alias):
                with self.assertRaisesRegex(ValueError, "inside the Tico install"):
                    catalog.refresh("assistant", path, NAMES, directory=self.catalog)
            with self.assertRaisesRegex(ValueError, "inside the Tico install"):
                manifest.folder_dir(alias.parent, alias.name)

    @pytest.mark.slow
    def test_a_refresh_refuses_an_outside_file_directory_or_stamp_link_before_any_write(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "fleet.md").write_text("Keep me.\n")
        (self.template / "AGENTS.md").write_text("Updated instructions.\n")
        before = (self.path / "AGENTS.md").read_text()
        for rel, destination in (
            ("playbooks/fleet.md", outside / "fleet.md"),
            (catalog.STAMP, outside / "new-stamp"),
            ("playbooks", outside),
        ):
            path = self.path / rel
            backup = path.with_name(path.name + ".saved")
            path.rename(backup)
            path.symlink_to(destination)
            with self.assertRaisesRegex(ValueError, "outside the bot repo"):
                self.refresh()
            self.assertEqual((self.path / "AGENTS.md").read_text(), before)
            self.assertEqual((outside / "fleet.md").read_text(), "Keep me.\n")
            self.assertFalse((outside / "new-stamp").exists())
            path.unlink()
            backup.rename(path)

    @pytest.mark.slow
    def test_refresh_replaces_an_inside_file_link_without_writing_its_target(self):
        kept = self.path / "knowledge/kept.md"
        kept.write_text("Keep my notes.\n")
        path = self.path / "playbooks/fleet.md"
        path.unlink()
        path.symlink_to("../knowledge/kept.md")
        (self.template / "playbooks/fleet.md").write_text("New fleet rules.\n")
        self.assertEqual(self.refresh()["updated"], ["playbooks/fleet.md"])
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.read_text(), "New fleet rules.\n")
        self.assertEqual(kept.read_text(), "Keep my notes.\n")

    def test_a_changed_template_file_wins_and_the_rest_stays_the_bots(self):
        (self.path / "playbooks" / "fleet.md").write_text("Improved by the bot.\n")
        (self.path / "playbooks" / "mine.md").write_text("A playbook the bot wrote.\n")
        (self.path / "notes.md").write_text("Mid-task.\n")
        (self.template / "playbooks" / "fleet.md").write_text("Check the fleet, then fix it, for {{company_name}}.\n")
        self.assertEqual(self.refresh(), {**NOTHING, "updated": ["playbooks/fleet.md"]})
        self.assertEqual(
            (self.path / "playbooks" / "fleet.md").read_text(), "Check the fleet, then fix it, for Acme Ltd.\n"
        )
        self.assertEqual((self.path / "playbooks" / "mine.md").read_text(), "A playbook the bot wrote.\n")
        self.assertIn("Mid-task.", (self.path / "notes.md").read_text())
        self.assertIn("Refresh 1 file from the assistant template", git(self.path, "log", "-1", "--format=%s"))
        self.assertEqual(
            git(self.path, "show", "HEAD~1:playbooks/fleet.md"), "Improved by the bot."
        )  # kept in the history
        self.assertEqual(self.refresh(), NOTHING)  # once


class SeatRefresh(unittest.TestCase):
    """Every bot made from a template follows it after an update, not only the built-in ones: a file it was given and
    never changed takes the newer template's, a file it or a person changed is kept and reported."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = Path(self.tmp.name) / "catalog"
        self.template = fixture(self.catalog, "sales", SEAT_CARD)
        (self.template / "playbooks" / "leads.md").write_text("Answer leads for {{company_name}}.\n")
        (self.template / "playbooks" / "old.md").write_text("A playbook the next release drops.\n")
        (self.template / ".claude").mkdir()
        (self.template / ".claude" / "settings.json").write_text(SETTINGS)
        (self.template / "knowledge").mkdir()
        (self.template / "knowledge" / "README.md").write_text("What this bot knows.\n")
        self.instructions = self.reviewed(AGENT)
        self.path = self.make("sam")

    def reviewed(self, agent):
        """What a person reviews when a seat is filled: the template filled in, then the seat's job description."""
        values = {**NAMES, "bot_name": "Sam"}
        return catalog.fill(agent, values) + "\n## Job description\nAnswer Acme's inbound leads.\n"

    def make(self, slug):
        return catalog.materialize(
            "sales",
            slug,
            Path(self.tmp.name) / "ws",
            NAMES,
            ANSWERS,
            display_name="Sam",
            instructions=self.instructions,
            directory=self.catalog,
        )

    def refresh(self, version="0.6.0-rc.5"):
        return catalog.refresh(
            "sales", self.path, NAMES, "Sam", self.catalog, instructions=self.instructions, version=version
        )

    def test_a_seat_bot_takes_a_changed_playbook_and_settings_file_and_keeps_its_job_description(self):
        (self.template / "playbooks" / "leads.md").write_text("Answer leads for {{company_name}} within an hour.\n")
        newer = SETTINGS.replace('"Read"', '"Read", "Bash(gh pr checks *)"')
        (self.template / ".claude" / "settings.json").write_text(newer)
        (self.template / "AGENTS.md").write_text(AGENT + "\n## Done means\nThe lead has an answer.\n")
        self.assertEqual(
            self.refresh(), {**NOTHING, "updated": [".claude/settings.json", "AGENTS.md", "playbooks/leads.md"]}
        )
        self.assertEqual(
            (self.path / "playbooks" / "leads.md").read_text(), "Answer leads for Acme Ltd within an hour.\n"
        )
        self.assertEqual((self.path / ".claude" / "settings.json").read_text(), newer)
        agent = (self.path / "AGENTS.md").read_text()
        self.assertIn("## Done means", agent)
        self.assertTrue(agent.endswith("## Job description\nAnswer Acme's inbound leads.\n"))
        self.assertIn("Acme Ltd", (self.path / "knowledge" / "company.md").read_text())
        stamp = yaml.safe_load((self.path / catalog.STAMP).read_text())
        self.assertEqual(stamp["version"], "0.6.0-rc.5")
        self.assertIn("Refresh 3 files from the sales template", git(self.path, "log", "-1", "--format=%s"))
        self.assertEqual(self.refresh(), NOTHING)  # a second run changes nothing
        self.assertIn("Refresh 3 files", git(self.path, "log", "-1", "--format=%s"))

    def test_a_file_the_bot_changed_is_kept_reported_once_and_new_and_dropped_files_follow_the_template(self):
        (self.path / "playbooks" / "leads.md").write_text("My own way with leads.\n")
        (self.path / "knowledge" / "notes.md").write_text("What I learned.\n")
        (self.template / "playbooks" / "leads.md").write_text("Answer leads within an hour.\n")
        (self.template / "playbooks" / "follow-up.md").write_text("Follow up after a day.\n")
        (self.template / "playbooks" / "old.md").unlink()
        self.assertEqual(
            self.refresh(),
            {
                "added": ["playbooks/follow-up.md"],
                "updated": [],
                "removed": ["playbooks/old.md"],
                "kept": ["playbooks/leads.md"],
            },
        )
        self.assertEqual((self.path / "playbooks" / "leads.md").read_text(), "My own way with leads.\n")
        self.assertFalse((self.path / "playbooks" / "old.md").exists())
        self.assertEqual((self.path / "knowledge" / "notes.md").read_text(), "What I learned.\n")
        self.assertEqual(self.refresh(), NOTHING)  # reported once
        # The template changes it again: offered again, and kept again while it is the bot's own.
        (self.template / "playbooks" / "leads.md").write_text("Answer leads within ten minutes.\n")
        self.assertEqual(self.refresh(), {**NOTHING, "kept": ["playbooks/leads.md"]})

    def test_a_dropped_file_the_bot_changed_stays(self):
        (self.path / "playbooks" / "old.md").write_text("Still mine.\n")
        (self.template / "playbooks" / "old.md").unlink()
        self.assertEqual(self.refresh(), {**NOTHING, "kept": ["playbooks/old.md"]})
        self.assertEqual((self.path / "playbooks" / "old.md").read_text(), "Still mine.\n")
        self.assertEqual(self.refresh(), NOTHING)

    def test_a_bot_from_before_the_stamp_counts_its_first_commit_as_what_it_was_given(self):
        git(self.path, "rm", "-q", catalog.STAMP)
        git(self.path, "-c", "user.name=Sam", "-c", "user.email=sam@acme.example", "commit", "-qm", "Before the stamp")
        (self.path / ".claude" / "settings.json").write_text(SETTINGS.replace('"Read"', '"Read", "Write"'))
        (self.template / "playbooks" / "leads.md").write_text("Answer leads within an hour.\n")
        (self.template / ".claude" / "settings.json").write_text(SETTINGS.replace("Read", "Edit"))
        self.assertEqual(
            self.refresh(), {**NOTHING, "updated": ["playbooks/leads.md"], "kept": [".claude/settings.json"]}
        )
        self.assertIn('"Write"', (self.path / ".claude" / "settings.json").read_text())
        self.assertTrue((self.path / catalog.STAMP).is_file())
        self.assertEqual(self.refresh(), NOTHING)

    def test_files_from_a_newer_release_stay_until_this_computer_has_it(self):
        (self.template / "playbooks" / "leads.md").write_text("Answer leads within an hour.\n")
        self.assertEqual(self.refresh("0.6.1")["updated"], ["playbooks/leads.md"])
        (self.template / "playbooks" / "leads.md").write_text("Answer leads for {{company_name}}.\n")
        self.assertEqual(self.refresh("0.6.0"), NOTHING)
        self.assertTrue(catalog.newer("0.6.0", "0.6.0-rc.5") and not catalog.newer("dev", "0.6.0"))

    def test_instructions_a_person_edited_before_the_bot_was_made_count_as_changed(self):
        self.instructions = self.instructions.replace("## Tools and scope", "## Tone\nShort.\n\n## Tools and scope")
        self.path = self.make("sue")
        (self.template / "AGENTS.md").write_text(AGENT + "\n## Done means\nThe lead has an answer.\n")
        self.assertEqual(self.refresh(), {**NOTHING, "kept": ["AGENTS.md"]})
        self.assertIn("## Tone", (self.path / "AGENTS.md").read_text())


@functools.lru_cache(maxsize=None)
def read_catalog(directory):
    """Every card once, by template name: the checks below look cards up a hundred times."""
    return {card["template"]: card for card in catalog.cards(directory)}


def starters(directory):
    """Every template a company can pick: all but the built-ins the platform always creates."""
    return sorted(
        name for name, card in read_catalog(directory).items() if not card.get("required") and not card.get("bootstrap")
    )


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
            self.assertNotIn("approval_required", card, f"{where}: sending follows the sending policy")
            manifest = yaml.safe_load((folder / "bot.yaml").read_text())
            self.assertNotIn("outbound_send", manifest, where)  # sending is the server's policy, never bot.yaml
            routines = validate_schedules(manifest.get("routines") or [], lambda rel: (folder / rel).read_text())
            self.assertTrue(
                all(r["enabled"] is False for r in routines), where
            )  # a bot with no routines has none to arm
            for access in manifest["tools"]:
                self.assertFalse({"send", "write", "modify", "delete"} & set(access.get("can", [])), where)


class BotOpsStopsOnARefusal(unittest.TestCase):
    """Building a bot for a company whose GitHub App may not create repositories: BotOps asks once for the exact fix
    and ends the run. No retry loop, no `sleep`, no repository copied onto a computer by hand; a Tico fault is reported."""

    folder = catalog.ROOT / "templates/catalog/botops"

    def text(self, name):
        return " ".join((self.folder / name).read_text().split())


# A Manager has reports and runs its team's pipelines; an Owner owns an area and does the work. These templates are
# named "Manager" and lead no team: a follow-up pull request renames each and empties this list. Add none.
MANAGERS_THAT_LEAD_NO_TEAM = {
    "account-manager",
    "community-manager",
    "compliance-manager",
    "contracts-manager",
    "employee-experience-manager",
    "equity-manager",
    "escalations-manager",
    "event-marketing-manager",
    "fundraising-manager",
    "general-manager",
    "inbox-manager",
    "investor-relations-manager",
    "legal-operations-manager",
    "marketing-operations-manager",
    "office-manager",
    "pr-manager",
    "privacy-manager",
    "procurement-manager",
    "product-operations-manager",
    "reputation-manager",
    "sales-enablement-manager",
    "sales-operations-manager",
    "social-media-manager",
    "vendor-manager",
}


if __name__ == "__main__":
    unittest.main()
