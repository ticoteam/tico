"""Auto dream (docs/learnings.md, Dream): once a night, a dream for each bot whose memory changed a lot since its last
dream. Plain code end to end: no model call decides who is due. The dream is the task a person starts from the panel
(backend/dream.py `start`), with the auto variant of its instructions, a `dream.auto` event under the system actor, and
the reason line at the top of its body.

A bot is due when, read from `bot_memory_updates` (the commits its computer reported):
- it is active, keeps memory, and has no open dream;
- its last dream of either kind started at least MIN_GAP_HOURS ago (a bot that never dreamed must be that old);
- its memory since that dream's start (or LOOKBACK_DAYS ago, whichever is later) changed at least MIN_LINES lines over
  at least MIN_TURNS memory turns. Counted: memory/ except memory/state.md, and AGENT.md, AGENTS.md and CLAUDE.md. Not
  counted: the commits of dreams and of memory tidy passes, which memory_tidy_runs.changed leaves out the same way.

The requester is the bot's nearest human manager (org_rule_check.represented), else the company owner. The dream lands
where that requester's work goes (shared_bots.route, as the dream route files it), and the gates are read on that bot.

At most NIGHTLY_MAX start a company night, most changed lines first, counted again inside each start's write
transaction so overlapping runs cannot pass the cap. A bot that is refused or fails is held and the night goes on.

The scheduled run claims its night once (a registry_metadata key per night, read for the switch again in the same
transaction), so a restart or a second process does not run it twice. The pacing gate stops a second start of the same
bot anyway: a started dream is the bot's last one.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import dream
from . import system_routines as SR
from .shared_bots import route
from .store import H, Problem
from .updates import ZONE

LOG = logging.getLogger("tico.dream_auto")

KEY = "auto_dream"
MIN_GAP_HOURS = 48          # between dreams of any kind; a bot's first must be this old
NIGHTLY_MAX = 3             # auto dreams a company night, Run now's included
MIN_LINES = 200             # changed lines of counted memory since the last dream
MIN_TURNS = 10              # memory turns (commits that changed a counted file) since the last dream
CLAIM = "auto_dream:night:"  # the registry_metadata key that claims one company night
INSTRUCTION_FILES = ("AGENT.md", "AGENTS.md", "CLAUDE.md")
STATE = "memory/state.md"   # rewritten every turn: churn, not memory
DREAM_SUBJECT = "Dream:"    # a dream's own commits
TIDY_SUBJECT = "Dreamed "   # a memory tidy pass's commits (memory_tidy_runs.changed)
# The task ids of every dream of one owner, as a subquery: the runs that worked on them are that owner's dream runs.
DREAM_IDS = (f"SELECT tasks.id FROM tasks JOIN task_tags tt ON tt.task_id=tasks.id JOIN tags g ON g.id=tt.tag_id "
             f"WHERE g.key=? AND tasks.owner_id=? AND {dream.MARKED}")


@dataclass(frozen=True)
class Verdict:
    bot: str
    name: str
    status: str             # "due", or why not: "inactive", "stateless", "busy", "new", "paced", "quiet"
    lines: int = 0
    turns: int = 0
    since: datetime | None = None


def night_of(at):
    """The company night a run at `at` belongs to: its local date (updates.ZONE)."""
    return at.astimezone(ZONE).date().isoformat()


def stamp(at):
    """A timestamp as bot_memory_updates stores committed_at, for comparing."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def counted(path):
    """Whether a changed file counts: memory/, but not memory/state.md, and the instruction files."""
    return path in INSTRUCTION_FILES or (path.startswith("memory/") and path != STATE)


def _name(rest, prefix):
    """A path from the rest of a `---` or `+++` header line: the `a/` or `b/` prefix off, git's tab after a name with a
    space off, and None for /dev/null (an added or deleted side)."""
    name = rest.rstrip("\t")
    if name == "/dev/null":
        return None
    return name[len(prefix):] if name.startswith(prefix) else name


def changed_lines(diff):
    """{path: changed lines} of one commit's diff: the `+` and `-` lines inside each file's hunks. The path is read from
    the `+++ b/` header, the rest of its line, so a name with spaces reads whole; a deletion has `+++ /dev/null`, so its
    path comes from the `--- a/` header. The headers before a file's first hunk are not counted."""
    counts, path, old, in_hunk = {}, None, None, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, old, in_hunk = None, None, False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk:
            if path and line[:1] in ("+", "-"):
                counts[path] = counts.get(path, 0) + 1
        elif line.startswith("--- "):
            old = _name(line[4:], "a/")
        elif line.startswith("+++ "):
            path = _name(line[4:], "b/") or old
    return counts


