"""A page posted into a company thread follows the page's own audience, read live, at every door.

Page permissions are independent of thread permissions: a narrow page may be posted into a thread everyone reads, and
when its audience narrows later, the thread's readers outside it can no longer see it there or open it. The post keeps
its link to the page (as a task comment's attachment does), so each read checks the reader against the page now:

- the thread's timeline, the message on its own, the live stream and `tico sql` show a reader outside the page's
  audience the placeholder alone (no title, file id, blob or size), and
- the address the attachment gave before the narrowing no longer opens for them.

Fictional people only: Ben made the page public, then narrowed it to himself; Ana (the owner, who reads every page) and
Cara read the company thread; ops, the bot that wrote the page, reads it throughout.
"""

import json

import pytest

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.page_audience import edit, promoted
from backend.tests.helpers.files import turn
from backend.tests.helpers.org_rule_check import COLOR, ruled  # noqa: F401  (the fixture)
from backend.tests.helpers.org_rules import rule
from backend.tests.helpers.pages import page, read
from backend.tests.helpers.sql import query
from backend.tests.threads_e2e import call, create_thread, ok

PEOPLE = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test"}
BOTS = ("ops", "cpo")  # ops wrote the page; cpo is a bot of the company with no part in it
READERS = ("ana", "ben", "cara", *BOTS)
REMOVED = [{"removed": True, "label": "A page you can't open"}]  # files.page_ref_denials' placeholder, nothing more
DOORS = ("timeline", "message", "sse", "sql")


def _token(w, reader):
    return w["bots"][reader]["token"] if reader in BOTS else PEOPLE[reader]


def _identity(api, w, reader):
    if reader in BOTS:
        return Identity("bot:" + reader, "bot", attempt_id=w["bots"][reader]["id"])
    return api.app.state.auth.settings.test_identities[PEOPLE[reader]]


def _company(api, fid, attempt, post=None, token="ben-test"):
    """A company thread with the page posted in it by `token`'s person, and a run of the unrelated bot cpo."""
    _, other = turn(api, "cpo", who="ana-test")
    company = create_thread(api, title="All hands", audience="company")["id"]
    message = (post or _posted_as_json)(api, company, fid, token)
    return {"file": fid, "bots": {"ops": attempt, "cpo": other}, "thread": company, "message": message["id"]}


def _posted_as_json(api, thread_id, fid, token="ben-test"):
    return ok(
        call(api, "POST", f"threads/{thread_id}/messages", token, {"text": "zqdeck", "attachments": [fid + "@1"]})
    )


