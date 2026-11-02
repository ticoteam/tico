"""Email digests (backend/thread_digest.py): a person who follows a thread on digest gets one email an hour that lists the
threads with new messages for them, as a title, an unread count and a link. The email carries no message text, and a
title goes only for a thread the person can still read. Nothing new sends nothing, and the email reaches the issuer
with the install's credentials and only the digest's fields."""

import json
from datetime import datetime, timezone

from backend import scheduler, thread_digest, threads
from backend.tests.test_api import api, post  # noqa: F401  (the api fixture)
from backend.tests.test_oidc import CLIENT, provider, signin  # noqa: F401  (the fixtures)


class Issuer:
    """Stands in for the issuer's digest call: it records each email and answers the next answer (sent when none is
    queued)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, email, items):
        self.calls.append((email, items))
        return self.answers.pop(0) if self.answers else (202, {"status": "sent"})


def thread(api, title):
    """A members thread Cara started, with Ben in it and nobody mentioned: Ben's messages are not queued to him, so
    what a digest lists is what the thread holds past his read mark."""
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=["human:ben"])["id"]


def follow(api, tid, notify="digest"):
    with api.app.state.store.transaction() as c:
        threads.follow(c, threads.actor_viewer(c, "human:ben"), tid, notify=notify)


def say(api, tid, text):
    with api.app.state.store.transaction() as c:
        return threads.post(c, "human:cara", tid, text)["id"]


def read_all(api, tid):
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, threads.actor_viewer(c, "human:ben"), tid)


def digested(api):
    """What the digests have covered for Ben: thread id -> the last sequence number an email covered."""
    with api.app.state.store.read() as c:
        return {r["thread_id"]: r["emailed_seq"] for r in c.execute(
            "SELECT thread_id, emailed_seq FROM thread_digests WHERE actor='human:ben'")}


def snapshot(api):
    with api.app.state.store.read() as c:
        return (digested(api), c.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def sweep(api, send):
    return thread_digest.sweep(api.app.state.store, send)


def test_a_person_gets_one_email_listing_each_thread_with_its_unread_count_and_a_link(api):
    launch, budget = thread(api, "Launch plans"), thread(api, "Budget")
    for tid in (launch, budget):
        follow(api, tid)
        say(api, tid, "Secret pricing 4711")
        say(api, tid, "Second note")
    issuer = Issuer()
    assert sweep(api, issuer) == 1
    [(email, items)] = issuer.calls
    assert email == "ben@acme.example"
    assert sorted(items, key=lambda item: item["title"]) == [
        {"title": "Budget", "unread": 2, "path": f"/#/thread/{budget}"},
        {"title": "Launch plans", "unread": 2, "path": f"/#/thread/{launch}"}]
    assert "Secret" not in json.dumps(items) and "Second note" not in json.dumps(items)
    with api.app.state.store.read() as c:
        detail = [r[0] for r in c.execute("SELECT detail_json FROM events WHERE action='thread.digest'")]
    assert len(detail) == 1 and "Launch plans" not in detail[0] and "Budget" not in detail[0]
    # What was listed is not listed again, and a new message counts with the ones the person has not read yet.
    assert sweep(api, issuer) == 0 and len(issuer.calls) == 1
    say(api, launch, "One more")
    assert sweep(api, issuer) == 1
    assert [item["unread"] for item in issuer.calls[1][1]] == [3]


def test_a_thread_the_person_can_no_longer_read_is_not_listed_and_its_title_is_not_sent(api):
    gone, kept = thread(api, "Acquisition talks"), thread(api, "Launch plans")
    for tid in (gone, kept):
        follow(api, tid)
    say(api, gone, "Numbers")
    say(api, kept, "Hello")
    with api.app.state.store.transaction() as c:    # the follower row stays: the membership is what went
        c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='human:ben'", (gone,))
    issuer = Issuer()
    assert sweep(api, issuer) == 1
    [(_, items)] = issuer.calls
    assert [item["title"] for item in items] == ["Launch plans"]
    assert "Acquisition" not in json.dumps(items) and gone not in digested(api)


def test_nothing_new_sends_nothing_and_writes_nothing(api):
    issuer = Issuer()
    assert sweep(api, issuer) == 0 and issuer.calls == []           # no one on digest at all
    tid = thread(api, "Launch plans")
    follow(api, tid)
    say(api, tid, "Hi")
    read_all(api, tid)
    before = snapshot(api)
    assert sweep(api, issuer) == 0 and issuer.calls == []
    assert snapshot(api) == before


def test_a_person_who_turned_email_digests_off_is_not_emailed_until_they_turn_them_back_on(api):
    tid = thread(api, "Launch plans")
    follow(api, tid)
    say(api, tid, "Hi")
    post(api, "humans/ben", {"email_digest": False}, token="ben-test")
    issuer = Issuer()
    assert sweep(api, issuer) == 0 and issuer.calls == [] and digested(api) == {}
    post(api, "humans/ben", {"email_digest": True}, token="ben-test")
    assert sweep(api, issuer) == 1 and issuer.calls[0][1][0]["title"] == "Launch plans"


def test_a_follower_on_live_is_not_emailed_a_digest(api):
    tid = thread(api, "Launch plans")
    follow(api, tid, notify="live")
    say(api, tid, "Hi")
    issuer = Issuer()
    assert sweep(api, issuer) == 0 and issuer.calls == [] and digested(api) == {}


def test_a_refused_email_records_nothing_and_the_next_run_sends_it(api):
    tid = thread(api, "Launch plans")
    follow(api, tid)
    say(api, tid, "Hi")
    issuer = Issuer((429, {"error": "too_many", "retry_after": 3600}))
    assert sweep(api, issuer) == 0 and len(issuer.calls) == 1      # one attempt in the run, not a retry loop
    assert digested(api) == {}
    assert sweep(api, issuer) == 1 and len(issuer.calls) == 2
    assert tid in digested(api)


def test_at_most_twenty_threads_go_in_one_email_and_the_rest_wait_for_the_next_run(api):
    for n in range(21):
        tid = thread(api, f"Thread {n:02d}")
        follow(api, tid)
        say(api, tid, "Hi")
    issuer = Issuer()
    assert sweep(api, issuer) == 1 and len(issuer.calls[0][1]) == 20
    assert sweep(api, issuer) == 1 and len(issuer.calls[1][1]) == 1
    assert sweep(api, issuer) == 0


def test_a_title_is_one_line_within_the_issuers_limit_so_the_email_still_goes(api):
    tid = thread(api, "Launch\n\tplans " + "x" * 300)
    follow(api, tid)
    say(api, tid, "Hi")
    issuer = Issuer()
    assert sweep(api, issuer) == 1
    title = issuer.calls[0][1][0]["title"]
    assert title.startswith("Launch plans xxx") and len(title) <= 200 and "\n" not in title


def test_a_long_or_blank_subject_becomes_a_one_line_title_the_issuer_accepts():
    assert thread_digest.title_of("Launch\n\tplans " + "x" * 300) == "Launch plans " + "x" * 187
    assert thread_digest.title_of(" \n\t ") == "Untitled thread"


def test_an_install_without_a_tico_account_issuer_reads_and_writes_no_digest(api):
    tid = thread(api, "Launch plans")
    follow(api, tid)
    say(api, tid, "Hi")
    before = snapshot(api)
    scheduler.run_thread_digest(api.app.state.store, api.app.state.auth, datetime.now(timezone.utc))
    assert snapshot(api) == before


def test_the_issuer_gets_the_install_credentials_and_only_titles_counts_and_links(signin):  # noqa: F811
    api = signin.api
    signin.fake.digests = [(202, {"status": "sent"})]
    tid = thread(api, "Launch plans")
    follow(api, tid)
    say(api, tid, "Secret pricing 4711")
    scheduler.run_thread_digest(api.app.state.store, signin.auth, datetime.now(timezone.utc))
    [call] = signin.fake.digest_calls
    assert set(call) == {"client_id", "client_secret", "email", "threads"} and call["client_id"] == CLIENT
    assert call["email"] == "ben@acme.example"
    assert json.loads(call["threads"]) == [{"title": "Launch plans", "unread": 1, "path": f"/#/thread/{tid}"}]
    assert tid in digested(api)


def test_a_rehearsal_sends_no_digest_and_asks_the_issuer_nothing(signin, monkeypatch):  # noqa: F811
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    monkeypatch.setattr(signin.auth.proxy, "_discover", lambda *a, **k: 1 / 0)
    assert signin.auth.proxy.digest("ben@acme.example", [{"title": "t", "unread": 1, "path": "/#/thread/x"}]) is None
    assert signin.fake.digest_calls == []


def test_a_digest_counts_what_the_thread_counts_as_unread(api):
    tid = thread(api, "Launch")
    follow(api, tid)
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", tid, "A bot posted, naming nobody")
        threads.post(c, "human:ben", tid, "Ben's own post")
    issuer = Issuer()
    assert sweep(api, issuer) == 0 and issuer.calls == []           # activity and his own post are not unread
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", tid, "A bot asks Ben", to_actor="human:ben")
    say(api, tid, "Cara too")
    assert sweep(api, issuer) == 1
    [(_, [item])] = issuer.calls
    assert item["unread"] == 2
