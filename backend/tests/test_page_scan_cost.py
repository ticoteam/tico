"""Finding page links costs time only for text that could name a page (perf P08), and finds exactly what the full parse
of every value found before: the same page ids and the same candidate identities, in the same order, for every form
a page can be named in."""

import json
import random
import re
from urllib.parse import unquote, urlsplit

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.page_audience import edit, promoted

FID = "file-" + "0123456789abcdef01234567"
OTHER = "file-" + "fedcba9876543210fedcba98"
DIGEST = "a" * 64
BLOB = "6f1c0a52-8d1e-4c43-9a57-3c9f1b6e2d10"


# ------------------------------------------------------------------ the old path, as main had it before this change
def old_page_reference(value):
    if not isinstance(value, str):
        return None
    try:
        value = unquote(value)
        url = urlsplit(value)
        if url.scheme in ("http", "https"):
            value = "#" + url.fragment if url.fragment else url.path
        match = re.fullmatch(
            r"(?:page:|#/page/|/(?:api/(?:v2/)?)?(?:pages|files)/)?"
            r"(file-[^/?#@\s]{24})(?:@\d+|/(?:versions(?:/\d+)?|meta|thumb|poster|activity))?(?:[?][^#]*)?",
            value,
        )
        return match[1] if match else None
    except ValueError:
        return None


def old_names(value, file_ids=None):
    found, names = set(), []

    def collect(part, explicit=None):
        if isinstance(part, str):
            fid = old_page_reference(part)
            if fid:
                found.add(fid)
            candidate = unquote(part)
            try:
                url = urlsplit(candidate)
                if url.scheme in ("http", "https"):
                    candidate = url.path
                candidate = re.sub(r"^/(?:api/(?:v2/)?)?files/", "", candidate)
                candidate = re.sub(r"^(?:page|file|blob):", "", candidate)
                names.append((re.split(r"[/?@]", candidate, maxsplit=1)[0], explicit))
            except ValueError:
                pass
            if part.startswith(("{", "[")):
                collect(H._json(part, None), explicit)
        elif isinstance(part, dict):
            ids = {fid for key in ("file", "file_id", "page", "page_id") if (fid := old_page_reference(part.get(key)))}
            for child in part.values():
                collect(child, ids or explicit)
        elif isinstance(part, (list, tuple)):
            for child in part:
                collect(child, explicit)

    collect(value, file_ids)
    return found, names


