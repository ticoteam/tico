"""HQ's per-version messages, known issues and fixed-in notices: they ride the update check, an install too old to show
a message gets its link as the release link, and a release that ships a linked fix posts the notice once, by itself."""

import threading
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from hq.app import create_app
from hq.db import Database
from hq.notices import Notices, Watch, affected, covers, fixed_refs

STAFF = "s" * 32
LINK = "https://github.com/ticoteam/tico/blob/main/docs/updates.md"


def staff():
    return {"Authorization": "Bearer " + STAFF}


class Release:
    """The latest release GitHub reports, which a test moves on."""

    def __init__(self, tag="v0.3.51"):
        self.tag = tag

    def get(self):
        return {"tag_name": self.tag, "html_url": "https://github.com/ticoteam/tico/releases/tag/" + self.tag,
                "published_at": "2026-10-20T10:00:00Z", "name": "Tico " + self.tag}


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "hq.db")


@pytest.fixture
def release():
    return Release()


@pytest.fixture
def commits():
    return {"v0.3.51...v0.3.52": ["Merge pull request #712 from ticoteam/fix-board", "Fix the tunnel (#715)\n\nFixes #700"]}


@pytest.fixture
def asked():
    return []


@pytest.fixture
def client(db, release, commits, asked):
    def github(request):
        asked.append(request.url.path)
        span = request.url.path.rsplit("/", 1)[-1]
        if span not in commits:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"commits": [{"commit": {"message": m}} for m in commits[span]]})
    judge = lambda text: ("spam", "test") if "SPAM" in text else ("legit", "test")      # noqa: E731
    app = create_app(db, release, staff_key=STAFF, watch_args={"transport": httpx.MockTransport(github)}, watch_every=0,
                     judge=judge)
    with TestClient(app, client=("203.0.113.9", 5000)) as c:
        yield c


def ping(client, version):
    return client.get("/v1/latest", params={"install_id": str(uuid.uuid4()), "version": version,
                                            "active_people": "true", "active_bots": "true"})


def ticket(client, version="0.3.50"):
    return client.post("/v1/support", json={"message": "The board will not load", "version": version}).json()


def test_matching_by_version():
    assert covers("0.2", "0.2.15") and covers("0.2", "0.2.42") and not covers("0.2", "0.3.1")
    assert covers("0.3.50", "0.3.50") and not covers("0.3.50", "0.3.51") and not covers("0.2", "dev")
    issue = {"since": "0.3.48", "fixed_in": "0.3.52"}
    assert [affected(issue, v) for v in ("0.3.47", "0.3.48", "0.3.51", "0.3.52")] == [False, True, True, False]
    assert affected({"since": "0.3.48", "fixed_in": ""}, "0.4.0")


def test_the_update_check_carries_messages_and_known_issues(client):
    assert client.post("/v1/staff/notices", json={"versions": "0.2", "body": "You're on 0.2: update to get one-tap support.",
                                                  "url": LINK}, headers=staff()).status_code == 201
    assert client.post("/v1/staff/known-issues", json={"title": "Board stays empty after a restart", "since": "0.3.50",
                                                       "ref": "#712"}, headers=staff()).status_code == 201
    body = ping(client, "0.3.52").json()
    assert body["tag_name"] == "v0.3.51" and body["html_url"].endswith("/v0.3.51")
    assert body["notices"] == [{"id": 1, "versions": "0.2", "body": "You're on 0.2: update to get one-tap support.", "url": LINK}]
    assert body["known_issues"] == [{"id": 1, "title": "Board stays empty after a restart", "since": "0.3.50",
                                     "fixed_in": "", "url": ""}]
    # A bare lookup (counting off would never ask, but a mirror or a person might) gets the same lists and no swap.
    bare = client.get("/v1/latest").json()
    assert bare["notices"] == body["notices"] and bare["html_url"].endswith("/v0.3.51")


def test_an_install_too_old_to_show_a_message_gets_its_link_as_the_release_link(client):
    client.post("/v1/staff/notices", json={"versions": "0.2", "body": "You're on 0.2: update to get one-tap support.",
                                           "url": LINK}, headers=staff())
    assert ping(client, "0.2.15").json()["html_url"] == LINK
    assert ping(client, "0.2.42").json()["html_url"] == LINK
    assert ping(client, "0.3.40").json()["html_url"].endswith("/v0.3.51")      # no message for 0.3


