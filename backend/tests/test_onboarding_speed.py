"""Saving the first-run draft stays fast with the real catalog and a company-sized org (23 bots in four departments)."""
import shutil
import time
from pathlib import Path

import pytest
import yaml

from backend.tests.test_onboarding import ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, environment, signed_in  # noqa: F401

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
DEPARTMENTS = ("sales", "support", "finance", "operations")


def picks(count=23):
    """The first `count` role templates of four departments, as the org builder would select them."""
    chosen = {}
    for folder in sorted(CATALOG.iterdir()):
        card = yaml.safe_load((folder / "card.yaml").read_text())
        if card.get("group") in DEPARTMENTS and not card.get("bootstrap") and card.get("kind") != "helper":
            chosen[card["slug"]] = {"template": card["template"], "display_name": card["name"], "instructions": ""}
    return dict(list(chosen.items())[:count])


@pytest.mark.slow
def test_saving_a_23_bot_draft_against_the_real_catalog_is_fast(environment):
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")])
    shutil.copytree(CATALOG, api.app.state.store.settings.catalog_dir, dirs_exist_ok=True)
    selected = picks()
    assert len(selected) == 23
    body = {"names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"},
            "answers": {"what_we_do": "We sell software.", "customers": "businesses", "departments": list(DEPARTMENTS),
                        "briefings": {name: "A few sentences about it." for name in DEPARTMENTS}},
            "selected": selected}
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        response = api.put("/api/v2/onboarding", json=body, headers=signed_in())
        timings.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
    assert len(response.json()["selected"]) == 23
    # It took 4.6 s here (20 s on a small server): every selected bot parsed all 94 cards again. The first save also
    # parses the catalog once; the bound is generous, the ones after it are a few milliseconds.
    assert max(timings) < 1.5, timings
