"""Skill usage (Library › Skills, docs/skills.md): the daily rollup of `skill_usages` into `skill_usage_dailies`, the
90-day keep of the raw rows, and the reads behind the Skills page.

Raw rows arrive from the runner, the skills helper and typed `/name` messages (backend/skill_runtime.py,
backend/skill_devices.py). The rollup (scheduler FIXED, hourly) recomputes whole every day that a row created since its
last run touches, from the raw rows, so running it twice changes nothing; then it deletes raw rows older than 90 days.
A day that is partly pruned is never recomputed (its rollup was made while its rows were all there).

Reads count the days before the last rollup's day from the rollup and the rest from the raw rows, so a use shows at
once. Who used a skill is for admins: anyone else sees the totals and their own rows only. A use links to its task or thread only when
the caller may read that task or thread. Coverage says "unknown" where Tico has no data, never "unused": an install
is unused only when its target reports staging (a receipt), has had the install for 30 days, reports usage (a
person's skills helper with reporting on), and shows no use in those 30 days.

Rows hold a skill, who, where and when. Nothing here reads or writes prompt text, arguments or file contents.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Query, Request

from . import hubdb as H
from . import skills as SK
from .store import Problem

KEEP_DAYS = 90
WATERMARK_KEY = "skill_usage_rollup"     # registry_metadata: when the last rollup started
LATE = timedelta(hours=1)                # a row may commit a little after the time stamped on it
PRUNE_BATCH = 5_000
PRUNE_BATCHES = 20                       # per run: a backlog drains over a few runs, never in one long transaction
WINDOWS = (7, 30)
TOP = 5
RECENT = 20
UNUSED_DAYS = 30
TRIGGERS = ("model", "person", "nested")


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _day(at):
    return at.astimezone(timezone.utc).date().isoformat()


def _next_day(day):
    return (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()


# ----------------------------------------------------------------------------- the rollup
def _watermark(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (WATERMARK_KEY,)).fetchone()
    return H.parse_ts(row[0]) if row else None


def roll_up_days(c, at):
    """Recompute the rollup for every day a raw row created since the last run touches (every day there is, the first
    time). Days at or before the 90-day cut are left alone. Returns the days recomputed."""
    since = _watermark(c)
    if since is None:
        rows = c.execute("SELECT DISTINCT substr(used_at, 1, 10) FROM skill_usages").fetchall()
    else:
        rows = c.execute("SELECT DISTINCT substr(used_at, 1, 10) FROM skill_usages WHERE created_at >= ?",
                         (_stamp(since - LATE),)).fetchall()
    cut = _day(at - timedelta(days=KEEP_DAYS))
    days = sorted(r[0] for r in rows if r[0] and r[0] > cut)
    for day in days:
        c.execute("DELETE FROM skill_usage_dailies WHERE used_on=?", (day,))
        c.execute("INSERT INTO skill_usage_dailies (used_on, skill_id, actor_type, actor_id, trigger, uses_count) "
                  "SELECT ?, skill_id, actor_type, actor_id, trigger, COUNT(*) FROM skill_usages "
                  "WHERE used_at >= ? AND used_at < ? GROUP BY skill_id, actor_type, actor_id, trigger",
                  (day, day, _next_day(day)))
    c.execute("INSERT INTO registry_metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (WATERMARK_KEY, _stamp(at)))
    return days


def prune(store, at):
    """Delete raw rows older than 90 days, in short transactions. Returns how many went."""
    cut = _stamp(at - timedelta(days=KEEP_DAYS))
    gone = 0
    for _ in range(PRUNE_BATCHES):
        with store.transaction() as c:
            n = c.execute("DELETE FROM skill_usages WHERE id IN (SELECT id FROM skill_usages WHERE used_at < ? LIMIT ?)",
                          (cut, PRUNE_BATCH)).rowcount
        gone += n
        if n < PRUNE_BATCH:
            break
    return gone


def run(store, at=None):
    """One rollup: recompute the touched days, then prune. {days, pruned}."""
    at = at or datetime.now(timezone.utc)
    with store.transaction() as c:
        days = roll_up_days(c, at)
    return {"days": len(days), "pruned": prune(store, at)}


# ----------------------------------------------------------------------------- reads
def _caller(c, auth, who):
    """(admin, (type, id)) of the caller."""
    return SK._admin(auth, who), SK._actor(c, who)


def _bounds(at, days):
    """(the first day counted, the last day counted: today)."""
    today = at.astimezone(timezone.utc).date()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def _uses_sql(c, days, at, skill_id=None):
    """A subquery of uses in the window, (skill_id, actor_type, actor_id, trigger, n, at), and its arguments, for one
    skill or all: the rollup's days before the day of its last run, and the raw rows from that day on. A day before
    the last run was recomputed whole from the rows it had then, so nothing is counted twice or missed (a late row
    waits for the next run)."""
    first = _bounds(at, days)[0]
    since = _watermark(c)
    split = max(first, _day(since)) if since else first
    one = " AND skill_id=?" if skill_id else ""
    sql = ("SELECT skill_id, actor_type, actor_id, trigger, uses_count AS n, used_on AS at FROM skill_usage_dailies "
           f"WHERE used_on >= ? AND used_on < ?{one} "
           "UNION ALL SELECT skill_id, actor_type, actor_id, trigger, 1 AS n, used_at AS at FROM skill_usages "
           f"WHERE used_at >= ?{one}")
    return sql, [first, split, *([skill_id] if skill_id else []), split, *([skill_id] if skill_id else [])]


def _names(c, actors):
    """{(type, id): name} for these actors: a bot's display name, a person's name."""
    bots = sorted({i for t, i in actors if t == "Bot"})
    people = sorted({i for t, i in actors if t == "Human"})
    out = {}
    for start in range(0, len(bots), 500):
        part = bots[start:start + 500]
        for r in c.execute(f"SELECT id, slug, display_name FROM bots WHERE id IN ({','.join('?' * len(part))})", part):
            out[("Bot", r["id"])] = r["display_name"] or r["slug"] or r["id"]
    for start in range(0, len(people), 500):
        part = people[start:start + 500]
        for r in c.execute(f"SELECT id, name FROM humans WHERE id IN ({','.join('?' * len(part))})", part):
            out[("Human", r["id"])] = r["name"] or r["id"]
    return out


def _actor_view(names, typ, ident):
    return {"type": typ, "id": ident, "name": names.get((typ, ident), ident)}


def _days(value):
    if value not in WINDOWS:
        raise Problem("validation", "days is 7 or 30", 422)
    return value


def overview(c, auth, who, days, at=None):
    """Per skill in the directory: uses by trigger, distinct people and bots, last used, the top actors (admins; anyone
    else sees only themselves there)."""
    at = at or datetime.now(timezone.utc)
    admin, me = _caller(c, auth, who)
    sub, args = _uses_sql(c, days, at)
    out = {}

    def entry(skill_id):
        return out.setdefault(skill_id, {"uses": 0, "by_trigger": dict.fromkeys(TRIGGERS, 0), "people": 0, "bots": 0,
                                         "last_used_at": None, "top": []})
    for r in c.execute(f"SELECT skill_id, trigger, SUM(n) AS uses FROM ({sub}) GROUP BY skill_id, trigger", args):
        e = entry(r["skill_id"])
        e["uses"] += r["uses"]
        e["by_trigger"][r["trigger"]] = e["by_trigger"].get(r["trigger"], 0) + r["uses"]
    for r in c.execute(f"SELECT skill_id, actor_type, COUNT(DISTINCT actor_id) AS n FROM ({sub}) GROUP BY skill_id, actor_type", args):
        entry(r["skill_id"])["people" if r["actor_type"] == "Human" else "bots"] = r["n"]
    mine = "" if admin else " WHERE actor_type=? AND actor_id=?"
    top_args = args + ([] if admin else list(me))
    tops = c.execute(f"SELECT skill_id, actor_type, actor_id, SUM(n) AS uses FROM ({sub}){mine} "
                     "GROUP BY skill_id, actor_type, actor_id ORDER BY skill_id, uses DESC, actor_id", top_args).fetchall()
    picked = {}
    for r in tops:
        rows = picked.setdefault(r["skill_id"], [])
        if len(rows) < TOP:
            rows.append(r)
    names = _names(c, {(r["actor_type"], r["actor_id"]) for rows in picked.values() for r in rows})
    for skill_id, rows in picked.items():
        entry(skill_id)["top"] = [{**_actor_view(names, r["actor_type"], r["actor_id"]), "uses": r["uses"]} for r in rows]
    # Last used, whatever the window: the raw rows' newest, else the rollup's newest day (older than 90 days).
    for r in c.execute("SELECT skill_id, MAX(used_at) AS at FROM skill_usages GROUP BY skill_id"):
        entry(r["skill_id"])["last_used_at"] = r["at"]
    for r in c.execute("SELECT skill_id, MAX(used_on) AS at FROM skill_usage_dailies GROUP BY skill_id"):
        e = entry(r["skill_id"])
        e["last_used_at"] = e["last_used_at"] or r["at"]
    skills = c.execute("SELECT id, name FROM skills WHERE deleted_at IS NULL ORDER BY name LIMIT ?", (SK.LIST_CAP,)).fetchall()
    return {"days": days, "admin": admin,
            "skills": [{"name": s["name"], **entry(s["id"])} for s in skills]}


def _link(c, auth, who, viewer, row):
    """The use's task and thread ids, each only when the caller may read it."""
    task_id = thread_id = None
    if row["task_id"]:
        try:
            auth.task(c, who, row["task_id"])
            task_id = row["task_id"]
        except Problem:
            pass
    if row["thread_id"]:
        from . import threads
        try:
            if threads.readable(c, viewer, row["thread_id"]):
                thread_id = row["thread_id"]
        except Problem:
            pass
    return task_id, thread_id