def test_staff_input_is_strict_and_staff_only(client):
    assert client.post("/v1/staff/notices", json={"versions": "0.2", "body": "x"}).status_code == 401
    bad = [{"versions": "0.2;", "body": "Update"}, {"versions": "0.2", "body": ""}, {"versions": "0.2", "body": "a" * 281},
           {"versions": "0.2", "body": "Update", "url": "http://plain.example"}, {"versions": "0.2", "body": "Update", "x": 1}]
    for data in bad:
        assert client.post("/v1/staff/notices", json=data, headers=staff()).status_code == 422, data
    assert client.post("/v1/staff/known-issues", json={"title": "T", "since": "latest"}, headers=staff()).status_code == 422
    assert client.post("/v1/staff/known-issues", json={"title": "T", "since": "0.3.1", "ref": "abc"},
                       headers=staff()).status_code == 422
    assert client.post("/v1/staff/tickets/TK-AAAAAAAA/fix", json={"ref": "#1"}, headers=staff()).status_code == 404


def test_known_issues_are_listed_fixed_and_removed(client):
    client.post("/v1/staff/known-issues", json={"title": "Slow board", "since": "0.3.40"}, headers=staff())
    assert client.post("/v1/staff/known-issues/1", json={"fixed_in": "v0.3.52"}, headers=staff()).json()["fixed_in"] == "0.3.52"
    assert client.get("/v1/staff/known-issues", headers=staff()).json()["known_issues"][0]["fixed_in"] == "0.3.52"
    assert client.delete("/v1/staff/known-issues/1", headers=staff()).status_code == 200
    assert client.get("/v1/latest").json()["known_issues"] == []
    assert client.delete("/v1/staff/notices/9", headers=staff()).status_code == 404


def test_a_release_that_ships_a_linked_fix_posts_the_notice_once_by_itself(client, db, release, asked):
    linked = ticket(client)
    other = ticket(client)
    by_issue = ticket(client)
    assert client.post(f"/v1/staff/tickets/{linked['ticket_id']}/fix", json={"ref": "#712"}, headers=staff()).json()["refs"] == [712]
    client.post(f"/v1/staff/tickets/{by_issue['ticket_id']}/fix", json={"ref": 700}, headers=staff())
    client.post(f"/v1/staff/tickets/{other['ticket_id']}/fix", json={"ref": "#999"}, headers=staff())
    client.post("/v1/staff/known-issues", json={"title": "Board stays empty", "since": "0.3.50", "ref": "#712"}, headers=staff())
    notices = Notices(db)
    look = Watch(notices, release, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"commits": [
        {"commit": {"message": "Merge pull request #712 from ticoteam/fix-board"}},
        {"commit": {"message": "Fix the tunnel (#715)\n\nFixes #700"}}]})))
    assert look.check() is None                     # the first tag seen is only remembered
    assert look.check() is None                     # and nothing changes while it stays the latest
    release.tag = "v0.3.52"
    done = look.check()
    assert sorted(n["ticket_id"] for n in done["notified"]) == sorted([linked["ticket_id"], by_issue["ticket_id"]])
    assert done["known_issues_fixed"] == [1]
    mine = client.get(f"/v1/support/{linked['ticket_id']}", headers={"x-ticket-secret": linked["secret"]}).json()
    assert mine["status"] == "answered"
    assert mine["messages"][-1]["body"] == ("Fixed in v0.3.52, update here: "
                                            "https://github.com/ticoteam/tico/releases/tag/v0.3.52")
    untouched = client.get(f"/v1/support/{other['ticket_id']}", headers={"x-ticket-secret": other["secret"]}).json()
    assert untouched["messages"] == [] and untouched["status"] == "open"
    assert look.check() is None                     # once: the same release again posts nothing
    again = client.post("/v1/staff/releases", json={"tag": "v0.3.52", "refs": [712]}, headers=staff()).json()
    assert again["notified"] == []
    assert len(client.get(f"/v1/support/{linked['ticket_id']}", headers={"x-ticket-secret": linked["secret"]}).json()["messages"]) == 1
    assert client.get("/v1/latest").json()["known_issues"][0]["fixed_in"] == "0.3.52"


def test_a_closed_or_held_request_gets_no_notice_and_github_down_waits(client, db, release):
    gone = ticket(client)
    client.post(f"/v1/staff/tickets/{gone['ticket_id']}/fix", json={"ref": "#712"}, headers=staff())
    client.post(f"/v1/staff/tickets/{gone['ticket_id']}/status", json={"status": "closed"}, headers=staff())
    notices = Notices(db)
    down = Watch(notices, release, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    down.check()
    release.tag = "v0.3.52"
    assert down.check() is None and notices.last_swept() == "v0.3.51"   # GitHub down: asked again next time
    up = Watch(notices, release, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"commits": [
        {"commit": {"message": "Merge pull request #712"}}]})))
    assert up.check()["notified"] == [] and notices.last_swept() == "v0.3.52"


def test_the_staff_release_route_reads_the_commits_when_no_refs_are_given(client, db, release, asked):
    linked = ticket(client)
    client.post(f"/v1/staff/tickets/{linked['ticket_id']}/fix", json={"ref": "715"}, headers=staff())
    Notices(db).mark_swept("v0.3.51")
    done = client.post("/v1/staff/releases", json={"tag": "0.3.52"}, headers=staff()).json()
    assert [n["ticket_id"] for n in done["notified"]] == [linked["ticket_id"]] and done["tag"] == "v0.3.52"
    assert asked == ["/repos/ticoteam/tico/compare/v0.3.51...v0.3.52"]
    assert client.post("/v1/staff/releases", json={"tag": "v0.3.53", "refs": "712"}, headers=staff()).status_code == 422