# ------------------------------------------------------------------------------------------- every form, and near ones
FORMS = [
    # a page named outright, in every form today's recognizer reads
    FID,
    FID + "@3",
    FID + "/versions",
    FID + "/versions/3",
    FID + "/meta",
    FID + "/thumb",
    FID + "/poster",
    FID + "/activity",
    FID + "?version=2",
    "page:" + FID,
    "page:" + FID + "@1",
    "#/page/" + FID,
    "#/page/" + FID + "?version=1",
    "/pages/" + FID,
    "/files/" + FID,
    "/api/pages/" + FID,
    "/api/v2/pages/" + FID,
    "/api/v2/files/" + FID,
    "/api/v2/files/" + FID + "/versions/3",
    "https://acme.example/app/#/page/" + FID,
    "https://acme.example/#/page/" + FID + "?version=1",
    "http://acme.example/api/v2/files/" + FID,
    "HTTPS://acme.example/api/v2/pages/" + FID,
    " https://acme.example/#/page/" + FID,
    "ht\ttps://acme.example/#/page/" + FID,
    # urlsplit drops every tab, CR and LF before it splits, so these still name the page
    "https://acme.example/#/page/fi\tle-" + FID[5:],
    "https://acme.example/#/page/file\n-" + FID[5:],
    "https://acme.example/pages/fil\re-" + FID[5:],
    "/pages/file\n-" + FID[5:],
    "page:fi\rle-" + FID[5:],
    "fi\tle-" + FID[5:],
    "page%3A" + FID,
    "%66ile-0123456789abcdef01234567",
    "%2Fapi%2Fv2%2Ffiles%2F" + FID,
    # bytes, previews and digests: candidates only, resolved by the lookup
    DIGEST,
    DIGEST.upper(),
    BLOB,
    "blob:" + BLOB,
    "file:" + BLOB,
    "page:" + DIGEST,
    "/api/v2/files/" + BLOB,
    "/files/" + DIGEST + "/raw",
    BLOB + "?download=1",
    BLOB + "@2",
    "https://acme.example/api/v2/files/" + BLOB,
    "http:" + BLOB,
    "HTTP:" + BLOB,
    "Https:" + DIGEST,
    "\nhttp:" + BLOB,
    "h\ttp:" + BLOB,
    "logo-p",
    "blob-bench00000001-001",
    # JSON inside a string
    json.dumps({"file": FID, "text": "see it"}),
    json.dumps([{"page_id": FID + "@2"}, DIGEST]),
    json.dumps({"refs": json.dumps({"files": [FID]})}),
    "[1, 2, 3]",
    "{not json",
    "[",
    # ordinary text: none of these names a page
    "",
    " ",
    "open",
    "Weekly numbers",
    "2026-11-13T04:24:49.203726Z",
    "2026-11-13 04:24:49",
    "bot:ops",
    "human:ana",
    "task:" + BLOB,
    "conversation:" + BLOB,
    "page:",
    "file:",
    "blob:",
    "blob:x?y",
    "page:x@y",
    "Can you check? It is late.",
    "Mail ana@acme.example about it",
    "and/or",
    "50% done",
    "#3",
    "a:b:c",
    "1:2",
    ":leading colon",
    "-x:y",
    "Ünïcode: Ελληνικά",
    "https://acme.example/blog/post?x=1",
    "http://[::1",
    "http://ex℀ample.com/x",
    "mailto:ana@acme.example",
    "file-short",
    "file-0123456789abcdef0123456",
    "the file-0123456789abcdef01234567 is here",
    "\x00http:" + BLOB,
    "tab\there: x",
]


def fuzz(seed, count):
    """Strings built from the pieces the recognizer reads, in random order, to reach the corners the list misses."""
    rng = random.Random(seed)
    pieces = [
        "http", "https", "HTTP", ":", "//", "/", "?", "@", "#", "%", "%3A", "%2F", "%66", "file-", FID, BLOB, DIGEST,
        "page:", "blob:", "file:", "/api/v2/files/", "/pages/", "#/page/", "versions", "3", " ", "\t", "\n", "\x01",
        "acme.example", "[", "]", "{", "}", '"', ",", "ana", "Ω", "℀",
        "fi", "le-", "\r", FID[5:],  # a "file-" split by a tab or newline is reachable
    ]  # fmt: skip
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 6))) for _ in range(count)]


def answer():
    """An answer shaped like a task read: comments, events and messages, with pages named under the keys and in text."""
    return {
        "task": {"id": BLOB, "title": "Weekly review", "created_at": "2026-11-13T04:24:49Z", "owner": "bot:ops"},
        "comments": [{"id": str(i), "text": text, "actor": "human:ana"} for i, text in enumerate(FORMS)],
        "events": [
            {"kind": "file", "detail": {"file_id": FID, "digest": DIGEST, "thumb": BLOB}},
            {"kind": "page", "detail": json.dumps({"page": OTHER + "@2", "blob": BLOB})},
            {"kind": "asked", "options": [{"label": "Yes", "source": "page:" + FID}, ("tuple", DIGEST)]},
        ],
        "messages": [{"body": s, "refs_json": json.dumps({"file": FID})} for s in fuzz(1, 50)],
    }


def test_every_form_finds_what_the_full_parse_found():
    for value in FORMS + fuzz(7, 4000):
        assert files.page_reference(value) == old_page_reference(value), repr(value)
        assert files._names(value) == old_names(value), repr(value)
        assert files._names(value, {OTHER}) == old_names(value, {OTHER}), repr(value)
    assert files._names(answer()) == old_names(answer())
    assert files._names(answer(), {OTHER}) == old_names(answer(), {OTHER})


