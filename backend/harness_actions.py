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
            "state": row["state"], "message": row["message"], "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def sweep(c):
    now = H.now()
    c.execute("UPDATE runner_harness_actions SET state='failed', message='The computer did not answer in time', "
              "updated_at=? WHERE state IN ('requested','running') AND created_at<?",
              (now, H.shift(now, hours=-EXPIRES_HOURS)))
    c.execute("DELETE FROM runner_harness_actions WHERE created_at<?", (H.shift(now, days=-KEEP_DAYS),))


def request(c, who, rid, harness, action):
    if who.role != "owner":
        raise Problem("forbidden", "Only the owner can change the tools on a computer", 403)
    if action not in ACTIONS:
        raise Problem("kind", "Unknown action", 422)
    runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
    if not runner:
        raise Problem("not_found", "That computer is not registered", 404)
    known = readiness_document(runner["readiness"]).get("harnesses") or {}
    if harness not in known:
        raise Problem("not_found", "That computer has not reported this harness", 404)
    if not runner["last_seen_at"] or runner["last_seen_at"] < H.shift(H.now(), seconds=-ONLINE_SECONDS):
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
    c.execute("INSERT INTO runner_harness_actions(id,runner_id,harness,action,state,created_at,updated_at,requested_by) "
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
    report = (readiness_document(runner["readiness"]).get("harnesses") or {}).get(harness)
    if report is None:
        raise Problem("not_found", "That computer has not reported this harness", 404)
    # The runner keeps what a bot there runs on, its fallback included, so an "off" for it would only wait to apply.
    if not enabled and (report.get("runtime") or runtime_of(harness)) in providers.runtimes_run_on(c, settings).get(rid, ()):
        raise Problem("needed", "A bot on this computer runs on this tool, so it stays on", 409)
    now = H.now()
    c.execute("INSERT INTO runner_harness_choices(runner_id,harness,enabled,updated_at,updated_by) VALUES(?,?,?,?,?) "
              "ON CONFLICT(runner_id,harness) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at, "
              "updated_by=excluded.updated_by", (rid, harness, int(bool(enabled)), now, who.actor))
    H.event(c, who.actor, "runner.harness." + ("on" if enabled else "off"), rid, {"harness": harness})
    return {"runner_id": rid, "harness": harness, "enabled": bool(enabled)}


def choices(c, rid):
    """{harness: True | False} the owner set for this computer; a harness not listed was never chosen."""
    return {r["harness"]: bool(r["enabled"]) for r in c.execute(
        "SELECT harness,enabled FROM runner_harness_choices WHERE runner_id=?", (rid,))}


def choices_by_runner(c):
    """{runner_id: {harness: enabled}} for every computer in one pass (Operations listing)."""
    out = {}
    for r in c.execute("SELECT runner_id,harness,enabled FROM runner_harness_choices"):
        out.setdefault(r["runner_id"], {})[r["harness"]] = bool(r["enabled"])
    return out


def recent(c, rid):
    """Requests still in flight, and the last finished one per harness, for the Computers page. Reads
    only: the page loads on a read connection, and `sweep` runs on every write."""
    rows = c.execute("SELECT * FROM runner_harness_actions WHERE runner_id=? ORDER BY created_at DESC LIMIT 30",
                     (rid,)).fetchall()
    seen, out = set(), []
    for row in rows:
        if row["state"] in ACTIVE or row["harness"] not in seen:
            out.append(view(row))
        seen.add(row["harness"])
    return out


def recent_by_runner(c):
    """{runner_id: recent()} for every computer. Newest-first scan keeps each runner's latest 30 rows."""
    newest = {}
    for row in c.execute("SELECT * FROM runner_harness_actions ORDER BY created_at DESC"):
        bucket = newest.setdefault(row["runner_id"], [])
        if len(bucket) < 30:
            bucket.append(row)
    out = {}
    for rid, rows in newest.items():
        seen, items = set(), []
        for row in rows:
            if row["state"] in ACTIVE or row["harness"] not in seen:
                items.append(view(row))
            seen.add(row["harness"])
        out[rid] = items
    return out


def sweep_due(c):
    """Whether `sweep` has anything to change: a poll reads without the write lock when it has not."""
    now = H.now()
    return bool(c.execute("SELECT 1 FROM runner_harness_actions WHERE (state IN ('requested','running') AND created_at<?) "
                          "OR created_at<? LIMIT 1", (H.shift(now, hours=-EXPIRES_HOURS), H.shift(now, days=-KEEP_DAYS))).fetchone())


def pending(c, runner_id, swept=True):
    if swept:
        sweep(c)
    rows = c.execute("SELECT * FROM runner_harness_actions WHERE runner_id=? AND state IN ('requested','running') "
                     "ORDER BY created_at", (runner_id,)).fetchall()
    return [{"id": r["id"], "harness": r["harness"], "action": r["action"]} for r in rows]


def report(c, runner_id, aid, body):
    row = c.execute("SELECT * FROM runner_harness_actions WHERE id=? AND runner_id=?", (aid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That request does not exist", 404)
    if row["state"] in TERMINAL:
        return {"state": row["state"]}
    c.execute("UPDATE runner_harness_actions SET state=?, message=?, updated_at=? WHERE id=?",
              (body.state, redact(body.message), H.now(), aid))
    if body.state in TERMINAL:
        H.event(c, "runner:" + runner_id, "runner.harness." + body.state, runner_id,
                {"harness": row["harness"], "action": row["action"]})
    return {"state": body.state}
