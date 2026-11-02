"""Updates: every bot reports in once a day, and on Friday with the week.

"All bots report in daily, with Fridays being a more comprehensive week review ... a way to both
wake all bots make sure they are unstuck and make sure they make progress at least once per day."
"We should create a queue and get updates from the bots one at a time. These aren't really time
sensitive so it can take a while."

- `update_queue`: one request per enabled bot per day, sent one at a time (the next only once the
  last has posted or its run ended), so updates never crowd out real work on a Mac. Quietest bots
  first; a bot busy with a run is passed over and asked again later that day. A request is a
  keeper notice to the bot, which queues its run like any message; the facts it needs ride along.
- `updates`: what a bot posted (one per bot, day and kind; a second post replaces the first).
- `update_reads`: per person, so reading one is never someone else's read.
- A reply is an ordinary chat message to the bot carrying `refs.update`; the bot's session gets it
  like any message, and the update's thread is those replies plus the bot's answers to them.
- `update_settings`: a bot's daily and weekly switches (both on unless turned off).
- A week in review is five slides, swiped left to right: Goal, KPIs, Done last week, Focus next week,
  Biggest blockers. The bot writes the words and may add a few numbers; Tico adds its goal KPIs with
  their sparklines, frozen when it posts, so an old week shows that week's numbers. `body` keeps a
  plain-markdown copy for places that show one block of text.
- An owner can ask every bot to redo a past day (`redo`), and a redone request is sent like a new one.
- People post in the same shape. An update's author (the `bot` column) is a bot id, or `human:<id>`
  for a person, the key `reports_to` already uses. `drafted_by` is the bot that wrote a draft the
  author has not replaced, `edited_by` the author once they changed it, and `inputs` the
  reports' updates it was written from.
- Updates roll up the org chart: the queue asks the deepest bots first, and a manager is asked only
  once each direct report has posted, missed or been skipped, or `MANAGER_WAIT_S` after its first
  report was asked, whichever comes first. Its request carries its direct reports' updates (not
  theirs below: each manager already summed those up), and names who had none.
- A person's update is drafted for them: after the bots, the queue holds a row per person
  (`human:<id>`) whose drafting is on, deepest first, drafted once their reports are in. The draft
  posts at once as theirs, labelled as the Assistant's; they edit or replace it any time that day.
  The server writes it with one call to the company's own model (`write_draft`), not in an Assistant
  run: the call carries only `draft_facts`, what every member can already see (their non-private
  tasks and goals with no restricted bot on them, their decisions and comments on such tasks, the
  summaries of shared meetings they were in, their open reports' updates). No chat, memory or tool
  is in reach, so a private Assistant chat cannot reach the company. Once they post their own words
  that day, in either kind, nothing is drafted for them.
- Meetings are read only by the owner and people with an email on file (backend/sql.py), so a draft
  whose facts held a meeting summary, or a report's update that did, is flagged `meetings` and read
  only by them (`meeting_reader`): every other reader, bots included, gets it as not found and the
  feed, tree, counts and links leave it out. An edit keeps the flag; their own words clear it.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import run_triggers
from .store import H, Problem

ZONE = ZoneInfo("America/Los_Angeles")
START_HOUR = 5              # the day's queue is built from 05:00 Pacific
RUN_LIMIT_S = 75 * 60       # a request whose run has not ended in this long is given up on
PICKUP_LIMIT_S = 20 * 60    # ...and one no computer picked up in this long: move on (it may still post later)
KINDS = ("daily", "weekly")
MANAGER_WAIT_S = 90 * 60    # a manager waits this long for late reports, then writes with what it has
# A linter checks the shape of every update. The feed is skimmed, so a wall of text comes back with how to write it, as a
# warning on the accepted post (refused only when TICO_STYLE_LINT=refuse): rewriting cost bots whole runs. An empty update
# or a week in review without its slides is still refused. A writing correction never counts toward quarantine.
# Updates have no title and no sections, just 1-5 bullets, never task ids, only plain English;
# the word limits are 10% under the old 100 words.
BULLETS_MAX = 5
LIMITS = {"daily": {"words": 90, "bullet": 25}, "weekly": {"words": 180, "bullet": 40}}
SHAPE = ("An update is one to five bullets in plain English and nothing else: no title, no headings or "
         "sections (no Done / Next), no task ids or internal codes. Each bullet is one line: what you did, "
         "what you do next, or what waits on a person. Link a doc or PR in the words if it helps.")
BULLET = re.compile(r"^\s*[-*•]\s+\S")
HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*:?\s*$|__[^_]+__:?\s*$|[A-Z][A-Za-z /]{0,30}:\s*$)")
LABEL = re.compile(r"^\s*[-*•]\s+(\*\*[^*]+\*\*|(done|next|blocked|needs you|blocked / needs you|this week|next week)\s*[:—-])", re.I)
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# The week in review's slides: (fewest, most) bullets and words a bullet; the goal is one line.
SLIDES = {"done": (1, 5, 25), "focus": (1, 3, 25), "blockers": (0, 3, 25)}
GOAL_WORDS = 30
BOT_KPIS_MAX = 4           # numbers the bot adds to the KPI slide
TRACKED_MAX = 6            # goal KPIs Tico adds
SERIES_MAX = 30
SLIDES_SHAPE = ("A week in review is five slides, passed as `slides`: goal (one sentence, at most 30 words: the goal you "
                "work toward and where it stands), kpis (up to four numbers that show the week, each with name and value, "
                "and optionally unit, a short series of recent values oldest first for a chart, and a note; Tico adds your "
                "goal KPIs itself), done (one to five bullets: what got done last week), focus (one to three: your focus "
                "next week), blockers (zero to three: what blocks you and who can unblock it). Each bullet is one plain "
                "English line of at most 25 words, with no task ids.")
TASK_ID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
                     r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b"
                     r"|\b(?:task|job|attempt|msg|message)[:#\s]+[0-9a-f]{4,}\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS updates(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL,
 headline TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(bot, kind, day));
CREATE INDEX IF NOT EXISTS updates_created ON updates(created_at);
CREATE TABLE IF NOT EXISTS update_reads(
 update_id TEXT NOT NULL, actor TEXT NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY(update_id, actor));
CREATE TABLE IF NOT EXISTS update_archive_overrides(
 update_id TEXT NOT NULL, actor TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('archived','restored')), updated_at TEXT NOT NULL,
 PRIMARY KEY(update_id, actor));
CREATE INDEX IF NOT EXISTS update_archive_actor ON update_archive_overrides(actor, state, updated_at);
CREATE TABLE IF NOT EXISTS update_queue(
 id TEXT PRIMARY KEY, bot TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, rank INTEGER NOT NULL,
 state TEXT NOT NULL, message_id TEXT, sent_at TEXT, done_at TEXT, reason TEXT,
 UNIQUE(bot, kind, day));
CREATE INDEX IF NOT EXISTS update_queue_day ON update_queue(day, state, rank);
CREATE TABLE IF NOT EXISTS update_settings(
 bot TEXT PRIMARY KEY, daily INTEGER NOT NULL DEFAULT 1, weekly INTEGER NOT NULL DEFAULT 1);
"""


def local_now(at=None):
    at = at or H.parse_ts(H.now())
    return at.astimezone(ZONE)


def today(at=None):
    return local_now(at).date().isoformat()


def kind_for(day):
    """Friday's update is the week in review; every other day's is the daily one."""
    return "weekly" if datetime.fromisoformat(day).weekday() == 4 else "daily"


def effective_kind(kind, slides, day=None):
    """The kind `post` stores: the one asked for, else a week in review when it has slides, else the day's own. A daily
    keeps its bullets and drops any slides; a week in review is its slides alone."""
    return kind or ("weekly" if slides else kind_for(day or today()))


def settings(c, bot):
    row = c.execute("SELECT daily, weekly FROM update_settings WHERE bot=?", (bot,)).fetchone()
    return {"daily": bool(row["daily"]) if row else True, "weekly": bool(row["weekly"]) if row else True}


def set_settings(c, bot, daily=None, weekly=None):
    now = settings(c, bot)
    daily = now["daily"] if daily is None else bool(daily)
    weekly = now["weekly"] if weekly is None else bool(weekly)
    c.execute("INSERT INTO update_settings(bot,daily,weekly) VALUES(?,?,?) ON CONFLICT(bot) DO UPDATE SET "
              "daily=excluded.daily, weekly=excluded.weekly", (bot, int(daily), int(weekly)))
    return {"daily": daily, "weekly": weekly}


# ----------------------------------------------------------------------------- the org chart
def _chart(c):
    from . import views
    archived = {r["id"] for r in c.execute("SELECT id FROM bots WHERE state='archived'")}
    return views.roster(c), views.entries(c), archived


def direct_reports(c, author, chart=None):
    """The authors one level below this one on the org chart: bot ids and `human:<id>`. `chart` is
    `_chart(c)` when the caller already read it (the queue reads it once a step, not once a report)."""
    from . import goals as G
    roster, entries, archived = chart or _chart(c)
    return [a if H.actor_kind(a) == "human" else H.actor_id(a)
            for a in G.reports_of(author_actor(author), roster, entries, archived, True)]


