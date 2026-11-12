# pyright: strict
"""The linker (spec §7.1, steps 1 and 2): after a text is written, it links the text to the entities it is about.

Step 1, the run trace: the entities a bot's attempt read with `tico contacts get` or `rules` (store.run_lookups) are the
subjects of that attempt's text, role subject, with no quote.

Step 2, exact match: an email, a domain (a bare one, or a URL's host) or a name an entity has (its name or an alias,
case-insensitive, one or more words) that names exactly one entity is a mention of it. A name two entities share is
ambiguous, and it links nothing, even when one of them is hidden from the reader. Models (step 3) and person links are not
here.

Only what a company member may see is linked (store.company_visible): a link is never shown to a reader of the text that
the reader may not follow. A mention is standoff: the entity, the quote as written, the occurrence (the nth
token-aligned occurrence of that quote in the text, counting from 0; every occurrence counts, linked or not), the
confidence and how it was found. A mention below CUTOFF is `suggested` and renders as plain text.

A rerun on the same text writes the same mentions. A new text replaces the linker's mentions for the old one, and a
mention another method wrote (an inline link a person typed, a person's merge) stays.

run_pass is the background step (backend/heartbeat.py `entity_links`): each beat it reads the texts written or edited since
its cursors, matches them as one batch and writes them in one short transaction. The cursors are monotonic keys (messages.seq,
(created_at, id) of message_revisions, (updated_at, id) of docs, the id of meeting_versions), so a row is found again after
an edit and a VACUUM cannot make the cursor skip rows.
"""

import hashlib
import json
import re
import sqlite3
from bisect import bisect_left
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from backend.entities import store as ES

CUTOFF = 0.6  # a mention below this is `suggested`: it renders as plain text and feeds search only
RUN_TRACE_CONFIDENCE = 0.9
MAX_TEXT = 100_000  # characters matched per text; the rest of a longer text is not matched
MAX_CANDIDATES = 3000  # candidates per text, in document order
MIN_NAME = 4  # characters: a shorter name is never matched, alone or with others
MAX_NAME_WORDS = 4
BATCH = 100  # texts per source per beat

# Words that are never a name on their own or at the end of one: a sentence's common words, and the business words every
# company writes ("team", "sales", "meeting") that name a class of thing, not one entity.
STOP = frozenset(
    """a about after all also an and any are as at be before but by can could company did do does for from get group
    has have hello hi how i if in inc into is it its llc ltd meeting more most must new no not now of on one or our out
    over please corp co sales some support such team task tasks thank thanks that the their them then there these they
    this today tomorrow to us very was we were what when which will with would yes you your yesterday""".split()
)
SUFFIX = frozenset({"co", "corp", "inc", "llc", "ltd"})  # a company's last word, which a multi-word name may end with
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
URL = re.compile(r"https?://[^\s<>\"'()\[\]]+")
DOMAIN = re.compile(r"(?<![\w@./-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w@-])")
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
START: dict[str, list[Any]] = {
    "message": [0],
    "message_revision": ["", ""],
    "doc": ["", ""],
    "meeting_version": [0],
}
SOURCES = tuple(START)


@dataclass(frozen=True)
class Candidate:
    kind: str  # email | domain | name
    key: str  # the normalized value entity_identifiers stores: lower case, a bare domain, a name's words
    start: int
    end: int
    quote: str  # as written
    words: int  # a name's word count; 1 for the other kinds


@dataclass(frozen=True)
class Text:
    mentionable_type: str  # message | doc | meeting
    mentionable_id: str
    revision: str  # a hash of the text, so a rerun on the same text is the same revision
    body: str
    run_id: str | None = None  # the attempt that wrote the text, when it recorded one