def _posted_with_an_upload(api, thread_id, fid, token="ben-test"):
    """The multipart route (a person's message with files) shares a file already in Tico the same way."""
    r = api.post(
        f"/api/v2/uploads/threads/{thread_id}/messages",
        files={"text": (None, "zqdeck"), "attachments": (None, json.dumps([fid + "@1"]))},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["message"]


@pytest.fixture(params=[_posted_as_json, _posted_with_an_upload], ids=["json", "upload"])
def world(api, request):
    fid, attempt = promoted(api)  # a page ops wrote, promoted by Ben so the whole company reads it
    return _company(api, fid, attempt, request.param)


def _attachments(refs):
    """What a message's attachments show its reader: the page's file id (a copy's blob id where there is none), or the
    whole placeholder of one they may not open (compared whole, so a placeholder that kept the file's id, title or blob fails)."""
    refs = H._json(refs, {}) if isinstance(refs, str) else refs
    return [a if a.get("removed") else a.get("file_id") or a.get("id") for a in (refs or {}).get("attachments") or []]


def _doors(api, w, reader):
    """{door: the attachments it shows `reader` on the posted message}."""
    auth, store = api.app.state.auth, api.app.state.store
    mid, token, who = w["message"], _token(w, reader), _identity(api, w, reader)
    with store.transaction() as c:  # the message again on the stream, as an edit sends it
        since = E.latest(c)
        c.execute("UPDATE messages SET edited_at=? WHERE id=?", (H.now(), mid))
    with store.read() as c:
        sent, _, reset, _ = E.read(
            c, auth, who, since, topics=("messages",), task_views=lambda *_, **__: [], task_view=None
        )
    assert not reset
    (live,) = [d for _, topic, d in sent if topic == "messages" and d.get("id") == mid]
    timeline = ok(call(api, "GET", f"threads/{w['thread']}/timeline", token))["messages"]
    (row,) = query(api, f"SELECT refs FROM messages WHERE id='{mid}'", token)["rows"]
    return {
        "timeline": [_attachments(r.get("refs")) for r in timeline if r.get("id") == mid][0],
        "message": _attachments(ok(call(api, "GET", "messages/" + mid, token))["refs"]),
        "sse": _attachments((live.get("message") or live).get("refs")),
        "sql": _attachments(row[0]),
    }


def _address(api, w):
    """The address Ben's view of the attachment opens: what every reader had before the narrowing."""
    (item,) = ok(call(api, "GET", "messages/" + w["message"], "ben-test"))["refs"]["attachments"]
    return item["url"]


def test_the_post_keeps_its_link_to_the_page_and_copies_none_of_its_bytes(api, world):
    w = world
    with api.app.state.store.read() as c:
        stored = H._json(H.message(c, w["message"])["refs"], {})
        (item,) = stored["attachments"]
        assert (item["file_id"], item["version"]) == (w["file"], 1)
        # no new blob of the page's bytes, linked to the message for whoever reads it
        assert not c.execute("SELECT 1 FROM message_assets WHERE message_id=?", (w["message"],)).fetchone()


def test_narrowing_the_page_removes_it_from_the_company_thread_at_every_door_for_readers_outside_it(api, world):
    w = world
    fid = w["file"]
    address = _address(api, w)
    # Before: the whole company reads the page in the thread and opens it. (What the attachment names is pinned by the
    # test above; here it is only the same at every door for every reader.)
    # The bot cpo reads the company thread but not the page, which a bot reads only through its own work.
    shown = _doors(api, w, "ben")["timeline"]
    assert len(shown) == 1 and shown != REMOVED, shown
    _expect(api, w, address, {"ben": shown, "ops": shown, "ana": shown, "cara": shown, "cpo": REMOVED})

    edit(api, fid, {"people": ["ben"]})

    # After: Ben (its audience), ops (who wrote it) and Ana (the owner reads every page) still read it, in the thread
    # as on the page itself; Cara and the bot cpo see the placeholder alone and open nothing.
    _expect(api, w, address, {"ben": shown, "ops": shown, "ana": shown, "cara": REMOVED, "cpo": REMOVED})


def _expect(api, w, address, expected):
    fid = w["file"]
    for reader, want in expected.items():
        status = 200 if want != REMOVED else 404
        assert read(api, fid, _token(w, reader)).status_code == status, (reader, "the page itself")
        assert _doors(api, w, reader) == dict.fromkeys(DOORS, want), reader
        opened = api.get(address, headers=headers(_token(w, reader)))
        assert opened.status_code == status, (reader, opened.status_code)


def test_a_page_private_by_where_it_was_written_shows_only_the_placeholder_to_company_readers_outside_it(api):
    """A page with no audience of its own reads as the chat it was written in does (Ben's chat with ops): posted into a
    company thread, it is still that chat's alone, and nothing of it (title, name, size, id, address) reaches the others."""
    _, attempt = turn(api, "ops", who="ben-test")
    made = page(api, attempt, title="Ben's plan")
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    w = _company(api, fid, attempt)
    shown = [fid]
    assert _doors(api, w, "ben") == dict.fromkeys(DOORS, shown)
    _expect(api, w, _address(api, w), {"ben": shown, "ops": shown, "ana": REMOVED, "cara": REMOVED, "cpo": REMOVED})


def test_an_edit_answers_its_writer_with_the_page_as_they_may_read_it_now(api):
    """Cara posted the page while the company read it; once it is narrowed away from her, the answer to her own edit of
    that message shows her the placeholder, as every read does."""
    fid, attempt = promoted(api)
    w = _company(api, fid, attempt, token="cara-test")
    edit(api, fid, {"people": ["ben"]})
    edited = ok(call(api, "PATCH", "messages/" + w["message"], "cara-test", {"text": "zqdeck, edited"}))
    assert _attachments(edited["refs"]) == REMOVED
    assert _attachments(
        ok(call(api, "PATCH", "messages/" + w["message"], "cara-test", {"text": "zqdeck"}))["refs"]
    ) == (REMOVED)


def test_sending_a_held_edit_anyway_answers_the_reviewer_with_the_page_as_they_may_read_it(ruled):
    """Ben's edit of his post of a page from his chat with ops is held by a rule; Ana sends it anyway. She is no reader
    of that page, so the answer to her send shows her the placeholder alone, as every read of the message does."""
    api, _ = ruled
    _, attempt = turn(api, "ops", who="ben-test")
    fid = page(api, attempt, title="Ben's plan").json()["page"]["file_id"]
    w = _company(api, fid, attempt)
    rule(api, {"kind": "internal", "text": COLOR, "who": {"everyone": True}})
    held = ok(call(api, "PATCH", "messages/" + w["message"], "ben-test", {"text": "Our favorite color is gold."}))
    view = ok(call(api, "GET", f"rule-holds/{held['held']['id']}", "ana-test"))
    assert view["can_send"]
    sent = ok(call(api, "POST", f"rule-holds/{held['held']['id']}/send", "ana-test", {"nonce": view["nonce"]}))
    assert sent["result"]["body"] == "Our favorite color is gold."
    assert _attachments(sent["result"]["refs"]) == REMOVED
    assert _doors(api, w, "ana")["message"] == REMOVED


def test_a_copy_posted_before_shares_kept_the_link_follows_its_page_once_migrated(api):
    """A message from before this change holds a new blob of the page's bytes, not the page. The migration names that
    blob as the page's, so narrowing the page takes the copy from every door, its download included, as it does a link."""
    from importlib import import_module

    from backend import threads
    from backend.blobs import register

    fid, attempt = promoted(api)
    w = _company(api, fid, attempt)
    store = api.app.state.store
    with store.transaction() as c:  # the copy the old thread_api.shared_files made, posted by Ben
        version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=? AND version=1", (fid,)).fetchone()
        ben = threads.viewer(api.app.state.auth, api.app.state.auth.settings.test_identities["ben-test"])
        item = register(c, ben.who, version["digest"], version["size"], version["name"], version["mime"])
        old = threads.post(c, ben, w["thread"], "zqold deck", attachments=[{**item, "title": "Audience secret"}])
        import_module("backend.migrations.20261127074210_page_copies_follow_their_page").up(c)
    w["message"] = old["id"]
    copy = [item["id"]]
    _expect(api, w, item["url"], {"ben": copy, "ops": copy, "ana": copy, "cara": copy, "cpo": REMOVED})
    edit(api, fid, {"people": ["ben"]})
    _expect(api, w, item["url"], {"ben": copy, "ops": copy, "ana": copy, "cara": REMOVED, "cpo": REMOVED})