def depths(c, slugs):
    """Each bot's depth on the org chart, counting the bots above it (the people above do not
    post through the queue), so the deepest are asked first."""
    from . import people as P
    roster, entries, archived = _chart(c)
    out, bare = {}, True
    for slug in slugs:
        n, parent, seen = 0, P.org_parent("bot", slug, roster, entries, archived, bare), set()
        while parent.startswith("b:") and parent not in seen:
            seen.add(parent)
            n += 1
            parent = P.org_parent("bot", parent[2:], roster, entries, archived, bare)
        out[slug] = n
    return out


def visible_authors(c, auth, who, mine=False, scope=None):
    """The authors whose updates `who` may read: bots they have Read on, and every person on the
    chart (`human:<id>`), since a person's update is for the whole company. An external agent
    (`who.agent`) is outside the company and sees only its bots. With `mine`, only
    the bots they operate (for the owner, also bots with no operator) and their own update, the
    Updates page's My bots filter (the count must match what the feed shows). With
    `scope=direct`, only their direct reports on the org chart: the one level that alerts.
    The feed, the tree and the Updates chat's turn (backend/updates_chat.py) all read through this."""
    from . import goals as G
    from . import people as P
    from . import views
    readable = {slug for slug, level in auth.bot_accesses(c, who).items() if level["read"]}
    if who.role in ("owner", "human") or (who.role == "bot" and not who.agent):
        readable |= {H.human_actor(p["id"]) for p in P.visible_people(views.roster(c))}
    if mine:
        pid = H.actor_id(who.actor)
        operators = {r[0]: r[1] for r in c.execute("SELECT bot, owner_id FROM bot_config")}
        readable = {slug for slug in readable if slug == who.actor or operators.get(slug) == pid
                    or (not is_person(slug) and not operators.get(slug) and who.role == "owner")}
    if scope == "direct":
        archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
        reports = G.reports_of(who.actor, views.roster(c), views.entries(c), archived, True)
        readable &= {a if H.actor_kind(a) == "human" else H.actor_id(a) for a in reports}
    return readable


def restricted_bots(c):
    """The bots not every member may read: nothing of theirs goes into a person's company-wide update."""
    from . import bot_access as A
    return {r["bot"] for r in c.execute("SELECT bot, access FROM bot_config WHERE access IS NOT NULL")
            if not A.document(r["access"])["read"]["everyone"]}


def team_updates(c, author, kind, day):
    """(the direct reports' updates for that day, the reports who have none) for a manager's request.
    A person's update is read by everyone, so their restricted bot reports are left out. Each row says
    whether it is flagged `meetings`, so a person's draft carries the flag up and a bot never gets its text."""
    reports = direct_reports(c, author)
    if is_person(author):
        hidden = restricted_bots(c)
        reports = [a for a in reports if a not in hidden]
    if not reports:
        return [], []
    rows = {r["bot"]: dict(r) for r in c.execute(
        "SELECT id, bot, body, meetings FROM updates WHERE kind=? AND day=? AND bot IN (" + ",".join("?" * len(reports)) + ")",
        (kind, day, *reports))}
    asked = {r["bot"] for r in c.execute(
        "SELECT bot FROM update_queue WHERE kind=? AND day=? AND bot IN (" + ",".join("?" * len(reports)) + ")",
        (kind, day, *reports))}
    missing = [a for a in reports if a not in rows and (a in asked or is_person(a))]
    return [rows[a] for a in reports if a in rows], missing


def _waited_out(row):
    """Whether a held manager's own wait is over, so it is asked with what is in."""
    return bool(row["held_at"]) and (H.parse_ts(H.now()) - H.parse_ts(row["held_at"])).total_seconds() >= MANAGER_WAIT_S


def _holding(c, row, chart=None, seen=frozenset()):
    """(waits, clock) for a queued request: whether a report is still queued or out, and whether this
    manager's own 90-minute clock runs. A report that is itself a manager still waiting on its own
    reports has not been asked yet, so the clock above it has not started, until that report's own
    wait is over: a stack of managers busy all day still adds one wait each, not a day. `seen` stops a
    `reports_to` loop."""
    chart = chart or _chart(c)
    seen = seen | {row["bot"]}
    reports = direct_reports(c, row["bot"], chart)
    if not reports:
        return False, False
    marks = ",".join("?" * len(reports))
    rows = c.execute(f"SELECT * FROM update_queue WHERE kind=? AND day=? AND bot IN ({marks})",
                     (row["kind"], row["day"], *reports)).fetchall()
    if not any(r["state"] in ("queued", "sent") for r in rows):
        return False, False
    if any(r["state"] == "queued" and r["bot"] not in seen and not _waited_out(r) and _holding(c, r, chart, seen)[0]
           for r in rows):
        return True, False
    return True, True


def waits_on_reports(c, row, chart=None):
    """Whether a manager's request still waits for its reports. Its clock starts the first time it is
    held with its reports asked (`held_at`, kept through a report's retry), and after `MANAGER_WAIT_S`
    it is asked with what is in."""
    waits, clock = _holding(c, row, chart)
    if not waits or not clock:
        return waits
    if not row["held_at"]:
        c.execute("UPDATE update_queue SET held_at=? WHERE id=?", (H.now(), row["id"]))
        return True
    return not _waited_out(row)


def _label(c, author):
    if is_person(author):
        from . import people as P
        p = P.person(author[6:], _chart(c)[0])
        return (p or {}).get("name") or author[6:]
    bot = H.bot(c, author) or {}
    return bot.get("display_name") or bot.get("slug") or author


# ----------------------------------------------------------------------------- the queue
def build_queue(c, day, kind, assistant=None):
    """Today's requests, once: every active bot whose switch for `kind` is on, the deepest on the
    org chart first and the quietest first among equals; then, with an `assistant`, a draft for each
    person whose drafting is on."""
    if c.execute("SELECT 1 FROM update_queue WHERE day=? LIMIT 1", (day,)).fetchone():
        return 0
    rows = c.execute(
        "SELECT b.id, b.slug, (SELECT max(u.created_at) FROM updates u WHERE u.bot=b.id) last_update, "
        "(SELECT last_turn_at FROM bot_status s WHERE s.bot=b.id) last_turn "
        "FROM bots b LEFT JOIN update_settings us ON us.bot=b.id "
        "WHERE b.state='active' AND coalesce(us." + ("daily" if kind == "daily" else "weekly") + ",1)=1").fetchall()
    depth = depths(c, [r["id"] for r in rows])
    order = sorted(rows, key=lambda r: (-depth[r["id"]], r["last_update"] or "", r["last_turn"] or "", r["slug"]))
    for rank, row in enumerate(order):
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state) VALUES(?,?,?,?,?,'queued')",
                  (H.new_id(), row["id"], kind, day, rank))
    people = drafted_people(c) if assistant and (H.bot(c, assistant) or {}).get("state") == "active" else []
    depth = person_depths(c, people)
    people.sort(key=lambda a: (-depth[a], a))
    for rank, author in enumerate(people, len(order)):
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state) VALUES(?,?,?,?,?,'queued')",
                  (H.new_id(), author, kind, day, rank))
    return len(order) + len(people)


def drafted_people(c):
    """The people on the chart whose Assistant drafts their update, as `human:<id>`."""
    from . import people as P
    return [H.human_actor(p["id"]) for p in P.visible_people(_chart(c)[0]) if p.get("update_draft") is not False]


def person_depths(c, authors):
    """Each person's depth on the org chart (the people above them), so the deepest are drafted first."""
    from . import people as P
    roster = _chart(c)[0]
    out = {}
    for author in authors:
        n, p, seen = 0, P.person(author[6:], roster), set()
        while p and p.get("reports_to") and p["id"] not in seen:
            seen.add(p["id"])
            p = P.person(p["reports_to"], roster)
            n += bool(p)
        out[author] = n
    return out


def _busy(c, bot):
    return c.execute("SELECT 1 FROM jobs WHERE bot_id=? AND state IN ('queued','leased','running','input') "
                     f"AND {H.unheld_sql('jobs')} LIMIT 1",
                     (bot,)).fetchone() is not None


def ensure_schema(c):
    """Columns added after the tables first shipped."""
    H.add_column(c, "update_queue", "tries", "INTEGER NOT NULL DEFAULT 0")
    H.add_column(c, "update_queue", "redo", "INTEGER NOT NULL DEFAULT 0")
    H.add_column(c, "update_queue", "held_at", "TEXT")
    H.add_column(c, "updates", "slides", "TEXT")
    H.add_column(c, "updates", "drafted_by", "TEXT")
    H.add_column(c, "updates", "edited_by", "TEXT")
    H.add_column(c, "updates", "inputs", "TEXT")
    # `source`: the automation run that posted it (`post_update`, backend/automation_actions.py); a post by hand
    # clears it. Shown as `source` only when set.
    H.add_column(c, "updates", "source", "TEXT")
    # `meetings`: a person's draft written with meeting summaries in its facts, or from a report's update that was,
    # is read only by a meeting reader (`meeting_reader`). A draft from before the column cannot say what it used,
    # so every one already there counts as having used them.
    if "meetings" not in {row[1] for row in c.execute("PRAGMA table_info(updates)")}:
        c.execute("SAVEPOINT update_meetings")
        try:
            H.add_column(c, "updates", "meetings", "INTEGER NOT NULL DEFAULT 0")
            c.execute("UPDATE updates SET meetings=1 WHERE drafted_by IS NOT NULL AND bot LIKE 'human:%'")
        except Exception:
            c.execute("ROLLBACK TO update_meetings")
            c.execute("RELEASE update_meetings")
            raise
        c.execute("RELEASE update_meetings")


