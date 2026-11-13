"""The threads sidebar reads its page in a few statements however many threads, DMs and members the page holds."""

from backend.tests.helpers.api import headers  # noqa: F401
from backend.tests.helpers.health_reads import counted


def seed_threads(api, start, count):
    """`count` threads in the company with ana, ben and cara as members, half of them started by ben, each with a few
    messages from them."""
    for i in range(start, start + count):
        starter = "ben-test" if i % 2 else "ana-test"
        response = api.post(
            "/api/v2/threads",
            json={"title": f"Topic {i}", "audience": "members", "members": ["human:ana", "human:ben", "human:cara"]},
            headers=headers(starter),
        )
        assert response.status_code == 200, response.text
        thread_id = response.json()["id"]
        for token in ("ben-test", "cara-test", "ana-test"):
            api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": f"Note {i}"}, headers=headers(token))


def seed_dms(api):
    """Ana's DMs with ben, with cara, and with both: each with a reply, so they are on her sidebar."""
    for members in (["human:ben"], ["human:cara"], ["human:ben", "human:cara"]):
        dm = api.post(
            "/api/v2/threads/dm", json={"members": members, "title": "Dm", "body": "Hello"}, headers=headers("ana-test")
        )
        assert dm.status_code == 200, dm.text
        api.post(f"/api/v2/threads/{dm.json()['id']}/messages", json={"text": "Reply"}, headers=headers("ben-test"))
