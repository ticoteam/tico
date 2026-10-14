"""Keeping a computer's model CLIs current from Settings: Update, Pin to this version, Resume updates, and
which ones the computer installs at all.

The owner asks for one action on one harness of one computer; the runner asks the server what is
waiting (the same shape as the browser sign-in relay in model_login.py), applies it between turns
and reports back. Update waits until no running turn uses the harness. Only the owner may ask, and
every request leaves an event.

A computer installs the harnesses its bots run on by itself. The owner can turn any other one on for a computer
(to sign it in before moving bots there) or turn one off, which uninstalls the runner's own copy once no bot there
needs it. The choice is kept per computer here and handed to the runner with its pending actions (`choices`);
a harness never chosen is neither, and the runner keeps whatever it already has.
"""

from . import providers
from .harnesses import runtime_of
from .model_login import ONLINE_SECONDS, redact
from .store import H, Problem, readiness_document

ACTIONS = ("update", "pin", "unpin")
ACTIVE = ("requested", "running")
TERMINAL = ("done", "failed")
KEEP_DAYS = 2
# Longer than a slow install plus the wait for a long turn; after this the request is forgotten.
EXPIRES_HOURS = 6


def view(row):
    return {"id": row["id"], "runner_id": row["runner_id"], "harness": row["harness"], "action": row["action"],
            "state": row["state"], "message": row["message"], "created": row["created"],
            "updated": row["updated"]}


def sweep(c):
    now = H.now()
    c.execute("UPDATE runner_harness_actions SET state='failed', message='The computer did not answer in time', "
              "updated=? WHERE state IN ('requested','running') AND created<?",
              (now, H.shift(now, hours=-EXPIRES_HOURS)))
    c.execute("DELETE FROM runner_harness_actions WHERE created<?", (H.shift(now, days=-KEEP_DAYS),))


def request(c, who, rid, harness, action):
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner can change the tools on a computer", 403)
    if action not in ACTIONS:
        raise Problem("kind", "Unknown action", 422)
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
    if not runner:
        raise Problem("not_found", "That computer is not registered", 404)
    known = readiness_document(runner["readiness_json"]).get("harnesses") or {}
    if harness not in known:
        raise Problem("not_found", "That computer has not reported this harness", 404)
    if not runner["last_seen"] or runner["last_seen"] < H.shift(H.now(), seconds=-ONLINE_SECONDS):
        raise Problem("offline", "That computer is offline, so it cannot change its tools", 409)
    report = known[harness]
    if not report.get("managed") and action != "unpin":
        raise Problem("unmanaged", "This tool was installed outside Tico; update it where it was installed", 409)
    if action == "pin" and not report.get("version"):
        raise Problem("state", "The installed version is not known yet", 409)
    sweep(c)
    same = c.execute("SELECT * FROM runner_harness_actions WHERE runner_id=? AND harness=? AND action=? "
                     "AND state IN ('requested','running')", (rid, harness, action)).fetchone()
    if same:
        return view(same)
    now, aid = H.now(), H.new_id()
    c.execute("INSERT INTO runner_harness_actions(id,runner_id,harness,action,state,created,updated,requested_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (aid, rid, harness, action, "requested", now, now, who.actor))
    H.event(c, who.actor, "runner.harness." + action, rid,
            {"harness": harness, "version": report.get("version", "")})
    return view(c.execute("SELECT * FROM runner_harness_actions WHERE id=?", (aid,)).fetchone())


def choose(c, who, rid, harness, enabled, settings=None):
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner can change the tools on a computer", 403)
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
    if not runner:
        raise Problem("not_found", "That computer is not registered", 404)
    report = (readiness_document(runner["readiness_json"]).get("harnesses") or {}).get(harness)
    if report is None:
        raise Problem("not_found", "That computer has not reported this harness", 404)
    # The runner keeps what a bot there runs on, its fallback included, so an "off" for it would only wait to apply.
    if not enabled and (report.get("runtime") or runtime_of(harness)) in providers.runtimes_run_on(c, settings).get(rid, ()):
        raise Problem("needed", "A bot on this computer runs on this tool, so it stays on", 409)
    now = H.now()
    c.execute("INSERT INTO runner_harness_choices(runner_id,harness,enabled,updated,updated_by) VALUES(?,?,?,?,?) "
              "ON CONFLICT(runner_id,harness) DO UPDATE SET enabled=excluded.enabled, updated=excluded.updated, "
              "updated_by=excluded.updated_by", (rid, harness, int(bool(enabled)), now, who.actor))
    H.event(c, who.actor, "runner.harness." + ("on" if enabled else "off"), rid, {"harness": harness})
    return {"runner_id": rid, "harness": harness, "enabled": bool(enabled)}


def choices(c, rid):
    """{harness: True | False} the owner set for this computer; a harness not listed was never chosen."""
    return {r["harness"]: bool(r["enabled"]) for r in c.execute(
        "SELECT harness,enabled FROM runner_harness_choices WHERE runner_id=?", (rid,))}


def recent(c, rid):
    """Requests still in flight, and the last finished one per harness, for the Computers page. Reads
    only: the page loads on a read connection, and `sweep` runs on every write."""
    rows = c.execute("SELECT * FROM runner_harness_actions WHERE runner_id=? ORDER BY created DESC LIMIT 30",
                     (rid,)).fetchall()
    seen, out = set(), []
    for row in rows:
        if row["state"] in ACTIVE or row["harness"] not in seen:
            out.append(view(row))
        seen.add(row["harness"])
    return out


def pending(c, runner_id):
    sweep(c)
    rows = c.execute("SELECT * FROM runner_harness_actions WHERE runner_id=? AND state IN ('requested','running') "
                     "ORDER BY created", (runner_id,)).fetchall()
    return [{"id": r["id"], "harness": r["harness"], "action": r["action"]} for r in rows]


def report(c, runner_id, aid, body):
    row = c.execute("SELECT * FROM runner_harness_actions WHERE id=? AND runner_id=?", (aid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That request does not exist", 404)
    if row["state"] in TERMINAL:
        return {"state": row["state"]}
    c.execute("UPDATE runner_harness_actions SET state=?, message=?, updated=? WHERE id=?",
              (body.state, redact(body.message), H.now(), aid))
    if body.state in TERMINAL:
        H.event(c, "runner:" + runner_id, "runner.harness." + body.state, runner_id,
                {"harness": row["harness"], "action": row["action"]})
    return {"state": body.state}
