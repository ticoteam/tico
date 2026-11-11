"""A template's slug is its card's name in kebab-case, and its folder is its slug (docs/onboarding.md), so a bot's slug,
its repository `bot-<slug>` and the name a person reads never drift apart. The exceptions are named here."""

import re
from pathlib import Path

import pytest
import yaml

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"

# Slugs whose card name is not their kebab-case: the Assistant's name is the one the person picks, and the two segment
# owners are named for the segment their bot runs ("Marketing Owner" of a segment), not for the template.
EXCEPTIONS = {
    "assistant": "{{assistant_name}}",
    "segment-marketing-owner": "Marketing Owner",
    "segment-product-owner": "Product Owner",
}


def kebab(name):
    """ "FP&A Analyst" -> "fpa-analyst": an ampersand joins its letters, any other run of non-letters is one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", "")).strip("-")


def cards():
    return sorted(path.parent for path in CATALOG.glob("*/card.yaml"))


@pytest.mark.parametrize("folder", cards(), ids=lambda path: path.name)
def test_a_cards_slug_is_its_name_in_kebab_case_and_its_folder(folder):
    card = yaml.safe_load((folder / "card.yaml").read_text())
    assert card["slug"] == folder.name, f"{folder.name}/card.yaml: slug {card['slug']!r} is not its folder"
    assert card["template"] == folder.name, f"{folder.name}/card.yaml: template {card['template']!r} is not its folder"
    if folder.name in EXCEPTIONS:
        assert card["name"] == EXCEPTIONS[folder.name], (
            f"{folder.name}: the exception's name changed; update EXCEPTIONS"
        )
    else:
        assert kebab(card["name"]) == folder.name, f"{folder.name}: name {card['name']!r} is not the slug in kebab-case"
    bot = folder / "bot.yaml"
    if bot.is_file():
        assert yaml.safe_load(bot.read_text())["name"] == folder.name, f"{folder.name}/bot.yaml: name is not the slug"


def test_every_exception_is_a_template():
    assert set(EXCEPTIONS) <= {folder.name for folder in cards()}


def test_kebab_case():
    assert kebab("FP&A Analyst") == "fpa-analyst"
    assert kebab("CRO Owner") == "cro-owner"
    assert kebab("Returns and Refunds Specialist") == "returns-and-refunds-specialist"
