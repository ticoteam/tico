"""An ask that shows a page: its words come from that page, so a reader who may no longer open it reads none of them."""

import json

import pytest

from backend import question_marks as Q
from backend import task_engine as E
from backend import task_pages as P
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.page_audience_review4 import public_page
from backend.tests.helpers.page_requirement import WRITER, _page, _pipeline, _say, _task, _v

BEN = "human:ben"


def test_an_ask_that_showed_a_page_goes_dark_for_whoever_may_no_longer_open_it(api):
    # Codex review 2 of the asks track, finding 7: the ask's line, answer, history and wake words stayed readable after
    # its page was narrowed away from the reader, or deleted.
    tid, _, fid = public_page(api)
    label = "Confidential draft"
    with api.app.state.store.transaction() as c:
        asked = X.ok_ask(c, tid, "bot:ops", "cara", f"OK the {label}?", shows=["page:" + fid])
    rid = asked["requirement_id"]
    answered = api.post(
        f"/api/v2/tasks/{tid}/asks/{rid}/answer",
        json={"value": "ok", "version": get(api, f"tasks/{tid}")["task"]["version"]},
        headers=headers("cara-test"),
    )
    assert answered.status_code == 200, answered.text
    assert label in json.dumps(get(api, f"tasks/{tid}", token="cara-test"))
    with api.app.state.store.read() as c:
        wakes = [
            json.loads(row[0] or "{}")
            for row in c.execute(
                "SELECT refs FROM messages WHERE wake_key IS NOT NULL AND body LIKE ?", (f"%{label}%",)
            )
        ]
    # The bot's wake carries where its words came from, so its message reads go through the same page check.
    assert wakes and all("page:" + fid in json.dumps(refs.get("body_sources")) for refs in wakes), wakes

    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert api.get("/api/v2/pages/" + fid, headers=headers("cara-test")).status_code == 404
        for path in (f"tasks/{tid}", f"tasks/{tid}/comments", f"tasks/{tid}/context"):
            shown = json.dumps(get(api, path, token="cara-test"))
            assert label not in shown and fid not in shown, (deleted, path, shown)


@pytest.fixture
def spec(company):
    """A Spec stage whose page a person OKs (people only), with the bot's page attached and passed: (conn, task, file
    id). The person OKs that page there, every version."""
    company.blobs = Blobs(company.settings)
    with company.transaction() as c:
        for slug in ("writer", "manager"):
            X.bot(c, slug)
        _pipeline(c, who_oks="people_only")
        row = _task(c)
        fid, _ = _page(c, company)
        _say(c, P.attach(c, row["id"], "Spec page", "page:" + fid, WRITER, seen_version=_v(c, row)), "met")
        yield c, E.task(c, row["id"]), fid


def test_a_bots_question_naming_the_page_a_person_oks_is_refused_like_its_ok_of_that_page(spec):
    c, row, fid = spec
    with pytest.raises(H.Refused, match="Attach the new version; Tico asks the person"):
        E.add_requirement(
            c, row["id"], WRITER, "ok", BEN, "Is this spec OK?", shows=[f"page:{fid}@1"], seen_version=_v(c, row)
        )
    with pytest.raises(H.Refused, match="Attach the new version; Tico asks the person"):
        Q.ask(c, WRITER, row["id"], BEN, f"Is this spec OK? https://tico.example/pages/{fid}")
    assert not Q.on_task(c, row["id"]), "a refused ❓ leaves nothing"
    asked = Q.ask(c, WRITER, row["id"], BEN, "Should the importer skip empty rows?")
    assert asked["person_id"] == BEN, "a ❓ that names no page is a plain question"
