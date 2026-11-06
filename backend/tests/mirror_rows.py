"""Read-only built-in automation mirrors in tests: list them, find a live one, run the sync, make a dead one.
Shared by test_system_jobs_list.py and test_heartbeat.py, kept out of a test module so importing it does not pull in
that module's whole-backend scans (scripts/pr_checks.py selection)."""

import json

from backend import automations
from backend import redesign_migration as M


def db(api):
    return api.app.state.store


def mirrors(api):
    """Every read-only built-in automation: {id: (trigger name, source id, deleted_at, enabled)}."""
    with db(api).read() as c:
        out = {}
        for row in c.execute("SELECT * FROM automations WHERE builtin=1 AND json_extract(flow,'$.readonly')=1"):
            flow = json.loads(row["flow"])
            out[row["id"]] = (flow["trigger"]["name"], (flow.get("source") or {}).get("id"), row["deleted_at"],
                              row["enabled"])
        return out


def live(api, name):
    return [aid for aid, (trigger, _, deleted, _) in mirrors(api).items() if trigger == name and not deleted]


def sync(api):
    M.catch_up(db(api))                                          # a cutover that waited for its owner, as the tick does
    with db(api).transaction() as c:
        return automations.sync_system_mirrors(c)


def dead_mirror(api, name, key, source, words="Gone"):
    """A mirror the cutover made for something Tico no longer runs."""
    with db(api).transaction() as c:
        who = M.system_who(c)
        flow = M._mirror(name, M._owner_person(who), key, {"on": True}, source, words)
        return M._save(c, who, flow)
