"""Throwaway QA for #3544: recent runs and the listening filter, through the real route."""
import json
from backend.tests.helpers.api import get
from backend.tests.helpers.automation_api import as_bot
from backend.tests.test_automation_overview import _make, _run

def call(api, q, who=None, expected=None):
    kw = {} if expected is None else {"expected": expected}
    try:
        body = get(api, "automations/runs?range=1h" + q, *( [who] if who else [] ), **kw)
        return expected or 200, body
    except AssertionError as e:
        return "error", str(e)[:200]

def names(body, by):
    return sorted(by.get(r["id"], r["id"]) for r in body.get("runs", [])) if isinstance(body, dict) else body

def test_qa(api):
    out = []
    def line(tag, q, status, got):
        out.append(f"{tag}  GET /api/v2/automations/runs?range=1h{q}  ->  {status}  {got}")
    # E1 empty state first: no listening automation yet
    s, b = call(api, "&trigger=listening"); line("E1 empty team", "&trigger=listening", s, names(b, {}))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    watch = _make(api, "Competitor watch", "person:ana", kind="listening")
    daily = _make(api, "Daily", "person:ana", kind="schedule")
    cara = _make(api, "Cara's listening", "person:cara", kind="listening")
    by = {}
    an = {watch: "Competitor watch", daily: "Daily", cara: "Cara's listening"}
    by[_run(api, watch, "ok", 1)] = "Competitor watch ok"
    by[_run(api, watch, "failed", 3)] = "Competitor watch failed"
    by[_run(api, daily, "ok", 2)] = "Daily ok"
    by[_run(api, cara, "ok", 4)] = "Cara's listening ok"
    for tag, q in [("A1", "&trigger=listening"), ("A2", ""), ("A3", "&trigger=listening,schedule"),
                   ("E3 owner", "&trigger=listening&owner=person:ana"), ("E4 status", "&trigger=listening&status=failed"),
                   ("E6 repeated", "&trigger=listening,listening")]:
        s, b = call(api, q); line(tag, q, s, names(b, by))
    s, b = call(api, "&trigger=nonsense", expected=422); line("A4", "&trigger=nonsense", s, json.dumps(b)[:160])
    s, b = call(api, "&trigger=Listening", expected=422); line("E6 capital L", "&trigger=Listening", s, "refused")
    # E2 no permission: ben may see ana's? he sees ops only; cara sees her own; a peer bot sees nothing
    s, b = call(api, "&trigger=listening", "ben-test"); line("E2 ben", "&trigger=listening", s, names(b, by))
    s, b = call(api, "&trigger=listening", "cara-test"); line("E2 cara", "&trigger=listening", s, names(b, by))
    s, b = call(api, "&trigger=listening", as_bot(api, "finance")); line("E2 peer bot", "&trigger=listening", s, names(b, by))
    s, b = call(api, f"&trigger=listening&ids={cara}", "ben-test"); line("E2 ben by id", "&trigger=listening&ids=<cara's>", s, names(b, by))
    st = get(api, "automations/stats?range=1h")
    out.append(f"E5 stats  GET /api/v2/automations/stats?range=1h  ->  200  automations={sorted(an[a['id']] for a in st['automations'])} runs={st['totals']['runs']}")
    # E7 the agent tool's request
    from clients.commands import automations as cmd
    seen = []
    class P:
        def get(self, path): seen.append(path); return {}
    cmd._as_person = lambda api: P()
    cmd.automation_recent_runs(None, {"range": "7d", "trigger": "listening,schedule"})
    out.append(f"E7 tool  tico_automation_recent_runs(range=7d, trigger=listening,schedule) sends  {seen[0]}")
    print("\n" + "\n".join(out))
