"""The growth channel packs (docs/pipelines-v3.md "Growth and Success"): each channel Owner works its tasks through Growth
Experiments and Growth Monitoring with the same stage playbooks, runs exactly two routines (Watch and Find ideas) on its
channel's cadence, and tags its tasks with the channel key the setup chart uses.

The stage playbooks are one text shipped in every pack, so a fix to one is a fix to all: a copy that drifts would teach
one Owner a different way out of a stage. A routine or requirement renamed in one place and not the other would leave a
bot running a playbook that names a stage line nobody checks."""

import re
from pathlib import Path

import pytest
import yaml

from backend import pipeline_presets_more as P
from backend import recruit_rank as R
from backend.company_file import chart_formula as F
from clients import catalog
from clients.routines import validate_schedules

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
GROUPS = CATALOG.parent / "groups.yaml"

# Each pack's channel key (backend/company_file/chart_formula.json) and its routines' crons: Watch on the channel's
# cadence (plan §6), Find ideas weekly.
DAILY, WEEKLY, FIND = "0 7 * * *", "0 7 * * 1", "0 8 * * 2"
PACKS = {
    "seo-owner": ("seo", {"watch": WEEKLY, "find-ideas": FIND}),
    "content-owner": ("content", {"watch": WEEKLY, "find-ideas": FIND}),
    "email-owner": ("email", {"watch": DAILY, "find-ideas": FIND}),
    "cro-owner": ("cro", {"watch": DAILY, "find-ideas": FIND}),
    "search-ads-owner": ("search-ads", {"watch": DAILY, "find-ideas": FIND}),
    "social-ads-owner": ("social-ads", {"watch": DAILY, "find-ideas": FIND}),
}
# One text in every pack: the stage playbooks and AGENTS.md's "Your pipelines" section.
SHARED = (
    "playbooks/watch.md",
    "playbooks/find-ideas.md",
    "playbooks/build-a-bet.md",
    "playbooks/run-and-measure.md",
    "playbooks/fix-a-slip.md",
)


def manifest(template):
    return yaml.safe_load((CATALOG / template / "bot.yaml").read_text())


def section(template, heading):
    text = (CATALOG / template / "AGENTS.md").read_text()
    found = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert found, f"{template}: no ## {heading}"
    return found.group(1)


@pytest.mark.parametrize("template", sorted(PACKS))
def test_a_pack_materializes_and_passes_the_preflight_checks(tmp_path, template):
    card = catalog.card(template)
    names = {"company_name": "Acme", "app_name": "Tico", "assistant_name": "Tico"}
    repo = catalog.materialize(template, template, tmp_path, names, {}, display_name=card["name"])
    assert catalog.check(repo, template) == []
    assert "{{" not in (repo / "knowledge" / "channel-playbook.md").read_text()


def test_setup_offers_the_six_packs_in_marketing_and_checks_none_by_itself():
    """Each pack is offered (`common`), never pre-checked: no Owner is hired without a person's pick. Marketing's
    question is how customers find the team, and an answer naming the channels lists all six."""
    built = R.build(yaml.safe_load(GROUPS.read_text()), catalog.cards())
    cards = {card["template"]: card for card in built["cards"]}
    assert all(cards[t]["department"] == "marketing" and cards[t]["suggest"] == "common" for t in PACKS)
    assert set(PACKS) <= set(catalog.card("growth-manager")["team_templates"])
    answer = "Search and our blog, a monthly newsletter, Google search ads and Meta ads; the signup page loses people"
    offered = R.rank(built, "marketing", answer)
    assert set(PACKS) <= {row["template_id"] for row in offered["bots"]}
    assert not set(PACKS) & set(offered["suggested_default"])


def test_what_the_team_says_it_does_not_do_is_no_reason_to_offer_a_pack():
    """Initech, Nov 11: "people finding our website on Google. No ads, no email list yet" put the Search Ads Owner
    first, "Matches “google ads”": a sentence break and a "no" were read past. Neither is a match now."""
    built = R.build(yaml.safe_load(GROUPS.read_text()), catalog.cards())
    answer = "Mostly word of mouth and people finding our website on Google. No ads, no email list yet."
    offered = R.rank(built, "marketing", answer)["bots"]
    assert not [row for row in offered if row["why"].startswith("Matches")], offered
    # Said plainly, the same words still match.
    said = R.rank(built, "marketing", "We run Google ads and an email list")["bots"]
    assert said[1] == {"template_id": "search-ads-owner", "why": "Matches “google ads”"}
