"""Flow's suggested rules are gone, but a rule a person added to a page check there keeps binding it
(task_pages.added_rules): Jev still asks it of the next page attached, as one more yes/no part; an undone or dismissed
one is not asked. Acme is the fictional company."""

import json

from backend import page_verdicts as V
from backend import pipeline_judges as PJ
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_pages
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import post  # noqa: F401  (the api fixture)
from backend.tests.helpers.page_requirement import WRITER, _page, _pipeline

RULE = "Lists the tests run and their results."


def _suggestion(c, rid, text, state):
    now = H.now()
    c.execute(
        "INSERT INTO flow_suggestions (id, requirement_id, stage_id, pipeline_id, text, state, acted_by, acted_at, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (H.new_id(), rid, "spec", "product", text, state, "human:ana", now, now, now),
    )


def test_a_rule_added_before_still_binds_its_check(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        for slug in ("writer", "manager"):
            X.bot(c, slug)
        rid = _pipeline(c, page_settings={"rubric": PS.SPEC_RUBRIC})
        _suggestion(c, rid, RULE, "added")
        _suggestion(c, rid, "Names the PR it is about.", "open")
        _suggestion(c, rid, "Says who reviewed it.", "dismissed")
        row = X.new_task(c, "human:ana", "Importer spec", "product", assignee=WRITER)
        fid = _page(c, store, "<h1>Spec</h1><p>Goal, scope and criteria.</p>")[0]
        attached = task_pages.attach(
            c, row["id"], "Spec page", "page:" + fid, WRITER, seen_version=E.task(c, row["id"])["version"]
        )
    assert json.loads(attached["judged_against"])["rules"] == [RULE], "only the added one"
    asked: list[dict] = []

    def raw(state, questions, label):
        asked.append(questions)
        answers = {q: {"type": "noul", "noul": 0.1 if q == "part_rule1" else 0.9} for q in questions}
        return {"answers": answers, "model": "tico-decide-low", "provider": "gateway", "cost_usd": 0.001, "ms": 3}

    monkeypatch.setattr(PJ, "ASK", raw)
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as c:
        verdict = V.get(c, attached["file_id"], attached["version"], attached["rubric_hash"])
    assert "part_rule1" in asked[0]
    assert (verdict["verdict"], verdict["reasons"]) == ("not_met", f'Doesn\'t meet "{RULE}".')