def test_plain_text_is_not_parsed(monkeypatch):
    """An answer naming no page (ids, times, actors, sentences) never reaches the URL parser or the unescaper: the
    cost of a scan is a look at each value, and the parse only for text that could name a page."""
    calls = []
    if kept := getattr(files, "_cached_string_names", None):
        kept.cache_clear()  # a scan kept from another test would hide the parse
    monkeypatch.setattr(files, "urlsplit", lambda *a, **k: calls.append(a) or urlsplit(*a, **k))
    monkeypatch.setattr(files, "unquote", lambda *a, **k: calls.append(a) or unquote(*a, **k))
    plain = {
        "task": {"id": BLOB, "title": "Weekly review", "status": "open", "created_at": "2026-11-13T04:24:49Z"},
        "comments": [
            {
                "id": f"{i:08d}-8d1e-4c43-9a57-3c9f1b6e2d10",
                "actor": "human:ana",
                "text": f"Update {i}. The totals match the ledger, except two refunds. Can you check? Thanks.",
                "created_at": f"2026-11-13T04:{i % 60:02d}:49.203726Z",
                "count": i,
                "flags": [True, None, 1.5],
            }
            for i in range(500)
        ],
    }
    found, names = files._names(plain)
    assert found == set() and len(names) > 2000
    assert calls == []
    # A value that does name a page is still parsed, and found.
    assert files._names({"text": "page:" + FID})[0] == {FID}
    assert calls


def old_marked(value):
    """readable_page_refs' two walks before this change: legacy_labels(refs) or removed_page(refs)."""

    def removed_page(value):
        if isinstance(value, dict):
            return (
                value.get("removed") is True
                and value.get("label") in ("A page you can't open", "A deleted page")
                or any(removed_page(v) for v in value.values())
            )
        return isinstance(value, list) and any(removed_page(v) for v in value)

    def legacy_labels(value):
        if isinstance(value, dict):
            return (
                isinstance(value.get("answers"), dict)
                or any(
                    not option.get("source") and not option.get("file")
                    for option in value.get("options") or []
                    if isinstance(option, dict)
                )
                or any(legacy_labels(v) for v in value.values())
            )
        return isinstance(value, list) and any(legacy_labels(v) for v in value)

    return legacy_labels(value) or removed_page(value)


def test_clean_up_is_needed_exactly_when_it_was():
    deep = {"a": [{"b": {"c": [{"removed": True, "label": "A deleted page"}]}}]}
    values = [
        answer(),
        deep,
        {"removed": True, "label": "A page you can't open"},
        {"removed": True, "label": "Something else"},
        {"removed": "yes", "label": "A deleted page"},
        {"answers": {"q1": ["Yes"]}},
        {"answers": ["Yes"]},
        {"options": [{"label": "Yes"}]},
        {"options": [{"label": "Yes", "source": "page:" + FID}, {"label": "No", "file": FID}]},
        {"options": ["Yes", "No"]},
        {"options": []},
        {"options": None},
        {"x": ({"removed": True, "label": "A deleted page"},)},  # a tuple is not walked, as before
        [[{"options": [{"label": "Maybe", "source": ""}]}]],
        "page:" + FID,
        None,
    ]
    for value in values:
        assert files._marked(value) == old_marked(value), repr(value)


def test_a_link_split_by_a_tab_or_newline_still_hides_the_page(api):
    """urlsplit drops tabs and newlines, so a wrapped link names the page as it did before: a reader who may not open
    it gets the placeholder, not the link and the page's title."""
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["ben"]})
    split = "https://acme.example/#/page/fi\tle-" + fid[5:]
    refs = [
        {"page": split, "title": "Secret plan"},
        {"text": "see", "link": split},
        {"page": "https://acme.example/pages/file\n-" + fid[5:] + "@1", "title": "Secret plan"},
    ]
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        files.prime_page_references(c, [refs])
        shown = files.readable_page_refs(c, auth, Identity("human:cara", "human", "cara@acme.example"), refs)
        assert shown == [{"removed": True, "label": "A page you can't open"}] * 3
        assert files.readable_page_refs(c, auth, Identity("human:ben", "human", "ben@acme.example"), refs) == refs
