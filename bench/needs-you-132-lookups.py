"""Done-when 5's measurement: one Needs you build on a fixture shaped like production (132 candidate tasks asking the
person's OK on a page, each with a linked GitHub PR Tico has read, and 33 ❓s to her), and the statements it spends on
the three per-task lookups: PR links (task_links), item metadata (item_metadata) and requirement pages
(requirement_pages). It uses only what main has too, so the same file measures main first and then this change:

    .venv/bin/python -m pytest bench/needs-you-132-lookups.py -n 0 -s -q   # in a checkout of main, then of the PR

Set NEEDS_132_OUT to a file to write the list (ids and times taken out) and compare the two.
"""

import json
import os
import re

from backend import question_marks as Q
from backend import task_pages as P
from backend import views as V
from backend.blobs import Blobs
from backend.item_sources import github as GS
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import Identity
from backend.tests.helpers.page_requirement import _page, _say

ANA = Identity("human:ana", "owner", "ana@acme.example")
LOOKUPS = ("FROM task_links", "FROM item_metadata", "FROM requirement_pages")


def test_132_candidates_lookup_statements(api):
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    stage = {
        "name": "QA",
        "owner": "assignee",
        "requirements": [{"kind": "page", "line": "QA page", "person": ANA.actor}],
    }
    with store.transaction() as c:
        X.bot(c, "writer")
        X.pipeline(c, "eng", "Engineering", (stage,), manager="bot:writer")
        for k in range(132):
            made = X.new_task(c, ANA.actor, f"Ship change {k}", "eng", assignee="bot:writer")
            H.task_link(c, "bot:writer", made["id"], f"https://github.com/acme/app/pull/{100 + k}")
            GS.save_pr(c, "acme/app", 100 + k, {"title": f"Change {k}", "state": "open", "head_sha": "a" * 40}, 1)
            fid, _ = _page(c, store, f"<h1>QA {k}</h1>")
            row = H.task(c, made["id"])
            _say(c, P.attach(c, made["id"], "QA page", "page:" + fid, "bot:writer", seen_version=row["version"]), "met")
            if k % 4 == 0:
                other = H.task_create(c, "bot:writer", f"Note {k}", "x", "bot:writer", lint=False)
                said = H.task_comment(c, "bot:writer", other["id"], f"Should note {k} go out on Friday?", wake=False)
                Q.mark(c, "bot:writer", said["id"], ANA.actor)
    seen: list[str] = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        items = V.needs_items(c, api.app.state.auth, ANA)
        c.set_trace_callback(None)
    lookups = sum(1 for s in seen if any(table in s for table in LOOKUPS))
    kinds = json.dumps({k: sum(1 for i in items if i["kind"] == k) for k in ("ask", "question")})
    if os.environ.get("NEEDS_132_OUT"):  # the list, ids and times taken out, to compare main's with this change's
        shape = re.sub(
            r'[0-9a-f]{8}-[0-9a-f-]{27}|file-[0-9a-f]{24}|"\d{4}-\d\d-\d\dT[^"]*"',
            '"x"',
            json.dumps(items, sort_keys=True, default=str),
        )
        with open(os.environ["NEEDS_132_OUT"], "w") as out:
            out.write(shape)
    print(f"\n  items {len(items)} {kinds}  statements {len(seen)}  of them the three lookups {lookups}")