def _own_runs(c, owner):
    """The runs that worked on any dream of this owner, in one query: a run started by a message of one of its dreams, or
    handed one as an input (learnings.run_attempts, for every dream at once). Their commits are the dream's, not memory.
    A run is its attempts row, so its id is the attempt's id."""
    params = (dream.LABEL, owner)
    return {row[0] for row in c.execute(
        "SELECT a.id FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
        f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL} IN ({DREAM_IDS}) UNION "
        "SELECT i.attempt_id FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
        f"LEFT JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL} IN ({DREAM_IDS})",
        params + params)}


def _tidy_runs(c, bot):
    """The runs of this bot's memory tidy passes. memory_tidy_runs.changed leaves them out the same way."""
    return {row[0] for row in c.execute(
        "SELECT t.id FROM attempts t JOIN memory_tidy_passes p ON t.task_id=p.parent_task_id OR t.task_id=p.check_task_id "
        "WHERE (t.job_id IS NULL OR t.trigger IS NOT NULL) AND p.bot=?", (bot,))}


def change(c, bot, since, owner=None):
    """(changed lines, memory turns) of the bot's counted memory committed after `since` (a stamp). With no commit since,
    nothing else is looked up: the dream and tidy runs are only needed to tell which commits count."""
    rows = c.execute("SELECT subject,files,diff,attempt_id FROM bot_memory_updates "
                     "WHERE bot=? AND shared=1 AND committed_at>?", (bot, since)).fetchall()
    if not rows:
        return 0, 0
    skip = _own_runs(c, owner or "bot:" + bot) | _tidy_runs(c, bot)
    lines = turns = 0
    for row in rows:
        if row["subject"].startswith((DREAM_SUBJECT, TIDY_SUBJECT)) or row["attempt_id"] in skip:
            continue
        if not any(counted(path) for path in json.loads(row["files"])):
            continue
        turns += 1
        lines += sum(n for path, n in changed_lines(row["diff"]).items() if counted(path))
    return lines, turns


def verdict(c, bot, at, owner=None):
    """Whether `bot` is due an auto dream at `at`, with the numbers behind it. `owner` is the bot the dream would land on
    (run resolves it with shared_bots.route); its busy and pacing checks read that owner's dreams. Default: the bot."""
    row = H.bot(c, bot)
    name = (row or {}).get("display_name") or bot
    if not row or row["state"] != "active":
        return Verdict(bot, name, "inactive")
    if dream.stateless(c, bot):
        return Verdict(bot, name, "stateless")
    owner = owner or "bot:" + bot
    if dream._open(c, [owner]):
        return Verdict(bot, name, "busy")
    dreams = dream.dreams(c, owner)
    gap = timedelta(hours=MIN_GAP_HOURS)
    last = H.parse_ts(dreams[0]["created_at"]) if dreams else None
    if last is None:
        created = H.parse_ts(row["created_at"])
        if created is None or at - created < gap:
            return Verdict(bot, name, "new")
    elif at - last < gap:
        return Verdict(bot, name, "paced")
    floor = at - timedelta(days=dream.SINCE_DAYS)
    since = max(last or floor, floor)
    lines, turns = change(c, bot, stamp(since), owner)
    status = "due" if lines >= MIN_LINES and turns >= MIN_TURNS else "quiet"
    return Verdict(bot, name, status, lines, turns, since)


def requester(c, bot, auth):
    """The bot's nearest human manager (the first person above it, when on the roster and not left), else the company
    owner. None when there is no owner either."""
    from .org_rule_check import represented
    actor = represented(c, "bot:" + bot)
    if actor:
        return actor
    pid = auth.owner_id(c)
    return H.human_actor(pid) if pid else None


def reason(v):
    """The line at the top of an auto dream's body, and the reason its event keeps."""
    return f"Auto dream: {v.lines} memory lines changed in {v.turns} turns since {v.since.astimezone(ZONE):%Y-%m-%d}"


def claimed(c, night):
    """Whether this company night's scheduled run has claimed it."""
    return bool(c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (CLAIM + night,)).fetchone())


