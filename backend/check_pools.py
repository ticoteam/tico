"""Check pools: the bots that may do a check step (a step whose flow names its `check` kind: Review 1, Review 2, In QA
and Post Release QA on the Dev board), set on the step in Settings > Tasks, and how Tico gives a ticket's check to
one of them (Dev 534 A).

When a ticket enters a step with a pool and has no open check there for its pull request head, Tico files the check
(`Review 1: PR #n`, the head recorded as any check's is: review_gate.remember_heads) and gives it to the least busy
eligible member: other model company first, then fewest open checks, then longest idle. Eligible means active and
online, not paused or held, under the step's limit of open checks, and on a later review step not the bot of an
earlier one (Review 2 is never Review 1's bot). For a review or a QA check, model company is a preference by default: a
bot of another model company than the builder's goes first, and a bot of the builder's company takes the check when no
other is free, rather than the check waiting. A step whose `other_company` is `require` gives its check only to another
company's bot: a bot of the builder's company is never free for it, so the check waits when every free bot runs on that
company, and such a bot that frees up does not take it. The check is filed as the ticket's owner would file it, so
the gate, the one-check-per-head rule, the reviewer's wake and the verdict's way back to the builder are the ones a
builder's own request gets.

With no pool member free, the check waits on the ticket ("Waiting for a reviewer": a `check_waits` row) and the first
pool member to finish a check takes it (`freed`). An empty pool waits the same way. A wait ends when its check is filed,
or the ticket leaves the step or finishes. A pool member only ever takes checks this way, never other work.
"""
import json
import logging
import re

from . import hubdb as H

log = logging.getLogger("tico.check_pools")

DEFAULT_LIMIT = 2
MAX_LIMIT = 20
ONLINE_MINUTES = 5          # a computer that has not been seen for this long is offline
HELD = ("paused", "quarantined", "limited", "crashed")     # bot_status states that take no new check
# The pools Head of Engineering proposed (spec dev-ticket-flow.md, question 3), given once to each Dev tickets type,
# with only the bots the company has. A person changes them on the step.
SEED = {
    "Review 1": ("pr-reviewer-sol", "reviewer-2", "reviewer-xai", "reviewer-muse"),
    "Review 2": ("pr-reviewer-sol", "reviewer-2", "reviewer-xai", "reviewer-muse"),
    "In QA": ("qa-engineer", "qa-claude", "qa-xai", "qa-muse"),
    "Post Release QA": ("qa-engineer-copy",),
}
SEEDED = "check_pools.seeded"
# A Security check is asked for by title; it is no step of a board.
SECURITY = re.compile(r"^Security(?:\b|:)", re.I)


def is_security(title):
    return bool(SECURITY.match(str(title or "").strip()))


def is_check_title(c, ticket, title):
    """Any check request under a ticket, whichever step filed it: what counts toward a checker's limit."""
    from . import step_flow
    return is_security(title) or step_flow.is_kind(c, ticket, str(title or "").strip())

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_waits(
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, step_id TEXT NOT NULL, title TEXT NOT NULL,
  heads TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'waiting',
  child_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempt_id TEXT);
CREATE INDEX IF NOT EXISTS check_waits_open ON check_waits(state, created_at);
CREATE INDEX IF NOT EXISTS check_waits_ticket ON check_waits(ticket_id, state);
"""


def ensure(c):
    """Unversioned and idempotent: the step's pool and limit, and the waits table."""
    # executescript() commits the outer request savepoint when a runner completion routes a check.
    for statement in SCHEMA.split(";"):
        if statement.strip():
            c.execute(statement)
    H.add_column(c, "task_steps", "check_pool", "TEXT")
    H.add_column(c, "task_steps", "check_limit", "INTEGER")
    H.add_column(c, "check_waits", "attempt_id", "TEXT")