def revision_of(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _host(raw: str) -> str | None:
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def name_candidates(body: str) -> list[Candidate]:
    """Runs of one to MAX_NAME_WORDS words, separated by spaces or tabs only, that could be a name: the first word starts
    with a capital, no run starts or ends with a common word, and a single word is at least MIN_NAME characters."""
    words = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(body)]
    out: list[Candidate] = []
    for i, (start, _, first) in enumerate(words):
        if not first[:1].isupper() or first.lower() in STOP:
            continue
        for count in range(1, MAX_NAME_WORDS + 1):
            j = i + count - 1
            if j >= len(words):
                break
            if count > 1:
                separator = body[words[j - 1][1] : words[j][0]]
                if not separator or separator.strip(" \t"):
                    break
            last = words[j][2].lower()
            if last in STOP and not (count > 1 and last in SUFFIX):  # "Quiet Co" ends in a suffix, and that is its name
                continue
            key = " ".join(word.lower() for _, _, word in words[i : j + 1])
            if len(key) < MIN_NAME:
                continue
            end = words[j][1]
            out.append(Candidate("name", key, start, end, body[start:end], count))
    return out


def candidates(text: str) -> list[Candidate]:
    """Every candidate in a text, in document order. An email's domain and a URL's text are not also domains of their own."""
    body = text[:MAX_TEXT]
    out: list[Candidate] = []
    covered: list[tuple[int, int]] = []
    for m in EMAIL.finditer(body):
        out.append(Candidate("email", m.group(0).lower(), m.start(), m.end(), m.group(0), 1))
        covered.append(m.span())
    for m in URL.finditer(body):
        raw = m.group(0).rstrip(".,;:!?")
        host = _host(raw)
        covered.append((m.start(), m.start() + len(raw)))
        if host:
            out.append(Candidate("domain", host, m.start(), m.start() + len(raw), raw, 1))
    for m in DOMAIN.finditer(body):
        if any(start <= m.start() < end for start, end in covered):
            continue
        key = m.group(0).lower()
        out.append(Candidate("domain", key[4:] if key.startswith("www.") else key, m.start(), m.end(), m.group(0), 1))
    out += name_candidates(body)
    out.sort(key=lambda cand: (cand.start, -(cand.end - cand.start)))
    return out[:MAX_CANDIDATES]


def _occurrence(body: str, cand: Candidate, starts: dict[str, list[int]]) -> int:
    """The token-aligned occurrence of the candidate's quote in the text, ignoring case, counting from 0. `starts` caches
    each quote's occurrences for one text, so a quote is searched once however often it is linked."""
    key = cand.quote.lower()
    if key not in starts:
        pattern = r"(?<!\w)" + re.escape(cand.quote) + r"(?!\w)"
        starts[key] = [m.start() for m in re.finditer(pattern, body, re.IGNORECASE)]
    return bisect_left(starts[key], cand.start)


def _confidence(cand: Candidate) -> float:
    if cand.kind in ("email", "domain"):
        return 1.0
    return 0.7 if cand.words > 1 else 0.5


def _status(confidence: float) -> str:
    return "linked" if confidence >= CUTOFF else "suggested"


def _run_rows(run_ids: Sequence[str], visible: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": entity_id,
            "role": "subject",
            "quote": "",
            "occurrence": 0,
            "confidence": RUN_TRACE_CONFIDENCE,
            "method": "run_trace",
            "status": _status(RUN_TRACE_CONFIDENCE),
        }
        for entity_id in run_ids
        if entity_id in visible
    ]


def _exact_rows(
    body: str,
    cands: Sequence[Candidate],
    found: dict[tuple[str, str], list[str]],
    visible: set[str],
) -> list[dict[str, Any]]:
    """The matched candidates, longest first and then earliest, each taking its span: a span already taken (by a match, an
    ambiguous one or a hidden one) blocks the candidates that overlap it. Only a span with one visible entity is a mention."""
    matched = [index for index, cand in enumerate(cands) if (cand.kind, cand.key) in found]
    matched.sort(key=lambda index: (-(cands[index].end - cands[index].start), cands[index].start))
    taken: list[tuple[int, int]] = []
    starts: dict[str, list[int]] = {}
    rows: list[dict[str, Any]] = []
    for index in matched:
        cand = cands[index]
        if any(cand.start < end and start < cand.end for start, end in taken):
            continue
        taken.append((cand.start, cand.end))
        ids = found[(cand.kind, cand.key)]
        if len(ids) != 1 or ids[0] not in visible:
            continue
        confidence = _confidence(cand)
        rows.append(
            {
                "entity_id": ids[0],
                "role": "mentioned",
                "quote": cand.quote,
                "occurrence": _occurrence(body, cand, starts),
                "confidence": confidence,
                "method": "exact",
                "status": _status(confidence),
            }
        )
    return rows


