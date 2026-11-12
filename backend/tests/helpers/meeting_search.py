"""A synthetic meeting archive for meeting search: every access path, dates and realistic transcripts.

Shared by backend/tests/test_meeting_search_bounds.py and bench/perf-meeting-search.py. All names are fictional.
"""

import contextlib
import json
import random

from backend import meetings
from backend.context_search import excerpt, meeting_info, readable, terms, transcript_text
from backend.store import Problem

WORDS = (
    "pricing renewal contract onboarding churn invoice discount pilot rollout budget quarter roadmap security review "
    "integration export calendar seats migration support latency dashboard report forecast hiring launch partner "
    "procurement legal approval timeline feedback demo trial upgrade billing account usage metric customer team"
).split()
SPEAKERS = ("Dana", "Lee", "Priya", "Omar", "Ben Ito", "Cara Ruiz")

# Each case: (meeting fields, review state, deleted, bot attached, bot attached through a discarded live meeting)
CASES = (
    ({"kind": "meeting", "private": False, "owner": "ben@acme.example"}, "live", False, False, False),  # team
    ({"kind": "meeting", "private": None, "owner": "dee@acme.example"}, "live", False, False, False),  # team, unset
    (  # private, ben attends in another spelling
        {
            "kind": "meeting",
            "private": True,
            "owner": "dee@acme.example",
            "calendar": {"attendees": [" BEN@Acme.Example "]},
        },
        "live",
        False,
        False,
        False,
    ),
    (  # private, cara named by the importer
        {
            "kind": "meeting",
            "private": True,
            "owner": "dee@acme.example",
            "participants": [{"email": "Cara@acme.example"}],
        },
        "live",
        False,
        True,
        False,
    ),
    (  # participants only, listed in any case
        {
            "kind": "meeting",
            "private": False,
            "audience": "participants",
            "owner": "dee@acme.example",
            "audience_emails": ["CARA@acme.example ", "eve@acme.example"],
        },
        "live",
        False,
        False,
        False,
    ),
    (  # participants only, the owner's own (the company owner is not listed)
        {
            "kind": "meeting",
            "private": False,
            "audience": "participants",
            "owner": "Ben@ACME.example",
            "audience_emails": ["dee@acme.example"],
        },
        "live",
        False,
        True,
        False,
    ),
    (
        {"kind": "meeting", "private": False, "owner": "ben@acme.example"},
        "pending",
        False,
        False,
        False,
    ),  # filed for ben
    ({"kind": "meeting", "private": False, "owner": "cara@acme.example"}, "dismissed", False, False, False),
    ({"kind": "meeting", "private": False, "owner": "ben@acme.example"}, "live", True, False, False),  # deleted
    ({"kind": "note", "private": False, "owner": "ben@acme.example"}, "live", False, False, False),  # typed note
    ({"kind": "note", "private": True, "owner": "cara@acme.example"}, "live", False, True, False),  # attached note
    ({"kind": "meeting", "private": True, "owner": "dee@acme.example"}, "live", False, False, True),  # discarded live
    ({"kind": "meeting", "private": False, "owner": "dee@acme.example", "started": ""}, "live", False, False, False),
    ({"kind": "meeting", "private": True, "owner": "eve@acme.example"}, "live", True, True, False),  # deleted, attached
)
STARTED = ("2026-01-05T09:00:00Z", "2026-02-14T16:30:00Z", "2026-03-31T23:59:59Z", None, "2026-04-01")


def transcript(rng, turns):
    """Turns of 15 to 40 words; about 6 KB per meeting at 30 turns."""
    return [
        {
            "text": " ".join(rng.choice(WORDS) for _ in range(rng.randint(15, 40))).capitalize() + ".",
            "speaker": rng.choice(SPEAKERS),
            "start_ms": n * 9000,
            "end_ms": n * 9000 + 8000,
        }
        for n in range(turns)
    ]