def test_a_staffed_hq_looks_for_releases_by_itself(db, release):
    looked = threading.Event()

    class Stub:
        def check(self):
            looked.set()
    with TestClient(create_app(db, release, staff_key=STAFF, watch=Stub(), watch_every=3600)):
        assert looked.wait(10)
    looked.clear()
    with TestClient(create_app(db, release, watch=Stub(), watch_every=3600)):     # no staff key: no tickets to notify
        assert not looked.wait(0.5)


def test_only_what_a_commit_says_it_fixes_counts():
    assert fixed_refs("Merge pull request #712 from ticoteam/fix-board\n\nBoard fix") == {712}
    assert fixed_refs("Fix the tunnel (#715)\n\nFixes #700, closes #701 and resolved: #702") == {715, 700, 701, 702}
    for message in ('Revert "Fix the tunnel (#715)"\n\nThis reverts commit abc. Fixes #700', "Part 1 of #415",
                    "Refs #700", "Escape &#39; in titles", "Split the board (#415, part 1)", "Test text #4821 (#)"):
        assert fixed_refs(message) == set(), message


def test_the_commit_list_is_read_page_by_page_and_a_short_one_is_not_swept(db, release):
    messages = [f"Merge pull request #{n} from x" for n in range(1, 271)]

    def github(request, cut=False):
        page, per = int(request.url.params["page"]), int(request.url.params["per_page"])
        chunk = messages[(page - 1) * per:page * per]
        if cut and page == 3:
            chunk = []
        return httpx.Response(200, json={"total_commits": len(messages), "commits": [{"commit": {"message": m}} for m in chunk]})
    full = Watch(Notices(db), release, transport=httpx.MockTransport(github))
    assert full.refs_between("v0.3.51", "v0.3.52") == set(range(1, 271))
    short = Watch(Notices(db), release, transport=httpx.MockTransport(lambda r: github(r, cut=True)))
    assert short.refs_between("v0.3.51", "v0.3.52") is None
    notices = Notices(db)
    notices.mark_swept("v0.3.51")
    release.tag = "v0.3.52"
    assert short.check() is None and notices.last_swept() == "v0.3.51"      # asked again, not half-swept


def test_named_refs_by_hand_leave_the_release_to_the_watch(client, db):
    Notices(db).mark_swept("v0.3.51")
    client.post("/v1/staff/releases", json={"tag": "v0.3.52", "refs": [712]}, headers=staff())
    assert Notices(db).last_swept() == "v0.3.51"


def test_a_held_request_gets_its_missed_notice_once_when_a_person_releases_it(client, db, release):
    held = ticket(client)
    spam = client.post("/v1/support", json={"message": "SPAM buy now", "version": "0.3.50"}).json()
    for t in (held, spam):
        client.post(f"/v1/staff/tickets/{t['ticket_id']}/fix", json={"ref": "#712"}, headers=staff())
    done = client.post("/v1/staff/releases", json={"tag": "v0.3.52", "refs": [712]}, headers=staff()).json()
    assert [n["ticket_id"] for n in done["notified"]] == [held["ticket_id"]]
    mine = lambda: client.get(f"/v1/support/{spam['ticket_id']}", headers={"x-ticket-secret": spam["secret"]}).json()  # noqa: E731
    assert mine()["messages"] == []
    assert client.post(f"/v1/staff/tickets/{spam['ticket_id']}/verdict", json={"verdict": "legit"}, headers=staff()).status_code == 200
    assert [m["body"] for m in mine()["messages"]] == [
        "Fixed in v0.3.52, update here: https://github.com/ticoteam/tico/releases/tag/v0.3.52"]
    client.post(f"/v1/staff/tickets/{spam['ticket_id']}/verdict", json={"verdict": "spam"}, headers=staff())
    client.post(f"/v1/staff/tickets/{spam['ticket_id']}/verdict", json={"verdict": "legit"}, headers=staff())
    assert len(mine()["messages"]) == 1
    # A released request whose fix has not shipped gets nothing yet, and the release posts it as usual.
    later = client.post("/v1/support", json={"message": "SPAM again", "version": "0.3.50"}).json()
    client.post(f"/v1/staff/tickets/{later['ticket_id']}/fix", json={"ref": "#800"}, headers=staff())
    client.post(f"/v1/staff/tickets/{later['ticket_id']}/verdict", json={"verdict": "legit"}, headers=staff())
    assert client.get(f"/v1/support/{later['ticket_id']}", headers={"x-ticket-secret": later["secret"]}).json()["messages"] == []