def meeting_reader(who):
    """Whether `who` may read an update flagged `meetings`: the owner, or a person with an email on file, the
    same callers backend/sql.py lets read meetings. No bot, restricted or not, and no member without an email."""
    return who.role == "owner" or (who.role == "human" and bool((who.email or "").strip()))


def withheld(c, who, uid):
    """Whether this update is kept from `who` because it is flagged `meetings`; the caller answers as not found."""
    return not meeting_reader(who) and c.execute(
        "SELECT 1 FROM updates WHERE id=? AND meetings=1", (uid,)).fetchone() is not None


def _kept(meetings, update="u"):
    """The SQL that keeps flagged updates out for a reader who is not a meeting reader."""
    return "1" if meetings else f"coalesce({update}.meetings,0)=0"


def is_person(author):
    return str(author or "").startswith("human:")


def author_actor(author):
    """The actor an update's author key stands for."""
    return author if is_person(author) else H.bot_actor(author)


def _retry_or_miss(c, row, reason):
    """A run that ended without a post is asked once more, at the end of the day's queue: its
    computer may not have had the posting command yet (the first request can go out a
    minute after a deploy, before the computer's checkout catches up), or the run went sideways."""
    if (row["tries"] if "tries" in row.keys() else 0) < 1:
        last = c.execute("SELECT coalesce(max(rank), 0) FROM update_queue WHERE day=?", (row["day"],)).fetchone()[0]
        c.execute("UPDATE update_queue SET state='queued', rank=?, tries=tries+1, message_id=NULL, sent_at=NULL, "
                  "reason=? WHERE id=?", (last + 1, reason, row["id"]))
        return
    _finish(c, row, "missed", reason)


def _finish(c, row, state, reason=None):
    c.execute("UPDATE update_queue SET state=?, done_at=?, reason=? WHERE id=?", (state, H.now(), reason, row["id"]))


def settle_sent(c, row):
    """The request out now: posted, ended without a post, or still going. True while it is going."""
    redo = row["redo"] if "redo" in row.keys() else 0
    if c.execute("SELECT 1 FROM updates WHERE bot=? AND kind=? AND day=?" + (" AND updated_at>=?" if redo else ""),
                 (row["bot"], row["kind"], row["day"], *([row["sent_at"]] if redo else []))).fetchone():
        _finish(c, row, "posted")
        return False
    if is_person(row["bot"]):
        # The server's draft (`write_draft`) is under way; one that never finished is given up on.
        if (H.parse_ts(H.now()) - H.parse_ts(row["sent_at"])).total_seconds() > PICKUP_LIMIT_S:
            _finish(c, row, "missed", "its draft did not finish")
            return False
        return True
    job = c.execute("SELECT j.state, a.state attempt_state FROM jobs j LEFT JOIN attempts a ON a.id=j.attempt_id "
                    "WHERE j.message_id=?", (row["message_id"],)).fetchone()
    age = (H.parse_ts(H.now()) - H.parse_ts(row["sent_at"])).total_seconds()
    if job and job["state"] in ("completed", "canceled", "uncertain", "held"):
        _retry_or_miss(c, row, {"completed": "its run ended without posting an update",
                                   "canceled": "the request was cancelled",
                                   "uncertain": "its run stopped part-way",
                                   "held": "it could not run"}.get(job["state"], job["state"]))
        return False
    if (not job or job["state"] == "queued") and age > PICKUP_LIMIT_S:
        # Its computer is off, or it runs elsewhere (an external agent has no job here). The
        # request stays with the bot, and a late post still lands; the queue moves on.
        _finish(c, row, "missed", "it had not started after 20 minutes" if job else "it did not pick the request up")
        return False
    if age > RUN_LIMIT_S + PICKUP_LIMIT_S:
        _finish(c, row, "missed", "its run did not end")
        return False
    return True


def request_text(c, bot, kind, day, redo=False):
    """What the bot is asked, with its facts: done since its last update, open work, what waits on
    a person, its goals. It writes from these, so the turn is short and says only what happened.
    A redo of a past week takes that week's finished work and the bot's old update to rewrite."""
    actor = H.bot_actor(bot)
    week = kind == "weekly"
    if redo:
        start = datetime.combine(datetime.fromisoformat(day).date(), datetime.min.time(), ZONE)
        utc = lambda at: at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        since, until = utc(start - timedelta(days=6 if week else 0)), utc(start + timedelta(days=1))
    else:
        last = c.execute("SELECT max(created_at) FROM updates WHERE bot=?", (bot,)).fetchone()[0]
        since, until = last or H.shift(H.now(), days=-7 if week else -1), "9999"
    done = c.execute("SELECT title FROM tasks WHERE owner_id=? AND status IN ('done','closed') AND updated_at>=? AND updated_at<? "
                     "ORDER BY updated_at DESC LIMIT 12", (actor, since, until)).fetchall()
    open_ = c.execute("SELECT title, status FROM tasks WHERE owner_id=? AND status IN ('open','doing','waiting','review','ready') "
                      "ORDER BY updated_at DESC LIMIT 12", (actor,)).fetchall()
    asked = c.execute("SELECT title, owner_id FROM tasks WHERE requester_id=? AND owner_id LIKE 'human:%' AND status IN "
                      "('open','doing','waiting') ORDER BY updated_at DESC LIMIT 8", (actor,)).fetchall()
    goals = c.execute("SELECT title FROM goals WHERE owner_id=? AND (status IS NULL OR status NOT IN ('done','dropped')) "
                      "ORDER BY rank LIMIT 5", (actor,)).fetchall()
    line = lambda rows, f: "\n".join("- " + f(r) for r in rows) or "- (none)"
    post_day = f" with day {day}" if redo else ""
    if week:
        tracked = kpi_snapshot(c, bot)
        ask = [
            f"Time for your week in review ({day}). Post it with hub_update_create{post_day} "
            "(or `hub update create --slides-file`), then carry on.",
            "",
            SLIDES_SHAPE,
            "Say it plainly: these are skimmed by swiping, so a slide is a few short lines, never a wall of text. "
            "If nothing got done, say why in one bullet, then pick your most useful open task and start it in this turn. "
            "A review that breaks these rules is refused.",
        ]
        facts = [
            "", f"Finished {'that week' if redo else 'this week'}:", line(done, lambda r: r["title"]),
            "Open:", line(open_, lambda r: f"{r['title']} ({r['status']})"),
            "Waiting on a person:", line(asked, lambda r: f"{r['title']} ({H.actor_id(r['owner_id'])})"),
            "Your goals:", line(goals, lambda r: r["title"]),
            "Your goal KPIs (Tico puts these on the KPI slide):",
            line(tracked, lambda k: f"{k['name']}: {k['value'] if k['value'] is not None else 'no data'}"
                                     f"{(' ' + k['unit']) if k['unit'] and k['value'] is not None else ''}"
                                     f"{(' (' + k['target_label'] + ')') if k.get('target_label') else ''}"),
        ]
    else:
        ask = [
            f"Time for your daily update ({day}). Post it with hub_update_create{post_day} "
            "(or `hub update create`), in plain words, then carry on.",
            "",
            "Write one to five bullets in plain English and nothing else: no title, no headings or sections, "
            "no task ids or internal codes. Each bullet is one line: what you did, what you do next, or what "
            f"waits on a person (say who). At most {LIMITS[kind]['bullet']} words a bullet and {LIMITS[kind]['words']} in all.",
            "If nothing got done, say why in one bullet, then pick your most useful open task and start it in "
            "this turn. Every bot moves at least once a day. An update that breaks these rules is refused.",
        ]
        facts = [
            "", f"Finished {'that day' if redo else 'since your last update'}:", line(done, lambda r: r["title"]),
            "Open:", line(open_, lambda r: f"{r['title']} ({r['status']})"),
            "Waiting on a person:", line(asked, lambda r: f"{r['title']} ({H.actor_id(r['owner_id'])})"),
            "Your goals:", line(goals, lambda r: r["title"]),
        ]
    team, missing = team_updates(c, bot, kind, day)
    if team or missing:
        ask.insert(1, "You manage a team: your update is your own work and what matters from theirs, naming who did it. "
                      "Sum it up in your own words; do not copy their bullets.")
        facts += ["Your team's " + ("weeks in review" if week else "updates") + " today:"]
        # A bot may not read a flagged update: it learns that the report posted, not what.
        facts += [f"{_label(c, u['bot'])}:\n{u['body']}" if not u["meetings"] else
                  f"{_label(c, u['bot'])}: posted an update (not shown here)" for u in team] or ["- (none yet)"]
        if missing:
            facts.append("No update from: " + ", ".join(_label(c, a) for a in missing))
    if redo:
        old = c.execute("SELECT body FROM updates WHERE bot=? AND kind=? AND day=?", (bot, kind, day)).fetchone()
        ask.insert(1, f"This redoes your {'week in review' if week else 'update'} for {day} in the current shape; "
                      "write it as of that day.")
        if old:
            facts += ["What you posted then:", old["body"]]
    return "\n".join(ask + facts)


