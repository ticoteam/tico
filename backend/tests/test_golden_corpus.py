"""Golden corpus: a team's own files come back byte for byte through every path that rewrites content.

The paths: the Librarian's wording repair (its doc and message writes, its doc and task-comment edits, and the
upgrade repairs that redo it on legacy rows), the display names added to stable API answers, and the file storage
round trip. Only Tico's own terms change, and each such case has its one expected answer. The corpus is synthetic (backend/tests/fixtures/golden_corpus/README.md).

Releases this guards: v0.2.36 to v0.2.42 (the Librarian rewrote teams' words, one case fixed per release), v0.3.9
(display names reformatted .json attachments) and v0.3.10 (the fix left renamed routes without names).
"""
import base64
import hashlib
import struct
import zlib
from pathlib import Path

import pytest

from backend import docs
from backend.store import H
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_files import turn

CORPUS = Path(__file__).parent / "fixtures" / "golden_corpus"
KEPT = sorted((CORPUS / "kept").iterdir())
REWRITTEN = sorted(p for p in (CORPUS / "rewritten").iterdir() if p.suffix != ".expected")
# Rewrites this corpus finds that main still makes, by file name: each runs as a strict xfail until its fix lands,
# then the entry goes.
KNOWN = {}


def _kept():
    return [pytest.param(p, id=p.name, marks=[pytest.mark.xfail(strict=True, raises=AssertionError,
                                                                reason=KNOWN[p.name])]
                         if p.name in KNOWN else []) for p in KEPT]


def _png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes((y * 40, 90, 200)) * 4 for y in range(4))     # 4x4 RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


# Attachments with no text in them: an image, and bytes that are not UTF-8 nor JSON whatever the name says.
BINARY = {"van-photo.png": _png(), "scale-readings.json": b'{"kg": [12.5, 9.0]}\xff\xfe\x00 trailing'}


def _text(path):
    return path.read_bytes().decode("utf-8")


def _expected(path):
    return _text(path.with_name(path.name + ".expected"))


def _ids(paths):
    return [p.name for p in paths]


def _cases():
    """(path, the team's text, what the Librarian's write of it must be) for every text it may write."""
    out = [(p, _text(p), _text(p)) for p in KEPT if p.suffix in (".md", ".txt") and p.name not in KNOWN]
    return out + [(p, _text(p), _expected(p)) for p in REWRITTEN]


def _librarian(c):
    c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
              "VALUES('librarian','librarian','Librarian','fake','','','','keeper','active',?)", (H.now(),))
    c.execute("INSERT OR IGNORE INTO bot_config(bot,config,owner_id) VALUES('librarian','{}','ana')")


