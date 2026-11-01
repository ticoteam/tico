"""Every core template, end to end: a company made from it on a temporary database. Its repository is written and passes
the preflight checks a fresh repository can answer for itself, its bot is created through the API from that template,
and the first task the bot is given queues a job for it. The core set is the templates the onboarding suggests by
default, plus BotOps, which every company gets."""

import pytest

from clients import catalog
from backend.tests.test_api import api, get, post  # noqa: F401

CORE = sorted({row["template"] for row in catalog.cards() if row.get("suggest") == "default"} | {"botops"})
NAMES = {"company_name": "Acme", "app_name": "Tico", "assistant_name": "Tico"}


def jobs_for(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]


def test_the_core_set_is_the_default_suggestions_and_botops():
    assert "botops" in CORE
    assert len(CORE) >= 19, f"only {len(CORE)} core templates: a card was lost or stopped suggesting by default"


@pytest.mark.slow
@pytest.mark.parametrize("template", CORE)
def test_a_company_from_the_template_has_its_bot_and_a_first_task_queues_a_job(api, tmp_path, template):
    slug = template
    card = next(row for row in catalog.cards() if row["template"] == template)
    repo = catalog.materialize(template, slug, tmp_path, NAMES, {}, display_name=card.get("name") or template)
    assert catalog.committed(repo), f"{repo} was not committed"
    assert catalog.check(repo, slug) == [], f"{template} does not pass the preflight checks"

    post(api, "bots", {"slug": slug, "display_name": card.get("name") or template, "template": template,
                       "status": "active"})
    assert get(api, f"bots/{slug}")["slug"] == slug
    # A new bot is still onboarding and takes no task until a person above it on the org chart says it is ready.
    post(api, f"bots/{slug}/onboarded", {})

    before = jobs_for(api, slug)
    post(api, "tasks", {"owner": slug, "title": f"First task for {template}",
                        "body": "Say what you can do for the company this week, in two sentences."})
    assert jobs_for(api, slug) == before + 1, "the first task did not queue a job for the bot"