def capture_attempt(c, turn_id):
    """Freeze the actual check attempt and its ticket-head provenance after this run's usage is recorded."""
    turn = c.execute("SELECT task_id FROM turns WHERE id=?", (turn_id,)).fetchone()
    check = H.task(c, turn["task_id"]) if turn and turn["task_id"] else None
    from . import step_flow
    from . import task_relations as TR
    ticket_id = TR.parent_of(c, check["id"]) if check else None
    if not check or not ticket_id or not (is_check_title(c, H.task(c, ticket_id), check.get("title"))
                                          or step_flow.check_step(c, check)):
        return None
    finished_owner = check.get("owner")
    head = str(check.get("verdict_head") or "").lower()
    if not head:
        event = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head' "
                          "ORDER BY ts DESC,rowid DESC LIMIT 1", (check["id"],)).fetchone()
        head = str(event[0] if event else "").split(",")[0].lower()
    from . import check_independence
    result = check_independence.capture_attempt(c, ticket_id, check["id"], head, turn_id)
    verdict = str(check.get("verdict") or "").lower()
    decided = c.execute("SELECT ts FROM task_events WHERE task_id=? AND field='verdict' "
                        "ORDER BY ts DESC,rowid DESC LIMIT 1", (check["id"],)).fetchone()
    started = c.execute("SELECT started_at FROM turns WHERE id=?", (turn_id,)).fetchone()
    from . import verdict_moves
    if verdict and decided and started and decided["ts"] >= started["started_at"]:
        verdict_moves.after_capture(c, check["id"], head, turn_id)
    elif not verdict and check.get("status") == "done" and step_flow.check_step(c, check):
        # Finished with no verdict: its comments are the feedback, and the ticket goes to the step's on_fail.
        if verdict_moves.after_verdict(c, check):
            verdict_moves.after_capture(c, check["id"], head, turn_id)
    current = H.task(c, check["id"])
    if current and current.get("status") in H.ACTIVE_STATUSES and current.get("owner") == finished_owner:
        return result
    done = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='check_pool_freed' AND new=? LIMIT 1",
                     (check["id"], turn_id)).fetchone()
    if not done:
        H._task_event(c, check["id"], H.KEEPER, "check_pool_freed", None, turn_id, "")
        freed(c, {**check, "owner": finished_owner})
    return result


def _ready(c):
    return "check_pool" in {r[1] for r in c.execute("PRAGMA table_info(task_steps)")}


# ----------------------------------------------------------------------------------------------- the setting
def members(raw):
    """A step's pool as stored: a JSON list of bot slugs, or None for a step that is no check (no pool, or an empty one)."""
    if raw in (None, ""):
        return None
    value = H._json(raw, None) if isinstance(raw, str) else raw
    return [str(s) for s in value] or None if isinstance(value, list) else None


def view(step):
    """A step as the API shows it: its pool as a list (empty for none) and its limit."""
    pool = members(step.get("check_pool"))
    return {**step, "check_pool": pool or [], "check_limit": int(step.get("check_limit") or DEFAULT_LIMIT)}


def clean(c, actor, raw, existing):
    """(check_pool, check_limit) to store for one step of a type being saved: what was sent, else what it had."""
    pool = raw.get("check_pool")
    limit = raw.get("check_limit")
    if pool is None:
        stored = members((existing or {}).get("check_pool"))
        pool = json.dumps(stored) if stored is not None else None
    else:
        slugs = []
        for value in pool:
            slug = H.actor_id(str(value or "").strip())
            if not slug or slug in slugs:
                continue
            if not H.bot(c, slug):
                H.refuse(c, actor, "not-found", f"{slug} is not a bot, so it cannot be in a step's check pool")
            slugs.append(slug)
        pool = json.dumps(slugs) if slugs else None
    if limit is None:
        limit = (existing or {}).get("check_limit")
    elif not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        H.refuse(c, actor, "kind", f"A step's limit of open checks is a whole number from 1 to {MAX_LIMIT}")
    return pool, limit