def coverage(c, auth, who, skill, at):
    """The installs the caller may see, each with its uses in the last 30 days and whether Tico can tell it is used:
    used | unused | unknown."""
    since = _stamp(at - timedelta(days=UNUSED_DAYS))
    rows = [r for r in SK._installs(c, "i.skill_id=?", (skill["id"],))
            if SK._may_see(c, auth, who, r["target_type"], r["target_id"])]
    sub, args = _uses_sql(c, UNUSED_DAYS, at, skill["id"])
    counts = {(r["actor_type"], r["actor_id"]): r["uses"] for r in c.execute(
        f"SELECT actor_type, actor_id, SUM(n) AS uses FROM ({sub}) GROUP BY actor_type, actor_id", args)}
    reporting = {r["person_id"] for r in c.execute(
        "SELECT DISTINCT person_id FROM skill_devices WHERE reporting_enabled=1 AND revoked_at IS NULL")}
    out = []
    for r in rows:
        uses = counts.get((r["target_type"], r["target_id"]), 0)
        reports = r["target_type"] == "Bot" or r["target_id"] in reporting
        if uses:
            state = "used"
        elif r["staged_sha256"] and reports and r["created_at"] <= since:
            state = "unused"
        else:
            state = "unknown"
        view = SK._install_view(r)
        out.append({"id": view["id"], "target": view["target"], "uses_30d": uses, "coverage": state})
    return out


