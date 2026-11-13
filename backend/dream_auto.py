"""Auto dream (docs/learnings.md, Dream): the Maintenance pass queues a dream for each bot most in need of one, on the
release policy for runs that can wait (the job hold in backend/wake_presets.py). Plain code end to end: no model call
decides who is in need. The dream is the task a person starts from the panel (backend/dream.py `start`), with the auto
variant of its instructions, a `dream.auto` event under the system actor, and the reason line at the top of its body.
Its wake carries `release` (Prefers warm, wait 48 hours, Skip at the deadline, Fork), so the hold starts it in a 45-55
minute warm window on a fork of the bot's latest session, or drops it at the deadline. The original session is left
alone.

A bot is in need when:
- it is active, keeps memory, has no dream job waiting (held and queued) and no open dream;
- its last dream of either kind started at least MIN_GAP_HOURS ago (a bot that never dreamed must be that old);
- its need since that dream's start (or SINCE_DAYS ago, whichever is later) is at least MIN_NEED. Need is work since the
  last dream: its turns (finished runs, the dreams' own left out), plus people's corrections weighed CORRECTION_WEIGHT
  each: a person's thumbs-down on its work, an org rule that applies to it added, changed or retired by a person, and a
  commit to its memory or instruction files made outside any run (a person's edit). Memory diffs no longer count: auto
  memory writes as the bot works, so their size says little about how much there is to tidy.

A queued dream whose job is skipped at the deadline (no warm window in 48 hours) has its task closed by the release
policy itself (wake_presets), so the bot can be in need again on a later pass.

The requester is the bot's nearest human manager (org_rule_check.represented), else the company owner. The dream lands
where that requester's work goes (shared_bots.route, as the dream route files it), and the gates are read on that bot.

`run` queues at most `limit` of them, most need first: the Maintenance run's dream step passes its "Most bots per run".
A bot that is refused or fails is held, and the pass goes on. Maintenance claims each run once, so a restart does not
queue the same occurrence twice; an open dream stops a second one of one bot anyway.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import dream
from . import maintenance_limits as L
from . import system_routines as SR
from .shared_bots import route
from .store import H, Problem
from .updates import ZONE

LOG = logging.getLogger("tico.dream_auto")

MIN_GAP_HOURS = 48  # between dreams of any kind; a bot's first must be this old
MAX_PER_RUN = 3  # the default "Most bots per run" (backend/maintenance_steps/dream.py)
MIN_NEED = 10  # turns, plus corrections weighed below, since the last dream
CORRECTION_WEIGHT = 5  # one person's correction counts as this many turns
WAIT_HOURS = 48  # how long a queued dream waits for a warm window before it is skipped
WAKE_CLASS = "dream"  # the held job's class, as wake_presets labels it from refs.release_kind
# AGENT.md: the instruction file's name in a repository the runner has not moved to AGENTS.md yet.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "AGENT.md")
# The task ids of every dream of one owner, as a subquery: the runs that worked on them are that owner's dream runs.
DREAM_IDS = (
    f"SELECT tasks.id FROM tasks JOIN task_tags tt ON tt.task_id=tasks.id JOIN tags g ON g.id=tt.tag_id "
    f"WHERE g.key=? AND tasks.owner_id=? AND {dream.MARKED}"
)


@dataclass(frozen=True)
class Verdict:
    bot: str
    name: str
    status: str  # "due", or why not: "inactive", "stateless", "waiting", "busy", "new", "paced", "quiet"
    turns: int = 0
    corrections: int = 0
    since: datetime | None = None

    @property
    def need(self):
        return self.turns + CORRECTION_WEIGHT * self.corrections


def stamp(at):
    """A timestamp as the tables store them (H.now's form), for comparing."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def counted(path):
    """Whether a person's commit to this file is a correction: memory/ and the instruction files."""
    return path in INSTRUCTION_FILES or path.startswith("memory/")


def release():
    """The release policy every auto dream's wake carries (spec: Dreaming, the first built-in user), in the shape the job
    hold reads from `refs.release`: Due is the job's creation (None), then Prefers warm for up to WAIT_HOURS, Skip at the
    deadline, on a fork of the bot's latest session. A server without the hold starts the dream at once, as before."""
    return {"due": None, "wait": WAIT_HOURS * 3600, "warm": True, "deadline": "skip", "session": "fork"}


def waiting(c, bot):
    """Whether the bot already has a dream job held and queued: a waiting bot is skipped, so it never blocks another."""
    sql = "SELECT 1 FROM jobs WHERE bot_id=? AND held=1 AND state='queued' AND wake_class=? LIMIT 1"
    return c.execute(sql, (bot, WAKE_CLASS)).fetchone() is not None


def _turns(c, bot, since, owner):
    """The bot's finished runs since `since`, its dreams' runs left out."""
    row = c.execute(
        "SELECT count(*) FROM attempts a WHERE a.bot_id=? AND a.created_at>? AND a.settled_at IS NOT NULL "
        f"AND (a.task_id IS NULL OR a.task_id NOT IN ({DREAM_IDS}))",
        (bot, since, dream.LABEL, owner),
    ).fetchone()
    return row[0]


def _rule_changes(c, bot, since):
    """Org rules that apply to the bot, added, changed or retired by a person since `since`."""
    from . import org_rules

    rows = c.execute(
        "SELECT * FROM org_rules WHERE updated_at>? AND status IN ('active','retired') "
        "AND (coalesce(decided_by, suggested_by) LIKE 'human:%')",
        (since,),
    ).fetchall()
    if not rows:
        return 0
    return len(org_rules.applies_to(c, bot, [org_rules._row(r) for r in rows]))


def _person_commits(c, bot, since):
    """Commits to the bot's memory or instruction files made outside any run: a person's edits."""
    count = 0
    for row in c.execute(
        "SELECT files FROM bot_memory_updates WHERE bot=? AND shared=1 AND attempt_id='' AND committed_at>?",
        (bot, since),
    ):
        if any(counted(path) for path in json.loads(row["files"])):
            count += 1
    return count


def work(c, bot, since, owner=None):
    """(turns, corrections) of the bot since `since` (a stamp): its work since the last dream."""
    owner = owner or "bot:" + bot
    downs = c.execute(
        "SELECT count(*) FROM feedbacks WHERE bot_id=? AND rating='down' AND updated_at>?", (bot, since)
    ).fetchone()[0]
    corrections = downs + _rule_changes(c, bot, since) + _person_commits(c, bot, since)
    return _turns(c, bot, since, owner), corrections


def verdict(c, bot, at, owner=None):
    """Whether `bot` is in need of an auto dream at `at`, with the numbers behind it. `owner` is the bot the dream would
    land on (run resolves it with shared_bots.route); its busy and pacing checks read that owner's dreams. Default: the
    bot."""
    row = H.bot(c, bot)
    name = (row or {}).get("display_name") or bot
    if not row or row["state"] != "active":
        return Verdict(bot, name, "inactive")
    if dream.stateless(c, bot):
        return Verdict(bot, name, "stateless")
    owner = owner or "bot:" + bot
    if waiting(c, H.actor_id(owner)):
        return Verdict(bot, name, "waiting")
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
    turns, corrections = work(c, bot, stamp(since), owner)
    found = Verdict(bot, name, "quiet", turns, corrections, since)
    return found if found.need < MIN_NEED else Verdict(bot, name, "due", turns, corrections, since)


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
    said = f"Auto dream: {SR.count_words(v.turns, 'turn')}"
    if v.corrections:
        said += f" and {SR.count_words(v.corrections, 'correction')} from people"
    return f"{said} since {v.since.astimezone(ZONE):%Y-%m-%d}"


def _words(queued, held, over, not_due, limit):
    if not (queued or held or over):
        return "Nothing due"
    parts = []
    if queued:
        parts.append(f"Queued {SR.count_words(len(queued), 'dream')} ({', '.join(queued)})")
    if held:
        parts.append(f"{len(held)} not queued")
    if over:
        parts.append(f"{over} due past the {limit} this run")
    parts.append(f"{not_due} not due")
    return "; ".join(parts)


def run(store, auth, at, limit=MAX_PER_RUN, report=None):
    """The bots in need, most need first, up to `limit` queued in this pass. Each is one write transaction that resolves
    where the dream lands, judges that bot again and files it with its wake held on the release policy. A refusal or a
    failure holds that bot only. Returns the names queued; `report`, when given, gets the counts and the summary words."""
    create = dream.creator(store)
    if create is None:
        raise Problem("conflict", "The dream's task path is not wired on this server", 409)
    with store.read() as c:
        judged = [
            verdict(c, row["id"], at) for row in c.execute("SELECT id FROM bots WHERE state='active' ORDER BY id")
        ]
    due = sorted((v for v in judged if v.status == "due"), key=lambda v: (-v.need, v.bot))
    queued, held, limited, over = [], [], [], 0
    wake = {"release": release(), "release_kind": WAKE_CLASS}
    for v in due:
        if len(queued) >= limit:
            over += 1
            continue
        try:
            with store.transaction() as c:
                actor = requester(c, v.bot, auth)
                if not actor:
                    raise Problem("requester", "No person to start it for")
                # Where the requester's dream lands (shared_bots.route, as the dream route files it): the checks run there.
                landing = route(c, actor, "bot:" + v.bot) or "bot:" + v.bot
                again = verdict(c, v.bot, at, owner=landing)
                if again.status != "due":
                    continue
                if landing != "bot:" + v.bot:  # a branch takes it: this bot's own pass does not queue it
                    held.append(v.name)
                    continue
                if not L.allow(c, v.bot):  # the Maintenance wake limits (maintenance_limits.py)
                    held.append(v.name)
                    limited.append(v.name)
                    continue
                who = auth.identity_for_actor(c, actor)
                row, _ = dream.start(c, who, landing, create, reason=reason(again), wake_refs=wake)
                if row is None:
                    held.append(v.name)
                    continue
                L.record(c, v.bot)
                queued.append(v.name)
        except (Problem, H.Refused):
            held.append(v.name)
        except Exception:
            LOG.exception("auto dream for %s failed; the pass goes on", v.bot)
            held.append(v.name)
    if report is not None:
        report.update(
            reason=_words(queued, held, over, len(judged) - len(due), limit),
            judged=len(judged),
            due=len(due),
            started=queued,
            held=held,
            limited=limited,
            over=over,
        )
    return queued
