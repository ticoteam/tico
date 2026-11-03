"""First run: the runner sets the two bootstrap bots up from the catalog (`python -m runner_support catalog`,
runner_support/catalog_job.py).

No CLI and no network: the cloud is a fake client, and the catalog is a temporary directory.
"""
from pathlib import Path

from clients.tico import APIError

CONFIG = {"company_name": "Acme Ltd", "app_name": "Acme OS", "assistant_name": "Ada",
          "assistant_bot": "coo"}
ONBOARDING = {"names": CONFIG,
              "answers": {"what_we_do": "we clean holiday homes", "customers": "owners",
                          "team_size": "nine people", "work_arrives": ["email"],
                          "repetitive_work": "chasing photos", "never_without_person": ["refunds"]},
              "selected": {"seo": {"template": "specialist", "display_name": "Sam",
                                   "instructions": "# Sam\n\n## Owns\n- the blog\n"}},
              "completed": True}
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


def test_the_catalog_job_sets_a_bot_up_and_answers_on_stdout(tmp_path, monkeypatch):
    import io
    import json
    from runner_support import catalog_job
    monkeypatch.setenv("TICO_CATALOG_DIR", str(catalog(tmp_path / "catalog")))
    projects = tmp_path / "Companies/Acme"
    projects.mkdir(parents=True)
    path = projects / "bot-coo"
    request = {"bots": [{"bot": "coo", "entry": entry("coo", "assistant"), "path": str(path), "action": "bootstrap"},
                        {"bot": "botops", "entry": entry("botops", "botops"), "path": str(projects / "bot-botops"),
                         "action": "refresh"}]}
    out = io.StringIO()
    catalog_job.main({"projects_dir": str(projects)}, FakeClient(), io.StringIO(json.dumps(request)), out)
    answer = json.loads(out.getvalue())
    assert answer == {"coo": {"materialized": "materialized from catalog: assistant", "problem": ""},
                      "botops": {"changed": []}}
    assert "at Acme Ltd" in (path / "AGENT.md").read_text()


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
