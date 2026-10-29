"""The health checks read the company's settings once a page, and each bot's own rows once for all the bots."""
import contextlib

from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_status_reads import busy, setup


def counted(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements it took on the one snapshot it read."""
    store, seen = api.app.state.store, []
    store.__dict__.pop("roster_rows", None)          # per-store caches start empty, so every count is the same count
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):
            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(seen.append)
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)
                return borrowed()
            patched.setattr(store, name, traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def test_health_issues_cost_a_few_statements_per_extra_bot(api, monkeypatch):
    # Production: the bot-by-bot pass over every bot read about 30 statements a bot (its tools, its repositories, its
    # usage limit, its status, the company's settings once per bot). The company's settings are read once a page now,
    # and each bot's rows are read for all bots at once, so a bot costs about its own tools and repositories, not a repeat of the company's rows.
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/v2/health/issues")              # warm: the table checks each connection keeps
    few = counted(api, monkeypatch, "/api/v2/health/issues")[1]
    busy(api, machine, 6, 12)
    body, many = counted(api, monkeypatch, "/api/v2/health/issues")
    assert body["counts"] and body["bots"] >= 12
    assert (many - few) / 6 <= 12, (few, many)   # about 33 a bot before; what is left is each bot's own tools and repositories


def test_health_page_reads_the_same_statements_however_many_bots_it_shows(api, monkeypatch):
    machine = setup(api, monkeypatch, tasks=2)
    busy(api, machine, 0, 6)
    counted(api, monkeypatch, "/api/v2/health")
    few = counted(api, monkeypatch, "/api/v2/health")[1]
    busy(api, machine, 6, 12)
    assert counted(api, monkeypatch, "/api/v2/health")[1] == few
