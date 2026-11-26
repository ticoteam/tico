"""One Needs you build on the spec's production-shaped fixture (spec "Build Needs you once per change", done-when 5):
132 candidate tasks asking the person's OK, each with a linked GitHub PR Tico has read (its saved item metadata) and a
current QA page, and a ❓ to the person in chat for every fourth. Fictional rows only.

    .venv/bin/python -m pytest bench/needs-you-132-lookups.py -n 0 -s -q

It prints the statements of one build, those of the named lookup families (PR links and item metadata, requirement
pages, review heads) and a digest of the list with its ids and times masked. Self-contained, so the same file runs on
main first (copied into a main checkout) and then on the change: the two digests must be equal.
"""

import hashlib
import json
import re

from backend import task_pages as P
from backend import views as V
from backend.blobs import Blobs
from backend.item_sources import github as GS
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import Identity
from backend.tests.helpers.page_requirement import _page, _say

N = 132
ANA = Identity("human:ana", "owner", "ana@acme.example")
STAGE = {
    "name": "QA",
    "owner": "assignee",
    "requirements": [{"kind": "page", "line": "QA page", "person": "human:ana"}],
}
# The named lookup families, by the table each reads: PR links and saved metadata, requirement pages, review answers.
FAMILIES = (
    ("item metadata", re.compile(r"\bitem_metadata\b")),
    ("requirement pages", re.compile(r"\brequirement_pages\b")),
    ("review heads", re.compile(r"\bFROM answers a JOIN visits\b")),
)


def seed(store, n=N):
    """`n` tasks in QA asking Ana to OK their page, each with a read PR and a QA page; a ❓ to Ana in chat for every fourth."""
    from backend import question_marks as Q

    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as c:
        for slug in ["writer", *(f"asker{k:03d}" for k in range(0, n, 4))]:
            X.bot(c, slug)
        X.pipeline(c, "eng", "Engineering", (STAGE,), manager="bot:writer")
    for k in range(n):
        with store.transaction() as c:
            made = X.new_task(c, "human:ana", f"Ship change {k}", "eng", assignee="bot:writer")
            H.task_link(c, "bot:writer", made["id"], f"https://github.com/acme/app/pull/{100 + k}")
            GS.save_pr(c, "acme/app", 100 + k, {"title": f"Change {k}", "state": "open", "head_sha": "a" * 40}, 1)
            fid, _ = _page(c, store, f"<h1>QA {k}</h1>")
            row = H.task(c, made["id"])
            _say(c, P.attach(c, made["id"], "QA page", "page:" + fid, "bot:writer", seen_version=row["version"]), "met")
            if k % 4 == 0:  # a task's own ❓ is refused beside its page OK, and a bot's chats to a person are capped
                Q.ask_in_chat(c, f"bot:asker{k:03d}", "human:ana", f"Ship change {k} on Friday?")


def traced(store, auth, who=ANA):
    """(Ana's list, every statement of one build) in one read snapshot."""
    seen: list[str] = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        items = V.needs_items(c, auth, who)
        c.set_trace_callback(None)
    return items, seen


def families(seen):
    return {name: sum(1 for s in seen if pattern.search(s)) for name, pattern in FAMILIES}


_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|file-[0-9a-f]{24}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+Z?")


def digest(items):
    text = _TIME.sub("T", _ID.sub("ID", json.dumps(items, sort_keys=True, default=str)))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def test_one_build_on_132_candidates(api):
    store = api.app.state.store
    seed(store)
    items, seen = traced(store, api.app.state.auth)
    counts = families(seen)
    print(f"\n  {len(items)} items, digest {digest(items)}")
    print(f"  one build: {len(seen)} statements; named lookups {sum(counts.values())} ({counts})")
