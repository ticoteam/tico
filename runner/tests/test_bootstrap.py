"""First run: the runner sets the two bootstrap bots up from the catalog itself.

The assistant is who the person talks to and BotOps sets every other chosen bot up, so neither
can wait for a bot that does not exist yet. Everybody else stays missing until BotOps has run
`hub bot create` in a turn, which is what `HUB_WORKSPACE` is in a turn's environment for.

No CLI and no network: the cloud is a fake client, and the catalog is a temporary directory.
"""
import tempfile
import unittest
from pathlib import Path

from clients.tico import APIError
from runner.service import Runner

CONFIG = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada",
          "assistant_bot": "coo"}
ONBOARDING = {"names": CONFIG,
              "answers": {"what_we_do": "we clean holiday homes", "customers": "owners",
                          "team_size": "nine people", "work_arrives": ["email"],
                          "repetitive_work": "chasing photos", "never_without_person": ["refunds"]},
              "selected": {"seo": {"template": "specialist", "display_name": "Sam",
                                   "instructions": "# Sam\n\n## Owns\n- the blog\n"}},
              "completed": True}
RUNTIMES = {"codex": {"installed": True, "authenticated": "ready", "detail": "", "models": [],
                      "version": "codex 1.0", "controls": []}}
AGENT = """# {{bot_name}}

You are {{bot_name}} at {{company_name}}; {{app_name}} is where the work lives.

## Owns
- being useful
"""


def card(template, slug, bootstrap):
    return (f"template: {template}\nslug: {slug}\nname: The {slug.title()}\n"
            f"bootstrap: {'true' if bootstrap else 'false'}\nrequired: {'true' if bootstrap else 'false'}\n"
            f"summary: A bot.\nowns: []\nnever: []\nruntime: codex\n")


def catalog(root):
    """A catalog with one bootstrap template and one the runner must leave to BotOps."""
    for template, slug, bootstrap in (("assistant", "coo", True), ("botops", "botops", True),
                                      ("specialist", "seo", False)):
        directory = Path(root) / template
        directory.mkdir(parents=True)
        (directory / "card.yaml").write_text(card(template, slug, bootstrap))
        (directory / "AGENT.md").write_text(AGENT)
        (directory / "employee.yaml").write_text('name: CHANGE-ME\ndisplay_name: "Change Me"\nschedules: []\n')
        (directory / "state.md").write_text("# State\n")
    return Path(root)


class FakeClient:
    """The cloud: this installation's names and its onboarding record, and nothing else."""

    def __init__(self, onboarding=ONBOARDING):
        self.record, self.seen = onboarding, []

    def get(self, path, **query):
        self.seen.append(path)
        if path == "config":
            return dict(CONFIG)
        if path == "onboarding":
            if self.record is None:
                raise APIError("not_found", "This server has no onboarding", 404, False)
            return dict(self.record)
        raise APIError("not_found", path, 404, False)


def entry(bot, template=None, runner_id="r1", **config):
    row = {"bot": bot, "runner_id": runner_id, "state": "active",
           "config": {"runtime": "codex", "model": "gpt-6-sol", **config}}
    if template:
        row["config"]["template"] = template
    return row


class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / "Companies/Acme"
        self.projects.mkdir(parents=True)
        self.catalog = catalog(self.root / "catalog")
        self.client = FakeClient()
        self.runner = self.service()

    def service(self, client=None):
        runner = Runner.__new__(Runner)
        runner.config = {"url": "https://acme.test", "token": "t", "runner_id": "r1",
                         "projects_dir": str(self.projects)}
        runner.client = client or self.client
        runner._names = None
        return runner

    def rows(self, *entries):
        import os
        os.environ["TICO_CATALOG_DIR"] = str(self.catalog)
        self.addCleanup(os.environ.pop, "TICO_CATALOG_DIR", None)
        return {row["bot"]: row for row in self.runner.preflight(list(entries), RUNTIMES)}

    def test_only_the_machine_a_bot_is_assigned_to_writes_its_repository(self):
        rows = self.rows(entry("coo", "assistant", runner_id="another-mac"), entry("botops", "botops", runner_id=None))
        self.assertEqual(rows["coo"]["problems"], ["Missing bot repository or AGENT.md"])
        self.assertFalse((self.projects / "bot-coo").exists())
        self.assertFalse((self.projects / "bot-botops").exists())
        # A starter first run created says `materialize`: its computer sets it up the moment it is placed.
        # A bot BotOps builds from the same template stays missing until BotOps has.
        rows = self.rows(entry("seo", "specialist", materialize=True), entry("helper", "specialist"))
        self.assertTrue((self.projects / "bot-seo" / "AGENT.md").is_file())
        self.assertEqual(rows["helper"]["problems"], ["Missing bot repository or AGENT.md"])
        self.assertFalse((self.projects / "bot-helper").exists())

if __name__ == "__main__":
    unittest.main()


def test_readiness_candidates_only_include_assigned_bots():
    assigned = [{"bot": "ops", "runner_id": "this-computer", "config": {"runtime": "fake"}}]
    eligible = [{"bot": "support", "config": {"runtime": "fake"}},
                {"bot": "archived", "state": "archived", "config": {}},
                {"bot": "ops", "config": {"runtime": "other"}}]
    assert Runner.readiness_candidates(assigned, eligible) == assigned
    assert Runner.readiness_candidates([], eligible) == []


def test_an_archived_assignment_is_not_set_up_or_checked():
    live = {"bot": "ops", "runner_id": "this-computer", "state": "active", "config": {"runtime": "fake"}}
    gone = {"bot": "qa-old", "runner_id": "this-computer", "state": "archived", "config": {"runtime": "fake"}}
    assert Runner.readiness_candidates([gone, live]) == [live]


def test_the_catalog_is_parsed_again_only_when_a_card_changes(tmp_path, monkeypatch):
    import os
    from clients import catalog
    card = tmp_path / "helper" / catalog.CARD
    card.parent.mkdir()
    card.write_text("title: Helper\n")
    parses = []
    real = catalog.yaml.safe_load
    monkeypatch.setattr(catalog.yaml, "safe_load", lambda text: parses.append(1) or real(text))
    assert catalog.cards(tmp_path)[0]["title"] == "Helper"
    catalog.cards(tmp_path)[0]["title"] = "changed by a caller"
    assert catalog.cards(tmp_path)[0]["title"] == "Helper" and len(parses) == 1
    card.write_text("title: Assistant helper\n")
    os.utime(card, ns=(card.stat().st_atime_ns, card.stat().st_mtime_ns + 10**9))
    assert catalog.cards(tmp_path)[0]["title"] == "Assistant helper" and len(parses) == 2
