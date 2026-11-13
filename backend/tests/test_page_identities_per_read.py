"""A read looks up only the page identities its response names (perf I05), and answers exactly as reading the whole
page graph did: the same pages allowed and denied, with the same labels, for every reader."""

import json
import re
import sqlite3
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit

from backend import files, read_cache
from backend.auth import Identity
from backend.file_library import purge_rows
from backend.store import H
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.files import publish, turn
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import page

READERS = (
    Identity("human:ana", "owner", "ana@acme.example"),
    Identity("human:ben", "human", "ben@acme.example"),
    Identity("human:cara", "human", "cara@acme.example"),
    Identity("bot:ops", "bot"),
    Identity("bot:finance", "bot"),
)
IDENTITY_COLUMNS = ("blob_id", "poster_blob_id", "thumb_blob_id", "digest", "source_digest")
WHOLE = re.compile(
    r"\bSCAN (?:bot_file_versions|bot_files|pages|page_identities|[vfp])\b"
)  # a full index scan reads every entry too
TABLES = re.compile(r"\b(?:bot_file_versions|bot_files|pages|page_identities)\b")


# ------------------------------------------------------------------ the old path, as main had it before this change
def old_graph(c):
    """Every page row, alias, tombstone and restricted page, read the way files.py read them per snapshot before."""
    rows = {r["id"]: dict(r) for r in c.execute("SELECT f.* FROM bot_files f JOIN pages p ON p.file_id=f.id")}
    found = {}

    def add(identity, fid):
        if identity:
            found.setdefault(identity, set()).add(fid)

    for fid in rows:
        add(fid, fid)
    for r in c.execute("SELECT v.* FROM bot_file_versions v JOIN pages p ON p.file_id=v.file_id"):
        for key in IDENTITY_COLUMNS:
            add(r[key], r["file_id"])
    for r in c.execute("SELECT identity,file_id FROM page_identities"):
        add(r[0], r[1])
    live = {}
    digests = [
        identity
        for identity, ids in found.items()
        if re.fullmatch(r"[0-9a-f]{64}", identity) and any(rows.get(fid, {}).get("state") == "deleted" for fid in ids)
    ]
    for chunk in H.chunks(digests):
        marks = ",".join("?" * len(chunk))
        for r in c.execute(
            "SELECT f.*,v.digest,v.source_digest FROM bot_file_versions v "
            "JOIN bot_files f ON f.id=v.file_id WHERE f.state<>'deleted' "
            f"AND (v.digest IN ({marks}) OR v.source_digest IN ({marks}))",
            (*chunk, *chunk),
        ):
            context = ("task", r["task_id"]) if r["task_id"] else (r["bot_id"], r["scope"], r["conversation_id"])
            for key in ("digest", "source_digest"):
                if r[key]:
                    live.setdefault(r[key], set()).add(context)
    for identity, contexts in live.items():
        for fid in list(found.get(identity, ())):
            row = rows.get(fid)
            if row and row["state"] == "deleted":
                context = (
                    ("task", row["task_id"])
                    if row["task_id"]
                    else (row["bot_id"], row["scope"], row["conversation_id"])
                )
                if context in contexts:
                    found[identity].discard(fid)
    deleted = {
        r[0]
        for r in c.execute(
            "SELECT file_id FROM pages WHERE deleted_at IS NOT NULL "
            "UNION SELECT id FROM bot_files WHERE state='deleted' AND lower(mime) LIKE 'text/html%'"
        )
    }
    restricted = {
        r["id"]: dict(r)
        for r in c.execute(
            "SELECT f.*,p.audience FROM pages p JOIN bot_files f ON f.id=p.file_id WHERE p.audience IS NOT NULL"
        )
    }
    return rows, found, deleted, restricted


def old_page_references(aliases, value, c=None, *, file_ids=None):
    """files.page_references before this change, over the whole alias map."""
    found = set()

    def collect(part, explicit=None):
        if isinstance(part, str):
            fid = files.page_reference(part)
            if fid:
                found.add(fid)
            candidate = unquote(part)
            try:
                url = urlsplit(candidate)
                if url.scheme in ("http", "https"):
                    candidate = url.path
                candidate = re.sub(r"^/(?:api/(?:v2/)?)?files/", "", candidate)
                candidate = re.sub(r"^(?:page|file|blob):", "", candidate)
                candidate = re.split(r"[/?@]", candidate, maxsplit=1)[0]
                resolved = aliases.get(candidate, ()) if c is not None else ()
                if (
                    resolved
                    and explicit
                    and not set(resolved).issubset(explicit)
                    and re.fullmatch(r"[0-9a-f]{64}", candidate)
                ):
                    live = {
                        fid
                        for fid in explicit
                        if c.execute(
                            "SELECT 1 FROM bot_file_versions v JOIN bot_files f ON f.id=v.file_id "
                            "WHERE v.file_id=? AND f.state<>'deleted' AND (v.digest=? OR v.source_digest=?) LIMIT 1",
                            (fid, candidate, candidate),
                        ).fetchone()
                    }
                    if live:
                        resolved = set(resolved) & live
                found.update(resolved)
            except ValueError:
                pass
            if part.startswith(("{", "[")):
                collect(H._json(part, None), explicit)
        elif isinstance(part, dict):
            ids = {
                fid for key in ("file", "file_id", "page", "page_id") if (fid := files.page_reference(part.get(key)))
            }
            for child in part.values():
                collect(child, ids or explicit)
        elif isinstance(part, (list, tuple)):
            for child in part:
                collect(child, explicit)

    collect(value, file_ids)
    return found


