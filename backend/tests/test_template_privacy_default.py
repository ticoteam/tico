"""A bot made from a template takes that template's card's `private_tasks_default` (templates/catalog/*/card.yaml), so a
hired Legal Owner keeps its legal tasks private as its card promises, the same as a General Counsel does. A setting
the person gives on the create request still wins (backend/settings_admin.py create_bot)."""

import pytest

from backend import bot_settings as BS
from backend.store import H
from backend.tests.test_api import api, headers  # noqa: F401  (fixture)


def make(api, slug, **fields):
    body = {"slug": slug, "display_name": slug.title(), "description": "Legal work", "status": "active", **fields}
    r = api.post("/api/v2/bots", json=body, headers=headers())
    assert r.status_code == 200, r.text
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


@pytest.mark.parametrize("template", ["legal-owner", "general-counsel"])
def test_a_bot_from_a_card_that_declares_private_tasks_is_private_by_default(api, template):
    bot_id = make(api, "legal-desk", template=template)
    with api.app.state.store.read() as c:
        assert BS.read(c, bot_id)["private_tasks_default"] is True
        assert H.private_tasks_default(c, "bot:" + bot_id) is True


def test_a_bot_from_a_card_without_a_privacy_default_is_not_private(api):
    bot_id = make(api, "sales-desk", template="sales")
    with api.app.state.store.read() as c:
        assert BS.read(c, bot_id)["private_tasks_default"] is False


def test_a_privacy_setting_on_the_create_request_wins_over_the_card(api):
    bot_id = make(api, "legal-open", template="legal-owner", private_tasks_default=False)
    with api.app.state.store.read() as c:
        assert BS.read(c, bot_id)["private_tasks_default"] is False