def detail(c, auth, who, name, days, at=None):
    """One skill's usage: totals, per actor (admins all; anyone else themselves), the last uses (admins all; anyone
    else their own and the ones they asked a bot for), and the coverage of the installs they may see."""
    at = at or datetime.now(timezone.utc)
    skill = SK._skill_or_404(c, name)
    admin, me = _caller(c, auth, who)
    sub, args = _uses_sql(c, days, at, skill["id"])
    totals = {"uses": 0, "by_trigger": dict.fromkeys(TRIGGERS, 0), "people": 0, "bots": 0, "last_used_at": None}
    actors = {}
    for r in c.execute(f"SELECT actor_type, actor_id, trigger, SUM(n) AS uses, MAX(at) AS last FROM ({sub}) "
                       "GROUP BY actor_type, actor_id, trigger", args):
        totals["uses"] += r["uses"]
        totals["by_trigger"][r["trigger"]] = totals["by_trigger"].get(r["trigger"], 0) + r["uses"]
        key = (r["actor_type"], r["actor_id"])
        a = actors.setdefault(key, {"uses": 0, "by_trigger": dict.fromkeys(TRIGGERS, 0), "last_used_at": None})
        a["uses"] += r["uses"]
        a["by_trigger"][r["trigger"]] = a["by_trigger"].get(r["trigger"], 0) + r["uses"]
        a["last_used_at"] = max(filter(None, (a["last_used_at"], r["last"])), default=None)
    totals["people"] = sum(1 for t, _ in actors if t == "Human")
    totals["bots"] = sum(1 for t, _ in actors if t == "Bot")
    last = c.execute("SELECT MAX(used_at) FROM skill_usages WHERE skill_id=?", (skill["id"],)).fetchone()[0]
    totals["last_used_at"] = last or c.execute("SELECT MAX(used_on) FROM skill_usage_dailies WHERE skill_id=?",
                                               (skill["id"],)).fetchone()[0]
    shown = {k: v for k, v in actors.items() if admin or k == me}
    first = _bounds(at, days)[0]
    own = "" if admin else " AND ((actor_type=? AND actor_id=?) OR requested_by_id=?)"
    recent_args = [skill["id"], first] + ([] if admin else [me[0], me[1], me[1] if me[0] == "Human" else None])
    recent = c.execute("SELECT actor_type, actor_id, requested_by_id, trigger, source, task_id, thread_id, used_at "
                       f"FROM skill_usages WHERE skill_id=? AND used_at >= ?{own} ORDER BY used_at DESC, id DESC LIMIT ?",
                       recent_args + [RECENT]).fetchall()
    names = _names(c, set(shown) | {(r["actor_type"], r["actor_id"]) for r in recent}
                   | {("Human", r["requested_by_id"]) for r in recent if r["requested_by_id"]})
    from . import threads
    viewer = threads.viewer(auth, who)
    uses = []
    for r in recent:
        task_id, thread_id = _link(c, auth, who, viewer, r)
        uses.append({"actor": _actor_view(names, r["actor_type"], r["actor_id"]),
                     "requested_by": _actor_view(names, "Human", r["requested_by_id"]) if r["requested_by_id"] else None,
                     "trigger": r["trigger"], "source": r["source"], "used_at": r["used_at"],
                     "task_id": task_id, "thread_id": thread_id})
    ordered = sorted(shown.items(), key=lambda kv: (-kv[1]["uses"], kv[0][1]))
    return {"skill": skill["name"], "days": days, "admin": admin, "totals": totals,
            "actors": [{**_actor_view(names, t, i), **v} for (t, i), v in ordered],
            "recent": uses, "installs": coverage(c, auth, who, skill, at),
            # Skills used on computers that the directory does not have: not collected yet (the helper forwards
            # directory skills only), so the page has nothing to flag.
            "popular_elsewhere": None}