def plan(c: sqlite3.Connection, texts: Sequence[Text]) -> list[list[dict[str, Any]]]:
    """The mention rows each text should have, matched as one batch. Reads only: the caller writes them."""
    cands_by_text = [candidates(text.body) for text in texts]
    pairs = [(cand.kind, cand.key) for cands in cands_by_text for cand in cands]
    found = ES.identifier_entities(c, pairs)
    runs = [ES.run_lookups(c, text.run_id) if text.run_id else [] for text in texts]
    ids = [entity_id for entities in found.values() for entity_id in entities]
    ids += [entity_id for run in runs for entity_id in run]
    visible = ES.company_visible(c, ids)
    return [
        [*_run_rows(run, visible), *_exact_rows(text.body[:MAX_TEXT], cands, found, visible)]
        for text, cands, run in zip(texts, cands_by_text, runs, strict=True)
    ]


def tally(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        "linked": sum(1 for row in rows if row["status"] == "linked"),
        "suggested": sum(1 for row in rows if row["status"] == "suggested"),
    }


def link_output(
    c: sqlite3.Connection,
    mentionable_type: str,
    mentionable_id: str,
    revision: str,
    text: str,
    run_id: str | None = None,
) -> dict[str, int]:
    """Links one text: the run trace (when run_id is the attempt that wrote it) and the exact matches in the text, replacing
    the linker's mentions for it. Returns how many were linked and how many suggested."""
    rows = plan(c, [Text(mentionable_type, mentionable_id, revision, text, run_id)])[0]
    ES.replace_link_mentions(c, mentionable_type, mentionable_id, revision, rows)
    return tally(rows)


