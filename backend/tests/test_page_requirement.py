"""The page requirement (docs/pipelines-v3.md "The nine requirement kinds", 3): a page attached at a pinned version
(backend/task_pages.py), Jev's verdict on it (backend/page_verdicts.py, the Heartbeat's `jev_verdicts`), then the named
person's OK of that exact version (backend/answers.py). The lines are docs/dev/pipelines-v3-build.md's.

Synthetic rows only (backend/tests/helpers/engine.py). Jev is a fake engine (pipeline_judges.ENGINE); never a model.
"""

import json
import uuid

import pytest

from backend import answers as A
from backend import page_verdicts as V
from backend import pipeline_judges as PJ
from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

ANA, BEN = X.ANA, "human:ben"
WRITER, REVIEWER = "bot:writer", "bot:reviewer"


def _stages(page_settings=None, person=None, line="Spec page"):
    return (
        {
            "name": "Spec",
            "owner": WRITER,
            "requirements": [{"kind": "page", "line": line, "person": person, "settings": page_settings or {}}],
        },
    )


@pytest.fixture
def store(company):
    company.blobs = Blobs(company.settings)
    return company


@pytest.fixture
def c(store):
    with store.transaction() as conn:
        for slug in ("writer", "reviewer", "manager"):
            X.bot(conn, slug)
        yield conn


@pytest.fixture
def seeded(store):
    """For the Jev step, which opens its own reads and writes: the rows go in through a write of their own."""

    def seed(build):
        with store.transaction() as conn:
            for slug in ("writer", "reviewer", "manager"):
                X.bot(conn, slug)
            return build(conn)

    return seed


def _pipeline(c, **kw):
    made = X.pipeline(c, "product", "Product", _stages(**kw), manager="bot:manager")
    return made["requirements"]["Spec"][0]


def _page(c, store, text="<h1>Spec</h1><p>Build the importer.</p>", fid=None):
    """A page (a Tico file) with one more version holding `text`. Returns (file id, version)."""
    data = text.encode()
    digest = store.blobs.put(data, "text/html")
    now = H.now()
    blob = H.new_id()
    c.execute(
        "INSERT INTO blobs (id, owner, digest, size, name, content_type, created_at) VALUES (?,?,?,?,?,?,?)",
        (blob, "bot:writer", digest, len(data), "spec.html", "text/html", now),
    )
    if fid is None:
        fid = "file-" + uuid.uuid4().hex[:24]
        c.execute(
            "INSERT INTO bot_files (id, bot_id, scope, identity, title, kind, mime, locator, first_activity_at, "
            "last_activity_at) VALUES (?,?,?,?,?,'file','text/html','tico_blob',?,?)",
            (fid, "writer", "bot", fid, "Spec", now, now),
        )
    version = c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] + 1
    c.execute(
        "INSERT INTO bot_file_versions (file_id, version, blob_id, digest, size, name, mime, actor, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, version, blob, digest, len(data), "spec.html", "text/html", "bot:writer", now),
    )
    c.execute("UPDATE bot_files SET current_version=? WHERE id=?", (version, fid))
    return fid, version


def _task(c):
    return X.new_task(c, ANA, "Importer spec", "product", assignee=WRITER)


def _attach(c, row, ref, by=WRITER, requirement="Spec page"):
    seen = E.task(c, row["id"])["version"]
    return P.attach(c, row["id"], requirement, ref, by, seen_version=seen)


def _standing(c, row):
    (standing,) = R.standings(c, E.task(c, row["id"]))
    return standing


def _say(c, page_row, verdict, reasons="", failed=False):
    """Jev's verdict on an attached page, as the Jev step records it."""
    key = page_row["rubric_hash"]
    owed = V.owe(c, page_row["file_id"], page_row["version"], key)
    result = {"verdict": verdict, "p": {"met": 0.9, "not_met": 0.1}.get(verdict, 0.5), "reason": reasons}
    for _ in range(V.TRIES if failed else 1):
        V.record(c, owed["id"], result, failed=failed)
    return V.get(c, page_row["file_id"], page_row["version"], key)


def _refused(rule):
    return pytest.raises(H.Refused, match=rule)


