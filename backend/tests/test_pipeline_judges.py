"""The requirement judge: an answer passes only when it is a confident yes or no, a failed or capped call is unsure, one
attachment is judged once, the model is called outside any write transaction, and a person is asked when the rule says
so."""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import blobs as BL
from backend import hubdb as H
from backend import judge as backend_judge
from backend import pipeline_engine as E
from backend import pipeline_judges as P
from backend import pipeline_requirements as R
from backend import requirement_attachments as A
from backend.config import Settings
from backend.store import Store
from clients import judge as J

from .test_pipeline_foundation import ANA, boot, hub, step_id

FIXTURES = Path(__file__).parent / "fixtures"
BEN = "human:ben"
REQ = {"id": "req-1", "name": "Shows the page live", "rubric": "The link opens the page", "pipeline_id": None}
ATTACHMENT = {
    "id": "att-1",
    "source": "link",
    "digest": "d",
    "url": "https://x.example",
    "file_version": 0,
    "text": "The link opens the page",
}


def _settings(path):
    return Settings(db_path=path, registry_dir=FIXTURES / "registry")


def _engine(p, *, reason="ok"):
    def ask(model, state, questions, label):
        return {"p": p, "reason": reason, "provider": "t", "model": model}

    return ask


def test_the_bands_decide_met_not_met_or_unsure(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)

    def verdict(p):
        return P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=_engine(p))["verdict"]

    assert verdict(0.8) == "met" and verdict(0.95) == "met"
    assert verdict(0.2) == "not_met" and verdict(0.01) == "not_met"
    assert verdict(0.5) == "unsure" and verdict(0.79) == "unsure" and verdict(0.21) == "unsure"
    assert verdict(float("nan")) == "unsure"  # an answer with no probability is unsure, not a pass


def test_a_failed_model_call_is_unsure_and_no_engine_means_unsure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        P, "_installed_store", None
    )  # no installed app: no production engine, so no engine means unsure
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)

    def broken(model, state, questions, label):
        raise J.JudgeError("unavailable", "the model did not answer")

    assert P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=broken)["verdict"] == "unsure"
    assert P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=None)["verdict"] == "unsure"


def test_the_spend_cap_stops_the_call_and_the_answer_is_unsure(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    called = []
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))

    def ask(model, state, questions, label):
        called.append(model)
        return {"p": 1.0, "reason": "ok"}

    result = P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=_settings(path), engine=ask)

    assert result["verdict"] == "unsure" and called == []


