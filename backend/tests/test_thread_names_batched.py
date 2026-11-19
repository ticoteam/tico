"""A list of threads (the sidebar, Home's unread card) names each untitled thread by the people and bots in it. The names
are read together, once a list, not one bot at a time: forty DMs with forty bots read the bots' names in one statement,
and every row is named as it is alone. Fictional people and bots only."""

from backend import threads
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get  # noqa: F401  (the api fixture)
from backend.tests.helpers.task_privacy_reads import traced

BOTS = [f"helper{n:02d}" for n in range(40)]


def test_a_list_of_dms_reads_its_bots_names_once(api):
    store = api.app.state.store
    with store.transaction() as c:
        for slug in BOTS:
            X.bot(c, slug, computer=False)
            c.execute("UPDATE bots SET display_name=? WHERE id=?", (f"Helper {slug[-2:]}", slug))
            threads.post(c, "human:ana", None, f"Hi {slug}", start={"dm": ["bot:" + slug], "title": ""})
    page, statements = traced(api, lambda: get(api, "threads?limit=100", "ana-test"))
    named = {row["name"] for row in page["dms"]}
    assert {f"Helper {slug[-2:]}" for slug in BOTS} <= named, named
    bot_names = [s for s in statements if s.startswith("SELECT id,slug,display_name FROM bots WHERE id IN")]
    assert len(bot_names) <= 1, bot_names
    # Each row is named as it is when the thread is read alone.
    for row in page["dms"]:
        if row["name"].startswith("Helper"):
            alone = get(api, f"threads/{row['id']}", "ana-test")
            assert alone["name"] == row["name"]