def seed(c):
    """Give each Dev tickets type the proposed pools once, with the bots the company has; a step that already has one
    keeps it. A type is marked only once at least one of those bots exists, so a new team gets its pools when it has
    its reviewers."""
    from .roadmap_flow import _dev
    if not _ready(c):
        return
    for ident, name in c.execute("SELECT id,name FROM task_types").fetchall():
        if not _dev((ident, name)) or c.execute("SELECT 1 FROM events WHERE action=? AND target=? LIMIT 1",
                                                 (SEEDED, ident)).fetchone():
            continue
        given = {}
        for step, slugs in SEED.items():
            have = [s for s in slugs if c.execute("SELECT 1 FROM bots WHERE slug=? AND coalesce(state,'')<>'archived'",
                                                  (s,)).fetchone()]
            if have and c.execute("UPDATE task_steps SET check_pool=?,check_limit=coalesce(check_limit,?) "
                                  "WHERE type_id=? AND name=? AND check_pool IS NULL",
                                  (json.dumps(have), DEFAULT_LIMIT, ident, step)).rowcount:
                given[step] = have
        if any(c.execute("SELECT 1 FROM bots WHERE slug=?", (s,)).fetchone() for v in SEED.values() for s in v):
            H.event(c, H.KEEPER, SEEDED, ident, {"pools": given})


def pool(c, step_id):
    """(step row, members, limit) for a step with a pool, else None."""
    if not step_id or not _ready(c):
        return None
    step = c.execute("SELECT * FROM task_steps WHERE id=?", (step_id,)).fetchone()
    found = members(step["check_pool"]) if step else None
    if found is None:
        return None
    return dict(step), found, int(step["check_limit"] or DEFAULT_LIMIT)


def kind(step):
    """The title a check step's requests start with (backend/step_flow.label)."""
    from . import step_flow
    return step_flow.label(step)


# ----------------------------------------------------------------------------------------------- who may take it
def open_checks(c, slug):
    """The check requests this bot holds open: subtasks titled as a check, on any ticket."""
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows = c.execute(f"SELECT t.title, p.type_id FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
                     f"JOIN tasks p ON p.id=r.to_task "
                     f"WHERE t.owner=? AND t.status IN ({marks})", ("bot:" + slug, *H.ACTIVE_STATUSES)).fetchall()
    return sum(1 for title, type_id in rows if is_check_title(c, {"type_id": type_id}, title))


def online(c, slug):
    """Whether the bot can start a check now: active, not held, and on a computer seen in the last few minutes."""
    row = c.execute("SELECT b.state, s.state AS status FROM bots b LEFT JOIN bot_status s ON s.bot=b.slug "
                    "WHERE b.slug=?", (slug,)).fetchone()
    if not row or row["state"] != "active" or row["status"] in HELD:
        return False
    seen = c.execute("SELECT r.last_seen_at FROM assignments a JOIN runners r ON r.id=a.runner_id "
                     "WHERE a.bot=? AND r.revoked_at IS NULL", (slug,)).fetchone()
    return bool(seen and seen[0] and seen[0] >= H.shift(H.now(), minutes=-ONLINE_MINUTES))


def _task_model(c, slug):
    """(config, task model or None, whether it runs now): the bot's config, and the task model a task filed to it
    would run on. A check is a task the bot owns, so it runs on the bot's task model whenever the bot gives its tasks
    their own sessions, unless that model cannot run on the bot's computer now (retired, not installed or signed in,
    held at a usage limit): then on the bot's own model, as backend/task_models.py `pick` falls back."""
    from clients import sessions
    from . import task_models
    from .shared_bots import follow
    row = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
    config = follow(c, slug, H._json(row["config"], {}) or {}) if row else {}
    task = task_models.of(config) if sessions.session_key(config, slug, "check", None).startswith("task:") else None
    if not task:
        return config, None, False
    runner = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (slug,)).fetchone()
    return config, task, not task_models.why_not(c, None, slug, task, runner[0] if runner else None)


def _company(model, runtime):
    """The named model's maker from Tico's catalog, or with no model named, the runtime's; '' for a model the catalog
    does not list (a harness can run another maker's model) or one a reseller picks (Cursor auto, OpenRouter), as
    usage_records.family reads a run."""
    from . import providers
    from .usage_records import RESELLERS
    found = (providers.MODEL_BY_ID.get(model) or {}).get("provider") if model else providers.runtime_provider(runtime)
    return "" if not found or found in RESELLERS else found