# ----------------------------------------------------------------------------- attach
def test_no_page_is_the_owners_turn_and_attaching_owes_jev_a_verdict(c, store):
    _pipeline(c)
    row = _task(c)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, WRITER, "No page attached")
    fid, _ = _page(c, store)
    _page(c, store, "<p>v2</p>", fid=fid)
    made = _attach(c, row, "page:" + fid)
    assert made["version"] == 2, "no version pins the newest"
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, R.JEV, "Jev is checking")
    owed = V.owed(c, H.shift(H.now(), seconds=1))
    assert [(o["file_id"], o["version"], o["rubric_hash"]) for o in owed] == [(fid, 2, made["rubric_hash"])]
    assert "page_attached" in X.events(c, row["id"])
    assert json.loads(made["judged_against"]) == {"rubric": "Spec page", "context": {}}


def test_attaching_again_replaces_the_page_and_the_same_page_twice_is_one(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    first = _attach(c, row, f"page:{fid}@1")
    assert _attach(c, row, f"page:{fid}@1")["id"] == first["id"]
    _page(c, store, "<p>v2</p>", fid=fid)
    second = _attach(c, row, f"page:{fid}@2")
    assert P.current(c, row["visit"]["id"], first["requirement_id"])["id"] == second["id"]
    assert H._one(c, "SELECT replaced_at FROM requirement_pages WHERE id=?", (first["id"],))["replaced_at"]


def test_attach_refuses_what_it_cannot_take(c, store):
    _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    with _refused("task_requirement"):
        _attach(c, row, "page:" + fid, requirement="No such thing")
    with _refused("task_requirement"):
        _attach(c, row, f"page:{fid}@7")
    with _refused("task_requirement"):
        _attach(c, row, "page:file-" + "0" * 24)
    with _refused("task_requirement"):
        _attach(c, row, "file-abc")
    with _refused("task_changed"):
        P.attach(c, row["id"], "Spec page", "page:" + fid, WRITER)  # a bot says which version it saw
    with _refused("identity"):
        _attach(c, row, "page:" + fid, by=REVIEWER)
    assert _attach(c, row, "page:" + fid, by=BEN)["attached_by_id"] == BEN  # any person may
    E.move(c, row["id"], "product-closed", ANA, "Not now")
    with _refused("task_ended"):
        P.attach(c, row["id"], "Spec page", "page:" + fid, ANA)


# ----------------------------------------------------------------------------- Jev's verdict, then the person's OK
def test_jev_passed_with_no_person_named_meets_it_and_the_task_moves(c, store):
    _pipeline(c)
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "met")
    assert _standing(c, row).line == "Jev passed it"
    assert E.advance(c, row["id"])["stage"]["name"] == "Done"


def test_jev_sends_it_back_to_the_owner_with_the_reasons(c, store):
    _pipeline(c)
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "not_met", "No  acceptance\ncriteria.")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.NOT_MET,
        WRITER,
        "Jev sent it back: No acceptance criteria.",
    )


def test_after_jev_passes_the_named_person_oks_that_exact_version(c, store):
    rid = _pipeline(c, person=ANA)
    row = _task(c)
    fid, _ = _page(c, store)
    made = _attach(c, row, "page:" + fid)
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, ANA, "ok")  # Jev has not looked yet
    _say(c, made, "met")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.WAITING, ANA, "OK Ana (version 1)")
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, BEN, "ok")  # asked of Ana
    with _refused("task_why"):
        A.answer(c, row["id"], rid, ANA, "no")
    sent = A.answer(c, row["id"], rid, ANA, "no", "Say how errors show", page_version=f"{fid}@1")
    assert sent["stage"]["name"] == "Spec"
    assert _standing(c, row).line == "Sent back: Say how errors show"
    assert H._one(c, "SELECT page_version FROM answers WHERE id=?", (sent["answer_id"],))["page_version"] == 1
    _page(c, store, "<p>v2 with errors</p>", fid=fid)
    second = _attach(c, row, "page:" + fid)
    assert _standing(c, row).line == "Jev is checking", "a new version starts the verdict and the OK over"
    _say(c, second, "met")
    assert _standing(c, row).line == "OK Ana (version 2)", "the send back was for version 1"
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{fid}@1")  # she was shown version 1
    done = A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{fid}@2")
    assert done["stage"]["name"] == "Done"