DRAFT_LIST_MAX = 8          # each of a draft's decisions, answers and comments
DRAFT_SCAN_MAX = 40         # the person's latest messages of a kind looked at for those
MEETINGS_MAX, MEETING_CHARS = 3, 600
OWN = "they posted their own"


def own_words(c, author, day):
    """Whether the person wrote or edited their own update that day, in either kind: then no draft is
    written for them, so none lands beside their words."""
    return c.execute("SELECT 1 FROM updates WHERE bot=? AND day=? AND (drafted_by IS NULL OR edited_by IS NOT NULL) "
                     "LIMIT 1", (author, day)).fetchone() is not None


DRAFT_INSTRUCTIONS = (
    "You draft a person's update for their company from the facts given, in the first person, as they would "
    "write it: what they did, do next and wait on. Use only the facts given; never invent work. ")


def shared_tasks_sql(hidden, prefix=""):
    """The rule every source of a person's draft is held to: a task that is not private and has no
    restricted bot (`hidden`, as actors) as owner or requester, so every member may read it."""
    rule = f"coalesce({prefix}private,1)=0"
    if hidden:
        from . import bot_access as A
        rule += " AND {0}owner NOT IN {1} AND {0}requester NOT IN {1}".format(prefix, A.qlist(hidden))
    return rule


def _shared_message(c, message, shared):
    """The title of the task a person's message is on, when every task it names (its refs, the message it
    answers, the run it came from: `task_privacy.message_tasks`) passes the shared rule; else None."""
    from . import task_privacy as TP
    try:
        ids = TP.message_tasks(c, message, current=True)
    except Problem:
        return None
    if not ids:
        return None
    marks = ",".join("?" * len(ids))
    rows = {r["id"]: r["title"] for r in c.execute(f"SELECT id, title FROM tasks WHERE id IN ({marks}) AND {shared}",
                                                     tuple(ids))}
    if len(rows) != len(ids):
        return None
    refs = H._json(message["refs"], {}) or {}
    return rows.get(H.message_task_id({"refs": refs})) or next(iter(rows.values()))


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def draft_facts(c, author, kind, day):
    """Everything a person's draft is written from: what every member can already see. Their tasks that
    are not private and have no restricted bot as owner or requester, their goals, their decisions and
    comments on such tasks, the summaries of the shared meetings they were in, and their reports'
    updates. Each list is bounded by the day or week and capped. No chat is read here, so none can reach
    the draft. Approvals stay out: only their owner, requester and recipient may read one. Returns (the
    facts, whether a meeting summary or a flagged report's update went in), the draft's `meetings` flag."""
    from . import people as P
    name = _label(c, author)
    week = kind == "weekly"
    since = H.shift(H.now(), days=-7 if week else -1)
    hidden = [H.bot_actor(slug) for slug in sorted(restricted_bots(c))]
    shared = shared_tasks_sql(hidden)
    done = c.execute(f"SELECT title FROM tasks WHERE owner_id=? AND {shared} AND status IN ('done','closed') AND updated_at>=? "
                     "ORDER BY updated_at DESC LIMIT 12", (author, since)).fetchall()
    open_ = c.execute(f"SELECT title, status FROM tasks WHERE owner_id=? AND {shared} AND status IN "
                      "('open','doing','waiting','review','ready') ORDER BY updated_at DESC LIMIT 12", (author,)).fetchall()
    asked = c.execute(f"SELECT title, owner_id FROM tasks WHERE requester_id=? AND {shared} AND owner_id<>? AND status IN "
                      "('open','doing','waiting') ORDER BY updated_at DESC LIMIT 8", (author, author)).fetchall()
    goals = c.execute("SELECT title FROM goals WHERE owner_id=? AND (status IS NULL OR status NOT IN "
                      "('done','dropped')) ORDER BY rank LIMIT 5", (author,)).fetchall()
    # Their decisions in Needs you: what they moved a shared task to (approved, declined, done, closed)...
    decided = c.execute(f"SELECT t.title, e.new FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE e.actor=? "
                        f"AND e.field='status' AND e.old IS NOT NULL AND e.created_at>=? AND {shared_tasks_sql(hidden, 't.')} "
                        "ORDER BY e.created_at DESC LIMIT ?", (author, since, DRAFT_LIST_MAX)).fetchall()
    # ...and their answers to asks, then their comments; a message counts only when every task it names is shared.
    said = lambda where: [(title, m["body"]) for m in c.execute(
        "SELECT * FROM messages WHERE sender_id=? AND deleted_at IS NULL AND created_at>=? AND " + where
        + " ORDER BY created_at DESC LIMIT ?", (author, since, DRAFT_SCAN_MAX)).fetchall()
        if (title := _shared_message(c, m, shared))][:DRAFT_LIST_MAX]
    answers = said("kind='answer'")
    comments = said("kind IN ('say','ask') AND json_extract(refs,'$.task') IS NOT NULL "
                    "AND json_extract(refs,'$.comment')")
    # The shared meetings they owned or were in (media.shared_meeting; sql.py has the same rule): live,
    # not private, not deleted, with a summary.
    email = ((P.person(author[6:], _chart(c)[0]) or {}).get("email") or "").strip().lower()
    meetings = c.execute(
        "SELECT m.title, m.notes FROM meetings m LEFT JOIN media_control mc ON mc.meeting_id=m.id "
        "WHERE m.review_state='live' AND mc.deleted_at IS NULL AND json_extract(m.metadata,'$.kind')='meeting' "
        "AND coalesce(json_extract(m.metadata,'$.private'),0)=0 AND trim(m.notes)<>'' AND m.created_at>=? AND ("
        "lower(m.owner)=? OR EXISTS (SELECT 1 FROM json_each(json_extract(m.metadata,'$.calendar.attendees')) "
        "WHERE lower(trim(value))=?) OR EXISTS (SELECT 1 FROM json_each(json_extract(m.metadata,'$.participants')) "
        "WHERE lower(trim(json_extract(value,'$.email')))=?)) ORDER BY m.created_at DESC LIMIT ?",
        (since, email, email, email, MEETINGS_MAX)).fetchall() if email else []
    line = lambda rows, f: "\n".join("- " + f(r) for r in rows) or "- (none)"
    facts = [f"{name}'s {'week in review' if week else 'daily update'} for {day}.", "",
             f"Finished {'this week' if week else 'since yesterday'}:", line(done, lambda r: r["title"]),
             "Open:", line(open_, lambda r: f"{r['title']} ({r['status']})"),
             "Waiting on someone else:", line(asked, lambda r: f"{r['title']} ({_label(c, r['owner_id'] if is_person(r['owner_id']) else H.actor_id(r['owner_id']))})"),
             "Their goals:", line(goals, lambda r: r["title"]),
             "Their decisions:", line(decided, lambda r: f"{r['title']}: {r['new']}"),
             "Their answers:", line(answers, lambda r: f"On {r[0]}: {_clip(r[1], 200)}"),
             "Their comments on tasks:", line(comments, lambda r: f"On {r[0]}: {_clip(r[1], 200)}"),
             "Meetings they were in:", line(meetings, lambda r: f"{r['title']}: {_clip(r['notes'], MEETING_CHARS)}")]
    team, missing = team_updates(c, author, kind, day)
    if team or missing:
        facts += ["Their team's " + ("weeks in review" if week else "updates") + " today:"]
        facts += [f"{_label(c, u['bot'])}:\n{u['body']}" for u in team] or ["- (none yet)"]
        if missing:
            facts.append("No update from: " + ", ".join(_label(c, a) for a in missing))
    return "\n".join(facts), bool(meetings) or any(u["meetings"] for u in team)


def draft_instructions(kind):
    if kind == "weekly":
        return DRAFT_INSTRUCTIONS + SLIDES_SHAPE + (' Reply with one JSON object and nothing else: {"slides": {"goal": "...", '
                                                    '"kpis": [], "done": [...], "focus": [...], "blockers": [...]}}.')
    return DRAFT_INSTRUCTIONS + (f"Write one to five bullets in plain English: no title, headings or task ids, at most "
                                 f"{LIMITS[kind]['bullet']} words a bullet and {LIMITS[kind]['words']} in all. Reply with "
                                 'one JSON object and nothing else: {"body": "- first bullet\\n- second bullet"}.')


def writer_for(c, settings, store=None, vault=None):
    """The model a person's draft is written with: Tico Mid 1.0 on the Tico AI Gateway under the two Gateway models
    choices (backend/models_preset.py) while it is connected, else the company's own provider, or None (no drafts).
    The gateway key is read from `store` at the call, never held by the writer."""
    if settings.rehearsal:
        return None
    from clients import gateway as G, judge as J
    from . import gateway_connection, models_preset, providers
    from .judge import fallback_model
    record = providers.load(c, settings)
    if store is not None and models_preset.spends_gateway(record) and gateway_connection.connected(c, settings):
        base = G.chat_base(gateway_connection.url(c, settings))

        def key_of():
            from .credentials import Vault
            keys = vault or Vault(store)
            if not keys.configured and not gateway_connection.env_key(settings):
                return ""
            with store.read() as c:
                secret, now_base, _ = gateway_connection.target(c, keys, settings)
            return secret if G.chat_base(now_base) == base else ""   # moved since: the key is not this address's
        return J.gateway_writer(key_of, base, models_preset.pick())
    found = fallback_model(record)
    return J.llm_writer(*found) if found else None