def maker(c, slug):
    """The model company a task filed to the bot runs on (`anthropic`, `openai`, ...): its task model's when it has
    one that can run, else its own model's; '' when its config names none Tico knows."""
    from . import providers
    config, task, runs = _task_model(c, slug)
    if runs:
        return _company(task["model"], task["runtime"])
    runtime, model = providers.bot_choice(c, None, config)
    return _company(model or config.get("model") or "", runtime or config.get("runtime") or "")


RECENT_TURNS = 3
TURNS_READ = 20


def _recent_turns(c, slug, task, runs):
    """The bot's last few finished turns that ran where a check filed to it would: none on a task with a model of its
    own or a check model, which ran on that. With a task model that runs, only its turns on tasks; with one that
    cannot run now, all of them (`makers` sets aside what ran on that task model)."""
    from . import task_run_models
    where = " AND task_id IS NOT NULL" if task and runs else ""
    rows = c.execute("SELECT id,task_id FROM turns WHERE bot=? AND finished_at IS NOT NULL" + where
                     + " ORDER BY finished_at DESC LIMIT ?", (slug, TURNS_READ)).fetchall()
    return [r[0] for r in rows
            if not (r[1] and (task_run_models.of(c, r[1]) or H.check_model(c, r[1])))][:RECENT_TURNS]


def makers(c, slug, known_only=False, whole=False):
    """Every model company a checker may run on: the one a task filed to it runs on (`maker`), and what its last few
    such runs actually ran on (a bot whose model is not signed in runs on its fallback, which is the company that would
    do the check). A run with an unknown segment makes the answer empty, or with `known_only` adds nothing, leaving
    what is known. With `whole`, a model of a company Tico cannot name makes it empty too."""
    from .usage_records import ran_on
    mine = maker(c, slug)
    if whole and not mine:
        return set()
    _, task, runs = _task_model(c, slug)
    recent = ran_on(c, _recent_turns(c, slug, task, runs)).values()
    # A task model that cannot run now won't do the check, so what ran on it says nothing; what fell back from it does.
    idle = task["model"] if task and not runs else None
    found = set()
    for run in recent:
        segments = [segment for segment in run["segments"] if not (idle and segment.get("model") == idle)]
        if run["segments"] and not segments:
            continue
        if not known_only and (not segments or any(not segment.get("family") for segment in segments)):
            return set()
        found.update(segment["family"] for segment in segments if segment.get("family"))
    return (found | {mine}) - {""}


def builder_makers(c, ticket, whole=False):
    """The model companies the ticket's builder builds on: what its runs on the ticket actually ran on. Its configured
    model's company stands in only while none of those runs is known, since a run that fell back ran on another one.
    With `whole`, a run on the ticket with a part of unknown company makes the answer empty: that part may be any."""
    from .check_independence import _executed
    built, known = _executed(c, ticket["id"])
    if whole and not known and c.execute("SELECT 1 FROM turns WHERE task_id=?", (ticket["id"],)).fetchone():
        return set()
    if built:
        return set(built)
    owner = ticket.get("owner") or ""
    return ({maker(c, H.actor_id(owner))} if H.is_bot(owner) else set()) - {""}


def candidate_details(c, slugs):
    """Configured model and latest executed model for a small pool shown in Settings > Tasks."""
    from . import providers
    from .shared_bots import follow
    from .usage_records import ran_on
    out = []
    for slug in dict.fromkeys(str(s) for s in slugs):
        bot = c.execute("SELECT display_name FROM bots WHERE slug=? AND coalesce(state,'')<>'archived'", (slug,)).fetchone()
        if not bot:
            continue
        row = c.execute("SELECT config FROM bot_config WHERE bot=?", (slug,)).fetchone()
        config = follow(c, slug, H._json(row["config"], {}) or {}) if row else {}
        runtime, model = providers.bot_choice(c, None, config)
        configured = providers.MODEL_BY_ID.get(model or "") or {}
        configured_provider = configured.get("provider") or providers.author_provider(runtime)
        turn = c.execute("SELECT id FROM turns WHERE bot=? AND finished_at IS NOT NULL ORDER BY finished_at DESC,id DESC LIMIT 1",
                         (slug,)).fetchone()
        actual = None
        if turn:
            run = ran_on(c, [turn["id"]])[turn["id"]]
            segments = run["segments"]
            makers = {part["family"] for part in segments if part.get("family")}
            models = list(dict.fromkeys(part.get("model") for part in segments if part.get("model")))
            family = next(iter(makers)) if len(makers) == 1 else None
            if family != configured_provider or not family:
                labels = [providers.MODEL_BY_ID.get(mid, {}).get("label") or mid for mid in models]
                actual = {"model": ", ".join(labels) if labels else "Unknown model",
                          "provider": providers.PROVIDER_BY_ID.get(family, {}).get("label") if family else "Unknown provider",
                          "family": family}
        out.append({"slug": slug, "name": bot["display_name"],
                    "configured": {"model": configured.get("label") or model or "Unknown model",
                                   "provider": providers.PROVIDER_BY_ID.get(configured_provider, {}).get("label")
                                   if configured_provider else "Unknown provider",
                                   "family": configured_provider},
                    "runtime": actual})
    return out