def test_an_ok_of_one_page_does_not_approve_another_page_at_the_same_version(c, store):
    rid = _pipeline(c, person=ANA)
    row = _task(c)
    shown, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + shown), "met")
    other, _ = _page(c, store, "<p>Another spec</p>")  # page B, also at version 1, replaces page A
    _say(c, _attach(c, row, "page:" + other), "met")
    with _refused("task_changed"):
        A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{shown}@1")  # she was shown page A
    assert E.task(c, row["id"])["stage"]["name"] == "Spec"
    done = A.answer(c, row["id"], rid, ANA, "ok", page_version=f"{other}@1")
    assert done["stage"]["name"] == "Done"


def test_the_named_persons_needs_you_has_the_page_at_the_version_to_ok(c, store):
    from backend import waiting_on as W

    _pipeline(c, person=ANA)
    row = _task(c)
    fid, _ = _page(c, store)
    _say(c, _attach(c, row, "page:" + fid), "met")
    (item,) = [i for i in W.needs(c, ANA) if i["kind"] == "ask"]
    (ask,) = [r for r in H.task_view(c, row["id"], ANA)["to_leave"] if r["requirement_id"] == item["requirement_id"]]
    assert (ask["kind"], ask["shows"], ask["page"]["version"]) == ("page", [f"page:{fid}@1"], 1)
    assert sorted(ask["may"]) == ["no", "ok"]


def test_a_private_tasks_page_ok_falls_to_the_tasks_person_when_the_named_one_cannot_open_it(c, store):
    from backend import waiting_on as W

    _pipeline(c, person=BEN)
    row = _task(c)
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
    _say(c, _attach(c, row, "page:" + _page(c, store)[0]), "met")
    assert W.unreadable(c, E.task(c, row["id"]), R.standings(c, E.task(c, row["id"]))) == BEN


def test_unsure_or_failed_goes_to_a_person_once_per_visit(c, store):
    rid = _pipeline(c)
    row = _task(c)
    fid, _ = _page(c, store)
    made = _attach(c, row, "page:" + fid)
    _say(c, made, "unsure", "Can't tell if the scope is set")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (
        R.WAITING,
        None,
        "Jev is unsure: Can't tell if the scope is set",
    ), "nobody named: the task's person decides"
    with _refused("task_requirement"):
        A.answer(c, row["id"], rid, WRITER, "ok", seen_version=E.task(c, row["id"])["version"])
    A.answer(c, row["id"], rid, BEN, "no", "Set the scope")
    _page(c, store, "<p>v2</p>", fid=fid)
    second = _attach(c, row, "page:" + fid)
    _say(c, second, "unsure", "Still can't tell", failed=True)
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, WRITER, "Jev couldn't check this"), (
        "a person already decided one this visit: the next goes back to the owner, not to their list"
    )


def test_a_failed_check_is_a_persons_to_decide_and_their_ok_meets_it(c, store):
    rid = _pipeline(c)
    row = _task(c)
    made = _attach(c, row, "page:" + _page(c, store)[0])
    _say(c, made, "unsure", "no model", failed=True)
    assert _standing(c, row).line == "Jev couldn't check this"
    assert A.answer(c, row["id"], rid, BEN, "ok")["stage"]["name"] == "Done"


def _link_pr(c, task_id, repo, number, head):
    now = H.now()
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, created_at) VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), task_id, "pr", f"https://github.com/{repo}/pull/{number}", repo, number, now),
    )
    _push(c, repo, number, head)


def _push(c, repo, number, head, state="open", merged_sha=None):
    now = H.now()
    c.execute(
        "INSERT INTO pr_facts (id, repo, number, state, head_sha, merged_sha, fact_version, read_at, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,1,?,?,?) ON CONFLICT(repo, number) DO UPDATE SET head_sha=excluded.head_sha, "
        "state=excluded.state, merged_sha=excluded.merged_sha",
        (H.new_id(), repo, number, state, head, merged_sha, now, now, now),
    )