def seed(store, count, *, bot="ops", turns=30, prefix="m"):
    """`count` meetings cycling through CASES; ids sort in creation order within one created_at second."""
    rng = random.Random(count)
    with store.transaction() as c:
        for n in range(count):
            fields, state, deleted, attached, discarded = CASES[n % len(CASES)]
            rid = f"{prefix}{n:05d}"
            spoken = transcript(rng, turns)
            text = "\n".join(t["text"] for t in spoken)
            meta = {
                "id": rid,
                "title": f"{rng.choice(WORDS).capitalize()} call {n}",
                "status": "done",
                "turns": spoken,
                "review_state": state,
                **({"started": STARTED[n % len(STARTED)]} if STARTED[n % len(STARTED)] else {}),
                **fields,
            }
            if meta["kind"] == "note":
                meta["note"] = text
            meetings.snapshot(meta, transcript=text, readable=text, notes="Notes: " + rng.choice(WORDS), conn=c)
            if deleted:
                c.execute("INSERT INTO media_control(meeting_id,deleted_at) VALUES(?, '2026-05-01T00:00:00Z')", (rid,))
            if attached or discarded:
                live = "live-" + rid
                c.execute(
                    "INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,imported_meeting_id,created_at,"
                    "updated_at,started_at,discarded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        live,
                        "human:dee",
                        "Live",
                        "ended",
                        30000,
                        rid,
                        "2026-01-01",
                        "2026-01-01",
                        "2026-01-01",
                        "2026-01-02" if discarded else None,
                    ),
                )
                c.execute(
                    "INSERT INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,?,?,?)",
                    (live, bot, "human:dee", "2026-01-01"),
                )


def reference(store, who, q="", person="", since=None, until=None, limit=20, offset=0):
    """GET /api/v2/meetings/search as it was before the candidates were bounded (tico main 97389b8d4)."""
    words, found = terms(q), []
    with store.read() as c:
        for item in c.execute("SELECT id FROM meetings ORDER BY created_at DESC,id"):
            try:
                row = readable(c, who, item[0])
            except Problem as exc:
                if exc.status not in (403, 404):
                    raise
                continue
            recorded_date = str(row["metadata"].get("started") or row["created_at"])[:10]
            if since and recorded_date < since:
                continue
            if until and recorded_date > until:
                continue
            meta = row["metadata"]
            turns = meta.get("turns") or []
            people = json.dumps(
                [
                    row["owner"],
                    meta.get("recorded_by"),
                    meta.get("calendar"),
                    meta.get("participants"),
                    (meta.get("source_context") or {}).get("lead_name"),
                    [t.get("speaker") for t in turns],
                ]
            )
            if person and person.casefold() not in people.casefold():
                continue
            text = transcript_text(row)
            body = "\n".join(
                [text, row["notes"] or "", str(meta.get("note") or ""), "\n".join(t.get("text", "") for t in turns)]
            )
            if not all(w in (row["title"] + " " + body).casefold() for w in words):
                continue
            passages = [
                {
                    "text": excerpt(t.get("text", ""), words),
                    "speaker": t.get("speaker"),
                    "start_ms": t.get("start_ms"),
                    "end_ms": t.get("end_ms"),
                }
                for t in turns
                if words and any(w in t.get("text", "").casefold() for w in words)
            ][:5]
            found.append({**meeting_info(row), "excerpt": excerpt(body, words), "passages": passages})
            if len(found) > offset + limit:
                break
    return {
        "results": found[offset : offset + limit],
        "next_offset": offset + limit if len(found) > offset + limit else None,
        "mode": "keyword",
    }


@contextlib.contextmanager
def traced(store):
    """Every statement run on a connection the store lends for reading (read() and read_transaction())."""
    seen, original = [], store.read

    @contextlib.contextmanager
    def borrowed():
        with original() as c:
            c.set_trace_callback(seen.append)
            try:
                yield c
            finally:
                c.set_trace_callback(None)

    store.read = borrowed
    try:
        yield seen
    finally:
        del store.read