def _writable_bots(c, auth, who):
    """[{id, name}] of the bots the caller may write (so install skills on), by name."""
    rows = {r["id"]: r for r in H.bots(c) if r.get("state") != "archived"}
    levels = auth.bot_accesses(c, who, sorted(rows)) if rows else {}
    return sorted(({"id": i, "name": rows[i].get("display_name") or rows[i].get("slug") or i}
                   for i, level in levels.items() if i in rows and level.get("write")), key=lambda b: b["name"].lower())


def installed(c, auth, who):
    """The caller's own installs and those of the bots they may write: what each asks for, what is staged, and
    whether a pinned install has a newer version."""
    admin, me = _caller(c, auth, who)
    bots = [b["id"] for b in _writable_bots(c, auth, who)]
    clauses, args = [], []
    if me[0] == "Human":
        clauses.append("(i.target_type='Human' AND i.target_id=?)")
        args.append(me[1])
    for start in range(0, len(bots), 500):
        part = bots[start:start + 500]
        clauses.append(f"(i.target_type='Bot' AND i.target_id IN ({','.join('?' * len(part))}))")
        args.extend(part)
    if not clauses:
        return {"installs": []}
    rows = SK._installs(c, " OR ".join(clauses), args)[:SK.LIST_CAP * 4]
    out = []
    for r in rows:
        view = SK._install_view(r)
        view["latest"] = r["latest_number"]
        view["update_available"] = bool(r["version_policy"] == "pinned" and r["latest_number"] and r["pinned_number"]
                                        and r["latest_number"] > r["pinned_number"])
        out.append(view)
    return {"installs": out}