@contextmanager
def old_path(monkeypatch, c):
    rows, found, deleted, restricted = old_graph(c)
    with monkeypatch.context() as m:
        m.setattr(files, "page_references", lambda value, c=None, **kw: old_page_references(found, value, c, **kw))
        m.setattr(files, "page_rows", lambda c, ids: {i: rows[i] for i in ids if i in rows})
        m.setattr(files, "deleted_page_refs", lambda c, ids: {i for i in ids if i in deleted})
        m.setattr(files, "restricted_page_rows", lambda c, ids: {i: restricted[i] for i in ids if i in restricted})
        yield


# ------------------------------------------------------------------ the fixture
def html(text):
    return f"<!doctype html><html><body><h1>{text}</h1></body></html>".encode()


def made(api, attempt, task, name, data):
    r = page(api, attempt, name=name, task=task, title=name, data=data)
    assert r.status_code == 200, r.text
    return r.json()["page"]["file_id"]


def company(api):
    """Live pages, a restricted page, reused bytes in two contexts, deleted pages whose digest a live file in the same
    context or another one still has, purged versions, previews, a source digest and an ordinary file."""
    _, attempt = turn(api)
    one = post(api, "tasks", {"owner": "ops", "title": "One", "body": "b", "private": False})["id"]
    two = post(api, "tasks", {"owner": "ops", "title": "Two", "body": "b", "private": True})["id"]
    p = {
        "live": made(api, attempt, one, "live.html", html("live")),
        "restricted": made(api, attempt, one, "restricted.html", html("restricted")),
        "reused_one": made(api, attempt, one, "reused-a.html", html("reused")),
        "reused_two": made(api, attempt, two, "reused-b.html", html("reused")),
        "gone_here": made(api, attempt, one, "gone.html", html("gone here")),
        "twin_here": made(api, attempt, one, "twin.html", html("gone here")),
        "gone_there": made(api, attempt, one, "gone-there.html", html("gone there")),
        "twin_there": made(api, attempt, two, "twin-there.html", html("gone there")),
        "gone_alone": made(api, attempt, one, "alone.html", html("alone")),
    }
    made(api, attempt, one, "live.html", html("live, second version"))
    edit(api, p["restricted"], {"people": ["ben"]})
    ordinary = publish(api, attempt, name="plan.md", text="# Plan").json()
    p["ordinary"] = ordinary.get("file_id") or ordinary.get("file", {}).get("id")
    store, now = api.app.state.store, H.now()
    with store.transaction() as c:
        for bid in ("blob-poster-live", "blob-thumb-live", "blob-source-live"):
            c.execute(
                "INSERT INTO blobs(id,owner,digest,size,name,content_type,created_at) VALUES(?,'bot:ops',?,1,'x','image/png',?)",
                (bid, "0" * 64, now),
            )
        c.execute(
            "UPDATE bot_file_versions SET poster_blob_id='blob-poster-live',thumb_blob_id='blob-thumb-live' "
            "WHERE file_id=? AND version=1",
            (p["live"],),
        )
        c.execute(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,source_digest,size,name,mime,actor,created_at) "
            "VALUES(?,3,'blob-source-live',?,?,1,'live.html','text/html','bot:ops',?)",
            (p["live"], "a" * 64, "b" * 64, now),
        )
        c.execute("UPDATE bot_files SET current_version=3 WHERE id=?", (p["live"],))
        versions = [dict(r) for r in c.execute("SELECT * FROM bot_file_versions")]
    for name in ("gone_here", "gone_there", "gone_alone"):
        r = api.delete("/api/v2/files/" + p[name], headers=headers())
        assert r.status_code == 200, r.text
    with store.transaction() as c:
        # A purge kept by hand too, the way an older delete left it.
        row = c.execute("SELECT * FROM bot_files WHERE id=?", (p["twin_there"],)).fetchone()
        if row["state"] != "deleted":
            purge_rows(c, "human:ana", row)
    return p, versions, one, attempt