def test_a_review_page_counts_only_for_the_newest_commit(c, store):
    _pipeline(c, page_settings={"at_head": True, "rubric": "Reviewed"}, line="Review")
    row = _task(c)
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, created_at) VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), row["id"], "pr", "https://github.com/acme/app/pull/12", "acme/app", 12, H.now()),
    )
    fid, _ = _page(c, store)
    with _refused("Tico is still reading acme/app#12"):
        _attach(c, row, "page:" + fid, requirement="review")
    _push(c, "acme/app", 12, "aaa")
    made = _attach(c, row, "page:" + fid, requirement="review")
    assert json.loads(made["heads"]) == {"acme/app#12": "aaa"}
    assert json.loads(made["judged_against"])["context"] == {"heads": {"acme/app#12": "aaa"}}
    _say(c, made, "met")
    assert _standing(c, row).met
    _push(c, "acme/app", 12, "bbb")
    standing = _standing(c, row)
    assert (standing.state, standing.on, standing.line) == (R.NOT_MET, WRITER, "New commits since the review")
    again = _attach(c, row, "page:" + fid, requirement="review")
    assert again["rubric_hash"] != made["rubric_hash"], "the same page for a new commit is a new verdict"
    assert _standing(c, row).line == "Jev is checking"


def test_a_validate_page_is_judged_against_the_release_its_filed_work_went_out_in(c, store):
    _pipeline(c, page_settings={"with_release": True})
    row = _task(c)
    filed = X.new_task(c, ANA, "Build the importer", assignee=WRITER)
    c.execute(
        "INSERT INTO links (id, from_task_id, to_task_id, visit_id, kind, created_at) VALUES (?,?,?,?,?,?)",
        (H.new_id(), row["id"], filed["id"], row["visit"]["id"], "handoff", H.now()),
    )
    _link_pr(c, filed["id"], "acme/app", 3, "abc")
    _push(c, "acme/app", 3, "abc", state="merged", merged_sha="m1")
    for tag, at in (("v1.1.0", "2026-11-01T00:00:00Z"), ("v1.2.0", "2026-11-05T00:00:00Z")):
        rid = H.new_id()
        c.execute(
            "INSERT INTO releases (id, repo, tag, commit_sha, published_at, fact_version, read_at, created_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (rid, "acme/app", tag, "x", at, at, at),
        )
        c.execute(
            "INSERT INTO release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,1,?)",
            (rid, "m1", at),
        )
    made = _attach(c, row, "page:" + _page(c, store)[0])
    assert json.loads(made["judged_against"])["context"] == {"release": ["v1.1.0"]}, "the first release it is in"


# ----------------------------------------------------------------------------- the Jev step
def _engine(p, calls, reason="Clear and complete"):
    def ask(model, state, questions, label):
        calls.append(state)
        return {"p": p, "reason": reason, "model": model, "cost_usd": 0.01}

    return ask


def test_the_jev_step_judges_the_page_text_and_moves_the_task(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c, page_settings={"rubric": "Says what done looks like"})
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store, "<p>Done means the importer runs nightly.</p>")[0])
        return row

    row = seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.95, calls))
    assert V.run(store, H.shift(H.now(), seconds=1)) == 1
    (state,) = calls
    assert state["requirement"]["rubric"] == "Says what done looks like"
    assert "importer runs nightly" in state["attachment"]["text"]
    assert state["work"]["title"] == "Importer spec"
    with store.read() as r:
        assert E.task(r, row["id"])["stage"]["name"] == "Done"
        assert "jev_verdict" in X.events(r, row["id"])
    assert V.run(store, H.shift(H.now(), seconds=1)) == 0, "judged once"


def test_jevs_send_back_wakes_the_bot_owner_with_the_reasons(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        X.settled(c)
        return row

    row = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.05, [], reason="No scope"))
    V.run(store, H.shift(H.now(), seconds=1))
    with store.read() as r:
        bodies = [
            m[0]
            for m in r.execute(
                "SELECT body FROM messages WHERE recipient_id=? AND wake_key LIKE ?", (WRITER, row["id"] + ":%:jev:%")
            )
        ]
        assert _standing(r, row).line == "Jev sent it back: No scope"
    assert len(bodies) == 1 and 'Jev sent back "Spec page": No scope' in bodies[0]