def targets(c, auth, who):
    """Who the caller may install a skill for: bots they can write, people (an admin: everyone; anyone else:
    themselves), and each team as its current members the caller may install for (nested teams included)."""
    from . import people as P
    from . import teammate_access
    from . import views
    admin, me = _caller(c, auth, who)
    bots = _writable_bots(c, auth, who)
    roster = views.roster(c, shared=True)
    shown = P.visible_people(roster)
    if admin:
        people = [{"id": p["id"], "name": p.get("name") or p["id"]} for p in shown]
    elif me[0] == "Human":
        person = next((p for p in shown if p["id"] == me[1]), None)
        people = [{"id": me[1], "name": (person or {}).get("name") or me[1]}]
    else:
        people = []
    people.sort(key=lambda p: p["name"].lower())
    writable = {b["id"] for b in bots}
    allowed = {p["id"] for p in people}
    teams = []
    entries = views.entries(c, shared=True)
    for gid, group in sorted((roster.get("org_groups") or {}).items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        members = []
        for actor in sorted(teammate_access.group_member_actors(c, gid, roster, entries)):
            kind, ident = actor.split(":", 1)
            if kind == "bot":
                bot = H.resolve_bot(c, ident)
                if bot and bot["id"] in writable:
                    members.append({"target_type": "Bot", "target_id": bot["id"]})
            elif kind == "human" and ident in allowed:
                members.append({"target_type": "Human", "target_id": ident})
        if members:
            teams.append({"id": gid, "name": group.get("name") or gid, "targets": members})
    return {"admin": admin, "me": {"type": me[0], "id": me[1]}, "bots": bots, "people": people, "teams": teams}


def install(app, store, auth):
    """The read routes. Installed before backend/skills.py's, so `skills/usage`, `skills/installed` and
    `skills/install-targets` are not read as a skill's name."""
    def reader(request):
        who = request.state.identity
        auth.domain(who)
        if who.role not in SK.PEOPLE_AND_BOTS:
            raise Problem("forbidden", "Skills are read by people and bots", 403)
        return who

    @app.get("/api/v2/skills/usage")
    def skills_usage(request: Request, days: int = Query(default=30)):
        """Each skill's use over the last 7 or 30 days: by trigger, how many people and bots, last used, and the top
        users (admins; anyone else sees only themselves there)."""
        who = reader(request)
        with store.read() as c:
            return overview(c, auth, who, _days(days))

    @app.get("/api/v2/skills/install-targets")
    def skills_install_targets(request: Request):
        """Who the caller may install for: bots they can write, people, and teams as their current members."""
        who = reader(request)
        with store.read() as c:
            return targets(c, auth, who)

    @app.get("/api/v2/skills/installed")
    def skills_installed(request: Request):
        """The caller's installs and those of the bots they may write, with desired and staged versions."""
        who = reader(request)
        with store.read() as c:
            return installed(c, auth, who)

    @app.get("/api/v2/skills/{name}/usage")
    def skill_usage(request: Request, name: str, days: int = Query(default=30)):
        """One skill's use: totals, per actor and the last 20 uses (admins see everyone; anyone else their own), each
        use's task or thread when the caller may read it, and the installs' coverage (used, unused or unknown)."""
        who = reader(request)
        with store.read() as c:
            return detail(c, auth, who, name, _days(days))


def words(found):
    """The run's result in words for Settings > Routines."""
    if not found["days"] and not found["pruned"]:
        return None
    parts = []
    if found["days"]:
        parts.append(f"{found['days']} day{'s' if found['days'] != 1 else ''} rolled up")
    if found["pruned"]:
        parts.append(f"{found['pruned']} old use{'s' if found['pruned'] != 1 else ''} deleted")
    return ", ".join(parts)