def _earlier_reviewers(c, ticket, step):
    """The bots that did a review step of the ticket's board ahead of `step` (Review 1's bot for Review 2)."""
    from . import step_flow
    from . import task_relations as TR
    before = {s["id"] for s in step_flow.reviews_before(c, step)} if step_flow.check_kind(step) == "review" else set()
    out = set()
    for child_id in TR.child_ids(c, ticket["id"]) if before else ():
        child = H.task(c, child_id)
        found = step_flow.child_check(c, child, ticket) if child else None
        if found and found["id"] in before and H.is_bot(child["owner"]):
            out.add(H.actor_id(child["owner"]))
    return out


def _company_rank(c, slug, builder):
    """How a bot's model company orders it for a check of a build on the `builder` companies: 0 for another company,
    1 for one Tico can't name all of, which may be the builder's, 2 for the builder's own."""
    if makers(c, slug, known_only=True) & builder:
        return 2
    return 0 if makers(c, slug, whole=True) else 1


def ranked(c, ticket, step, slugs, limit, exclude=()):
    """(free members best first, why none is) for this ticket's check at `step`. A check ranks a bot of another model
    company than the builder's (`builder_makers`) ahead of a bot of a company Tico can't name, and that ahead of a bot of
    the builder's company, which is still free when no other is, unless the step's `other_company` is `require`: then such a bot is never free for the ticket, nor is any
    bot while Tico cannot name the builder's company or the bot's. Then eligibility from `assess`, fewest open checks,
    the longest idle (no turn yet counts as longest), then by name."""
    from . import step_flow
    from .check_independence import assess
    excluded = set(exclude) | _earlier_reviewers(c, ticket, step)
    kind = step_flow.check_kind(step)
    required = bool(kind) and step_flow.other_company(step) == "require"
    builder = builder_makers(c, ticket, whole=required) if kind else set()
    free, why = [], set()
    for slug in slugs:
        if slug in excluded or "bot:" + slug == ticket["owner"]:
            continue
        decision = assess(c, ticket, slug)
        if not online(c, slug):
            why.add("offline")
            continue
        held = open_checks(c, slug)
        if held >= limit:
            why.add("busy")
            continue
        idle = (c.execute("SELECT last_turn_at FROM bots WHERE slug=?", (slug,)).fetchone() or [None])[0] or ""
        # Require needs both companies named and apart: a company Tico cannot name, or a run with a segment it cannot
        # name, may be the builder's.
        if required:
            known = makers(c, slug, whole=True) if builder else set()
            same = bool(known & builder)
            if not builder or same or not known:
                why.add("builder" if not builder else "company" if same else "unknown")
                continue
        # Model company only orders the free bots, so it ranks first; eligibility, workload and idle time break ties.
        rank = _company_rank(c, slug, builder) if builder and not required else 0
        free.append((rank, 0 if decision["eligible"] else 1, held, idle, slug))
    free.sort()
    if free:
        return [slug for _, _, _, _, slug in free], ""
    base = ("every reviewer in the pool is at its limit of open checks" if "busy" in why
            else "no reviewer in the pool is online" if "offline" in why else "")
    if "builder" in why:
        rest = "Tico can't name the model company the builder ran on, so no bot is shown to run on another"
        reason = f"{base}; {rest}" if base else rest
    elif "unknown" in why:
        rest = "the builder's model company or one Tico can't name" if "company" in why else \
            "a model company Tico can't name"
        reason = f"{base}; the rest run on {rest}" if base else f"every free bot in the pool runs on {rest}"
    elif "company" in why:
        reason = (f"{base}; the rest run on the builder's model company" if base
                  else "every free bot in the pool runs on the model company the builder ran on")
    elif base:
        reason = base
    elif slugs:
        reason = "no reviewer in the pool is eligible for this check"
    else:
        reason = "the step's pool has no reviewers"
    return [], reason