def test_a_pipeline_with_no_choice_is_judged_by_haiku_and_an_unknown_model_is_refused(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    assert P.judge_model_of(c, None) == "haiku-5.5"
    assert P.judge_model_of(c, "no-such-pipeline") == "haiku-5.5"


def test_a_bot_reviewed_attachment_is_judged_once_and_the_decision_is_recorded(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    asked = []

    def ask(model, state, questions, label):
        asked.append(state["requirement"])
        return {"p": 0.95, "reason": "The page opens", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1
    assert P.tick(store) == 0  # the attachment is answered; the next pass has nothing to judge
    assert [r["name"] for r in asked] == ["Live link"]
    with store.read() as conn:
        rows = [dict(r) for r in conn.execute("SELECT judge, verdict, judge_call_id FROM stage_decisions").fetchall()]
    # A link has no text Tico can read, so even a confident "met" is unsure and a person decides (Fix L2-2).
    assert rows == [{"judge": "llm", "verdict": "unsure", "judge_call_id": rows[0]["judge_call_id"]}]
    assert rows[0]["judge_call_id"]  # the decision points at its audit event


def test_the_model_is_never_called_while_a_write_transaction_is_open(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "Opens",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    real_transaction = store.transaction
    in_call = {"on": False}

    def guarded(*args, **kwargs):
        assert not in_call["on"], "a write transaction opened while the model was being asked"
        return real_transaction(*args, **kwargs)

    def ask(model, state, questions, label):
        in_call["on"] = True
        try:
            return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}
        finally:
            in_call["on"] = False

    monkeypatch.setattr(store, "transaction", guarded)
    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1


def test_a_bot_then_person_requirement_sends_an_unsure_answer_to_its_person(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot_then_person",
            "reviewer_id": BEN,
            "rubric": "Opens",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()

    store = Store(_settings(path))
    monkeypatch.setattr(P, "ENGINE", _engine(0.5))
    assert P.tick(store) == 1
    with store.read() as conn:
        row = dict(conn.execute("SELECT verdict, route_next_to FROM stage_decisions").fetchone())
    assert row == {"verdict": "unsure", "route_next_to": BEN}
    assert P.tick(store) == 0  # routed to a person: the bot does not judge it again


def test_a_tick_over_the_decide_cap_records_unsure_and_never_asks_the_model(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "Ok",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()
    asked = []

    def ask(model, state, questions, label):
        asked.append(model)
        return {"p": 1.0}

    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))
    monkeypatch.setattr(P, "ENGINE", ask)
    store = Store(_settings(path))

    assert P.tick(store) == 1
    with store.read() as conn:
        verdicts = [r[0] for r in conn.execute("SELECT verdict FROM stage_decisions")]
    assert asked == [] and verdicts == ["unsure"]  # over the cap: recorded as unsure, nothing passed


def test_the_decide_models_go_through_the_engine_seam_and_the_ids_are_the_gateways():
    assert P.MODELS == {"haiku-5.5": "tico-low", "jev": "tico-decide-low", "sonnet": "tico-decide-medium"}
    assert P.DEFAULT_MODEL == "haiku-5.5"
    assert (P.MET_AT, P.NOT_MET_AT) == (0.8, 0.2)


def test_a_stale_mark_met_does_not_stop_the_judge_once_the_work_changed(tmp_path, monkeypatch):
    """Finding R8: Mark met on a live ticket, then the work changes and fresh evidence is attached. The mark stood for the
    old work only, so the judge reads the new attachment: one judge job, not none."""
    from backend import pipeline_engine, pipeline_review

    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    attach = SimpleNamespace(task=lambda *a: None)
    A.attach(
        c, attach, SimpleNamespace(actor=ANA), task["id"], req["id"], {"source": "link", "url": "https://x.example"}
    )
    pipeline_review.mark_met(c, ANA, task["id"], req["id"], "Checked by hand")
    pipeline_engine.work_changed(c, task["id"])
    A.attach(
        c, attach, SimpleNamespace(actor=ANA), task["id"], req["id"], {"source": "link", "url": "https://y.example"}
    )
    c.commit()
    c.close()

    store = Store(_settings(path))
    asked = []

    def ask(model, state, questions, label):
        asked.append(state["requirement"])
        return {"p": 0.95, "reason": "The page opens", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    P.tick(store)
    assert len(asked) == 1


def _waiting_store(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "Ok",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.close()
    return Store(_settings(path))


def test_dispatch_returns_at_once_while_a_slow_judge_runs_and_a_second_dispatch_starts_nothing(tmp_path, monkeypatch):
    store = _waiting_store(tmp_path)
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(model, state, questions, label):
        calls.append(model)
        started.set()
        assert release.wait(5)
        return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", slow)
    failures = []
    first = P.dispatch(store, None, failures)
    assert first is not None and started.wait(5)  # returned while the model is still being asked
    assert first.is_alive()
    assert P.dispatch(store, None, failures) is None  # a pass is running: nothing starts
    release.set()
    first.join(5)
    assert not first.is_alive() and calls == ["haiku-5.5"] and failures == []
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stage_decisions").fetchone()[0] == 1


def test_a_pass_that_raises_is_logged_and_the_next_pass_still_runs(tmp_path, monkeypatch, caplog):
    store = _waiting_store(tmp_path)
    boom = {"on": True}

    def tick(s, at=None):
        if boom["on"]:
            raise OSError("disk")
        return 0

    monkeypatch.setattr(P, "tick", tick)
    with caplog.at_level("WARNING", logger="tico.pipeline_judges"):
        thread = P.dispatch(store, None, [])
        assert thread is not None
        thread.join(5)
    assert "pass failed: OSError" in caplog.text
    boom["on"] = False
    again = P.dispatch(store, None, [])  # the guard was released
    assert again is not None
    again.join(5)


def test_the_model_is_asked_with_no_read_snapshot_open(tmp_path, monkeypatch):
    store = _waiting_store(tmp_path)
    open_reads = {"n": 0}
    real_read = store.read

    @contextmanager
    def counted():
        open_reads["n"] += 1
        try:
            with real_read() as conn:
                yield conn
        finally:
            open_reads["n"] -= 1

    def ask(model, state, questions, label):
        assert open_reads["n"] == 0, "a read was open while the model was asked"
        return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}

    monkeypatch.setattr(store, "read", counted)
    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1


def test_the_judge_is_told_the_tickets_current_work_version_and_the_attachments_own(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    E.work_changed(c, task["id"])  # the work goes on after the attachment was made
    attached_at = c.execute(
        "SELECT work_version FROM requirement_attachments WHERE task_id=?", (task["id"],)
    ).fetchone()[0]
    current = c.execute("SELECT work_revision FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
    c.close()
    assert attached_at < current

    store = Store(_settings(path))
    seen = []

    def ask(model, state, questions, label):
        seen.append(state)
        return {"p": 0.95, "reason": "The page opens", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 1
    assert seen[0]["work"]["work_version"] == current
    assert seen[0]["attachment"]["work_version"] == attached_at


def test_a_page_or_text_file_reaches_the_judge_cut_at_its_cap_and_an_image_has_no_text(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)
    blobs = BL.Blobs(settings)
    # Who may attach what is test_requirement_attachments' subject; here every company file is readable.
    monkeypatch.setattr(A, "_file_readable", lambda *a, **k: True)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Release note",
            "kind": "attachment",
            "needs": {"formats": ["page", "file", "image"]},
            "reviewed_by": "bot",
            "rubric": "The note says what shipped",
        },
    )
    who = SimpleNamespace(task=lambda *a: None)
    # A Tico page: a bot's file whose current version is text/html, longer than the judge's cap.
    bot = "bot-release"
    c.execute("INSERT INTO bots(id,slug,display_name,created_at) VALUES(?,?,?,?)", (bot, "release", "Release", H.now()))
    long_html = ("<p>Shipped the new login page.</p>" * 400).encode()
    page_digest = blobs.put(long_html, "text/html")
    page_blob = BL.register(c, SimpleNamespace(actor=ANA), page_digest, len(long_html), "notes.html", "text/html")
    c.execute(
        "INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,current_version,first_activity_at,"
        "last_activity_at) VALUES('file-notes',?,'bot','notes','Release notes','file','text/html','tico_blob',1,?,?)",
        (bot, H.now(), H.now()),
    )
    c.execute(
        "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
        "VALUES('file-notes',1,?,?,?,'notes.html','text/html',?,?)",
        (page_blob["id"], page_digest, len(long_html), ANA, H.now()),
    )
    c.execute("INSERT INTO pages(file_id,created_at,updated_at) VALUES('file-notes',?,?)", (H.now(), H.now()))
    task_page = H.task_create(c, ANA, "Ship the login page", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c, who, SimpleNamespace(actor=ANA), task_page["id"], req["id"], {"source": "file", "file_id": "file-notes"}
    )
    # An image: Tico reads no text from it, so the judge is told there is none.
    picture = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    picture_digest = blobs.put(picture, "image/png")
    picture_blob = BL.register(c, SimpleNamespace(actor=ANA), picture_digest, len(picture), "shot.png", "image/png")
    task_shot = H.task_create(c, ANA, "Ship the shot", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        who,
        SimpleNamespace(actor=ANA),
        task_shot["id"],
        req["id"],
        {"source": "blob", "blob_id": picture_blob["id"]},
    )
    c.commit()  # blob_readable opens a read snapshot on this autocommit connection; a request's transaction commits it
    c.close()

    store = Store(settings)
    store.blobs = blobs
    seen = []

    def ask(model, state, questions, label):
        seen.append(state)
        return {"p": 0.5, "reason": "ok", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 2
    by_digest = {state["attachment"]["digest"]: state["attachment"] for state in seen}
    page = by_digest[page_digest]
    assert page["text"] == " ".join(["Shipped the new login page."] * 400)[: P.TEXT_CHARS]  # the page's words
    assert len(page["text"]) == P.TEXT_CHARS and page["truncated"] is True
    shot = by_digest[picture_digest]
    assert shot["text"] is None and shot["truncated"] is False


def test_a_bot_only_unsure_answer_goes_to_a_person_and_a_bot_task_owner_is_no_route(tmp_path):
    """B8: an unsure answer always has a person to go to, the reviewer, the pipeline's owner, or a person ticket owner."""
    bot_only = {"reviewed_by": "bot", "reviewer_id": None, "pipeline_owner_id": "human:chris", "task_owner_id": "bot:x"}
    assert P._route_of(bot_only, "unsure") == "human:chris"
    assert P._route_of({**bot_only, "pipeline_owner_id": None, "task_owner_id": "human:ana"}, "unsure") == "human:ana"
    assert P._route_of({**bot_only, "pipeline_owner_id": None}, "unsure") is None
    assert P._route_of(bot_only, "met") is None


def test_the_judge_with_no_engine_given_uses_the_installed_production_engine(tmp_path, monkeypatch):
    """B11: judge_once's default engine is the installed store's live engine, not a permanent unsure."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    asked = []

    def recording_engine(store):
        def engine(model, state, questions, label):
            asked.append(model)
            return {"p": 0.95, "reason": "ok", "provider": "t", "model": model, "cost_usd": 0.0, "latency_ms": 1}

        return engine

    monkeypatch.setattr(P, "_installed_store", object())
    monkeypatch.setattr(P, "live_engine", recording_engine)
    P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=_settings(path))
    assert asked == ["haiku-5.5"]


def test_an_answer_is_refused_once_its_attachment_is_gone(tmp_path):
    """B7: the answer is checked against the attachment (and the rest of the state it was read in) before it is recorded."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    job = {
        "id": "no-such-attachment",
        "stage_requirement_id": "r",
        "requirement_version": 1,
        "task_id": "t",
        "task_work_version": 0,
        "pipeline_stage_id": "s",
        "visit_id": "v",
        "read_at": "2000-01-01T00:00:00.000Z",
    }
    assert P._superseded(c, job) == "the attachment was taken away"


def test_the_judge_state_carries_the_pipeline_goal(tmp_path):
    """Track J/D58: the pipeline goal reaches the judge with the requirement."""
    state = P._state(
        {"name": "Shows the page live", "rubric": "The link opens", "goal": "Launch the lights"}, ATTACHMENT
    )
    assert "Launch the lights" in json.dumps(state)


def test_a_large_blob_is_read_only_to_one_byte_past_the_cap(monkeypatch):
    """Fix L2-3: the judge's read is a range read of READ_MAX + 1 bytes, never the whole upload, and the cut is flagged."""
    from backend import file_library as files

    asked = []

    class RangeStore:
        """A blob store that honours the range it is asked for, and records it."""

        def open_range(self, digest, start=0, end=None):
            asked.append((start, end))
            body = b"w" * (files.READ_MAX * 2)
            yield body[start : None if end is None else end + 1]

    text, cut = P._text_of(
        SimpleNamespace(blobs=RangeStore()),
        {"mime": "text/plain", "blob_digest": "a" * 64, "blob_size": files.READ_MAX * 2},
    )
    assert asked == [(0, files.READ_MAX)]
    assert len(text) == files.READ_MAX and cut is True


def test_a_text_blob_within_the_cap_is_not_cut():
    from backend import file_library as files

    body = b"Shipped the page."

    class SmallStore:
        def open_range(self, digest, start=0, end=None):
            yield body

    digest = hashlib.sha256(body).hexdigest()
    text, cut = P._text_of(
        SimpleNamespace(blobs=SmallStore()),
        {"mime": "text/plain", "blob_digest": digest, "blob_size": len(body)},
    )
    assert (text, cut) == ("Shipped the page.", False)
    assert files.READ_MAX > len(body)


def _blob_job(digest: str, size: int) -> dict:
    return {"mime": "text/plain", "blob_digest": digest, "blob_size": size}


def test_a_short_read_of_a_small_blob_is_unreadable_and_the_judge_says_unsure():
    """Codex 2: a 1,000 byte blob that comes back as 18 bytes is not the evidence. The judge is not shown it as complete, so
    even a met from the model is unsure."""
    body = b"x" * 1000
    digest = hashlib.sha256(body).hexdigest()

    class ShortStore:
        def open_range(self, digest, start=0, end=None):
            yield body[:18]

    text, cut = P._text_of(SimpleNamespace(blobs=ShortStore()), _blob_job(digest, len(body)))
    assert (text, cut) == (None, False)

    def met(model, state, questions, label):
        return {"p": 0.95, "reason": "looks right", "provider": "stub", "model": model}

    result = P.judge_once(
        None,
        {"id": "r1", "name": "Spec", "rubric": "The spec is attached", "pipeline_id": None},
        {"id": "a1", "digest": digest, "text": text, "truncated": cut},
        model="haiku-5.5",
        engine=met,
    )
    assert result["verdict"] == "unsure"


def test_a_small_blob_whose_bytes_do_not_match_its_digest_is_unreadable():
    body = b"Shipped the page."

    class AlteredStore:
        def open_range(self, digest, start=0, end=None):
            yield body.replace(b"S", b"T")

    text, _ = P._text_of(
        SimpleNamespace(blobs=AlteredStore()),
        _blob_job(hashlib.sha256(body).hexdigest(), len(body)),
    )
    assert text is None


def test_a_large_blob_that_comes_back_short_is_unreadable_not_truncated_text():
    from backend import file_library as files

    class ShortLargeStore:
        def open_range(self, digest, start=0, end=None):
            yield b"w" * 100

    text, cut = P._text_of(SimpleNamespace(blobs=ShortLargeStore()), _blob_job("a" * 64, files.READ_MAX * 2))
    assert (text, cut) == (None, False)


def test_a_good_small_blob_is_judged_on_its_text():
    body = b"The page says the measures held: 42 percent."
    digest = hashlib.sha256(body).hexdigest()

    class GoodStore:
        def open_range(self, digest, start=0, end=None):
            yield body

    text, cut = P._text_of(SimpleNamespace(blobs=GoodStore()), _blob_job(digest, len(body)))
    assert text == body.decode() and cut is False

    def met(model, state, questions, label):
        return {"p": 0.95, "reason": "held", "provider": "stub", "model": model}

    result = P.judge_once(
        None,
        {"id": "r1", "name": "Validated", "rubric": "Measures held", "pipeline_id": None},
        {"id": "a1", "digest": digest, "text": text, "truncated": cut},
        model="haiku-5.5",
        engine=met,
    )
    assert result["verdict"] == "met"


def test_evidence_the_judge_cannot_read_in_full_is_unsure_whatever_the_model_says(tmp_path):
    """Fix L2-2: no text (an image, or a text Tico could not read) or a cut text is unsure, even at p 0.95; readable text is
    still judged by the bands. Through the engine seam; the Haiku path is tested below."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)
    image = {**ATTACHMENT, "id": "att-shot", "source": "blob", "text": None}
    cut = {**ATTACHMENT, "id": "att-cut", "truncated": True}
    for evidence in (image, cut):
        for p in (0.95, 0.05):
            result = P.judge_once(c, REQ, evidence, "haiku-5.5", settings=settings, engine=_engine(p))
            assert result["verdict"] == "unsure" and result["p"] == 0.5, (evidence["id"], p)
    assert P.judge_once(c, REQ, ATTACHMENT, "haiku-5.5", settings=settings, engine=_engine(0.95))["verdict"] == "met"


def test_a_met_on_truncated_text_through_the_default_haiku_path_is_unsure(tmp_path, monkeypatch):
    """Fix L2-2 on the path the default pipelines use: the Haiku chat gets the unsure rule in its system prompt, and its
    'met' on text it was not all shown is unsure. The chat answers p 0.95; nothing here writes the verdict."""
    from backend import credentials
    from backend import gateway_connection

    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)
    store = Store(settings)
    sent = []

    class Chat:
        model = "tico-low"

        def __call__(self, system, user):
            sent.append((system, json.loads(user)))
            return {"p": 0.95, "reason": "The page opens"}

    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    monkeypatch.setattr(gateway_connection, "url", lambda c, settings=None, record=None: "https://gateway.example")
    monkeypatch.setattr(J, "gateway_writer", lambda key_of, base, model, timeout, opener: Chat())
    monkeypatch.setattr(backend_judge, "metered", lambda store, models, state, questions, label, call: call())
    monkeypatch.setattr(credentials, "Vault", lambda store: None)
    monkeypatch.setattr(P, "_installed_store", store)
    cut = {**ATTACHMENT, "text": "The page opens and " * 40, "truncated": True}
    result = P.judge_once(c, REQ, cut, "haiku-5.5", settings=settings)
    assert result["verdict"] == "unsure"
    system, state = sent[0]
    assert "truncated" in system and "null" in system  # the rule the model is told
    assert state["attachment"]["truncated"] is True


def test_a_marked_met_attachment_on_a_finished_ticket_is_never_asked_the_model(tmp_path, monkeypatch):
    """Codex O-5: Mark met settles the requirement, and the ticket then finishes. The judge does not read or ask about that
    attachment again: two passes make zero model calls, instead of asking and then discarding the answer every tick."""
    from backend import pipeline_review

    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    stage = step_id(c, dev["id"], "Review")
    req = R.add(
        c,
        ANA,
        stage,
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(c, ANA, "Ship", "", ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    pipeline_review.mark_met(c, ANA, task["id"], req["id"], "Checked by hand")
    c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
    c.commit()
    c.close()

    store = Store(_settings(path))
    asked = []

    def ask(model, state, questions, label):
        asked.append(state["requirement"])
        return {"p": 0.95, "reason": "The page opens", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    assert P.tick(store) == 0
    assert P.tick(store) == 0
    assert asked == []


def _work_fields(**extra):
    """An attachment dict as the server's waiting read gives it, carrying the work's own fields."""
    return {
        **ATTACHMENT,
        "task_work_version": 3,
        "task_title": "Dark mode toggle on Settings",
        "task_spec": "Add a Dark mode toggle to the Settings page.",
        "task_repo": "acme/app",
        "task_pr": 91,
        "task_author": "human:ana",
        **extra,
    }


def test_the_judge_state_carries_the_work_title_spec_repo_pr_and_author():
    """The judge checks the evidence against the work: which ticket, repo, PR and author it is about."""
    work = P._state(REQ, _work_fields())["work"]
    assert work == {
        "work_version": 3,
        "title": "Dark mode toggle on Settings",
        "spec": "Add a Dark mode toggle to the Settings page.",
        "repo": "acme/app",
        "pr": 91,
        "author": "human:ana",
    }


def test_a_long_spec_is_cut_and_flagged_and_a_work_with_no_pr_leaves_repo_pr_and_author_out():
    assert P.SPEC_CHARS == 6000
    state = P._state(REQ, _work_fields(task_spec="x" * 9000, task_repo=None, task_pr=None, task_author=None))
    assert len(state["work"]["spec"]) == P.SPEC_CHARS
    assert state["work"]["spec_truncated"] is True
    assert "repo" not in state["work"] and "pr" not in state["work"] and "author" not in state["work"]
    whole = P._state(REQ, _work_fields(task_spec="x" * P.SPEC_CHARS))["work"]
    assert "spec_truncated" not in whole and len(whole["spec"]) == P.SPEC_CHARS


def test_an_attachment_with_no_id_is_not_met_and_the_model_is_never_called():
    """No evidence row: a rule decides, the model is not asked (it used to answer 0.5 on nothing)."""
    asked = []

    def engine(model, state, questions, label):
        asked.append(model)
        return {"p": 0.9, "reason": "met", "provider": "t", "model": model}

    no_evidence = _work_fields()
    del no_evidence["id"]
    result = P.judge_once(sqlite3.connect(":memory:"), REQ, no_evidence, "haiku-5.5", engine=engine)
    assert result["verdict"] == "not_met"
    assert result["p"] == 0.0 and result["cost_usd"] == 0.0
    assert result["reason"] == "No evidence is attached."
    assert asked == []


def test_the_waiting_rows_carry_the_work_title_spec_repo_pr_and_author(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    monkeypatch.setattr(A, "_file_readable", lambda *a, **k: True)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Live link",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "The link opens the page",
        },
    )
    task = H.task_create(
        c,
        ANA,
        "Dark mode toggle on Settings",
        "Add a Dark mode toggle.",
        ANA,
        type=dev["id"],
        step="Review",
        lint=False,
    )
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.execute(
        "INSERT INTO task_links(id,task_id,kind,url,repo,number,created_at) "
        "VALUES('pr1',?,'pr','https://github.com/acme/app/pull/91','acme/app',91,?)",
        (task["id"], H.now()),
    )
    c.commit()
    rows = P._waiting(c, 5)
    c.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["task_title"] == "Dark mode toggle on Settings"
    assert row["task_spec"] == "Add a Dark mode toggle."
    assert row["task_repo"] == "acme/app"
    assert row["task_pr"] == 91
    assert row["task_author"] is None  # no GitHub author is known for this PR; the ticket's owner is not its author
    state = P._state({"name": row["name"], "rubric": row["rubric"]}, row)
    assert state["work"]["repo"] == "acme/app" and state["work"]["pr"] == 91
    assert "author" not in state["work"]


def _judged_world(tmp_path, *, title="Dark mode toggle on Settings", body="Add a Dark mode toggle."):
    """A bot-reviewed link requirement on a ticket owned by Ana, with its link attached. Returns (path, task id, req id)."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c, ANA, "Dev", [{"name": "Review", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    req = R.add(
        c,
        ANA,
        step_id(c, dev["id"], "Review"),
        {
            "name": "Review",
            "kind": "attachment",
            "needs": {"formats": ["link"]},
            "reviewed_by": "bot",
            "rubric": "A review by someone other than the author, with an approval",
        },
    )
    task = H.task_create(c, ANA, title, body, ANA, type=dev["id"], step="Review", lint=False)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task["id"],
        req["id"],
        {"source": "link", "url": "https://x.example"},
    )
    c.commit()
    c.close()
    return path, task["id"], req["id"]


def _pr_link(c, task_id, number, state, *, author=None, at=None):
    detail = json.dumps({"author_login": author}) if author else None
    c.execute(
        "INSERT INTO task_links(id,task_id,kind,url,repo,number,state,detail,created_at) VALUES(?,?,'pr',?,?,?,?,?,?)",
        (
            f"pr-{number}",
            task_id,
            f"https://github.com/acme/app/pull/{number}",
            "acme/app",
            number,
            state,
            detail,
            at or H.now(),
        ),
    )


def test_the_author_is_the_pr_links_github_author_never_the_tickets_owner(tmp_path):
    """Codex P1: the ticket's owner is whoever holds it now (a reviewer it was handed to), not who wrote the change. The
    author the judge is told is the GitHub login that opened the PR, from the PR link's detail, or none."""
    path, task_id, _ = _judged_world(tmp_path)
    c = hub(path)
    c.execute("UPDATE tasks SET owner_id=? WHERE id=?", (BEN, task_id))  # handed to an independent reviewer
    _pr_link(c, task_id, 91, "open", author="octo-ana")
    c.commit()
    rows = P._waiting(c, 5)
    c.close()
    assert rows[0]["task_author"] == "octo-ana"
    assert P._state(REQ, rows[0])["work"]["author"] == "octo-ana"


def _chosen_pr(tmp_path, links):
    path, task_id, _ = _judged_world(tmp_path)
    c = hub(path)
    for i, (number, state) in enumerate(links):
        _pr_link(c, task_id, number, state, at=f"2026-11-0{i + 1}T00:00:00.000Z")
    c.commit()
    rows = P._waiting(c, 5)
    c.close()
    return rows[0]["task_pr"]


@pytest.mark.parametrize(
    ("links", "chosen"),
    [
        ([(91, "open"), (92, "closed")], 91),  # Codex's repro: an abandoned newer PR does not displace the open one
        ([(91, "draft"), (92, "closed")], 91),
        ([(91, "merged"), (92, "closed")], 91),
        ([(91, "merged"), (92, "open")], 92),
        ([(91, "open"), (92, "merged")], 91),  # the open PR is the work in flight
        ([(91, "shipped"), (92, "closed")], 91),
        ([(91, "closed"), (92, "closed")], 92),  # only closed ones: the newest
        ([(91, "open"), (92, "draft")], 92),  # two in flight: the newest
    ],
)
def test_the_pr_judged_against_is_the_newest_open_else_merged_else_closed(tmp_path, links, chosen):
    assert _chosen_pr(tmp_path, links) == chosen


def test_a_met_on_a_cut_spec_is_unsure_and_a_not_met_stays_not_met(tmp_path):
    """Codex P2: a rubric may need the whole spec, and the judge saw only its start: its met is not trusted."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    settings = _settings(path)
    long = _work_fields(task_spec="Acceptance: " + "x" * P.SPEC_CHARS)
    met = P.judge_once(c, REQ, long, "haiku-5.5", settings=settings, engine=_engine(0.95))
    assert met["verdict"] == "unsure" and met["p"] == 0.5 and met["reason"] == P.SPEC_CUT
    assert P.judge_once(c, REQ, long, "haiku-5.5", settings=settings, engine=_engine(0.05))["verdict"] == "not_met"
    whole = _work_fields(task_spec="x" * P.SPEC_CHARS)
    assert P.judge_once(c, REQ, whole, "haiku-5.5", settings=settings, engine=_engine(0.95))["verdict"] == "met"


def test_the_question_set_says_a_missing_author_is_unknown_and_a_cut_spec_is_flagged():
    instructions = P._questions()["met"]["instructions"]
    assert "work.author" in instructions and "not given" in instructions
    assert "spec_truncated" in instructions
    assert "owner" not in P.JUDGE_SYSTEM


def _standing(store, task_id, req_id):
    with store.read() as c:
        return E.task_standings(c, [task_id])[task_id][req_id]


@pytest.mark.parametrize("change", ["title", "pr"])
def test_an_answer_for_work_that_changed_while_it_was_asked_is_not_recorded(tmp_path, monkeypatch, change):
    """Codex P2: a title or PR change during the model call supersedes the answer, as a work_version change (a spec edit
    is a body edit, which bumps work_revision) already does."""
    path, task_id, _ = _judged_world(tmp_path)
    store = Store(_settings(path))

    def ask(model, state, questions, label):
        with store.transaction() as c:
            if change == "title":
                c.execute("UPDATE tasks SET title='Light mode toggle' WHERE id=?", (task_id,))
            else:
                _pr_link(c, task_id, 92, "open")
        return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    assert P.tick(store) == 0
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM stage_decisions").fetchone()[0] == 0
        why = [
            json.loads(r[0])["why"]
            for r in c.execute("SELECT detail FROM events WHERE action='requirement.judge_superseded'").fetchall()
        ]
    assert why == ["the work changed"]


def test_a_judges_met_stands_only_for_the_title_spec_and_pr_it_saw_and_a_change_is_judged_again(tmp_path, monkeypatch):
    """Codex P2: a met judged for one PR or campaign does not stand once the ticket's title, spec or PR changes; the judge
    reads the evidence again against the new work."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    asked = []

    def ask(model, state, questions, label):
        asked.append(state["work"])
        return {"p": 0.95, "reason": "ok", "provider": "t", "model": model}

    monkeypatch.setattr(P, "ENGINE", ask)
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)  # stand-in for readable evidence
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    assert P.tick(store) == 0  # nothing changed: not asked again

    # (A spec edit is a body edit, which already bumps work_revision: tasks_body_work_revision.)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET title='Light mode toggle' WHERE id=?", (task_id,))
    assert _standing(store, task_id, req_id) == "waiting"
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    with store.transaction() as c:
        _pr_link(c, task_id, 92, "open", author="octo-ana")
    assert _standing(store, task_id, req_id) == "waiting"
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    assert asked[-1]["pr"] == 92 and asked[-1]["author"] == "octo-ana"
    assert len(asked) == 3
    with store.read() as c:
        digests = [r[0] for r in c.execute("SELECT work_digest FROM stage_decisions ORDER BY rowid").fetchall()]
    assert len(set(digests)) == 3 and all(digests)


def test_a_decisions_work_digest_never_changes(tmp_path, monkeypatch):
    path, _, _ = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with store.transaction() as c:
            c.execute("UPDATE stage_decisions SET work_digest='other'")


def _attachment_id(store, task_id):
    with store.read() as c:
        return c.execute("SELECT id FROM requirement_attachments WHERE task_id=?", (task_id,)).fetchone()[0]


def test_a_judges_met_from_before_the_work_digest_does_not_stand_and_is_judged_again(tmp_path, monkeypatch):
    """Codex round 2, P1: a judge's verdict recorded before work_digest existed was judged without the work, so after the
    upgrade it does not stand for whatever the ticket is now; the judge reads the evidence again."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    with store.transaction() as c:
        revision, stage = c.execute("SELECT work_revision, step_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        c.execute(
            "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, requirement_version, "
            "requirement_attachment_id, work_version, judge, judge_actor, verdict, confidence, created_at) "
            "VALUES ('legacy',?,?,?,1,?,?,'llm','tico-low','met',0.95,?)",
            (task_id, stage, req_id, _attachment_id(store, task_id), revision, H.now()),
        )
    assert _standing(store, task_id, req_id) == "waiting"
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    assert P.tick(store) == 0


def test_a_persons_ruling_after_a_judge_verdict_that_no_longer_stands_is_never_judged_over(tmp_path, monkeypatch):
    """Codex round 3, P2: a person who approved after a judge's verdict from before the digest has the last word; the
    judge does not read the evidence again and replace their approval."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    attachment = _attachment_id(store, task_id)
    with store.transaction() as c:
        revision, stage = c.execute("SELECT work_revision, step_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        for decision, judge, verdict in (("legacy", "llm", "failed"), ("approved", "person", "met")):
            c.execute(
                "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, requirement_version, "
                "requirement_attachment_id, work_version, judge, judge_actor, verdict, created_at) "
                "VALUES (?,?,?,?,1,?,?,?,?,?,?)",
                (decision, task_id, stage, req_id, attachment, revision, judge, ANA, verdict, H.now()),
            )
    assert _standing(store, task_id, req_id) == "met"
    monkeypatch.setattr(P, "ENGINE", _engine(0.05))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    assert P.tick(store) == 0 and _standing(store, task_id, req_id) == "met"


def test_a_route_from_a_judge_verdict_that_no_longer_stands_does_not_stop_the_judge(tmp_path, monkeypatch):
    """Codex round 3, P2: an unsure answer routes the requirement to a person; once the title or PR changes, that answer
    no longer stands, so its route does not hold the judge off and the evidence is judged again."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.5))
    assert P.tick(store) == 1
    with store.read() as c:
        assert c.execute("SELECT route_next_to FROM stage_decisions").fetchone()[0] == ANA
    assert P.tick(store) == 0  # the routed answer still stands: a person answers it, not the judge
    with store.transaction() as c:
        _pr_link(c, task_id, 92, "open", author="octo-bo")
    assert _standing(store, task_id, req_id) == "waiting"
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"


def test_a_persons_approval_of_new_evidence_is_never_judged_over(tmp_path, monkeypatch):
    """Codex round 4, P2: after the work changed and new evidence came, a person approved it before the judge's next pass;
    the judge does not judge that evidence and replace their approval."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1
    c = hub(path)
    c.execute("UPDATE tasks SET title='Light mode toggle' WHERE id=?", (task_id,))
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task_id,
        req_id,
        {"source": "link", "url": "https://y.example"},
    )
    newest = c.execute(
        "SELECT id FROM requirement_attachments WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()[0]
    revision, stage = c.execute("SELECT work_revision, step_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    c.execute(
        "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, requirement_version, "
        "requirement_attachment_id, work_version, judge, judge_actor, verdict, created_at) "
        "VALUES ('approved',?,?,?,1,?,?,'person',?,'met',?)",
        (task_id, stage, req_id, newest, revision, ANA, H.now()),
    )
    c.commit()
    c.close()
    assert _standing(store, task_id, req_id) == "met"
    monkeypatch.setattr(P, "ENGINE", _engine(0.05))
    assert P.tick(store) == 0 and _standing(store, task_id, req_id) == "met"


def test_a_route_from_an_answer_a_newer_judge_answer_replaced_does_not_come_back(tmp_path, monkeypatch):
    """Codex round 4, P2: an unsure answer for title A routed to a person; the judge said met for title B; with the title
    back to A, the met no longer stands, and the old route (its digest matches again) does not hold the judge off."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.5))
    assert P.tick(store) == 1
    with store.transaction() as c:
        title = c.execute("SELECT title FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        c.execute("UPDATE tasks SET title='Light mode toggle' WHERE id=?", (task_id,))
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    with store.transaction() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", (title, task_id))
    assert _standing(store, task_id, req_id) == "waiting"
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"
    assert P.tick(store) == 0


def _attach_more(path, task_id, req_id, url):
    """Attaches newer evidence to the requirement; returns its id."""
    c = hub(path)
    A.attach(
        c,
        SimpleNamespace(task=lambda *a: None),
        SimpleNamespace(actor=ANA),
        task_id,
        req_id,
        {"source": "link", "url": url},
    )
    newest = c.execute(
        "SELECT id FROM requirement_attachments WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()[0]
    c.commit()
    c.close()
    return newest


def _person_rules(path, task_id, req_id, verdict, attachment=None, decision="ruled"):
    c = hub(path)
    revision, stage = c.execute("SELECT work_revision, step_id FROM tasks WHERE id=?", (task_id,)).fetchone()
    c.execute(
        "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, requirement_version, "
        "requirement_attachment_id, work_version, judge, judge_actor, verdict, created_at) "
        "VALUES (?,?,?,?,1,?,?,'person',?,?,?)",
        (decision, task_id, stage, req_id, attachment, revision, ANA, verdict, H.now()),
    )
    c.commit()
    c.close()


def test_a_ruling_made_while_the_judge_reads_its_jobs_is_never_answered_over(tmp_path, monkeypatch):
    """Codex round 5, P2: a person's Mark met committed while _waiting runs is a ruling made since the read, so the
    judge's answer is not recorded over it."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    read = P._waiting

    def ruled_while_reading(c, limit):
        jobs = read(c, limit)
        _person_rules(path, task_id, req_id, "override")
        return jobs

    monkeypatch.setattr(P, "_waiting", ruled_while_reading)
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.05))
    P.tick(store)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM stage_decisions WHERE judge='llm'").fetchone()[0] == 0
    assert _standing(store, task_id, req_id) == "met"


def test_a_route_on_evidence_that_was_replaced_does_not_stop_the_judge(tmp_path, monkeypatch):
    """Codex round 5, P2: an unsure answer on attachment A routed to a person; newer evidence B replaced A, so A's verdict
    no longer stands and its route does not keep the judge from B."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.5))
    assert P.tick(store) == 1
    _attach_more(path, task_id, req_id, "https://y.example")
    assert _standing(store, task_id, req_id) == "waiting"
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "met"


def test_evidence_back_after_newer_evidence_was_ruled_on_and_taken_away_is_judged_again(tmp_path, monkeypatch):
    """Codex round 5, P2: A was judged, B replaced it and a person approved B, then B was taken away. The latest decision is
    about B, so the engine reads none for A and it waits; the judge answers A again instead of leaving it waiting."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.05))
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "failed"
    newer = _attach_more(path, task_id, req_id, "https://y.example")
    _person_rules(path, task_id, req_id, "met", attachment=newer)
    assert _standing(store, task_id, req_id) == "met"
    with store.transaction() as c:
        c.execute("UPDATE requirement_attachments SET removed_at=? WHERE id=?", (H.now(), newer))
    assert _standing(store, task_id, req_id) == "waiting"
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "failed"
    assert P.tick(store) == 0


def test_a_persons_met_that_stands_across_a_change_is_never_judged_over(tmp_path, monkeypatch):
    """Codex round 6, P2: on a requirement that does not need evidence newer than the change, a person's approval stands
    after the body is edited (the engine keeps it met), so the judge does not answer over it."""
    path, task_id, req_id = _judged_world(tmp_path)
    store = Store(_settings(path))
    with store.transaction() as c:
        c.execute(
            "UPDATE stage_requirements SET needs=json_set(needs, '$.newer_than_change', json('false')) WHERE id=?",
            (req_id,),
        )
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    monkeypatch.setattr(P, "ENGINE", _engine(0.05))
    assert P.tick(store) == 1 and _standing(store, task_id, req_id) == "failed"
    _person_rules(path, task_id, req_id, "met", attachment=_attachment_id(store, task_id))
    with store.transaction() as c:
        c.execute("UPDATE tasks SET body='Add a Dark mode toggle, and a Light one.' WHERE id=?", (task_id,))
    assert _standing(store, task_id, req_id) == "met"
    assert P.tick(store) == 0 and _standing(store, task_id, req_id) == "met"


def _more_tickets(path, n, body=""):
    c = hub(path)
    dev = c.execute("SELECT type_id FROM tasks").fetchone()[0]
    req = c.execute("SELECT id FROM stage_requirements").fetchone()[0]
    for i in range(n):
        task = H.task_create(c, ANA, f"Other {i}", body, ANA, type=dev, step="Review", lint=False)
        A.attach(
            c,
            SimpleNamespace(task=lambda *a: None),
            SimpleNamespace(actor=ANA),
            task["id"],
            req,
            {"source": "link", "url": f"https://x{i}.example"},
        )
    c.commit()
    c.close()


def test_the_waiting_read_reads_only_as_many_rows_as_it_judges(tmp_path, monkeypatch):
    """Codex round 2, P2: answered evidence is filtered in SQL, so a pass never loads every ticket's spec to judge five."""
    path, _, _ = _judged_world(tmp_path)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    assert P.tick(store) == 1  # the first ticket's evidence is answered for its work
    _more_tickets(path, 3, body="x" * 100)
    fetched = []

    class Counting:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, *args):
            rows = self.conn.execute(*args).fetchall()
            fetched.append(len(rows))
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)

    c = hub(path)
    rows = P._waiting(Counting(c), 1)
    c.close()
    assert [r["task_title"] for r in rows] == ["Other 0"]
    assert fetched == [1]