def _claim(c, night):
    """Claim the night for the scheduled run. False when it was already claimed (a restart, or another process)."""
    return c.execute("INSERT INTO registry_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                     (CLAIM + night, H.now())).rowcount == 1


def _started_tonight(c, at):
    """The auto dreams started since midnight company time: they count toward the night's NIGHTLY_MAX."""
    midnight = at.astimezone(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    return c.execute("SELECT count(*) FROM events WHERE actor=? AND action=? AND created_at>=?",
                     (H.KEEPER, dream.AUTO_MARK, stamp(midnight))).fetchone()[0]


def _words(started, held, over, not_due):
    if not (started or held or over):
        return "Nothing due"
    parts = []
    if started:
        parts.append(f"Started {SR.count_words(len(started), 'dream')} ({', '.join(started)})")
    if held:
        parts.append(f"{len(held)} not started")
    if over:
        parts.append(f"{over} due past the {NIGHTLY_MAX} a night")
    parts.append(f"{not_due} not due")
    return "; ".join(parts)


def run(store, auth, at, failures=None):
    """The night's auto dreams: the due bots, most changed lines first, up to NIGHTLY_MAX counting every auto dream started
    tonight, however many runs started them. Each start is one write transaction that counts the cap, resolves where the
    dream lands, judges that bot again and files it. A refusal or a failure holds that bot only. Returns the names started."""
    create = dream.creator(store)
    if create is None:
        raise Problem("conflict", "The dream's task path is not wired on this server", 409)
    with SR.recording(store, KEY, failures) as record:
        with store.read() as c:
            judged = [verdict(c, row["id"], at) for row in c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")]
        due = sorted((v for v in judged if v.status == "due"), key=lambda v: (-v.lines, v.bot))
        started, held, over = [], [], 0
        for v in due:
            try:
                with store.transaction() as c:
                    if _started_tonight(c, at) >= NIGHTLY_MAX:
                        over += 1
                        continue
                    actor = requester(c, v.bot, auth)
                    if not actor:
                        raise Problem("requester", "No person to start it for")
                    # Where the requester's dream lands (shared_bots.route, as the dream route files it): the checks run there.
                    landing = route(c, actor, "bot:" + v.bot) or "bot:" + v.bot
                    again = verdict(c, v.bot, at, owner=landing)
                    if again.status != "due":
                        continue
                    if landing != "bot:" + v.bot:      # a branch takes it: this bot's own night does not start it
                        held.append(v.name)
                        continue
                    row, _ = dream.start(c, auth.identity_for_actor(c, actor), landing, create, reason=reason(again))
                    if row is None:
                        held.append(v.name)
                        continue
                    started.append(v.name)
            except (Problem, H.Refused):
                held.append(v.name)
            except Exception:
                LOG.exception("auto dream for %s failed; the night goes on", v.bot)
                held.append(v.name)
        record.did(_words(started, held, over, len(judged) - len(due)), quiet=not (started or held or over))
    return started


def nightly(store, auth, at):
    """From the scheduler's tick: once a night, at or after the time on Settings > Routines (02:00 company time by
    default), while the job is on. Off in rehearsal and test servers, as the learning run is."""
    settings = store.settings
    if settings.rehearsal or settings.test_identities:
        return None
    # A store with no app around it (a bare Scheduler, a script) has no task path to file a dream through: no night.
    if dream.creator(store) is None:
        return None
    night = night_of(at)
    # The tick comes every few seconds: once this process has seen the night claimed, it never asks again.
    if store.__dict__.get("auto_dream_night") == night:
        return None
    with store.read() as c:
        cfg = SR.config_of(c, KEY)
    if not cfg["on"]:
        return None
    hour, minute = (int(x) for x in cfg["at"].split(":"))
    local = at.astimezone(ZONE)
    if (local.hour, local.minute) < (hour, minute):
        return None
    with store.transaction() as c:
        # Read again inside the claim: a switch made since the check above stops the night before it is claimed.
        if not SR.config_of(c, KEY)["on"]:
            return None
        won = _claim(c, night)
    store.__dict__["auto_dream_night"] = night
    if not won:
        return None
    return run(store, auth, at)


def run_now(store, auth):
    """Run now (Settings > Routines): the same selection and cap, at once. The clock is the only thing it ignores, and it
    claims no night, so the night's own run still happens. A rehearsal server starts no dreams."""
    if store.settings.rehearsal:
        raise Problem("conflict", "A rehearsal server starts no auto dreams", 409)
    run(store, auth, datetime.now(timezone.utc), failures=[])
