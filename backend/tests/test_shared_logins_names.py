"""Operations shared_logins can resolve display names from one registry pass."""

import json

from backend.tests.test_api import api, runner  # noqa: F401
from backend import views
from backend.subscription_identity import connection_id, display_names, identity


def test_display_names_batch_matches_identity(api):
    r = runner(api, "ana", "Studio Mac")
    rid = r["runner_id"]
    sid = connection_id(rid, "work")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  (sid, json.dumps({"display_name": "Work ChatGPT"})))
    with api.app.state.store.read() as c:
        names = display_names(c)
        assert names[sid] == "Work ChatGPT"
        assert identity(c, rid, "work")["display_name"] == names[sid]


def test_shared_logins_uses_preloaded_names(api, monkeypatch):
    r = runner(api, "ana", "Studio Mac")
    rid = r["runner_id"]
    sid = connection_id(rid, "work")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  (sid, json.dumps({"display_name": "Work ChatGPT"})))
    monkeypatch.setattr(views, "subscription_login", lambda runtime, detail: True)
    monkeypatch.setattr("backend.harnesses.harness_caps",
                        lambda runtime, *a, **k: {"one_turn_per_login": True})
    readiness = {"runtimes": {"fake": {"profiles": {"work": {"detail": "x"}}}},
                 "bots": {"coo": {"runtime": "fake", "profile": "work"},
                          "cpo": {"runtime": "fake", "profile": "work"}}}
    with api.app.state.store.read() as c:
        names = display_names(c)
        rows = views.shared_logins(c, rid, readiness, {"coo", "cpo"},
                                   {"coo": {"read": True}, "cpo": {"read": True}}, names=names)
        assert rows == [{"runtime": "fake", "profile": "work", "display_name": "Work ChatGPT",
                         "count": 2, "bots": ["coo", "cpo"]}]
