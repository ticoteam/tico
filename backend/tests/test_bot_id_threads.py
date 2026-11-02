"""Threads for a bot made after ids: its id is not its slug, so home, members and mentions all hold the id."""
from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_bot_access import bot, bot_id


def test_a_bot_whose_id_is_not_its_slug_has_a_home_members_and_mentions(api):
    bot(api, "mira")
    mira = bot_id(api, "mira")
    assert mira != "mira"
    home = api.get("/api/v2/threads/home/bot:mira", headers=headers("ana-test")).json()
    assert home["owner_id"] == "bot:" + mira
    assert api.get(f"/api/v2/threads/home/bot:{mira}", headers=headers("ana-test")).json()["id"] == home["id"]

    thread = post(api, "threads", {"title": "Launch", "audience": "members", "members": ["human:ben", "bot:mira"]})
    members = api.get(f"/api/v2/threads/{thread['id']}/members", headers=headers("ana-test")).json()["members"]
    assert "bot:" + mira in [m["actor"] for m in members]

    said = post(api, f"threads/{thread['id']}/messages", {"text": "@mira take this one"})
    with api.app.state.store.read() as c:
        job = c.execute("SELECT bot_id FROM jobs WHERE message_id=?", (said["id"],)).fetchone()
        mention = c.execute("SELECT actor FROM message_mentions WHERE message_id=?", (said["id"],)).fetchone()
        assert H.resolve_bot(c, "mira")["id"] == mira
    assert mention["actor"] == "bot:" + mira
    assert job is None or job["bot_id"] == mira

    shared = api.get("/api/v2/threads", params={"with": "bot:mira"}, headers=headers("ana-test")).json()
    assert thread["id"] in [t["id"] for t in shared["threads"] + shared["dms"] + shared["attention"]]

    listed = api.get("/api/employees", headers=headers("ana-test")).json()
    rows = listed if isinstance(listed, list) else listed["employees"]
    mine = next(row for row in rows if row.get("id") == mira)
    assert mine["my_access"]["manage"] is True