# ----------------------------------------------------------------------------------------------- filing
def _prs(c, ticket, step):
    """The pull requests a check at this step judges: the open ones, and after the merge (Post Release QA) the
    merged ones too."""
    from . import review_gate, step_flow
    if step["status"] in ("done", "closed") or step_flow.check_kind(step) == "release_qa":
        return [p for p in H.task_links(c, ticket["id"]) if p["kind"] == "pr" and p.get("state") != "closed"]
    return review_gate.links(c, ticket)


def _heads(prs, snapshots):
    from . import test_receipts
    out = []
    for pr in prs:
        detail = (snapshots or {}).get(pr["id"]) or H._json(pr.get("detail"), {}) or {}
        head = str(detail.get("head_sha") or pr.get("pr_sha") or "").lower()
        if not detail.get("error") and test_receipts.SHA.fullmatch(head):
            out.append(head)
    return sorted(set(out))


def _title(step, prs):
    numbers = [f"#{p['number']}" for p in prs if p.get("number")]
    return f"{kind(step)}: PR {', '.join(numbers)}" if numbers else f"{kind(step)}: {step['name']}"


def has_open_check(c, ticket, step, heads):
    """The open check request on this ticket for this step's kind and head (or an unknown head), else None."""
    from . import step_flow
    from . import task_relations as TR
    for child_id in TR.child_ids(c, ticket["id"]):
        child = H.task(c, child_id)
        if not child or child["status"] not in H.ACTIVE_STATUSES:
            continue
        found = step_flow.child_check(c, child, ticket)
        if not found or found["id"] != step["id"]:
            continue
        seen = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head' "
                         "ORDER BY ts DESC, rowid DESC LIMIT 1", (child_id,)).fetchone()
        judged = set(filter(None, (seen[0] if seen else "").split(",")))
        if heads and judged and not set(heads) & judged:
            continue
        return child
    return None


def waiting(c, ticket_id, step_id=None):
    """The ticket's open check waits (for one check step when `step_id` is given), oldest first."""
    if not _ready(c) or not c.execute("SELECT 1 FROM sqlite_master WHERE name='check_waits'").fetchone():
        return []
    rows = [dict(r) for r in c.execute("SELECT * FROM check_waits WHERE ticket_id=? AND state='waiting' "
                                       "ORDER BY created_at,id", (ticket_id,))]
    if step_id is not None:
        rows = [r for r in rows if r["step_id"] == step_id]
    return rows


def for_task(c, ticket_id):
    """A ticket's waiting checks, for its page."""
    out = []
    from . import step_flow
    for r in waiting(c, ticket_id):
        step = step_flow.step(c, r["step_id"])
        name = step["name"] if step else ""
        qa = step_flow.check_kind(step) in ("qa", "release_qa") if step else False
        label = "Waiting for QA" if qa else "Waiting for a reviewer"
        out.append({"id": r["id"], "step": name, "title": r["title"], "label": label, "reason": r["reason"],
                    "since": r["created_at"], "heads": [h for h in r["heads"].split(",") if h]})
    return out


def _drop(c, ticket_id, keep_step=None, why="the ticket moved on"):
    for r in waiting(c, ticket_id):
        if r["step_id"] != keep_step:
            c.execute("UPDATE check_waits SET state='dropped',reason=?,updated_at=? WHERE id=?", (why, H.now(), r["id"]))


