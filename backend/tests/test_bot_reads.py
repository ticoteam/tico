"""What a bot itself reads through the `hub` CLI: the org chart with each bot's reports_to and department, and the
daily and weekly updates. Runs the real CLI against a live server, as the bot."""

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path

from backend import bot_access as A
from backend import groups as G
from backend.tests.test_api import api, setup_attempt  # noqa: F401
from backend.tests.test_runner import live  # noqa: F401
from clients import hubcli


def hub(live, token, *argv):
    """`hub <argv>` as a bot in a turn: (exit code, parsed JSON output)."""
    env = {"HUB_API_URL": live, "HUB_TOKEN": token}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            code = hubcli.main(list(argv))
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return code, json.loads(out.getvalue())


def test_a_bot_sees_the_other_bots_with_reports_to_and_department_except_those_it_may_not_see(api, live):
    _, _, attempt = setup_attempt(api, "ops")
    hidden = A.stored({"see": A.audience({"people": ["ana"]}), "read": A.audience({"people": ["ana"]}),
                       "write": A.audience({"people": ["ana"]})})
    with api.app.state.store.transaction() as c:
        for slug, boss, template in (("cpo", "human:ana", "support-lead"), ("ops", "cpo", "support"),
                                     ("finance", "cpo", None), ("doc-updater", "cpo", None),
                                     ("product-design", "doc-updater", None), ("coo", "human:ana", None)):
            c.execute("UPDATE bot_config SET reports_to=?, config_json=json_set(config_json, '$.template', ?) WHERE bot=?",
                      (boss, template, slug))
        c.execute("UPDATE bot_config SET access_json=? WHERE bot='doc-updater'", (hidden,))
        # A department is a group now (first start made every bot name "" for none): cpo names its group,
        # and ops and finance, naming none, are in their manager's.
        c.execute("UPDATE bot_config SET config_json=json_remove(config_json, '$.team') WHERE bot IN ('ops', 'finance')")
        roster = G._roster(c)
        G._set_bot(c, "cpo", G.ensure(roster, "customer-support", "Customer Support"))
        G._save(c, roster)

    code, org = hub(live, attempt["token"], "team", "show")
    assert code == 0
    bots = {b["id"]: b for b in org["bots"]}
    assert set(bots) == {"coo", "ops", "cpo", "product-design", "finance"}, "everything but the bot it may not see"
    assert [bots[b]["reports_to"] for b in ("cpo", "ops", "finance")] == ["human:ana", "cpo", "cpo"]
    assert bots["cpo"]["department"] == bots["ops"]["department"] == "Customer Support", "its group's name, as the bot itself or through its manager"
    assert bots["finance"]["department"] == "Customer Support", "in no group of its own: its manager's group"
    assert bots["coo"]["department"] == "", "a bot in no department says so"
    assert (bots["ops"]["template"], bots["finance"]["template"]) == ("support", "")
    assert bots["product-design"]["reports_to"] == "cpo" and bots["product-design"]["org_parent"] == "b:cpo", \
        "under the hidden bot's manager, not dropped"
    assert {p["id"] for p in org["people"]} == {"ana", "ben", "cara"}