@pytest.fixture
def named(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET name='Ana Example' WHERE id='ana'")
        c.execute("UPDATE bots SET display_name='Operations' WHERE slug='ops'")
        _librarian(c)
    return api


# 1. The Librarian's wording repair, on its own.

@pytest.mark.parametrize("path", _kept())
def test_librarian_wording_keeps_a_teams_own_words(path):
    original = _text(path)
    assert H.librarian_text(original) == original


@pytest.mark.parametrize("path", REWRITTEN, ids=_ids(REWRITTEN))
def test_librarian_wording_rewrites_ticos_terms_to_the_expected_text(path):
    expected = _expected(path)
    assert H.librarian_text(_text(path)) == expected
    assert H.librarian_text(expected) == expected            # a second pass (the upgrade repairs) changes nothing


# 2. The Librarian's writes, the upgrade repairs that redo them, and the API answers that show them.

def test_librarian_docs_and_messages_change_only_ticos_terms_through_writes_upgrades_and_reads(named):
    api = named
    cases = _cases()
    made = {}
    with api.app.state.store.transaction() as c:
        store = docs.Docs(None, None, None, None)
        for path, original, _ in cases:
            made[path.name] = {
                "librarian": store.insert(c, "bot:librarian", path.stem, original, "_librarian/" + path.name)["id"],
                "message": H.say(c, "bot:librarian", "human:ana", original)["id"]}
    # A person's doc is the person's text, Tico's terms included, byte for byte: a doc body keeps its own ends
    # (backend/docs.py Body), as a stored file does (section 4).
    for path, original, _ in cases:
        made[path.name]["person"] = post(api, "docs", {"title": "Mine " + path.stem, "body": original,
                                                       "path": "team/" + path.name})["doc"]["id"]
    before = {}
    for path, original, want in cases:
        for who, doc_id in (("librarian", made[path.name]["librarian"]), ("person", made[path.name]["person"])):
            doc = api.get("/api/v2/docs/" + doc_id, headers=headers()).json()["doc"]
            assert doc["body"] == (want if who == "librarian" else original), (path.name, who)
            before[doc_id] = doc["version"]

    # Every upgrade repair runs again, as on an install that never ran them.
    _repair_markers(api)
    api.app.state.store.initialize(seed_market=False)
    for path, original, want in cases:
        for who, doc_id in (("librarian", made[path.name]["librarian"]), ("person", made[path.name]["person"])):
            doc = api.get("/api/v2/docs/" + doc_id, headers=headers()).json()["doc"]
            assert doc["body"] == (want if who == "librarian" else original), (path.name, who, "upgrade")
            assert doc["version"] == before[doc_id], (path.name, who, "a repair wrote a new version")

    inbox = api.get("/api/v2/messages", headers=headers())
    assert inbox.status_code == 200, inbox.text
    shown = {m["id"]: m for m in inbox.json()["messages"]}
    for path, _, want in cases:
        message = shown[made[path.name]["message"]]
        assert message["body"] == want, path.name
        assert message["from_actor_name"] == "Librarian" and message["to_actor_name"] == "Ana Example", path.name


def _repair_markers(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key IN ('librarian_wording35','librarian_wording36',"
                  "'librarian_fix33')")


def test_upgrade_repairs_fix_legacy_librarian_text_once_and_leave_a_persons_text_alone(named):
    api = named
    cases = _cases()
    rows = {}          # doc id -> (case, kind, body and version after the upgrade)
    with api.app.state.store.transaction() as c:
        store = docs.Docs(None, None, None, None)

        def legacy(title, body, path):
            return store.insert(c, "human:ana", title, body, path)["id"]       # a person's write: no rewrite
        for path, original, want in cases:
            # An older Librarian cache, written before its words were repaired (librarian_fix33).
            doc_id = legacy(path.stem, original, "_librarian/old-" + path.name)
            c.execute("UPDATE docs SET created_by_id='bot:librarian',updated_by_id='bot:librarian' WHERE id=?", (doc_id,))
            rows[doc_id] = (path.name, "unrepaired", want, 1 if want == original else 2)
            # The same, last touched by Tico's keeper (a title fix): only librarian_wording35 picks it up.
            doc_id = legacy(path.stem, original, "_librarian/kept-by-keeper-" + path.name)
            c.execute("UPDATE docs SET created_by_id='bot:librarian',updated_by_id='keeper' WHERE id=?", (doc_id,))
            rows[doc_id] = (path.name, "unrepaired, keeper", want, 1 if want == original else 2)
            # A doc the 0.2.35 repair broke (company -> team, machine -> Computer): redone from version 1
            # (librarian_wording36).
            doc_id = legacy(path.stem, original, "_librarian/broken-" + path.name)
            broken = original.replace("company", "team").replace("machine", "Computer") + "\nteam Computer"
            c.execute("UPDATE docs SET body=?,version=2,created_by_id='bot:librarian',updated_by_id='keeper' WHERE id=?",
                      (broken, doc_id))
            store.snapshot(c, store.get_row(c, doc_id), H.KEEPER, "Updated generated Tico wording")
            rows[doc_id] = (path.name, "broken", want, 3)
            # A person's own page where the repairs look: the person's words, Tico's terms included.
            doc_id = legacy(path.stem, original, "_librarian/person-" + path.name)
            rows[doc_id] = (path.name, "person", original, 1)
        faq = legacy("FAQ", _text(CORPUS / "rewritten" / "faq.md"), "FAQ.md")
        rows[faq] = ("faq.md", "person FAQ.md", _text(CORPUS / "rewritten" / "faq.md"), 1)

    def check(when):
        for doc_id, (name, kind, body, version) in rows.items():
            doc = api.get("/api/v2/docs/" + doc_id, headers=headers()).json()["doc"]
            assert doc["body"] == body, (name, kind, when)
            assert doc["version"] == version, (name, kind, when)

    _repair_markers(api)
    api.app.state.store.initialize(seed_market=False)
    check("first upgrade")
    with api.app.state.store.read() as c:
        for doc_id, (name, kind, _, _) in rows.items():
            if kind == "broken":
                notes = [r[0] for r in c.execute("SELECT note FROM doc_versions WHERE doc_id=? ORDER BY version",
                                                 (doc_id,))]
                assert notes == ["Created", "Updated generated Tico wording", "Redid generated Tico wording"], name
    api.app.state.store.initialize(seed_market=False)
    check("second start")
    _repair_markers(api)
    api.app.state.store.initialize(seed_market=False)
    check("repairs run again")

    # An install whose wording repairs are already recorded but whose librarian_fix33 is not: only fix33 runs, and
    # its rewrite is the only one these new unrepaired caches get.
    with api.app.state.store.transaction() as c:
        store = docs.Docs(None, None, None, None)
        for path, original, want in cases:
            doc_id = store.insert(c, "human:ana", path.stem, original, "_librarian/fix33-" + path.name)["id"]
            c.execute("UPDATE docs SET created_by_id='bot:librarian',updated_by_id='bot:librarian' WHERE id=?", (doc_id,))
            rows[doc_id] = (path.name, "fix33 only", want, 1 if want == original else 2)
        c.execute("DELETE FROM registry_metadata WHERE key='librarian_fix33'")
    api.app.state.store.initialize(seed_market=False)
    check("fix33 alone")


def test_librarian_edits_of_docs_and_task_comments_change_only_ticos_terms(named):
    api = named
    _, attempt = turn(api, bot="librarian")            # the Librarian's own run, with its own credential
    tid = post(api, "tasks", {"owner_id": "librarian", "title": "Brightwater FAQ", "body": "Tidy the FAQ."})["id"]
    for path, original, want in _cases():
        for token, body in ((attempt["token"], want), ("ana-test", original)):
            who = "person" if token == "ana-test" else "librarian"
            doc = post(api, "docs", {"title": who + " " + path.stem, "body": "Draft."}, token)["doc"]
            edited = api.patch("/api/v2/docs/" + doc["id"], json={"version": doc["version"], "body": original},
                               headers=headers(token))
            assert edited.status_code == 200, (path.name, who, edited.text)
            read = api.get("/api/v2/docs/" + doc["id"], headers=headers()).json()["doc"]
            assert read["body"] == body, (path.name, who, "doc edit")      # byte for byte, ends included

            made = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Draft."}, headers=headers(token))
            assert made.status_code == 200, (path.name, who, made.text)
            mid = made.json()["comment"]["id"]
            edited = api.post(f"/api/v2/tasks/{tid}/comments/{mid}", json={"text": original}, headers=headers(token))
            assert edited.status_code == 200, (path.name, who, edited.text)
            comments = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers()).json()["comments"]
            # A comment trims its ends (models.Contract), unlike a doc body; the words in between are the test.
            assert next(m for m in comments if m["id"] == mid)["body"] == body.strip(), (path.name, who, "comment edit")


# 3. Display names: added beside Tico's actor ids, never into a team's words, on every spelling of a route.

def test_display_names_add_names_beside_ids_and_keep_every_body_as_written(named):
    api = named
    texts = [(p.name, _text(p)) for p in KEPT + REWRITTEN]
    texts.append(("ids in prose", "Ask bot:ops or human:ana; owner: human:ana is prose, not a field."))
    sent = {}
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Brightwater handover", texts[0][1], "bot:ops")
        for name, body in texts:
            sent[H.say(c, "bot:ops", "human:ana", body, conversation_id=task["conversation_id"],
                       refs={"task": task["id"]})["id"]] = body
        notice = H.notice(c, "bot:ops", "human:ana", "New task from bot:ops: Brightwater handover")
    doc = post(api, "docs", {"title": "Brightwater export", "body": _text(CORPUS / "kept" / "customer-export.json")})["doc"]

    # The inbox through the door route_renames.py adds (v0.3.10 lost its names), and the old door, which is not a
    # stable route: no names there, and the same words.
    for url in ("/api/v2/messages", "/api/v2/messages?unread=1", "/api/v2/inbox"):
        named_door = url.startswith("/api/v2/messages")
        result = api.get(url, headers=headers())
        assert result.status_code == 200, (url, result.text)
        inbox = result.json()
        mine = [m for m in inbox["messages"] if m["id"] in sent]
        assert len(mine) == len(sent), url
        for message in mine:
            assert message["body"] == sent[message["id"]], url
            assert "body_raw" not in message, url
            if named_door:
                assert message["from_actor_name"] == "Operations", url
                assert message["to_actor_name"] == "Ana Example", url
        shown = next(m for m in inbox["notices"] if m["id"] == notice["id"])
        if named_door:
            assert inbox["actors"] == {"bot:ops": "Operations", "human:ana": "Ana Example"}, url
            assert shown["body"] == "New task from Operations: Brightwater handover", url
            assert shown["body_raw"] == "New task from bot:ops: Brightwater handover", url
        else:
            assert shown["body"] == "New task from bot:ops: Brightwater handover", url

    read = api.get("/api/v2/tasks/" + task["id"], headers=headers()).json()
    assert read["task"]["body"] == texts[0][1]
    assert read["task"]["owner_name"] == "Operations" and read["task"]["requester_name"] == "Ana Example"
    shown = api.get("/api/v2/docs/" + doc["id"], headers=headers()).json()["doc"]
    assert shown["body"] == _text(CORPUS / "kept" / "customer-export.json")      # byte for byte, ends included


# 4. Stored files: every corpus file, as uploaded, from every read path.

def _files():
    out = [(p.name, p.read_bytes()) for p in KEPT + REWRITTEN]
    out += [(p.name + ".expected", p.with_name(p.name + ".expected").read_bytes()) for p in REWRITTEN]
    return out + sorted(BINARY.items())


@pytest.mark.parametrize("name,data", _files(), ids=[n for n, _ in _files()])
def test_stored_files_come_back_byte_for_byte_from_every_read_path(named, name, data):
    api = named
    tid = post(api, "tasks", {"owner_id": "ops", "title": "Brightwater files", "body": "The depot's files."})["id"]
    uploaded = api.post(f"/api/v2/tasks/{tid}/files", files={"file": (name, data)}, headers=headers())
    assert uploaded.status_code == 200, uploaded.text
    first = uploaded.json()["file"]["id"]
    legacy = post(api, f"tasks/{tid}/files", {"name": name, "content_base64": base64.b64encode(data).decode()})
    second = legacy["file"]["id"]
    with api.app.state.store.read() as c:
        series = c.execute("SELECT file_id FROM bot_file_versions WHERE blob_id=?", (first,)).fetchone()["file_id"]
    digest = hashlib.sha256(data).hexdigest()
    for url in (f"/api/v2/files/{first}", f"/api/v2/files/{second}", f"/api/v2/files/{series}",
                f"/api/v2/files/{series}?v=1", f"/api/v2/files/{series}/versions/1",
                f"/api/v2/files/{first}?download=1"):
        full = api.get(url, headers=headers())
        assert full.status_code == 200, (url, full.text)
        assert full.content == data, url
        assert full.headers["x-content-sha256"] == digest, url
        assert full.headers["content-length"] == str(len(data)), url
        if len(data) > 4:
            part = api.get(url, headers={**headers(), "Range": "bytes=1-3"})
            assert part.status_code == 206 and part.content == data[1:4], url
        head = api.head(url, headers=headers())
        assert head.status_code == 200 and head.content == b"", url
        assert head.headers["content-length"] == str(len(data)), url