# ------------------------------------------------------------------------------------------------------- the pass
def _turn_of(refs: object) -> str | None:
    try:
        data: object = json.loads(str(refs or "{}"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    turn: object = cast("dict[str, object]", data).get("turn_id")
    return str(turn) if turn else None


def _cursor(c: sqlite3.Connection, source: str) -> list[Any]:
    row = c.execute("SELECT value FROM registry_metadata WHERE key = ?", (f"entity_links:{source}",)).fetchone()
    return list(START[source]) if row is None else json.loads(row[0])


def _save_cursor(c: sqlite3.Connection, source: str, cursor: list[Any]) -> None:
    c.execute(
        "INSERT OR REPLACE INTO registry_metadata (key, value) VALUES (?, ?)",
        (f"entity_links:{source}", json.dumps(cursor)),
    )


def _messages(c: sqlite3.Connection, cursor: list[Any], limit: int) -> tuple[list[Any], list[Text]]:
    rows = c.execute(
        "SELECT seq, id, body, refs FROM messages WHERE seq IS NOT NULL AND seq > ? ORDER BY seq LIMIT ?",
        (cursor[0], limit),
    ).fetchall()
    texts = [Text("message", str(row[1]), revision_of(row[2] or ""), row[2] or "", _turn_of(row[3])) for row in rows]
    return ([rows[-1][0]] if rows else cursor), texts


def _message_revisions(c: sqlite3.Connection, cursor: list[Any], limit: int) -> tuple[list[Any], list[Text]]:
    rows = c.execute(
        "SELECT created_at, id, message_id FROM message_revisions WHERE (created_at, id) > (?, ?) "
        "ORDER BY created_at, id LIMIT ?",
        (cursor[0], cursor[1], limit),
    ).fetchall()
    if not rows:
        return cursor, []
    ids = list(dict.fromkeys(str(row[2]) for row in rows))
    current = {
        str(row[0]): row
        for row in c.execute(
            "SELECT id, body, refs FROM messages WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)
        ).fetchall()
    }
    texts: list[Text] = []
    for message_id in ids:
        row = current.get(message_id)
        body = (row[1] or "") if row else ""
        turn = _turn_of(row[2]) if row else None
        texts.append(Text("message", message_id, revision_of(body), body, turn))
    return [rows[-1][0], rows[-1][1]], texts


def _docs(c: sqlite3.Connection, cursor: list[Any], limit: int) -> tuple[list[Any], list[Text]]:
    rows = c.execute(
        "SELECT updated_at, id, body, archived FROM docs WHERE (updated_at, id) > (?, ?) ORDER BY updated_at, id LIMIT ?",
        (cursor[0], cursor[1], limit),
    ).fetchall()
    texts: list[Text] = []
    for row in rows:
        body = "" if row[3] else str(row[2] or "")  # an archived doc has no text left to link
        texts.append(Text("doc", str(row[1]), revision_of(body), body))
    return ([rows[-1][0], rows[-1][1]] if rows else cursor), texts


def _meeting_versions(c: sqlite3.Connection, cursor: list[Any], limit: int) -> tuple[list[Any], list[Text]]:
    rows = c.execute(
        "SELECT v.id, v.meeting_id FROM meeting_versions v WHERE v.id > ? ORDER BY v.id LIMIT ?",
        (cursor[0], limit),
    ).fetchall()
    if not rows:
        return cursor, []
    ids = list(dict.fromkeys(str(row[1]) for row in rows))
    notes = {
        str(row[0]): row[1] or ""
        for row in c.execute(
            "SELECT id, notes FROM meetings WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)
        ).fetchall()
    }
    texts = [Text("meeting", mid, revision_of(notes.get(mid, "")), notes.get(mid, "")) for mid in ids]
    return [rows[-1][0]], texts


FETCH: dict[str, Callable[[sqlite3.Connection, list[Any], int], tuple[list[Any], list[Text]]]] = {
    "message": _messages,
    "message_revision": _message_revisions,
    "doc": _docs,
    "meeting_version": _meeting_versions,
}


def _last_per_output(texts: Sequence[Text]) -> list[Text]:
    latest: dict[tuple[str, str], Text] = {}
    for text in texts:
        latest.pop((text.mentionable_type, text.mentionable_id), None)
        latest[(text.mentionable_type, text.mentionable_id)] = text
    return list(latest.values())


def run_pass(
    store: Any,
    lock: Callable[[], AbstractContextManager[sqlite3.Connection]] | None = None,
    *,
    limit: int = BATCH,
) -> dict[str, int]:
    """One pass of the linker: the texts changed since the cursors, matched as a batch, written in one transaction with the
    cursors. `lock` is the write transaction (the Heartbeat passes its own, so the pass shows as its step); it defaults to
    the store's. Reads never hold the writer gate."""
    write = lock or store.transaction
    cursors: dict[str, list[Any]] = {}
    with store.read_transaction() as r:  # one snapshot: a text and its revision are read together
        old = {source: _cursor(r, source) for source in SOURCES}
        texts: list[Text] = []
        for source in SOURCES:
            cursors[source], found = FETCH[source](r, old[source], limit)
            texts += found
        outputs = _last_per_output(texts)
        plans = plan(r, outputs) if outputs else []
    moved = [source for source in SOURCES if cursors[source] != old[source]]
    if not moved:  # an idle beat takes no write lock
        return {"texts": 0, "linked": 0, "suggested": 0}
    with write() as w:
        for text, rows in zip(outputs, plans, strict=True):
            ES.replace_link_mentions(w, text.mentionable_type, text.mentionable_id, text.revision, rows)
        for source in moved:
            _save_cursor(w, source, cursors[source])
    return {"texts": len(outputs), **tally([row for rows in plans for row in rows])}
