"""Customer themes (docs/librarian.md): the market page is bootstrapped, the Librarian's template runs it on
`meeting.ready`, and only the owner and the Librarian write the page."""

from pathlib import Path

import yaml

from backend import market as M
from backend.blobs import Blobs
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_market import FIXTURE_REGISTRY
from clients.routines import validate_schedules

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "librarian"


def test_the_market_bootstrap_creates_the_customer_themes_page_and_leaves_an_edited_one_alone(api):
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document=M.load_snapshot(FIXTURE_REGISTRY))
        page = M.read_page(c, "market/customer-themes")
        assert page and page["title"] == "Customer themes"
        M.write_page(c, "human:ana", "market/customer-themes", "Customer themes", "# Customer themes\n\n## Slow exports\nSeen 3 times",
                     "Market / Customers", replace=True)
        M.seed_pages(c)
        assert "Slow exports" in M.read_page(c, "market/customer-themes")["content"]


def test_the_librarian_template_runs_customer_themes_on_meeting_ready_from_its_playbook():
    manifest = yaml.safe_load((CATALOG / "bot.yaml").read_text())
    entries = validate_schedules(manifest["routines"], lambda rel: (CATALOG / rel).read_text())
    [theme] = [e for e in entries if e["id"] == "customer-themes"]
    assert theme["on"] == "meeting.ready" and not theme["cron"]
    assert "private, pending or dismissed" in theme["instructions"]


def test_the_page_is_written_by_the_owner_and_not_by_another_member(api):
    store = api.app.state.store
    with store.transaction() as c:
        M.seed(c, {}, Blobs(store.settings), document=M.load_snapshot(FIXTURE_REGISTRY))
    post(api, "market/pages/customer-themes", {"body": "# Customer themes\n\n## Slow exports\nSeen 3 times"})
    assert "Slow exports" in get(api, "market/pages/customer-themes")["body"]
    post(api, "market/pages/customer-themes", {"body": "# x"}, token="cara-test", expected=403)
