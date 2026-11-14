"""The integration pages, their query catalogs and the shared learnings.

The pages in `integrations/` are data the release ships and every bot reads, so every one of
them is validated here against the frontmatter schema and the section order; the catalogs must
parse with unique ids. The API serves them to anyone signed in, takes a learning from anyone,
and lets only the owner delete one.
"""

import pytest

from backend import integrations as I
from backend.config import ROOT
from backend.tests.helpers.api import assign, get, runner  # noqa: F401  (fixtures)
from backend.tests.helpers.bot_tools import configure, report

# Only outside services Tico has built-in support for ship; a company adds its own pages in its
# config (<registry>/integrations or TICO_INTEGRATIONS_DIR), layered over these.
SHIPPED = {"github", "slack", "mail", "aside", "website", "close-crm", "postgres", "mysql", "mongodb", "sqlite"}


def test_the_release_ships_only_built_in_services_and_every_page_parses():
    pages, _ = I.load(ROOT / "integrations")
    assert set(pages) == SHIPPED
    assert pages["github"]["writes"] == "allowed"  # nothing gates GitHub writes, so the page must not say approval
    with pytest.raises(I.Problem, match='No tool named missing'):
        I.Catalog(ROOT / "integrations").resolve('missing')


def test_a_company_page_adds_a_service_and_replaces_a_shipped_one(tmp_path):
    github = (ROOT / "integrations" / "github.md").read_text()
    (tmp_path / "github.md").write_text(github.replace("title: GitHub", "title: GitHub (our org)"))
    (tmp_path / "stripe.md").write_text(github.replace("service: github", "service: stripe"))
    pages, _ = I.load(ROOT / "integrations", tmp_path)
    assert pages["github"]["title"] == "GitHub (our org)"
    assert set(pages) == SHIPPED | {"stripe"}


def test_the_tool_list_files_a_bots_alias_under_its_page_with_each_bots_write_level_and_problem(api):
    """Tools reads who has each tool from this list: a bot naming `close` is filed under Close CRM (the page
    keeps its title), its entry's verbs narrow the page's write level, and a missing credential is the tool's issue,
    named by its variable and never by a value."""
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    tools = [
        {
            "service": "close",
            "identity": "Acme's Close account",
            "can": ["read"],
            "env": "CLOSE_API_KEY",
            "credential": "missing",
        },
        {
            "service": "slack",
            "identity": "Acme workspace",
            "can": ["read", "post"],
            "env": "SLACK_TOKEN",
            "credential": "present",
        },
        {"service": "aside", "can": ["read"], "credential": "not-declared"},
    ]
    assert report(api, machine, "ops", tools).status_code == 200
    rows = {row["service"]: row for row in get(api, "tools")["integrations"]}
    assert "close" not in rows
    close = rows["close-crm"]
    assert close["title"] == "Close CRM" and close["bots"] == ["ops"] and close["status"] == "problem"
    [entry] = close["connections"]
    assert (entry["write"], entry["env"], entry["problem"]) == (
        "never",
        "CLOSE_API_KEY",
        "Credential missing on Test Mac",
    )
    assert [(c["bot"], c["write"]) for c in rows["slack"]["connections"]] == [("ops", "allowed")]
    assert "problem" not in rows["slack"]["connections"][0]
    # Aside allows writes, but this bot's entry may only read.
    assert [c["write"] for c in rows["aside"]["connections"]] == ["never"]
    # The model a bot runs on is a tool it uses, not one it writes through.
    model = next(
        row for row in rows.values() if any(c.get("identity", "").endswith("gpt-6-luna") for c in row["connections"])
    )
    assert model["writes"] == "never" and {c["write"] for c in model["connections"]} == {"never"}


def test_write_level_is_the_pages_narrowed_by_the_bots_verbs():
    assert I.write_level(["read", "list"], "allowed") == "never"
    assert I.write_level(["read", "refund"], "approval") == "approval"
    assert I.write_level(["read", "refund"], "never") == "never"
    assert I.write_level([], "allowed") == "never"
