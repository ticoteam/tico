"""Which release each computer runs, and whether this server can work with it.

A runner reports its release (`0.3.0`, or "" for a checkout that is not on a release tag), its kind
(mac, linux, docker) and how its self-update stands. The server answers with the release it runs
(`GET /api/v2/runners/desired`); runners follow that, never GitHub. `MIN_RUNNER_RELEASE` is the
oldest runner this server accepts: raise it in the release that changes the runner/server contract.
A runner below it is paused (it gets no work, with the reason) instead of failing turns. A runner that
does not report a release (before this existed, or a development checkout) is never paused.

A computer behind the server's release, or on no release at all, is outdated: Computers flags it, and its
owner gets one notice per server release once it has stayed outdated for `NUDGE_AFTER_S`. The wait leaves
room for the self-update that normally follows a server release within minutes, so only a computer that is
stuck gets a nudge.
"""
from . import releases
from .store import H

MIN_RUNNER_RELEASE = "0.4.0"
NUDGE_AFTER_S = 3600
KINDS = ("mac", "linux", "docker")
LABELS = {"current": "Up to date", "updating": "Updating", "needs_update": "Needs update",
          "incompatible": "Incompatible (bots paused)", "unknown": "Release not reported"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_versions(
 runner_id TEXT PRIMARY KEY REFERENCES runners(id), release TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL DEFAULT '', update_state TEXT NOT NULL DEFAULT '', update_target TEXT NOT NULL DEFAULT '',
 update_error TEXT NOT NULL DEFAULT '', reported_at TEXT NOT NULL);
"""


def ensure_schema(c):
    """Columns added after the table first shipped: the commit it runs, since when it has been outdated,
    and the server release its owner was last told about."""
    H.add_column(c, "runner_versions", "commit_sha", "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, "runner_versions", "outdated_since", "TEXT")
    H.add_column(c, "runner_versions", "nudged_at", "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, "runner_versions", "outdated_for", "TEXT NOT NULL DEFAULT ''")
    H.add_column(c, "runner_versions", "update_since", "TEXT")
    H.add_column(c, "runner_versions", "update_running", "INTEGER")
    H.add_column(c, "runner_versions", "update_handed_back", "INTEGER")


def clean(value):
    return str(value or "").strip().lstrip("v")


def desired():
    """What a runner should be on: this server's release, or "" for a build that has none (dev)."""
    server = releases.version()
    return {"version": server if releases.parse(server) else "", "min_runner": MIN_RUNNER_RELEASE}


def incompatible(release):
    parsed = releases.parse(release)
    return bool(parsed and parsed < releases.parse(MIN_RUNNER_RELEASE))


def at_least(c, runner_id, minimum):
    """One-time runner steps require a reported version that supports the step.

    Unknown versions keep the step pending, rather than consuming it without doing it.
    """
    row = c.execute("SELECT release FROM runner_versions WHERE runner_id=?", (runner_id,)).fetchone()
    have = releases.parse(row["release"]) if row else None
    want = releases.parse(minimum)
    return bool(have and want and have >= want)


def outdated(release, server=None):
    """Behind the server's release, or on none it can compare. Never on a development server, which has no release."""
    want = releases.parse(releases.version() if server is None else server)
    have = releases.parse(release)
    return bool(want and (not have or have < want))


def state(release, update_state="", server=None):
    """One of current, updating, needs_update, incompatible, unknown."""
    server = releases.version() if server is None else server
    if incompatible(release):
        return "incompatible"
    if update_state == "updating":
        return "updating"
    have, want = releases.parse(release), releases.parse(server)
    if not have or not want:
        return "unknown"
    return "needs_update" if have < want else "current"


def record(c, runner_id, body):
    """Store what a heartbeat reported. Called only when the runner sent a release."""
    release = clean(body.release)
    update = body.update
    since = update_since(c, runner_id, update.state if update else "")
    c.execute("INSERT INTO runner_versions(runner_id,release,kind,update_state,update_target,update_error,reported_at,commit_sha) "
              "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(runner_id) DO UPDATE SET release=excluded.release,"
              "kind=excluded.kind,update_state=excluded.update_state,update_target=excluded.update_target,"
              "update_error=excluded.update_error,reported_at=excluded.reported_at,commit_sha=excluded.commit_sha",
              (runner_id, release, body.kind or "", update.state if update else "", update.target if update else "",
               update.error if update else "", H.now(), body.commit or ""))
    c.execute("UPDATE runner_versions SET update_since=?,update_running=?,update_handed_back=? WHERE runner_id=?",
              (since, update.running if update else None, update.handed_back if update else None, runner_id))
    if body.checkout is None:   # it follows the server's release now, not main: an old "behind main" note is stale
        c.execute("UPDATE runners SET checkout=NULL WHERE id=?", (runner_id,))
    nudge(c, runner_id)


def nudge(c, runner_id, server=None):
    """Tell the computer's owner once per server release that it is outdated, after NUDGE_AFTER_S of it behind that
    release: the wait starts again with each server release, so a computer gets its usual chance to update itself
    first. A computer its owner pinned to a release is not nudged; its row still says Outdated.
    Returns whether a notice went out."""
    server = releases.version() if server is None else server
    row = c.execute("SELECT v.release,v.update_error,v.update_state,v.outdated_since,v.outdated_for,v.nudged_at,r.label,"
                    "r.owner_id FROM runner_versions v JOIN runners r ON r.id=v.runner_id WHERE v.runner_id=?",
                    (runner_id,)).fetchone()
    if not row:
        return False
    if not outdated(row["release"], server):
        if row["outdated_since"]:
            c.execute("UPDATE runner_versions SET outdated_since=NULL,outdated_for='' WHERE runner_id=?", (runner_id,))
        return False
    now = H.now()
    since = row["outdated_since"] if row["outdated_since"] and row["outdated_for"] == server else None
    if not since:
        since = now
        c.execute("UPDATE runner_versions SET outdated_since=?,outdated_for=? WHERE runner_id=?", (since, server, runner_id))
    if (row["nudged_at"] == server or H.shift(since, seconds=NUDGE_AFTER_S) > now or not row["owner_id"]
            or row["update_state"] == "pinned"):
        return False
    c.execute("UPDATE runner_versions SET nudged_at=? WHERE runner_id=?", (server, runner_id))
    have = f"Tico {row['release']}" if releases.parse(row["release"]) else "a build that is not a Tico release"
    why = f" Its last update error: {row['update_error']}" if row["update_error"] else ""
    H.notice(c, H.KEEPER, H.human_actor(row["owner_id"]),
             f"Your computer {row['label']} runs {have}; this server runs Tico {server}. It updates itself between "
             f"turns; when it stays behind, Settings > Computers says why.{why}")
    H.event(c, H.KEEPER, "runner.outdated", "runner:" + runner_id, {"release": row["release"], "server": server})
    return True


# Older runners keep claiming for this window; new runners report drain counts and hold immediately.
UPDATE_DRAIN_S = 20 * 60


def update_since(c, runner_id, state):
    """When this computer's reported update state began, as the runner keeps its own `pending_since`: a `waiting`
    that goes on (even for a newer target) keeps its start, and any other state starts the clock again, so a retry
    after a failed update waits its full drain time. An update that follows a drain already past holds from the
    drain's end, so Health's duration covers the whole hold."""
    row = c.execute("SELECT update_state,update_since FROM runner_versions WHERE runner_id=?", (runner_id,)).fetchone()
    now = H.now()
    if row and row["update_state"] == state and row["update_since"]:
        return row["update_since"]
    if row and state == "updating" and (held := holding_since(c, runner_id)):
        return held
    return now


VERSION_SQL = "SELECT * FROM runner_versions WHERE runner_id=?"


def _version(c, runner_id):
    """One runner_versions row for the current read snapshot (Health drain lines and holding_since share it)."""
    from .read_cache import one
    return one(c, VERSION_SQL, (runner_id,))


def holding_since(c, runner_id):
    """Since when this computer has taken no new work because it is updating, or None while it still takes work.
    New runners report drain counts as soon as they stop claiming. Older runners keep claiming for UPDATE_DRAIN_S.
    An `updating` runner claims nothing, whatever release it is on (a Docker update still settling reports it on the
    new release)."""
    row = _version(c, runner_id)
    if not row or row["update_state"] not in ("waiting", "updating") or not row["update_since"]:
        return None
    progress = drain_progress(c, runner_id)
    if row["update_state"] == "updating" or progress:
        return row["update_since"]
    since = H.shift(row["update_since"], seconds=UPDATE_DRAIN_S)
    return since if since <= H.now() else None


def drain_progress(c, runner_id):
    """Counts only, safe wherever a computer's update hold is already visible. Old runners report neither."""
    row = _version(c, runner_id)
    if not row or row["update_state"] not in ("waiting", "updating") or row["update_running"] is None:
        return ""
    return f"{row['update_running']} running, {row['update_handed_back'] or 0} handed back"


def paused(c, runner_id):
    """Why this computer must not claim work, or "" when it may.

    Claim waiter order asks once per candidate computer; reuse the release row for the
    current read snapshot via `read_cache.one`.
    """
    from .read_cache import one
    row = one(c, "SELECT release FROM runner_versions WHERE runner_id=?", (runner_id,))
    if not row or not incompatible(row["release"]):
        return ""
    return (f"This computer runs Tico {row['release']}, which this server ({releases.version()}) cannot work with; "
            f"it needs {MIN_RUNNER_RELEASE} or later. Its bots are paused until it updates.")


def load(c):
    """{runner_id: row} for every computer. Cached for the current read snapshot so Health /
    `/computers` / Operations callers that ask once per machine (via `computer_details`) do not
    re-SELECT the whole table each time."""
    from . import read_cache
    values = read_cache.cache(c)
    key = "runner_versions.load"
    if values is not None and key in values:
        return values[key]
    out = {r["runner_id"]: dict(r) for r in c.execute("SELECT * FROM runner_versions")}
    if values is not None:
        values[key] = out
    return out


# What a runner before this release said when nothing supervised it; the line now says how to fix that.
NO_SUPERVISOR = ("no supervisor would start this runner again after an update: run `scripts/tico install` "
                 "once on this computer and it updates itself")


def view(row, server=None):
    row = row or {}
    release = row.get("release") or ""
    found = state(release, row.get("update_state") or "", server) if row else "unknown"
    server = releases.version() if server is None else server
    return {"release": release, "commit": row.get("commit_sha") or "", "kind": row.get("kind") or "", "state": found,
            "label": LABELS[found], "outdated": outdated(release, server),
            "server_release": server if releases.parse(server) else "",
            "update_state": row.get("update_state") or "", "target": row.get("update_target") or "",
            "running": row.get("update_running"), "handed_back": row.get("update_handed_back"),
            "error": NO_SUPERVISOR if str(row.get("update_error") or "").startswith("nothing would start this runner")
            else row.get("update_error") or "", "min_runner": MIN_RUNNER_RELEASE}


def health_check(computers, server=None):
    """The Health line about computer versions, or None on a development build that has no release to
    compare with. `computers` carry `online`, `label` and `update`."""
    server = releases.version() if server is None else server
    online = [x for x in computers if x["online"]]
    if not releases.parse(server):
        return None
    bad = [x for x in online if x["update"]["state"] == "incompatible"]
    behind = [x for x in online if x["update"]["state"] in ("needs_update", "updating")]
    failed = [x for x in online if x["update"]["error"]]
    fixes = [{"label": "Open Computers", "href": "#/settings", "tab": "devices", "click": ""}]
    names = lambda rows: ", ".join(x["label"] for x in rows[:5])   # noqa: E731
    unknown = [x for x in online if x["update"]["state"] == "unknown"]
    newer = [x for x in online if releases.parse(x["update"].get("release"))
             and releases.parse(x["update"]["release"]) > releases.parse(server)]
    notes = []
    if unknown:
        notes.append(f"Release not reported: {names(unknown)}.")
    if newer:
        notes.append("Newer than this server: " + ", ".join(
            f"{x['label']} ({x['update']['release']})" for x in newer[:5]) + ".")
    extra = " " + " ".join(notes) if notes else ""
    if bad:
        return {"id": "runners", "label": "Computer versions", "status": "bad", "fixes": fixes,
                "summary": f"{names(bad)} cannot work with {server} and its bots are paused until it updates." + extra}
    if behind or failed:
        note = f" Last update error: {failed[0]['update']['error']}" if failed else ""
        return {"id": "runners", "label": "Computer versions", "status": "warn", "fixes": fixes,
                "summary": f"{names(behind or failed)} {'is' if len(behind or failed) == 1 else 'are'} not on {server} yet." + note + extra}
    return {"id": "runners", "label": "Computer versions", "status": "unknown" if unknown else "info" if newer else "ok",
            "fixes": fixes if unknown else [], "summary": " ".join(notes) if notes else
            f"Every online computer is on {server}." if online else "No computer is online to compare."}