def drafter(c, settings, author, assistant):
    """Whose draft a person's update is labelled: their Assistant's, the owner's included (backend/assistant.py: one
    Assistant for everyone)."""
    return assistant


def write_draft(store, author, write, assistant):
    """Draft the person's update Tico has out, with `write(system, user)`, and post it labelled. Runs
    outside a transaction (the model call holds no lock). The call carries the instructions and
    `draft_facts` and nothing else. Once they have posted their own words that day, in either kind,
    nothing is drafted. A failed or unusable draft, whatever went wrong, is tried once more, at the back
    of the queue."""
    with store.read() as c:
        row = c.execute("SELECT * FROM update_queue WHERE bot=? AND state='sent'", (author,)).fetchone()
        if not row:
            return None
        assistant = drafter(c, store.settings, author, assistant)
        own = own_words(c, author, row["day"])
        if not own:
            system = draft_instructions(row["kind"])
            user, flagged = draft_facts(c, author, row["kind"], row["day"])
    out = failed = None
    if not own:
        try:
            out = write(system, user)
        except Exception as exc:
            failed = "the draft model did not answer (" + type(exc).__name__ + ")"
    with store.transaction() as c:
        row = c.execute("SELECT * FROM update_queue WHERE id=? AND state='sent'", (row["id"],)).fetchone()
        if not row:
            return None
        if own or own_words(c, author, row["day"]):
            _finish(c, row, "posted", OWN)
            return None
        if out is None:
            _retry_or_miss(c, row, failed)
            return None
        c.execute("SAVEPOINT update_draft")
        try:
            posted = post(c, author, out.get("body"), kind=row["kind"], day=row["day"],
                          slides=out.get("slides") if row["kind"] == "weekly" else None, drafted_by=assistant,
                          meetings=flagged)
        except Exception as exc:
            # A model's answer can be any shape (slides as a string raise AttributeError): nothing of it
            # is kept, and the row is asked again rather than left out until it is given up on.
            if not c.in_transaction:
                raise
            c.execute("ROLLBACK TO update_draft")
            c.execute("RELEASE update_draft")
            if isinstance(exc, Problem) and exc.code == "own_update":
                _finish(c, row, "posted", OWN)
            else:
                _retry_or_miss(c, row, "its draft did not fit the format (" + type(exc).__name__ + ")")
            return None
        c.execute("RELEASE update_draft")
        _finish(c, row, "posted")
        return posted


def send_request(c, row, assistant=None):
    redo = bool(row["redo"]) if "redo" in row.keys() else False
    if is_person(row["bot"]):
        # No message: the server drafts it (`write_draft`), so no bot run is involved.
        c.execute("UPDATE update_queue SET state='sent', message_id=NULL, sent_at=? WHERE id=?", (H.now(), row["id"]))
        return None
    message = H.say(c, H.KEEPER, H.bot_actor(row["bot"]), request_text(c, row["bot"], row["kind"], row["day"], redo),
                    kind="notice", refs=run_triggers.stamp({"update_request": row["id"], "wake": "update"}, "schedule",
                                                           row["id"], purpose="write update"))
    c.execute("UPDATE update_queue SET state='sent', message_id=?, sent_at=? WHERE id=?",
              (message["id"], H.now(), row["id"]))
    return message


def dispatch(c, at=None, assistant=None):
    """One step, from the scheduler's tick: build today's queue after 05:00, settle the request
    that is out, and send the next one. At most one request is out at a time. With an `assistant`
    (drafting is on), a person's draft is next once no bot can be asked: the caller writes it."""
    local = local_now(at)
    day = local.date().isoformat()
    if local.hour >= START_HOUR:
        build_queue(c, day, kind_for(day), assistant)
    out = c.execute("SELECT * FROM update_queue WHERE state='sent' ORDER BY sent_at LIMIT 1").fetchone()
    if out and settle_sent(c, out):
        return None
    # Today's requests first, then any redo of a past day an owner asked for.
    chart = None
    for row in c.execute("SELECT q.* FROM update_queue q JOIN bots b ON b.id=q.bot WHERE q.state='queued' "
                         "AND (q.day=? OR q.redo=1) ORDER BY q.day=? DESC, q.rank", (day, day)).fetchall():
        bot = H.bot(c, row["bot"]) or {}
        if bot.get("state") != "active":
            _finish(c, row, "skipped", "the bot is " + (bot.get("state") or "gone"))
            continue
        if not settings(c, row["bot"])[row["kind"]]:
            _finish(c, row, "skipped", "its " + row["kind"] + " update is switched off")
            continue
        if _busy(c, row["bot"]):
            continue                      # asked later today, once its run is over
        chart = chart or _chart(c)
        if waits_on_reports(c, row, chart):
            continue                      # asked once its reports are in, or the wait is up
        send_request(c, row)
        return row["bot"]
    drafting = set(drafted_people(c))
    for row in c.execute("SELECT * FROM update_queue WHERE state='queued' AND day=? AND bot LIKE 'human:%' "
                         "ORDER BY rank", (day,)).fetchall():
        if own_words(c, row["bot"], day) or c.execute("SELECT 1 FROM updates WHERE bot=? AND kind=? AND day=?",
                                                      (row["bot"], row["kind"], day)).fetchone():
            _finish(c, row, "posted", OWN)
            continue
        if row["bot"] not in drafting or not assistant or (H.bot(c, assistant) or {}).get("state") != "active":
            _finish(c, row, "skipped", "drafting is off")
            continue
        chart = chart or _chart(c)
        if waits_on_reports(c, row, chart):
            continue
        send_request(c, row, assistant)
        return row["bot"]
    # A day that ends with bots never free leaves their requests behind; they are missed.
    for row in c.execute("SELECT * FROM update_queue WHERE state='queued' AND day<? AND redo=0", (day,)).fetchall():
        _finish(c, row, "missed", "it was busy all day")
    return None


def redo(c, kind, day, bots=None, managers=False):
    """Ask bots again for a past day's update, in today's shape: every active bot whose switch is on,
    or only `bots` (with `managers`, also the bots above them, so the roll-up is written again).
    Each goes to the back of the queue, deepest first, and is sent like any request."""
    if kind not in KINDS:
        raise Problem("kind", "An update is daily or weekly", 422)
    try:
        when = datetime.fromisoformat(day).date()
    except ValueError:
        raise Problem("day", "day is YYYY-MM-DD", 422)
    if when.isoformat() > today():
        raise Problem("day", "A redo is for today or an earlier day", 422)
    rows = c.execute("SELECT b.id FROM bots b LEFT JOIN update_settings us ON us.bot=b.id WHERE b.state='active' "
                     "AND coalesce(us." + kind + ",1)=1 ORDER BY b.slug").fetchall()
    ids = {b: (H.resolve_bot(c, b) or {}).get("id", b) for b in bots or ()}
    chosen = [r["id"] for r in rows if not bots or r["id"] in ids.values()]
    unknown = sorted(b for b, bid in ids.items() if bid not in chosen)
    if unknown:
        raise Problem("bot", "Not an active bot with its " + kind + " update on: " + ", ".join(unknown), 422)
    if bots and managers:
        from . import goals as G
        roster, entries, archived = _chart(c)
        bare = True
        above = {H.actor_id(a) for slug in chosen for a in G.above(H.bot_actor(slug), roster, entries, archived, bare)
                 if H.actor_kind(a) == "bot"}
        chosen += [r["id"] for r in rows if r["id"] in above and r["id"] not in chosen]
    depth = depths(c, chosen)
    chosen.sort(key=lambda slug: -depth[slug])
    last = c.execute("SELECT coalesce(max(rank), 0) FROM update_queue").fetchone()[0]
    for n, slug in enumerate(chosen, 1):
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,tries,redo,reason) VALUES(?,?,?,?,?,'queued',0,1,?) "
                  "ON CONFLICT(bot,kind,day) DO UPDATE SET state='queued', rank=excluded.rank, tries=0, redo=1, "
                  "message_id=NULL, sent_at=NULL, done_at=NULL, held_at=NULL, reason=excluded.reason",
                  (H.new_id(), slug, kind, when.isoformat(), last + n, "asked again in the current shape"))
    return chosen


# ----------------------------------------------------------------------------- the week's slides
def kpi_snapshot(c, bot):
    """The KPIs on the bot's open goals, as the KPI slide shows them: today's value, target and
    sparkline. With no goal KPIs, its automatic ones that have a reading."""
    from . import botkpis, kpis as K
    goal_ids = [r["id"] for r in c.execute(
        "SELECT id FROM goals WHERE owner_id=? AND (status IS NULL OR status NOT IN ('done','dropped')) ORDER BY rank LIMIT 5",
        (author_actor(bot),))]
    views, seen = [], set()
    for gid, items in K.goal_views(c, goal_ids).items() if goal_ids else ():
        for v in items:
            if v["id"] not in seen:
                seen.add(v["id"]); views.append(v)
    if not views and not is_person(bot):
        views = [v for v in botkpis.for_bot(c, bot) if v.get("latest")]
    out = []
    for v in views[:TRACKED_MAX]:
        latest = v.get("latest") or {}
        out.append({"name": v["name"], "unit": v.get("unit") or "", "value": latest.get("value"),
                    "period_end": latest.get("period_end"), "spark": v.get("spark") or [],
                    "status": v.get("status") or "gray", "target_label": v.get("target_label") or "",
                    "fresh": v.get("freshness", "fresh") == "fresh"})
    return out


