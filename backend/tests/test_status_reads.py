"""The status page reads each table once for every bot, and says exactly what each bot's own status line says."""
from backend import task_privacy as privacy, usage_limits
from backend.events import prime_status_lines, status_line
from backend.store import H
from backend.tests.test_api import api, assign, claim, post, ready  # noqa: F401
from backend.tests.test_run_backoff import hold_bot, setup


def lines(api, slugs, primed):
    """Every bot's status line from one snapshot, and the statements it took."""
    store, auth, who = api.app.state.store, api.app.state.auth, api.app.state.store.settings.test_identities["ana-test"]
    seen = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        default = usage_limits.company(c)
        inputs = privacy.status_inputs(c, slugs)
        if primed:
            prime_status_lines(c, slugs)
        out = {slug: status_line(c, who, slug, default, inputs[slug], auth=auth) for slug in slugs}
        c.set_trace_callback(None)
    return out, len(seen)


def test_primed_status_lines_match_each_bots_own_and_read_each_table_once(api, monkeypatch):
    machine = setup(api, monkeypatch)
    hold_bot(api, machine, monkeypatch)                  # ops: held after five failed runs, its last stop on record
    assign(api, machine, "doc-updater")
    ready(api, machine, ["ops", "doc-updater"])
    post(api, "tasks", {"owner": "doc-updater", "title": "Update the guide", "body": "Please update it."})
    live = claim(api, machine, "doc-updater")
    post(api, f"attempts/{live['id']}/started", {"thread_id": "t-live"}, machine["token"])       # a live run
    with api.app.state.store.transaction() as c:
        for slug, state in (("cpo", "idle"), ("finance", "idle"), ("coo", "crashed"), ("inbox", "idle")):
            H.status_set(c, H.KEEPER, slug, state=state, focus="Checked the queue")
        c.execute("INSERT INTO bot_budget(bot,held_at,window,runs,budget) VALUES('cpo',?,'hour',301,300)", (H.now(),))
        c.execute("INSERT INTO run_backoff(bot,fails,wait_until) VALUES('finance',2,?)",
                  (H.shift(H.now(), seconds=600),))
        c.execute("INSERT INTO usage_limits(bot,daily_usd) VALUES('coo',0.000001)")
    slugs = ["coo", "cpo", "doc-updater", "finance", "inbox", "ops", "product-design"]
    each, plain = lines(api, slugs, primed=False)
    together, primed = lines(api, slugs, primed=True)
    assert together == each
    assert each["ops"]["stopped"] == "run_hold" and each["cpo"]["stopped"] == "budget"
    assert each["finance"]["backoff"] and each["doc-updater"]["state"] == "running"
    # Seven single-row reads a bot become one read a table: what is left does not grow with the bots.
    # One read a table for every bot: idle bots added to the page add no statements, where each added several.
    fewer = [s for s in slugs if s not in ("inbox", "product-design")]
    assert lines(api, fewer, primed=True)[1] == primed < lines(api, fewer, primed=False)[1] < plain
