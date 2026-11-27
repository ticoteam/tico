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
from backend.tests.helpers.pages import read
from backend.tests.helpers.sql import query
from backend.tests.threads_e2e import call, create_thread, ok

PEOPLE = {"ana": "ana-test", "ben": "ben-test", "cara": "cara-test"}
READERS = ("ana", "ben", "cara", "ops")
REMOVED = [{"removed": True, "label": "A page you can't open"}]  # files.page_ref_denials' placeholder, nothing more
DOORS = ("timeline", "message", "sse", "sql")


def _token(w, reader):
    return w["attempt"]["token"] if reader == "ops" else PEOPLE[reader]


def _identity(api, w, reader):
    if reader == "ops":
        return Identity("bot:ops", "bot", attempt_id=w["attempt"]["id"])
    return api.app.state.auth.settings.test_identities[PEOPLE[reader]]


def _posted_as_json(api, thread_id, fid):
    return ok(
        call(api, "POST", f"threads/{thread_id}/messages", "ben-test", {"text": "zqdeck", "attachments": [fid + "@1"]})
    )


def _posted_with_an_upload(api, thread_id, fid):
    """The multipart route (a person's message with files) shares a file already in Tico the same way."""
    r = api.post(
        f"/api/v2/uploads/threads/{thread_id}/messages",
        files={"text": (None, "zqdeck"), "attachments": (None, json.dumps([fid + "@1"]))},
        headers=headers("ben-test"),
    )
    assert r.status_code == 200, r.text
    return r.json()["message"]


@pytest.fixture(params=[_posted_as_json, _posted_with_an_upload], ids=["json", "upload"])
def world(api, request):
    fid, attempt = promoted(api)  # a page ops wrote, promoted by Ben so the whole company reads it
    company = create_thread(api, title="All hands", audience="company")["id"]
    message = request.param(api, company, fid)
    return {"file": fid, "attempt": attempt, "thread": company, "message": message["id"]}


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
    shown = _doors(api, w, "ben")["timeline"]
    assert len(shown) == 1 and shown != REMOVED, shown
    for reader in READERS:
        assert _doors(api, w, reader) == dict.fromkeys(DOORS, shown), reader
        opened = api.get(address, headers=headers(_token(w, reader)))
        assert opened.status_code == 200, (reader, opened.text)

    edit(api, fid, {"people": ["ben"]})

    # After: Ben (its audience), ops (who wrote it) and Ana (the owner reads every page) still read it, in the thread
    # as on the page itself; Cara sees the placeholder alone and opens nothing.
    expected = {"ben": shown, "ops": shown, "ana": shown, "cara": REMOVED}
    for reader, want in expected.items():
        status = 200 if want != REMOVED else 404
        assert read(api, fid, _token(w, reader)).status_code == status, (reader, "the page itself")
        assert _doors(api, w, reader) == dict.fromkeys(DOORS, want), reader
        opened = api.get(address, headers=headers(_token(w, reader)))
        assert opened.status_code == status, (reader, opened.status_code)