def _words(text):
    return len(str(text or "").split())


def lint_slides(slides):
    """What is wrong with a week's slides, or None."""
    if not isinstance(slides, dict):
        return "Not a week in review yet: it has no slides. " + SLIDES_SHAPE + " Post again with hub_update_create."
    missing, style = slide_problems(slides)
    return _slides_message(missing + style)


def _slides_message(problems):
    if not problems:
        return None
    return "Not a week in review yet: " + "; ".join(problems) + ". " + SLIDES_SHAPE + " Post again with hub_update_create."


def slide_problems(slides):
    """(a slide that is missing, so the week is refused; the writing problems, which are warned about)."""
    missing, problems = [], []
    goal = str(slides.get("goal") or "").strip()
    if not goal:
        missing.append("the goal slide is empty")
    elif _words(goal) > GOAL_WORDS or "\n" in goal:
        problems.append(f"the goal is one sentence of at most {GOAL_WORDS} words (it is {_words(goal)})")
    names = {"done": "done", "focus": "focus", "blockers": "blockers"}
    for key, (low, high, words) in SLIDES.items():
        items = slides.get(key) or []
        if len(items) < low:
            missing.append(f"{names[key]} needs at least {low} bullet")
        if len(items) > high:
            problems.append(f"{names[key]} has {len(items)} bullets (at most {high})")
        long = [_words(b) for b in items if _words(b) > words]
        if long:
            problems.append(f"a {names[key]} bullet is {max(long)} words (at most {words})")
    kpis = slides.get("kpis") or []
    if len(kpis) > BOT_KPIS_MAX:
        problems.append(f"it adds {len(kpis)} KPIs (at most {BOT_KPIS_MAX}; Tico adds your goal KPIs)")
    text = " ".join([goal, *(str(b) for k in SLIDES for b in slides.get(k) or []),
                     *(str(k.get("note") or "") for k in kpis)])
    ids = TASK_ID.findall(MD_LINK.sub(r"\1", text))
    if ids:
        problems.append("it has a task id or internal code (" + ", ".join(sorted(set(ids))[:3]) + "); say it in plain English")
    return missing, problems


def clean_slides(raw):
    """The bot's slides with blank bullets dropped and every bullet on one line."""
    one = lambda v: " ".join(re.sub(r"^\s*[-*•]\s+", "", str(v or "")).split())
    out = {"goal": " ".join(str(raw.get("goal") or "").split())}
    for key in SLIDES:
        out[key] = [one(b) for b in raw.get(key) or [] if one(b)]
    out["kpis"] = []
    for k in raw.get("kpis") or []:
        k = dict(k)
        item = {"name": one(k.get("name")), "value": one(k.get("value"))}
        if not item["name"] or not item["value"]:
            continue
        if k.get("unit"):
            item["unit"] = one(k["unit"])
        if k.get("note"):
            item["note"] = one(k["note"])
        series = [float(v) for v in k.get("series") or [] if isinstance(v, (int, float))][-SERIES_MAX:]
        if series:
            item["series"] = series
        out["kpis"].append(item)
    return out


def slides_body(slides):
    """The plain-markdown copy of a week's slides, for the bot's page and summaries."""
    lines = ["- " + b for b in slides["done"]]
    lines += ["- Next: " + b for b in slides["focus"]]
    lines += ["- Blocked: " + b for b in slides["blockers"]]
    return "\n".join(lines)


def _decode(row):
    item = dict(row)
    raw = item.pop("slides", None)
    item["slides"] = json.loads(raw) if raw else None
    item.pop("meetings", None)            # read rules use it; it is not part of the card
    if "inputs" in item:
        inputs = item.pop("inputs")
        item["inputs"] = json.loads(inputs) if inputs else []
    source = item.pop("source", None)
    if source:
        item["source"] = json.loads(source)
    return item


def with_inputs(c, item, readable=None, meetings=False):
    """`inputs` as the reports' updates this reader may read, `[{id, author}]`, so a card links what it
    was written from. An input by an author outside `readable` (None: the author's own view), or one
    flagged `meetings` for a reader who is not a meeting reader, leaves nothing behind, not even its id;
    one since deleted drops out too."""
    ids = [i for i in item.get("inputs") or [] if isinstance(i, str)]
    rows = dict(c.execute("SELECT id, bot FROM updates u WHERE id IN (" + ",".join("?" * len(ids)) + ") AND "
                          + _kept(meetings), ids).fetchall()) if ids else {}
    item["inputs"] = [{"id": i, "author": rows[i]} for i in ids if i in rows and (readable is None or rows[i] in readable)]
    return item


# ----------------------------------------------------------------------------- posting and reading
def lint(body, kind):
    """What is wrong with an update, or None: 1-5 one-line bullets, plain English, short."""
    limit = LIMITS[kind]
    lines = [line for line in str(body or "").splitlines() if line.strip()]
    problems = []
    if not lines:
        problems.append("it is empty")
    headings = [line.strip() for line in lines if HEADING.match(line)]
    if headings:
        problems.append("it has a title or section heading (" + ", ".join(repr(h[:30]) for h in headings[:3]) + ")")
    loose = [line.strip() for line in lines if not BULLET.match(line) and not HEADING.match(line)]
    if loose:
        problems.append(f"{len(loose)} line{'s are' if len(loose) != 1 else ' is'} not a bullet (" + repr(loose[0][:40]) + ")")
    bullets = [line for line in lines if BULLET.match(line)]
    labelled = [b.strip() for b in bullets if LABEL.match(b)]
    if labelled:
        problems.append("a bullet starts with a section label (" + repr(labelled[0][:30]) + ")")
    if len(bullets) > BULLETS_MAX:
        problems.append(f"it has {len(bullets)} bullets (at most {BULLETS_MAX})")
    long = [len(b.split()) - 1 for b in bullets if len(b.split()) - 1 > limit["bullet"]]
    if long:
        problems.append(f"{len(long)} bullet{'s are' if len(long) != 1 else ' is'} over {limit['bullet']} words (longest {max(long)})")
    words = sum(len(b.split()) - 1 for b in bullets)
    if words > limit["words"]:
        problems.append(f"it is {words} words (a {kind} update is at most {limit['words']})")
    visible = MD_LINK.sub(r"\1", str(body or ""))
    ids = TASK_ID.findall(visible)
    if ids:
        problems.append("it has a task id or internal code (" + ", ".join(sorted(set(ids))[:3]) + "); say it in plain English")
    if not problems:
        return None
    return ("Not an update yet: " + "; ".join(problems) + ". " + SHAPE
            + " Rewrite it and post again with hub_update_create.")


def check_day(c, bot, day):
    """A bot posts for today, or for a past day Tico asked it to redo; never a back-dated post of its own."""
    if day and day != today() and not c.execute("SELECT 1 FROM update_queue WHERE bot=? AND day=? AND redo=1",
                                                (bot, day)).fetchone():
        raise Problem("day", "An update is for today, or for the day Tico asked you to redo", 422)