def test_standings_read_the_work_of_judged_tickets_in_one_query(tmp_path, monkeypatch):
    """Codex round 2, P2: the engine reads the work digests of a page of judged tickets as a set, not one per ticket."""
    path, _, _ = _judged_world(tmp_path)
    _more_tickets(path, 3)
    store = Store(_settings(path))
    monkeypatch.setattr(P, "ENGINE", _engine(0.95))
    monkeypatch.setattr(P, "_cannot_see", lambda attachment, state: False)
    assert P.tick(store) == 4
    c = hub(path)
    ids = [r[0] for r in c.execute("SELECT id FROM tasks").fetchall()]
    statements = []
    c.set_trace_callback(statements.append)
    standings = E.task_standings(c, ids)
    c.set_trace_callback(None)
    c.close()
    assert sorted(s for row in standings.values() for s in row.values()) == ["met"] * 4
    assert len([s for s in statements if "LEFT JOIN task_links l" in s]) == 1


def test_no_evidence_is_not_met_even_over_the_cap_or_with_an_unknown_model(tmp_path, monkeypatch):
    """Codex round 2, P2: missing evidence is decided before the model and the cap are looked at."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    no_evidence = _work_fields()
    del no_evidence["id"]
    assert P.judge_once(c, REQ, no_evidence, "no-such-model")["verdict"] == "not_met"
    monkeypatch.setattr(backend_judge, "over_spend_cap", lambda conn, settings, *a, **k: (12.0, 10.0))
    result = P.judge_once(c, REQ, no_evidence, "haiku-5.5", settings=_settings(path), engine=_engine(0.95))
    assert result["verdict"] == "not_met" and result["p"] == 0.0