def references(pages, versions):
    out = []
    for fid in pages.values():
        out += [
            fid,
            fid + "@1",
            "page:" + fid,
            "file:" + fid,
            "#/page/" + fid,
            "https://acme.tico.team/#/page/" + fid,
            "/api/v2/files/" + fid + "/versions/1",
            "/api/v2/pages/" + fid,
        ]
    identities = sorted({v[key] for v in versions for key in IDENTITY_COLUMNS if v[key]})
    for identity in identities:
        out += [identity, "blob:" + identity, identity + "@1", "/api/v2/files/" + identity + "/thumb?v=1"]
    digests = sorted({v[key] for v in versions for key in ("digest", "source_digest") if v[key]})
    for fid in pages.values():
        for digest in digests:
            out.append({"file": fid, "digest": digest})
            out.append(json.dumps({"page_id": fid, "sha": digest}))
    out.append({"comments": [{"text": "see page:" + fid} for fid in pages.values()], "digests": digests})
    out.append(["unrelated text", "file-" + "0" * 24, "0" * 64])
    return out, identities


def answers(api, refs, identities, task, primed=False):
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        if primed:
            files.prime_page_references(c, refs)  # as a list read does before checking each item
        return {
            "references": [sorted(files.page_references(ref, c)) for ref in refs],
            "blobs": [sorted(r["id"] for r in files.page_blobs(c, bid)) for bid in identities],
            "denials": [
                [files.page_ref_denials(c, a, who, ref) for ref in refs] for who in READERS for a in (auth, None)
            ],
            "shown": [
                [files.readable_page_refs(c, auth, who, ref, task_id=task) for ref in refs if isinstance(ref, dict)]
                for who in READERS
            ],
            "blob_visible": [[files.page_blob_visible(c, auth, who, bid) for bid in identities] for who in READERS],
        }


def test_every_reader_gets_the_same_answers_as_reading_the_whole_page_graph(api, monkeypatch):
    pages, versions, task, _ = company(api)
    refs, identities = references(pages, versions)
    new = answers(api, refs, identities, task)
    with api.app.state.store.read() as c, old_path(monkeypatch, c):
        old = answers(api, refs, identities, task)
    for key in new:
        assert new[key] == old[key], key
    labels = {label for per_reader in new["denials"] for per_ref in per_reader for label in per_ref.values()}
    # The fixture reaches both labels, allowed pages and the deleted-digest rule both ways.
    assert labels == {"A deleted page", "A page you can't open"}
    assert any(not d for per_reader in new["denials"] for d in per_reader)
    with api.app.state.store.read() as c:
        twin = c.execute("SELECT digest FROM bot_file_versions WHERE file_id=?", (pages["twin_here"],)).fetchone()[0]
        assert files.page_references(twin, c) == {pages["twin_here"]}  # the live file in the same context wins
        alone = [v["digest"] for v in versions if v["file_id"] == pages["gone_alone"]][0]
        assert files.page_references(alone, c) == {pages["gone_alone"]}  # a purged page keeps its alias


def test_priming_first_gives_every_reader_the_same_answers(api):
    """A read that primes its values first (a task, the inbox, threads) checks them through the record priming keeps,
    and every form, reader and label comes out as without it."""
    pages, versions, task, _ = company(api)
    refs, identities = references(pages, versions)
    assert answers(api, refs, identities, task, primed=True) == answers(api, refs, identities, task)


def test_a_page_named_only_by_its_bytes_stays_hidden_after_priming(api):
    pages, versions, _, _ = company(api)
    version = next(v for v in versions if v["file_id"] == pages["restricted"])
    refs = [
        {"text": "see ", "ref": name, "title": "Secret"}
        for name in (version["blob_id"], version["digest"], "blob:" + version["blob_id"])
    ]
    cara = Identity("human:cara", "human", "cara@acme.example")
    with api.app.state.store.read() as c:
        files.prime_page_references(c, refs)
        for ref in refs:
            assert files.readable_page_refs(c, api.app.state.auth, cara, ref) == {
                "removed": True,
                "label": "A page you can't open",
            }, ref