def _file(c, ticket, step, slug, title, heads, snapshots, why):
    """File the check to `slug` as the ticket's owner would; the child, or None when a rule refused it (the refusal is
    rolled back and the check keeps waiting)."""
    body = (f"{kind(step)} of {title.split(':', 1)[-1].strip()}"
            + (f" at head {', '.join(heads)}" if heads else "")
            + f". Tico gave it to you from the {step['name']} pool ({why}). End with your VERDICT line for that head.")
    c.execute("SAVEPOINT check_pool_file")
    try:
        child = H.task_create(c, ticket["owner"], title, body, "bot:" + slug, parent_id=ticket["id"],
                              review_prs=snapshots, wake_refs={"check_pool": step["name"]})
    except H.Refused as exc:
        c.execute("ROLLBACK TO check_pool_file")
        c.execute("RELEASE check_pool_file")
        H._task_event(c, ticket["id"], H.KEEPER, "check_not_filed", None, slug, f"{title}: {exc.detail}"[:500])
        return None
    c.execute("RELEASE check_pool_file")
    H._task_event(c, ticket["id"], H.KEEPER, "check_filed", None, child["id"], f"{title} to {slug} ({why})")
    return child


def _why(c, slug):
    held = open_checks(c, slug)
    return f"{held} open check{'s' if held != 1 else ''}, the least busy free reviewer"


def safely(c, work, *args):
    """Run a routing step for a move or a finished check; a fault in it is logged and undone, never the move's."""
    c.execute("SAVEPOINT check_pool_route")
    try:
        out = work(c, *args)
    except H.Refused:
        c.execute("ROLLBACK TO check_pool_route")
        c.execute("RELEASE check_pool_route")
        return None
    except Exception:  # noqa: BLE001 - a step move or a finished check never fails for its routing
        log.exception("check routing failed")
        c.execute("ROLLBACK TO check_pool_route")
        c.execute("RELEASE check_pool_route")
        return None
    c.execute("RELEASE check_pool_route")
    return out


def entered(c, actor, ticket, snapshots=None):
    """A ticket entered a step: file its check when the step has a pool and nothing checks this head yet, to the
    least busy eligible member, or leave it waiting for the first free one. Returns the child, the wait, or None."""
    if not _ready(c):
        return None
    _drop(c, ticket["id"], keep_step=ticket.get("step_id"))
    found = pool(c, ticket.get("step_id"))
    if not found or ticket.get("status") not in H.ACTIVE_STATUSES:
        return None
    step, slugs, limit = found
    prs = _prs(c, ticket, step)
    if not prs:
        return None          # nothing to judge at a head: a board-only move files no check
    heads = _heads(prs, snapshots)
    title = _title(step, prs)
    if has_open_check(c, ticket, step, heads):
        return None
    for r in waiting(c, ticket["id"]):
        if r["step_id"] == step["id"]:
            if set(filter(None, r["heads"].split(","))) == set(heads):
                return r
            c.execute("UPDATE check_waits SET state='dropped',reason='a new head',updated_at=? WHERE id=?",
                      (H.now(), r["id"]))
    free, reason = ranked(c, ticket, step, slugs, limit)
    for slug in free:
        child = _file(c, ticket, step, slug, title, heads, snapshots, _why(c, slug))
        if child:
            return child
    return _wait(c, ticket, step, title, heads, reason or "no reviewer could take it")


def _wait(c, ticket, step, title, heads, reason, attempt_id=None):
    ts = H.now()
    row = {"id": H.new_id(), "ticket_id": ticket["id"], "step_id": step["id"], "title": title,
           "heads": ",".join(heads), "reason": reason, "state": "waiting", "created_at": ts, "updated_at": ts,
           "attempt_id": attempt_id}
    c.execute("INSERT INTO check_waits(id,ticket_id,step_id,title,heads,reason,state,created_at,updated_at,attempt_id) "
              "VALUES(:id,:ticket_id,:step_id,:title,:heads,:reason,:state,:created_at,:updated_at,:attempt_id)", row)
    H._task_event(c, ticket["id"], H.KEEPER, "check_waiting", None, row["id"], f"{title}: waiting, {reason}")
    return row