def post(c, bot, body, kind=None, day=None, slides=None, drafted_by=None, inputs=None, replace_draft=False,
         meetings=False, reader=True, source=None, warning=""):
    """Store an update by `bot` (a bot id or `human:<id>`). A daily is its bullets; a week in review
    is its slides, with the goal KPIs added, and a markdown copy in `body`. `headline` keeps one line,
    for places that show one (MCP summaries). `drafted_by` posts a draft for a person; the person
    posting over it is an edit of that draft, or with `replace_draft` their own words. A draft never
    lands over or beside words the person wrote or edited that day, in either kind. `meetings` flags a draft
    written from meeting summaries (`draft_facts`); an edit keeps the draft's flag, their own words clear it.
    `reader` is whether the person posting is a meeting reader (`meeting_reader`): one who is not was never shown a
    flagged draft, so what they post over it is their own words, and their reply links no flagged input. `source` is
    the automation run posting it ({automation, run, task}); any other post clears it. `warning` is an org rule's warn
    line (org_rule_check), put before the update once its own words pass the writing checks: a week in review's goal,
    a daily's bullets, never linted itself."""
    day = day or today()
    kind = effective_kind(kind, slides, day)
    if kind not in KINDS:
        raise Problem("kind", "An update is daily or weekly", 422)
    stored = None
    if kind == "weekly":
        if not slides:
            raise H.Refused("lint", lint_slides(None), "normal")
        slides = clean_slides(slides)
        missing, style = slide_problems(slides)
        if missing:
            raise H.Refused("lint", _slides_message(missing + style), "normal")
        problem = _style(_slides_message(style))
        if warning:
            slides = {**slides, "goal": warning + "\n\n" + slides["goal"]}
        stored = json.dumps({**slides, "tracked": kpi_snapshot(c, bot)})
        body = slides_body(slides)
        headline = slides["goal"][:200]
    else:
        body = "\n".join(line.rstrip() for line in str(body or "").strip().splitlines() if line.strip())
        if not body:
            raise H.Refused("lint", lint(body, kind), "normal")
        problem = _style(lint(body, kind))
        if warning:
            body = warning + "\n\n" + body
        first = body.splitlines()[0]
        headline = MD_LINK.sub(r"\1", re.sub(r"^\s*[-*•]\s+", "", first))[:200]
    now = H.now()
    existing = c.execute("SELECT id, drafted_by, edited_by, inputs, meetings FROM updates WHERE bot=? AND kind=? AND day=?",
                         (bot, kind, day)).fetchone()
    if drafted_by and own_words(c, bot, day):
        raise Problem("own_update", "They wrote their own update today; a draft does not replace it", 409)
    if inputs is None:
        inputs = [u["id"] for u in team_updates(c, bot, kind, day)[0]]
    inputs_json = json.dumps(list(inputs)) if inputs else None
    source_json = json.dumps(source, sort_keys=True) if source else None
    if drafted_by:
        draft, edited, flagged = drafted_by, None, int(bool(meetings))
    elif existing and existing["drafted_by"] and not replace_draft and (reader or not existing["meetings"]):
        draft, edited, flagged = existing["drafted_by"], bot, existing["meetings"]
    else:
        draft, edited, flagged = None, None, 0
    if existing:
        c.execute("UPDATE updates SET headline=?, body=?, slides=?, updated_at=?, drafted_by=?, edited_by=?, "
                  "inputs=?, meetings=?, source=? WHERE id=?",
                  (headline, body, stored, now, draft, edited, inputs_json, flagged, source_json, existing["id"]))
        c.execute("DELETE FROM update_reads WHERE update_id=?", (existing["id"],))   # changed: unread again
        uid = existing["id"]
    else:
        uid = H.new_id()
        c.execute("INSERT INTO updates(id,bot,kind,day,headline,body,slides,created_at,updated_at,drafted_by,edited_by,"
                  "inputs,meetings,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (uid, bot, kind, day, headline, body, stored, now, now, draft, edited, inputs_json, flagged,
                   source_json))
        # A new latest logical day supersedes updates this reader previously restored. A backdated
        # redo must not revoke a restore: there is already a newer day, and this post did not
        # supersede it. Removing just restore overrides keeps manual archives intact. Same-day
        # retries and replacements take the existing-row path above and have no archive side effects.
        has_newer = c.execute("SELECT 1 FROM updates WHERE bot=? AND kind=? AND day>? LIMIT 1",
                              (bot, kind, day)).fetchone()
        if not has_newer:
            c.execute("DELETE FROM update_archive_overrides WHERE state='restored' AND update_id IN "
                      "(SELECT id FROM updates WHERE bot=? AND kind=? AND day<?)", (bot, kind, day))
    H.event(c, H.bot_actor(drafted_by) if drafted_by else author_actor(bot), "update.post", uid, {"kind": kind, "day": day, **({"warning": problem} if problem else {})})
    posted = with_inputs(c, one(c, uid), meetings=is_person(bot) and reader)
    return {**posted, "warning": problem} if problem else posted


def _style(problem):
    """An update's writing problem as the warning its accepted post carries; refused when TICO_STYLE_LINT=refuse."""
    if not problem or H.STYLE_LINT == "off":
        return None
    if H.STYLE_LINT == "refuse":
        raise H.Refused("lint", problem, "normal")
    problem = re.sub(r"^Not (?:an update|a week in review) yet: ", "Posted. Next time fix this: ", problem)
    return re.sub(r" (?:Rewrite it and post|Post) again with hub_update_create\.$", "", problem)


def purge_rejected(c):
    """Delete stored updates the linter would refuse now and ask their bots again (rejected updates are
    deleted and redone). Runs when the hub
    starts; the linter refuses new ones at the door. Each bot goes to the back of its day's queue."""
    from . import org_rule_check
    removed = []
    # A week in review with slides was checked as slides; an older bullet one keeps the bullet rules.
    # Only when the shape is refused: a post accepted with a warning stays.
    for row in c.execute("SELECT * FROM updates WHERE slides IS NULL").fetchall() if H.STYLE_LINT == "refuse" else ():
        if not lint(org_rule_check.unwarned(row["body"]), row["kind"]):
            continue
        c.execute("DELETE FROM update_reads WHERE update_id=?", (row["id"],))
        c.execute("DELETE FROM update_archive_overrides WHERE update_id=?", (row["id"],))
        c.execute("DELETE FROM updates WHERE id=?", (row["id"],))
        last = c.execute("SELECT coalesce(max(rank), 0) FROM update_queue WHERE day=?", (row["day"],)).fetchone()[0]
        c.execute("INSERT INTO update_queue(id,bot,kind,day,rank,state,tries) VALUES(?,?,?,?,?,'queued',0) "
                  "ON CONFLICT(bot,kind,day) DO UPDATE SET state='queued', rank=excluded.rank, tries=0, "
                  "message_id=NULL, sent_at=NULL, done_at=NULL, reason='asked again: the last one broke the update rules'",
                  (H.new_id(), row["bot"], row["kind"], row["day"], last + 1))
        H.event(c, H.KEEPER, "update.rejected", row["id"], {"bot": row["bot"]})
        removed.append(row["bot"])
    # The ones #635 deleted as "not given" (too long) are asked again the same way.
    for row in c.execute("SELECT * FROM update_queue WHERE state='missed' AND reason LIKE 'its update was too long%'").fetchall():
        last = c.execute("SELECT coalesce(max(rank), 0) FROM update_queue WHERE day=?", (row["day"],)).fetchone()[0]
        c.execute("UPDATE update_queue SET state='queued', rank=?, tries=0, message_id=NULL, sent_at=NULL, done_at=NULL, "
                  "reason='asked again: the last one broke the update rules' WHERE id=?", (last + 1, row["id"]))
        removed.append(row["bot"])
    return removed


def one(c, uid):
    row = c.execute("SELECT * FROM updates WHERE id=?", (uid,)).fetchone()
    return _decode(row) if row else None


def _is_archived_sql(update="u", override="ao"):
    """Logical-day supersession plus a person's bounded archive/restore choice.

    Existing history needs no content rewrite: any later day from the same bot and kind hides an
    older update. A manual archive always hides it; an explicit restore overrides that automatic
    state until another later-day post clears the restore override.
    """
    newer = (f"EXISTS (SELECT 1 FROM updates newer WHERE newer.bot={update}.bot "
             f"AND newer.kind={update}.kind AND newer.day>{update}.day)")
    return f"(coalesce({override}.state='archived',0) OR ({override}.state IS NULL AND {newer}))"


def archived(c, actor, uid):
    row = c.execute("SELECT " + _is_archived_sql() + " AS archived FROM updates u "
                    "LEFT JOIN update_archive_overrides ao ON ao.update_id=u.id AND ao.actor=? WHERE u.id=?",
                    (actor, uid)).fetchone()
    return bool(row and row["archived"])


def inbox_ids(c, actor, readable, created_after=None, meetings=False):
    """IDs in this person's default feed, for the existing Mark all read action. `meetings`: the reader
    is a meeting reader (`meeting_reader`); else updates flagged `meetings` are left out, as in the feed."""
    if not readable:
        return []
    clause, values = _only(sorted(readable))
    where = [clause, f"NOT {_is_archived_sql()}", _kept(meetings)]
    args = [actor, *values]
    if created_after:
        where.append("u.created_at>=?"); args.append(created_after)
    return [r["id"] for r in c.execute(
        "SELECT u.id FROM updates u LEFT JOIN update_archive_overrides ao ON ao.update_id=u.id AND ao.actor=? "
        "WHERE " + " AND ".join(where), args).fetchall()]


def thread(c, uid):
    """Replies to an update (messages carrying refs.update) and the bot's answers to them."""
    replies = [dict(r) for r in c.execute(
        "SELECT id, sender_id, recipient_id, body, created_at, conversation_id FROM messages "
        "WHERE json_extract(refs,'$.update')=? ORDER BY created_at", (uid,))]
    ids = [r["id"] for r in replies]
    answers = [dict(r) for r in c.execute(
        "SELECT id, sender_id, recipient_id, body, created_at, conversation_id, in_reply_to FROM messages WHERE in_reply_to IN ("
        + ",".join("?" * len(ids)) + ") ORDER BY created_at", ids)] if ids else []
    return sorted(replies + answers, key=lambda m: m["created_at"])


def _only(bots, column="u.bot"):
    """The SQL for "updates of these bots" (`bots` is the ids the caller may read), so pages and
    counts are cut in the query and a hidden bot's updates leave no gap or number behind."""
    return f"{column} IN ({','.join('?' * len(bots))})", list(bots)