def test_page_checks_read_no_whole_page_table_and_a_fixed_number_of_statements(api):
    pages, versions, task, attempt = company(api)
    refs, identities = references(pages, versions)
    store, auth = api.app.state.store, api.app.state.auth
    many = [made(api, attempt, task, f"many-{n}.html", html(f"many {n}")) for n in range(30)]
    who = Identity("human:cara", "human", "cara@acme.example")

    def statements(check):
        seen = []
        with store.read() as c:
            c.set_trace_callback(seen.append)
            try:
                check(c)
            finally:
                c.set_trace_callback(None)
        return seen

    traced = statements(
        lambda c: (
            [
                (
                    files.page_ref_denials(c, auth, who, ref),
                    files.readable_page_refs(c, auth, who, ref, task_id=task),
                    files.page_event_readable(c, auth, who, task, {"file": pages["live"]}),
                )
                for ref in refs
            ]
            + [files.page_blob_visible(c, auth, who, bid) for bid in identities]
        )
    )
    with sqlite3.connect(store.settings.db_path) as c:
        indexes = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE tbl_name='bot_file_versions'")}
        assert {"bot_file_versions_digest", "bot_file_versions_source_digest"} <= indexes  # a new database has them
        whole = []
        for q in traced:
            if TABLES.search(q) and q.lstrip().upper().startswith(("SELECT", "WITH")):
                plan = " | ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q))
                if WHOLE.search(plan):
                    whole.append((plan, q))
    assert not whole, whole
    one = statements(lambda c: files.page_ref_denials(c, auth, who, {"comments": [{"text": "page:" + many[0]}]}))
    thirty = statements(
        lambda c: files.page_ref_denials(c, auth, who, {"comments": [{"text": "page:" + fid} for fid in many]})
    )
    assert len(one) == len(thirty), (one, thirty)


def test_a_message_list_looks_its_pages_up_once(api):
    _, attempt = turn(api)
    task = post(api, "tasks", {"owner": "ops", "title": "One", "body": "b", "private": False})["id"]
    pages = [made(api, attempt, task, f"p{n}.html", html(f"p {n}")) for n in range(12)]
    messages = [{"id": f"m{n}", "text": "see page:" + fid, "refs": {"file": fid}} for n, fid in enumerate(pages)]
    store, lookups = api.app.state.store, []
    with store.read() as c:
        c.set_trace_callback(lambda q: lookups.append(q) if "page_identities WHERE identity IN" in q else None)
        files.prime_page_references(c, messages)
        for message in messages:
            files.readable_page_refs(c, api.app.state.auth, Identity("human:cara", "human"), message)
        c.set_trace_callback(None)
        assert read_cache.cache(c) is not None
    assert len(lookups) == 1, lookups


def test_a_failed_lookup_is_not_remembered_as_allowed(api, monkeypatch):
    """A read that fails part way must not leave its ids marked "not restricted" or "not a tombstone" for a retry in
    the same snapshot."""
    pages, _, _, _ = company(api)
    restricted, gone = pages["restricted"], pages["gone_alone"]

    class Broken:
        @staticmethod
        def dumps(value):
            raise RuntimeError("the lookup failed")

    with api.app.state.store.read() as c:
        monkeypatch.setattr(files, "json", Broken)
        for lookup in (files.restricted_page_rows, files.deleted_page_refs, files.page_rows):
            try:
                lookup(c, [restricted, gone])
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{lookup.__name__} did not fail")
        monkeypatch.setattr(files, "json", json)
        assert read_cache.cache(c) is not None
        assert set(files.restricted_page_rows(c, [restricted, gone])) == {restricted}
        assert files.deleted_page_refs(c, [restricted, gone]) == {gone}
        assert set(files.page_rows(c, [restricted, gone])) == {restricted, gone}


def test_a_task_scoped_page_without_a_task_id_still_hides_its_title(api):
    """A page whose scope names the task but whose task_id is empty is still the task's page, as the whole-graph rule
    (`task_id == tid or scope == "task:" + tid`) had it: its title stays hidden from a reader it denies."""
    pages, _, task, _ = company(api)
    fid = pages["restricted"]
    with api.app.state.store.transaction() as c:
        title, scope = c.execute("SELECT title, scope FROM bot_files WHERE id=?", (fid,)).fetchone()
        assert scope == "task:" + task
        c.execute("UPDATE bot_files SET task_id=NULL WHERE id=?", (fid,))
        # Only the scope ties it to the task: no attachment and no pin.
        c.execute(
            "DELETE FROM task_assets WHERE blob_id IN (SELECT blob_id FROM bot_file_versions WHERE file_id=?)", (fid,)
        )
        c.execute("DELETE FROM pins WHERE item=?", ("page:" + fid,))
    question = {"questions": [{"id": "q", "options": [{"label": title}, {"label": "Other"}]}]}
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        shown = files.readable_page_refs(c, auth, Identity("human:cara", "human"), question, task_id=task)
        allowed = files.readable_page_refs(c, auth, Identity("human:ben", "human"), question, task_id=task)
    assert title not in json.dumps(shown)
    assert title in json.dumps(allowed)