def TR_parent(c, task_id):
    from . import task_relations as TR
    return TR.parent_of(c, task_id)


def _take(c, wait, only=None):
    """Give one waiting check to `only` (a bot that just finished one) or to the best free member. The child, else
    None. A wait whose ticket left the step, finished or was checked meanwhile ends here."""
    ticket = H.task(c, wait["ticket_id"])
    if not ticket or ticket.get("step_id") != wait["step_id"] or ticket["status"] not in H.ACTIVE_STATUSES:
        c.execute("UPDATE check_waits SET state='dropped',reason='the ticket moved on',updated_at=? WHERE id=?",
                  (H.now(), wait["id"]))
        return None
    found = pool(c, wait["step_id"])
    if not found:
        c.execute("UPDATE check_waits SET state='dropped',reason='the step has no pool',updated_at=? WHERE id=?",
                  (H.now(), wait["id"]))
        return None
    step, slugs, limit = found
    heads = [h for h in wait["heads"].split(",") if h]
    if has_open_check(c, ticket, step, heads):
        c.execute("UPDATE check_waits SET state='dropped',reason='a check was filed',updated_at=? WHERE id=?",
                  (H.now(), wait["id"]))
        return None
    free, reason = ranked(c, ticket, step, slugs, limit)
    if only is not None:
        builder = builder_makers(c, ticket)
        def rank(slug):
            return _company_rank(c, slug, builder) if builder else 0
        # The bot that just finished takes the wait, unless a free bot ranks ahead of it by model company: then the
        # best free one does, as `ranked` orders them.
        free = [] if only not in free else [only] if rank(only) <= rank(free[0]) else free
    for slug in free:
        # Taken before the request is filed, so the one-check-per-head rule does not see its own wait.
        c.execute("UPDATE check_waits SET state='filed',updated_at=? WHERE id=?", (H.now(), wait["id"]))
        child = H.task(c, wait.get("child_id")) if wait.get("child_id") else None
        if child and TR_parent(c, child["id"]) == ticket["id"]:
            child = H.task_update(c, ticket["owner"], child["id"], owner="bot:" + slug, status="open",
                                  note="Tico routed this waiting check to the first available reviewer.")
        else:
            child = _file(c, ticket, step, slug, wait["title"], heads, None,
                          "it finished a check and took the next waiting one" if slug == only else _why(c, slug))
        if child:
            c.execute("UPDATE check_waits SET child_id=? WHERE id=?", (child["id"], wait["id"]))
            return child
        c.execute("UPDATE check_waits SET state='waiting' WHERE id=?", (wait["id"],))
    if reason and reason != wait["reason"]:
        c.execute("UPDATE check_waits SET reason=?,updated_at=? WHERE id=?", (reason, H.now(), wait["id"]))
    return None


def _all_waiting(c):
    if not _ready(c) or not c.execute("SELECT 1 FROM sqlite_master WHERE name='check_waits'").fetchone():
        return []
    return [dict(r) for r in c.execute("SELECT * FROM check_waits WHERE state='waiting' ORDER BY created_at,id")]


def freed(c, row):
    """A check request left the board (done, declined or closed): its bot takes the oldest waiting check it may. A wait
    a free bot of a better company takes instead does not stop it looking at the next."""
    parent = H.TR.parent_of(c, row["id"]) if H.is_bot(row.get("owner")) else None
    if not parent or not is_check_title(c, H.task(c, parent), row.get("title")):
        return None
    if c.execute("SELECT 1 FROM turns WHERE task_id=? AND finished_at IS NULL LIMIT 1", (row["id"],)).fetchone():
        return None
    slug = H.actor_id(row["owner"])
    for wait in _all_waiting(c):
        found = pool(c, wait["step_id"])
        if not found or slug not in found[1]:
            continue
        child = _take(c, wait, only=slug)
        if child and child["owner"] == "bot:" + slug:
            return child
    return None


def drain(c):
    """Give every waiting check to a free member, oldest first (a pool saved, or a sweep). Returns the children filed."""
    out = []
    for wait in _all_waiting(c):
        child = _take(c, wait)
        if child:
            out.append(child)
    return out