def listing(c, actor, readable, kind=None, bot=None, unread=False, before=None, limit=40,
            archive=False, include_archive=False, may_read=None, meetings=False):
    """The feed, newest first, with this person's read state, reply counts, and the requests that
    ended without an update (so a silent bot shows). `readable` is the set of bots to show; `may_read`,
    all the authors they may read, decides a card's links, so My bots or direct reports narrow the
    cards but not what a card was written from. Without `meetings` (`meeting_reader`), an update
    flagged `meetings` is left out, and so is any link to one."""
    where, args = [_kept(meetings)], []
    if not readable:
        where.append("0")
    else:
        clause, values = _only(sorted(readable))
        where.append(clause)
        args += values
    if kind:
        where.append("u.kind=?"); args.append(kind)
    if bot:
        bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        where.append("u.bot=?"); args.append(bot)
    is_archived = _is_archived_sql()
    if unread:
        where.append("r.read_at IS NULL")
    if not include_archive:
        where.append(is_archived if archive else f"NOT {is_archived}")
    if before:
        where.append("u.created_at<?"); args.append(before)
    rows = c.execute(
        f"SELECT u.*, r.read_at, CASE WHEN {is_archived} THEN 1 ELSE 0 END AS archived "
        "FROM updates u LEFT JOIN update_reads r ON r.update_id=u.id AND r.actor=? "
        "LEFT JOIN update_archive_overrides ao ON ao.update_id=u.id AND ao.actor=? "
        "WHERE " + " AND ".join(where) + " ORDER BY u.created_at DESC LIMIT ?",
        (actor, actor, *args, limit)).fetchall()
    items = []
    for row in rows:
        item = with_inputs(c, _decode(row), readable if may_read is None else may_read, meetings)
        item["read"] = bool(item.pop("read_at"))
        item["archived"] = bool(row["archived"])
        item["replies"] = c.execute("SELECT count(*) FROM messages WHERE json_extract(refs,'$.update')=?",
                                    (row["id"],)).fetchone()[0]
        items.append(item)
    missed = []
    if not unread and not before and not archive and not include_archive:
        for row in c.execute("SELECT bot, kind, day, reason, done_at FROM update_queue WHERE state='missed' "
                             "AND day>=? ORDER BY done_at DESC", (H.shift(H.now(), days=-2)[:10],)).fetchall():
            late = c.execute("SELECT 1 FROM updates WHERE bot=? AND kind=? AND day=?",
                             (row["bot"], row["kind"], row["day"])).fetchone()
            if not late and row["bot"] in readable and (not kind or row["kind"] == kind) and (not bot or row["bot"] == bot):
                missed.append(dict(row))
    unread_count = count_unread(c, actor, readable, kind, meetings)
    queue = c.execute("SELECT state, count(*) n FROM update_queue WHERE day=? GROUP BY state", (today(),)).fetchall()
    return {"updates": items, "missed": missed, "unread": unread_count,
            "next_before": items[-1]["created_at"] if len(items) >= limit else None,
            "today": {r["state"]: r["n"] for r in queue}}


def count_unread(c, actor, readable, kind=None, meetings=False):
    """How many updates of the last two weeks this person has not read (the badge), counted as the feed
    shows them: without `meetings`, flagged updates are not counted."""
    if not readable:
        return 0
    clause, values = _only(sorted(readable))
    is_archived = _is_archived_sql()
    return c.execute("SELECT count(*) FROM updates u LEFT JOIN update_reads r ON r.update_id=u.id AND r.actor=? "
                     "LEFT JOIN update_archive_overrides ao ON ao.update_id=u.id AND ao.actor=? "
                     f"WHERE r.read_at IS NULL AND u.created_at>=? AND {clause} "
                     f"AND NOT {is_archived} AND {_kept(meetings)}"
                     + (" AND u.kind=?" if kind else ""),
                     (actor, actor, H.shift(H.now(), days=-14), *values, *([kind] if kind else []))).fetchone()[0]


def off_tree():
    """The built-in bots the Updates tree leaves out: the Assistant's helpers (BotOps, the Strategy Bot, the
    Librarian), the Task Manager and the Bookkeeper. Nobody talks to them and they are not on the org chart."""
    from . import assistant, bookkeeper, task_manager
    return frozenset((*assistant.HELPERS, task_manager.SLUG, bookkeeper.SLUG))


def tree(c, actor, readable, kind, day=None, scope=None, meetings=False):
    """One day's updates nested by the org chart, for the Updates page: each person and bot the
    caller may read, its update or none, and the ones below it. `scope` below starts at the caller
    (the default when someone reports to them), all at the top of the chart. Only direct reports
    are `direct`, the one level that alerts; `unread` counts just those. Archived updates are left out as
    in the Inbox, so an explicit past `day` shows only that day's cards a newer day has not replaced.
    Without `meetings` (`meeting_reader`), an update flagged `meetings` is left off its node, as in the feed.
    The built-ins off the org chart that work behind the Assistant (`OFF_TREE`) get no node, so no empty row; their
    reports hang from the next node up, and an update they did post stays in the feed (List, Archive)."""
    from . import people as P
    from . import report_order
    roster, entries, archived = _chart(c)
    view = P.org_view(roster, entries, archived, bare_people=True)
    me = ("p:" if H.actor_kind(actor) == "human" else "b:") + H.actor_id(actor)
    parent_of, nodes = {}, {}
    names = {r["id"]: r["display_name"] or r["slug"] for r in c.execute("SELECT id, slug, display_name FROM bots")}
    for g in view["org_groups"]:
        parent_of["g:" + g["id"]] = g["org_parent"]
    for p in view["people"]:
        parent_of["p:" + p["id"]] = p["org_parent"]
        if H.human_actor(p["id"]) in readable:
            nodes["p:" + p["id"]] = {"key": "p:" + p["id"], "author": H.human_actor(p["id"]), "name": p["name"] or p["id"]}
    hidden = off_tree()
    for b in view["bots"]:
        parent_of["b:" + b["id"]] = b["org_parent"]
        if b["id"] in readable and b["id"] not in hidden:
            nodes["b:" + b["id"]] = {"key": "b:" + b["id"], "author": b["id"], "name": names.get(b["id"]) or b["id"]}
    authors = [n["author"] for n in nodes.values()]
    marks = ",".join("?" * len(authors)) or "''"
    if not day:
        day = c.execute(f"SELECT max(day) FROM updates u WHERE kind=? AND bot IN ({marks}) AND {_kept(meetings)}",
                        (kind, *authors)).fetchone()[0] or today()
    # The same per-reader archive filter as the feed: a card this person archived stays off its node,
    # and the node keeps its reports below it.
    rows = {r["bot"]: r for r in c.execute(
        f"SELECT u.*, r.read_at FROM updates u LEFT JOIN update_reads r ON r.update_id=u.id AND r.actor=? "
        "LEFT JOIN update_archive_overrides ao ON ao.update_id=u.id AND ao.actor=? "
        f"WHERE u.kind=? AND u.day=? AND u.bot IN ({marks}) AND NOT {_is_archived_sql()} AND {_kept(meetings)}",
        (actor, actor, kind, day, *authors))}
    direct = set(direct_reports(c, author_of(actor))) & set(authors)
    children = {}
    for key, node in nodes.items():
        parent, seen = parent_of.get(key, ""), {key}
        while parent and parent not in nodes and parent not in seen:   # past groups and hidden bots
            seen.add(parent)
            parent = parent_of.get(parent, "")
        node["parent"] = parent if parent in nodes else ""
        children.setdefault(node["parent"], []).append(key)
    # A manager's reports in the team chart's order (backend/report_order.py rank).
    chart_rank = report_order.rank(c)
    full = lambda k: ("human:" if k[0] == "p" else "bot:") + k[2:]

    def rank(parent, k):
        return chart_rank(full(k), full(parent) if parent else "")

    def build(key, seen):
        node = nodes[key]
        row = rows.get(node["author"])
        update = None
        if row:
            update = with_inputs(c, _decode(row), readable, meetings)
            update["read"] = bool(update.pop("read_at"))
        kids = [build(k, seen | {k}) for k in sorted(children.get(key, []), key=lambda k: rank(key, k))
                if k not in seen]
        return {"key": key, "author": node["author"], "name": node["name"], "direct": node["author"] in direct,
                "update": update, "children": kids}
    if scope not in ("below", "all"):
        scope = "below" if children.get(me) else "all"
    tops = [me] if scope == "below" and me in nodes else sorted(children.get("", []), key=lambda k: rank("", k))
    return {"kind": kind, "day": day, "scope": scope, "me": me, "roots": [build(k, {k}) for k in tops],
            "unread": count_unread(c, actor, direct, kind, meetings)}


def author_of(actor):
    """The author key an actor posts as: `human:<id>` for a person, the id for a bot."""
    return actor if H.actor_kind(actor) == "human" else H.actor_id(actor)


def set_archived(c, actor, ids, archived=True):
    """Set this reader's archive choice. Restoring a globally superseded update creates a
    per-reader exception; restoring a current update simply removes the manual archive."""
    now = H.now()
    for uid in ids:
        if archived:
            c.execute("INSERT INTO update_archive_overrides(update_id,actor,state,updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(update_id,actor) DO UPDATE SET state='archived',updated_at=excluded.updated_at",
                      (uid, actor, "archived", now))
        else:
            newer = c.execute("SELECT 1 FROM updates u WHERE u.id=? AND EXISTS (SELECT 1 FROM updates n "
                              "WHERE n.bot=u.bot AND n.kind=u.kind AND n.day>u.day)", (uid,)).fetchone()
            if newer:
                c.execute("INSERT INTO update_archive_overrides(update_id,actor,state,updated_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(update_id,actor) DO UPDATE SET state='restored',updated_at=excluded.updated_at",
                          (uid, actor, "restored", now))
            else:
                c.execute("DELETE FROM update_archive_overrides WHERE update_id=? AND actor=?", (uid, actor))
    return len(ids)


def mark(c, actor, ids, read=True):
    now = H.now()
    for uid in ids:
        if read:
            c.execute("INSERT OR IGNORE INTO update_reads VALUES(?,?,?)", (uid, actor, now))
        else:
            c.execute("DELETE FROM update_reads WHERE update_id=? AND actor=?", (uid, actor))
    return len(ids)