def test_three_failed_calls_give_the_page_to_a_person(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        return row, _attach(c, row, "page:" + _page(c, store)[0])

    row, made = seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", None)
    monkeypatch.setattr(PJ, "_installed_store", None)  # no judge configured: every call fails
    at = H.now()
    for tries in range(1, V.TRIES + 1):
        at = H.shift(at, seconds=V.RETRY_S[-1] + 1)
        V.run(store, at)
        with store.read() as r:
            verdict = V.get(r, made["file_id"], made["version"], made["rubric_hash"])
            assert verdict["tries"] == tries
    assert verdict["verdict"] == "failed" and verdict["last_error"] == "No judge is configured on this server"
    with store.read() as r:
        assert _standing(r, row).line == "Jev couldn't check this"


def test_a_failed_call_waits_before_the_next_try(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        _attach(c, _task(c), "page:" + _page(c, store)[0])

    seeded(build)
    monkeypatch.setattr(PJ, "ENGINE", None)
    monkeypatch.setattr(PJ, "_installed_store", None)
    at = H.shift(H.now(), seconds=1)
    V.run(store, at)
    V.run(store, H.shift(at, seconds=V.RETRY_S[0] - 5))
    with store.read() as r:
        assert r.execute("SELECT tries FROM page_verdicts").fetchone()[0] == 1


def test_a_verdict_no_open_task_waits_on_is_put_off_not_judged(store, seeded, monkeypatch):
    def build(c):
        _pipeline(c)
        row = _task(c)
        _attach(c, row, "page:" + _page(c, store)[0])
        E.move(c, row["id"], "product-closed", ANA, "Not now")

    seeded(build)
    calls = []
    monkeypatch.setattr(PJ, "ENGINE", _engine(0.9, calls))
    now = H.shift(H.now(), seconds=1)
    assert V.run(store, now) == 0 and calls == []
    with store.read() as r:
        assert r.execute("SELECT next_try_at FROM page_verdicts").fetchone()[0] > H.shift(now, hours=23)


# ----------------------------------------------------------------------------- the route and the task's read
def test_the_route_attaches_a_readable_page_and_the_task_read_shows_it(api):
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    with store.transaction() as conn:
        for slug in ("writer", "manager"):
            X.bot(conn, slug)
        rid = _pipeline(conn, person=ANA)
        row = _task(conn)
        fid, _ = _page(conn, store)
    r = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:" + fid},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"] == {"file_id": fid, "version": 1}
    (entry,) = [e for e in body["task"]["to_leave"] if e["requirement_id"] == rid]
    assert entry["page"] == {"file_id": fid, "version": 1, "ref": f"page:{fid}@1"}
    assert entry["standing"] == "Jev is checking" and entry["may"] == []
    missing = api.post(
        f"/api/v2/tasks/{row['id']}/pages",
        json={"requirement": "Spec page", "page": "page:file-" + "1" * 24},
        headers=headers("ana-test"),
    )
    assert missing.status_code == 404, missing.text
    with store.transaction() as conn:
        _say(conn, P.current(conn, row["visit"]["id"], rid), "met")
    stale = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": f"{fid}@2"},
        headers=headers("ana-test"),
    )
    assert stale.status_code == 409, stale.text
    bare = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": 1},
        headers=headers("ana-test"),
    )
    assert bare.status_code == 422, "a version without its file says nothing about which page was shown"
    ok = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{rid}/answer",
        json={"value": "ok", "page_version": f"{fid}@1"},
        headers=headers("ana-test"),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["task"]["stage"]["name"] == "Done"


# ----------------------------------------------------------------------------- the CLI and the bot tool
def test_tico_task_attach_with_req_attaches_a_page_through_the_pages_route(monkeypatch):
    from clients import hubcli, hubtools, remotecli

    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "bot:writer", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"task": {}}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    argv = ["task", "attach", "T-1", "page:file-abc@2", "--req", "Spec page", "--version", "4"]
    remotecli.run(hubcli.parser().parse_args(argv))
    assert sent[-1] == (
        "POST",
        "tasks/T-1/pages",
        {"requirement": "Spec page", "page": "page:file-abc@2", "version": 4},
    )
    with pytest.raises(ValueError, match="version"):
        hubtools.task_attach(Client(), {"id": "T-1", "req": "Spec page", "page": "page:file-abc"})
    with pytest.raises(ValueError, match="both req and page"):
        hubtools.task_attach(Client(), {"id": "T-1", "req": "Spec page", "version": 4})
    with pytest.raises(ValueError, match="name"):
        hubtools.task_attach(Client(), {"id": "T-1"})
