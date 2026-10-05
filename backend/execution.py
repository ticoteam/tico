"""Durable delivery, runner enrollment, and transactionally fenced execution attempts."""

import json
import secrets
import sqlite3

from . import inbox_isolation, providers, routines, runner_versions, usage_limits
from . import task_privacy as privacy
from .auth import Identity
from .batch_work import isolated
from .harnesses import reports_tool_calls
from .statuses import PARKED_SQL
from .store import H, P, Problem, bot_readiness, digest, encode, message_page, readiness_document

LIMIT_COOLDOWN = 1800        # seconds a bot waits after its runtime reported a usage limit
LIMIT_COOLDOWN_LONG = 7200   # after the third limit in a row: the subscription window is spent, not busy
LIMIT_STRIKES = 3
# How long a server that stopped answering runners is forgiven: leases that lapse meanwhile
# get grace instead of expiring, and a runner reporting in afterwards gets its attempt back.
# A backup can starve a single small API worker for minutes: 90 s leases lapsed and healthy running turns were marked interrupted for review.
STALL_MAX = 900
# A failed turn whose only events are these produced nothing and touched nothing.
SILENT_KINDS = {"error", "diagnostic", "status"}
NO_EFFECT_TRIES = 3
PRE_START_TRIES = 10        # lapsed leases before start: a broken computer must not retry forever
NO_EFFECT_WITHIN = 120       # seconds from start; a longer silent run may have used unreported tools
# A deploy drains every bot while it waits for runs to finish (at most 30 minutes) and then
# activates. A drain it left behind for longer than this is a leak, not a deploy in progress.
DEPLOY_DRAIN_MAX = 3600
# A machine that wakes for a few seconds — a macOS dark wake, a laptop on battery checking
# mail — reports in once and is gone before it can finish anything. A silence longer than
# AWAKE_GAP ends a waking period; AWAKE_SETTLE of unbroken contact starts a new one. Work
# waits in the queue meanwhile, which is what an owner expects a sleeping machine to do.
AWAKE_GAP = 60
AWAKE_SETTLE = 120
# A claim arrives four times a second; contact is recorded at most this often so the rule costs
# one write a minute on an idle machine rather than four a second.
CONTACT_EVERY = 10


def queued_task_sql():
    """Match message_task_id's string/list handling before testing interrupted work."""
    refs = []
    # The Unicode whitespace stripped by Python's _task_ref, including older saved refs.
    whitespace = "char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"
    for path in ("$.task", "$.task_id"):
        value = f"json_extract(m.refs_json,'{path}')"
        refs.append(f"CASE WHEN json_type(m.refs_json,'{path}')='text' THEN nullif(trim({value},{whitespace}),'') "
                    f"WHEN json_type(m.refs_json,'{path}')='array' THEN (SELECT trim(value,{whitespace}) "
                    f"FROM json_each(m.refs_json,'{path}') WHERE type='text' AND trim(value,{whitespace})<>'' "
                    "ORDER BY key LIMIT 1) END")
    return "coalesce(" + ",".join(refs) + f",nullif(trim(cv.task_id,{whitespace}),''))"


def limit_streak(c, bot):
    """How many of the bot's latest finished attempts in a row ended on a usage limit (capped
    at LIMIT_STRIKES; a lapsed lease or any other outcome ends the run)."""
    streak = 0
    for row in c.execute("SELECT result_json FROM attempts WHERE bot=? AND state IN ('completed','failed','interrupted') "
                         "ORDER BY created DESC LIMIT ?", (bot, LIMIT_STRIKES)):
        if not (row[0] and json.loads(row[0]).get("limited")):
            break
        streak += 1
    return streak


def limit_cooldown(c, bot):
    """Seconds a limited bot waits before its next claim; longer once the limits keep coming."""
    return LIMIT_COOLDOWN_LONG if limit_streak(c, bot) >= LIMIT_STRIKES else LIMIT_COOLDOWN


def bot_repository(c, settings, bot):
    """`owner/name` of a bot's GitHub repository, resolved the way the token route resolves it, or "".
    The runner clones and publishes this one (runner/service.py `fetch_repository`)."""
    from .github_app import repo_of
    from .shared_bots import declared, source_of
    config = declared(c, bot)
    # Assignment instances clone the source repository into a private machine-local tree.
    # They must never receive the source's scoped GitHub token or publish task work to its trunk.
    if config.get("assignment_branch"):
        return ""
    bot = source_of(config) or bot
    config = c.execute("SELECT repo FROM bot_config WHERE bot=?", (bot,)).fetchone()
    try:
        app = c.execute("SELECT org FROM github_app WHERE id='app'").fetchone()
    except sqlite3.OperationalError:
        app = None
    return repo_of(config["repo"] if config else "", (app["org"] if app else "") or settings.github_owner) or ""


def _reported(c, runner_id, bot):
    row = c.execute("SELECT label,readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    report = (readiness_document(row["readiness_json"]).get("bots") or {}).get(bot) if row else None
    return (row["label"] if row else "a computer"), report if isinstance(report, dict) else {}


def stranded(c, settings, bot, source_id, target_id):
    """Why moving `bot` from one computer to another would leave it without its repository, or "".

    The destination gets it by cloning from GitHub. That fails when the destination holds no copy and the
    computer being left reports a checkout GitHub does not have (`published` false): its history exists
    only there. A runner that does not report `published` is given the benefit of the doubt."""
    _, there = _reported(c, target_id, bot)
    if there.get("repository_present") is True:
        return ""
    source, here = _reported(c, source_id, bot)
    if here.get("published") is not False:
        return ""
    target, _ = _reported(c, target_id, bot)
    repository = bot_repository(c, settings, bot) or "its repository"
    return (f"{bot}'s history exists only on {source}: {repository} is not on GitHub yet, so {target} could not fetch "
            f"it and the bot would stop running. Create it as an empty private repository on GitHub "
            f"(`hub bot repo-create {bot} --empty`, or by hand); {source} then publishes it, and the bot can move")


def forget_report(c, runner_id, bot):
    """Drop a computer's stored report on a bot it no longer hosts, so a move never leaves the old computer
    claiming the bot is ready there until that computer's next heartbeat says what it holds now."""
    row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
    if not row:
        return
    document = readiness_document(row["readiness_json"])
    if bot in (document.get("bots") or {}):
        del document["bots"][bot]
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (encode(document), runner_id))


class Execution:
    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.booted = H.now()
        # Set by create_app: onboarding gives a newly enrolled machine the bots nobody placed.
        self.runner_enrolled = None
        self.served_at = None   # when this process last answered a runner (heartbeat, claim, attempt call)

    def runner(self, c, who):
        if who.role != "runner":
            raise Problem("identity", "A registered runner is required", 403)
        return c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                         (who.runner_id,)).fetchone()

    @staticmethod
    def waking(row, now):
        """When the machine's current stretch of unbroken contact began, given its last one.

        `last_seen` is contact of any kind, a heartbeat or a claim, so a silence between two
        wakes is visible whichever call happens to arrive first. NULL is a machine that has
        been here all along: one enrolling now, and every machine already running when this
        column arrived. Only a real silence starts a new stretch.
        """
        if row["last_seen"] and row["last_seen"] <= H.shift(now, seconds=-AWAKE_GAP):
            return now
        return row["awake_since"]

    def readable(self, c, who, conv):
        """Whether this person may read the conversation, not whether they may act on it.

        A leftover keeper-and-bot thread has no person in it, so there is no one's privacy
        to protect and the operator check is the whole authority. A conversation a person
        is in stays theirs.
        """
        if not any(str(p).startswith("human:") for p in conv["participants"]):
            return True
        try:
            self.auth.conversation(c, who, conv["id"])
            return True
        except Problem:
            return False

    @staticmethod
    def awake(awake_since, now):
        """Whether a machine has been reporting in long enough to be given work."""
        return not awake_since or awake_since <= H.shift(now, seconds=-AWAKE_SETTLE)

    def issue_enrollment(self, c, who, body):
        if who.role not in ("owner", "human"):
            raise Problem("identity", "A person must enroll a machine", 403)
        operator = body.operator or H.actor_id(who.actor)
        if not self.auth.bot_admin(who) and operator != H.actor_id(who.actor):
            raise Problem("forbidden", "You can enroll only your own machines", 403)
        if not H.human(c, operator):
            raise Problem("not_found", "Operator is not on the roster", 404)
        code = secrets.token_urlsafe(32)
        expires = H.shift(H.now(), minutes=15)
        c.execute("INSERT INTO enrollments(code_hash,operator,expires) VALUES(?,?,?)",
                  (digest(code), operator, expires))
        H.event(c, who.actor, "runner.enrollment", operator)
        return {"code": code, "expires": expires, "operator": operator}

    def enroll(self, body):
        with self.store.transaction() as c:
            row = c.execute("SELECT * FROM enrollments WHERE code_hash=?", (digest(body.code),)).fetchone()
            if not row or row["expires"] <= H.now():
                raise Problem("enrollment", "Enrollment code is invalid or expired", 401)
            # Deterministic credential makes a lost enrollment response safely retryable.
            token = digest("tico-runner:" + body.code)
            if row["consumed_at"]:
                runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                                   (row["runner_id"],)).fetchone()
                if not runner or runner["label"] != body.label:
                    raise Problem("enrollment", "Enrollment code was already used", 409)
                return {"runner_id": runner["id"], "token": token, "operator": row["operator"]}
            rid = H.new_id()
            # A new computer takes members' bots, so a team gets going without asking; an admin closes it per
            # computer (Settings > Computers). Computers that existed before this default keep what they had.
            c.execute("INSERT INTO runners(id,label,operator,token_hash,created,platform,accepts_member_bots) "
                      "VALUES(?,?,?,?,?,?,1)",
                      (rid, body.label, row["operator"], digest(token), H.now(), body.platform))
            c.execute("UPDATE enrollments SET consumed_at=?,runner_id=? WHERE code_hash=?",
                      (H.now(), rid, digest(body.code)))
            H.event(c, "human:" + row["operator"], "runner.enrolled", rid)
            if self.runner_enrolled:
                self.runner_enrolled(c, rid, row["operator"])
            return {"runner_id": rid, "token": token, "operator": row["operator"]}

    def heartbeat(self, c, who, body):
        row = self.runner(c, who)
        now = H.now()
        awake_since = self.waking(row, now)
        self.served_at = now
        del row
        from .subscriptions import record
        record(c, who.runner_id, body.profiles, body.readiness)
        readiness = readiness_document(body.readiness)
        from .repositories import save_metadata, metadata
        key = 'computer-repositories:' + who.runner_id
        previous = metadata(c, key)
        report = {'repositories': [r.model_dump() for r in body.repositories]} if body.repositories is not None else previous or {'repositories': 'unknown'}
        if report != previous:
            save_metadata(c, key, report)
        # Store what the runner actually reported: optional fields a runner left unset,
        # such as the per-profile rows older runners cannot produce, never enter the record.
        # Routines are the hub's own rows now; what a runner before 0.5.4 read from a
        # repository manifest is accepted and dropped.
        for row in readiness.get('bots', {}).values():
            for key in ('schedules', 'schedule_error', 'routine_revisions', 'routine_preparation_error'):
                row.pop(key, None)
            if not row.get('profile'):
                row.pop('profile', None)
            if row.get('sign_in') == 'unknown':
                row.pop('sign_in', None)
            if row.get('published') is None:
                row.pop('published', None)
            if not row.get('tools'):
                row.pop('tools', None)
        for row in readiness.get('runtimes', {}).values():
            if not row.get('profiles'):
                row.pop('profiles', None)
            if row.get('authenticated') != 'rejected':
                row.pop('rejected_at', None), row.pop('rejected_reason', None)
            if not row.get('credential_source'):
                row.pop('credential_source', None)
        if readiness.get('worktrees') is None:
            readiness.pop('worktrees', None)
        if not readiness.get('harnesses'):
            readiness.pop('harnesses', None)
        if not readiness.get('mail_key'):
            readiness.pop('mail_key', None)
        if not readiness.get('shared_env'):
            readiness.pop('shared_env', None)
        # The platform named at enrollment stands: backend/sql.py decides on it (a heartbeat
        # from a stolen credential must not turn a shared server into a personal Mac).
        c.execute("UPDATE runners SET last_seen=?,awake_since=?,version=?,platform=coalesce(nullif(platform,''),?),"
                  "capacity=?,readiness_json=?,capabilities_json=? WHERE id=?",
                  (H.now(), awake_since, body.version, body.platform, body.capacity,
                   encode(readiness), encode(sorted(set(body.capabilities))), who.runner_id))
        from . import bot_tools
        bot_tools.reconcile(c, {bot: row.get('tools') for bot, row in readiness.get('bots', {}).items()})
        if body.release:
            runner_versions.record(c, who.runner_id, body)
        if body.checkout:
            # How long it has been behind is the server's to remember: a runner checks every ten minutes.
            checkout = body.checkout.model_dump()
            before = H._json((c.execute("SELECT checkout_json FROM runners WHERE id=?", (who.runner_id,)).fetchone()
                              or {"checkout_json": None})["checkout_json"], {}) or {}
            if checkout["behind"]:
                checkout["behind_since"] = before.get("behind_since") if before.get("behind") else now
            c.execute("UPDATE runners SET checkout_json=? WHERE id=?", (encode(checkout), who.runner_id))
        if body.mail_agent_instructions:
            from .views import roster
            people = roster(c)
            for bot, content in body.mail_agent_instructions.items():
                if not P.inbox_person(bot, people) or len(content) > 100_000:
                    continue
                assigned = c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                     (bot, who.runner_id)).fetchone()
                if assigned:
                    c.execute("INSERT INTO mail_agent_instructions(bot,content,runner_id,updated) "
                              "VALUES(?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                              "content=excluded.content,runner_id=excluded.runner_id,"
                              "updated=excluded.updated WHERE "
                              "mail_agent_instructions.content<>excluded.content OR "
                              "mail_agent_instructions.runner_id<>excluded.runner_id",
                              (bot, content, who.runner_id, now))
        for bot, content in body.agent_instructions.items():
            if len(content) > 100_000:
                continue
            assigned = c.execute("SELECT 1 FROM assignments WHERE bot=? AND runner_id=?",
                                 (bot, who.runner_id)).fetchone()
            if assigned:
                c.execute("INSERT INTO bot_agent_instructions(bot,content,runner_id,updated) "
                          "VALUES(?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                          "content=excluded.content,runner_id=excluded.runner_id,"
                          "updated=excluded.updated WHERE "
                          "bot_agent_instructions.content<>excluded.content OR "
                          "bot_agent_instructions.runner_id<>excluded.runner_id",
                          (bot, content, who.runner_id, now))
        from . import worktrees
        worktree_actions = worktrees.heartbeat(c, who, body.worktrees, readiness.get('worktrees', False), self.store.settings.github_owner)
        # A Restart a person pressed goes to the runner once; it restarts when no turn is running.
        restart = c.execute("SELECT restart_requested FROM runners WHERE id=?", (who.runner_id,)).fetchone()
        if restart and restart["restart_requested"]:
            c.execute("UPDATE runners SET restart_requested=NULL WHERE id=?", (who.runner_id,))
        return {"server_time": H.now(), "assignments": self.assigned(c, who), "runtime_credential_source": True, "worktree_actions": worktree_actions,
                **({"restart": True} if restart and restart["restart_requested"] else {})}

    def assigned(self, c, who):
        runner = self.runner(c, who)
        # The environment owner hosts any bot; every other operator hosts only their own.
        owner = self.auth.owner_id(c)
        rows = c.execute("SELECT a.*,b.state,bc.config_json,bc.operator FROM assignments a "
                         "JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                         "WHERE a.runner_id=? ORDER BY a.bot", (who.runner_id,)).fetchall()
        result = []
        from .subscriptions import effective, context
        subscription_context = context(c)
        from .views import roster
        people = roster(c)
        # The runner gets a concrete runtime and model; a bot that names none runs on the
        # company default, so changing the default moves it without editing the bot.
        company = providers.load(c, self.store.settings)
        from .shared_bots import follow
        for row in rows:
            row_config = json.loads(row["config_json"] or "{}")
            assignment_info = None
            if row_config.get("assignment_branch"):
                active = c.execute("SELECT id,phase,runner_id,task_id,revision,checkpoint_json FROM assignment_branches WHERE bot=?",
                                   (row["bot"],)).fetchone()
                capabilities = set(H._json(runner["capabilities_json"], []) or [])
                if (not active or active["runner_id"] != who.runner_id
                        or active["phase"] in ("archived", "cancelled")
                        or "assignment_instances_v1" not in capabilities):
                    continue
                assignment_info = {"id": active["id"], "phase": active["phase"], "task_id": active["task_id"],
                                  "revision": active["revision"],
                                  "checkpoint": H._json(active["checkpoint_json"], {}) or {}}
            takes = runner['accepts_member_bots'] and self.auth.member_bot(c, row['bot'])
            if row['operator'] != runner['operator'] and runner['operator'] != owner and not takes:
                continue
            result.append({**dict(row), 'computer_label': runner['label'], 'profile': effective(c, row['bot'], subscription_context)[0], 'config': providers.fill(company, follow(c, row['bot'], json.loads(row['config_json']))),
                           'repository': bot_repository(c, self.store.settings, row['bot']),
                           'mail_agent': bool(P.inbox_person(row['bot'], people)),
                           **({'assignment': assignment_info} if assignment_info else {})})
        return result

    def assign(self, c, who, bot, body):
        from .shared_bots import check_runner
        check_runner(c, bot, body.runner_id)
        if not (self.auth.operator(c, who, bot) or self.auth.bot_manager(c, who, bot)):
            raise Problem("forbidden", "You do not manage this bot's machines", 403)
        from .agents import external_harness
        if external_harness(c, bot):
            raise Problem("harness", "This bot is run by an external agent; it has no computer to "
                          "assign. Give it an agent credential in Settings instead", 422)
        runner = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL",
                           (body.runner_id,)).fetchone()
        if not runner:
            raise Problem("not_found", "Computer is not registered", 404)
        operator = c.execute("SELECT operator FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]
        member_bot = self.auth.member_bot(c, bot)
        if (member_bot and not runner["accepts_member_bots"] and runner["operator"] != operator
                and not self.auth.bot_admin(who)):
            # A member's bot goes on its own operator's computer, or on one an admin has opened to members' bots:
            # never on another member's.
            raise Problem("computer_closed", "That computer does not take bots members create. Ask an admin to place "
                          "this bot, or to let the computer accept members' bots (Settings > Computers)", 409)
        # A computer hosts its operator's bots and the owner's; a member's bot may also go on a computer an
        # admin has opened to members' bots, and an admin may put a member's bot on any computer.
        if (runner["operator"] not in (operator, self.auth.owner_id(c))
                and not (member_bot and (runner["accepts_member_bots"] or self.auth.bot_admin(who)))):
            raise Problem("forbidden", "This machine's operator is not authorized to host this bot", 403)
        old = c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone()
        generation = old["generation"] if old else 0
        if body.expected_generation != generation:
            raise Problem("version_conflict", "Assignment changed; refresh before transferring", 409)
        active = c.execute("SELECT * FROM attempts WHERE bot=? AND state IN ('leased','running') "
                           "AND lease_until>?", (bot, H.now())).fetchone()
        if active:
            raise Problem("busy", "Drain the current run or wait for its lease to expire before transfer", 409)
        if not old or old["runner_id"] != body.runner_id:
            from .views import roster
            inbox_isolation.check(c, bot, body.runner_id, roster(c))
            if old and (blocked := stranded(c, self.store.settings, bot, old["runner_id"], body.runner_id)):
                raise Problem("repository_unpublished", blocked, 409)
        self.expire(c)
        c.execute("INSERT INTO assignments VALUES(?,?,?,?,?) ON CONFLICT(bot) DO UPDATE SET "
                  "runner_id=excluded.runner_id,generation=excluded.generation,updated=excluded.updated,"
                  "updated_by=excluded.updated_by",
                  (bot, body.runner_id, generation + 1, H.now(), who.actor))
        H.event(c, who.actor, "bot.assigned", bot, {"runner": body.runner_id, "generation": generation + 1})
        if old and old["runner_id"] != body.runner_id:
            forget_report(c, old["runner_id"], bot)
        return dict(c.execute("SELECT * FROM assignments WHERE bot=?", (bot,)).fetchone())

    def grace_leases(self):
        """After a restart, leases that lapsed while the API was away get one more lease
        period: the runner may well still be working, and a truly gone one expires exactly
        as before, one period later."""
        with self.store.transaction() as c:
            until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
            rows = c.execute("SELECT id,bot,lease_until FROM attempts WHERE state IN ('leased','running') AND lease_until<?",
                             (H.now(),)).fetchall()
            for row in rows:
                with isolated(c, "grace_leases", row["id"]):
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-grace", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "lease_until": until})
            return len(rows)

    def stalled(self, now):
        """True when this server has answered no runner for more than half a lease but less
        than STALL_MAX: a lease lapsing in that window is the server's doing, not the runner's,
        which is still renewing. A longer silence is a runner that is gone."""
        if not self.served_at:
            return False
        gap = (H.parse_ts(now) - H.parse_ts(self.served_at)).total_seconds()
        return self.store.settings.lease_seconds / 2 < gap <= STALL_MAX

    def expire(self, c):
        now = H.now()
        rows = c.execute("SELECT * FROM attempts WHERE state IN ('leased','running') AND lease_until<=?",
                         (now,)).fetchall()
        if rows and self.stalled(now):
            # The same grace as after a restart: one more lease period, then the usual rule.
            until = H.shift(now, seconds=self.store.settings.lease_seconds)
            for row in rows:
                with isolated(c, "expire", row["id"]):
                    c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, row["id"]))
                    H.event(c, H.KEEPER, "attempt.lease-grace", row["id"],
                            {"bot": row["bot"], "lapsed": row["lease_until"], "lease_until": until, "served_at": self.served_at})
            return
        for row in rows:
            with isolated(c, "expire", row["id"]):
                state = "queued" if row["state"] == "leased" else "uncertain"
                c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), row["id"]))
                if row["state"] == "leased":
                    tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=? AND started IS NULL "
                                      "AND state='expired' AND finished>=?", (row["job_id"], self.booted)).fetchone()[0]
                    if tries >= PRE_START_TRIES:
                        computer = c.execute("SELECT label FROM runners WHERE id=?", (row["runner_id"],)).fetchone()
                        label = (computer["label"] if computer else None) or "its Computer"
                        bot = H.bot(c, row["bot"])
                        name = (self.store.settings.assistant_name if row["bot"] == self.store.settings.assistant_bot
                                else (bot["display_name"] if bot else None)) or row["bot"]
                        version = c.execute("SELECT release FROM runner_versions WHERE runner_id=?",
                                            (row["runner_id"],)).fetchone()
                        remedy = ("update its Tico" if version and runner_versions.incompatible(version["release"])
                                  else "check that Computer")
                        reason = f"Your {name} couldn't start on {label}; {remedy}"
                        state = "failed"
                        c.execute("UPDATE attempts SET state='failed',final_text=? WHERE id=?", (reason, row["id"]))
                        job = c.execute("SELECT message_id FROM jobs WHERE id=? AND attempt_id=? "
                                        "AND state='leased'", (row["job_id"], row["id"])).fetchone()
                        if job:
                            msg = H.message(c, job["message_id"])
                            if H.is_human(msg["from_actor"]):
                                H.say(c, "bot:" + row["bot"] if bot else H.KEEPER, msg["from_actor"], reason, kind="notice",
                                      conversation_id=msg["conversation_id"], in_reply_to=msg["id"],
                                      refs={"turn_id": row["id"]})
                c.execute("UPDATE jobs SET state=? WHERE attempt_id=? AND state IN ('leased','running','input')", (state, row["id"]))
                H.turn_finish(c, H.KEEPER, row["id"], exit_code="failed" if state == "failed" else "interrupted",
                              summary=reason if state == "failed" else "Runner lease expired")
                if H.bot(c, row["bot"]):
                    H.status_set(c, H.KEEPER, row["bot"], state="crashed",
                                 focus=reason if state == "failed" else "One run stopped; saved for later")
                H.event(c, H.KEEPER, "attempt.expired", row["id"], {"job_state": state})

    @staticmethod
    def mark_rejected(c, runner_id, runtime, reason, profile=None):
        """Record the refusal now; the runner's next heartbeat repeats it and later lifts it."""
        record = c.execute("SELECT readiness_json FROM runners WHERE id=?", (runner_id,)).fetchone()
        ready = readiness_document(record[0] if record else None)
        row = (ready.get("runtimes") or {}).get(runtime)
        if not isinstance(row, dict):
            return
        if profile:
            row = {}
        row.update(authenticated="rejected", rejected_at=H.now(), rejected_reason=reason[:300],
                   detail=("Sign-in rejected: " + reason)[:500])
        for bot in ready.get("bots", {}).values():
            if isinstance(bot, dict) and bot.get("runtime") == runtime and bot.get("profile", "") == (profile or ""):
                bot["sign_in"] = "rejected"
                bot["ready"] = False
                bot["problems"] = [row["detail"]]
        if profile:
            report = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                               (runner_id, profile)).fetchone()
            if report:
                runtimes = json.loads(report[0] or "{}")
                runtimes[runtime] = {"signed_in": False}
                c.execute("UPDATE computer_profiles SET runtimes_json=?,updated=? WHERE runner_id=? AND profile=?",
                          (json.dumps(runtimes), H.now(), runner_id, profile))
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (json.dumps(ready), runner_id))

    def candidate(self, c, who, body, runner):
        """The queued job this runner would be given next, or None. Reads only."""
        runner_capabilities = set(H._json(runner["capabilities_json"], []) or [])
        ready = readiness_document(runner["readiness_json"])
        # Unconfirmed effects hold the interrupted conversation and task until review. Other
        # tasks and routines for the same bot can continue. A person's unrelated chat also
        # remains claimable so an interrupted routine cannot silence its owner.
        default = usage_limits.company(c)
        from .subscriptions import context, effective, covers, refusal
        from .repositories import metadata
        subscription_context = context(c)
        bots = []
        for row in c.execute("SELECT a.bot,bc.config_json FROM assignments a JOIN bots b ON b.slug=a.bot JOIN bot_config bc ON bc.bot=a.bot "
                             "WHERE a.runner_id=? AND b.state='active' AND (? IS NULL OR a.bot=?) "
                             "AND EXISTS(SELECT 1 FROM jobs j WHERE j.bot=a.bot AND j.state='queued') "
                             "AND NOT EXISTS(SELECT 1 FROM attempts t WHERE t.bot=a.bot AND t.state IN ('leased','running')) "
                             "AND NOT EXISTS(SELECT 1 FROM bot_control bc WHERE bc.bot=a.bot AND bc.draining=1)",
                             (who.runner_id, body.bot, body.bot)).fetchall():
            bot = row["bot"]
            if bot in (body.busy_bots or ()):
                continue
            try:
                config = json.loads(row["config_json"])
                if not isinstance(config, dict):
                    continue
            except (ValueError, TypeError):
                continue
            if config.get("assignment_branch"):
                assignment = c.execute("SELECT phase,task_id FROM assignment_branches WHERE bot=?", (bot,)).fetchone()
                if ("assignment_instances_v1" not in runner_capabilities or not assignment
                        or assignment["phase"] not in ("preparing", "working")):
                    continue
                has_linked_task = False
                for queued in c.execute("SELECT m.*,cv.task_id AS conversation_task FROM jobs j JOIN messages m ON m.id=j.message_id "
                                        "JOIN conversations cv ON cv.id=m.conversation_id WHERE j.bot=? AND j.state='queued'",
                                        (bot,)).fetchall():
                    if H.message_task_id(queued, {"task_id": queued["conversation_task"]}) == assignment["task_id"]:
                        has_linked_task = True
                        break
                if not has_linked_task:
                    continue
            if config.get("assignment_branch"):
                assignment = c.execute("SELECT phase FROM assignment_branches WHERE bot=?", (bot,)).fetchone()
                # Waiting, paused, interrupted and archived actors keep their identity and data,
                # but cannot take work until an authorized lifecycle transition resumes them.
                if not assignment or assignment["phase"] not in ("preparing", "working"):
                    continue
            profile = effective(c, bot, subscription_context)[0]
            if profile:
                if not metadata(c, "computer-profiles:" + runner["id"]).get("reported"):
                    continue
                runtime = providers.bot_choice(c, self.store.settings, config)[0]
                blocked = refusal(c, runner['id'], bot)
                if not covers({**config, 'runtime': runtime}) or (blocked.get('profile') == profile
                        and blocked.get('primary_runtime') == runtime
                        and blocked.get('primary_harness') == config.get('harness')):
                    continue
                report = c.execute("SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?",
                                   (runner["id"], profile)).fetchone()
                if not report or json.loads(report[0] or "{}").get(runtime, {}).get("signed_in") is False:
                    continue
            check = bot_readiness(ready, bot)
            if check.get("ready") is not True or (
                    ready.get("runtimes", {}).get(check.get("runtime")) or {}).get("authenticated") == "rejected" and not check.get("profile") and check.get("sign_in") != "ready":
                continue
            if usage_limits.blocked(c, bot, default=default):
                continue
            status = H.status(c, bot) or {}
            if (status.get("state") == "limited" and status.get("since")
                    and status["since"] > H.shift(H.now(), seconds=-limit_cooldown(c, bot))):
                continue
            bots.append(bot)
        if not bots:
            return None
        marks = ",".join("?" for _ in bots)
        task_sql = queued_task_sql()
        # Readiness, spend and cooldown are checked once per bot, never once per queued job.
        # Keep human chat priority and interrupted-task/conversation fencing in the query.
        candidates = c.execute(
            "SELECT j.*,a.generation FROM jobs j JOIN assignments a ON a.bot=j.bot "
            "JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE j.state='queued' AND j.bot IN ({marks}) AND a.runner_id=? "
            "AND (m.from_actor LIKE 'human:%' OR NOT EXISTS(SELECT 1 FROM bot_config pc "
            "WHERE pc.bot=j.bot AND pc.onboarding_state IN " + PARKED_SQL + ")) "
            f"AND ((m.from_actor LIKE 'human:%' AND cv.kind='chat' AND {task_sql} IS NULL) "
            "OR NOT EXISTS(SELECT 1 FROM jobs u JOIN messages um ON um.id=u.message_id "
            "JOIN conversations uc ON uc.id=um.conversation_id WHERE u.bot=j.bot AND u.state='uncertain' "
            f"AND (({task_sql} IS NOT NULL AND "
            + H.MESSAGE_TASK_SQL.replace("m.", "um.").replace("cv.", "uc.") + f"={task_sql}) "
            f"OR ({task_sql} IS NULL AND cv.kind='chat' AND um.conversation_id=m.conversation_id)))) "
            "ORDER BY CASE WHEN m.from_actor LIKE 'human:%' THEN 0 ELSE 1 END,j.created,j.id",
            (*bots, who.runner_id))
        for row in candidates:
            if not privacy.message_readable(c, "bot:" + row["bot"], H.message(c, row["message_id"])):
                continue
            assignment = c.execute("SELECT task_id FROM assignment_branches WHERE bot=?", (row["bot"],)).fetchone()
            if assignment:
                message = H.message(c, row["message_id"])
                conversation = H.conversation(c, message["conversation_id"])
                if H.message_task_id(message, conversation) != assignment["task_id"]:
                    continue
            return row
        return None

    def idle_claim(self, c, who, body, key=None, selected=None):
        """The answer to a claim that changes nothing, from a read-only connection, or None when
        the claim has something to write or to hand out and must run in the write transaction.

        Runners ask every fraction of a second, so an idle fleet would otherwise take the
        database's one write lock several times a second for nothing. Everything `claim` writes
        before it looks for work is checked here: a lapsed lease to expire, a contact stamp that
        is due, an obsolete notice to suppress. Any of them, or a job to hand out, sends the claim
        on to `claim`, which repeats every check under the lock."""
        # A retry of a claim that took a job is answered from the record of it, not from here.
        if not key or c.execute("SELECT 1 FROM idempotency WHERE actor=? AND operation='/api/v2/jobs/claim' AND key=?",
                                (who.actor, key)).fetchone():
            return None
        answer = self._idle_claim(c, who, body, selected)
        if answer is not None:
            self.served_at = H.now()        # answering a runner is what `stalled` measures
        return answer

    def _idle_claim(self, c, who, body, selected=None):
        runner = self.runner(c, who)
        if not runner:
            return None
        now = H.now()
        if c.execute("SELECT 1 FROM attempts WHERE state IN ('leased','running') AND lease_until<=? LIMIT 1",
                     (now,)).fetchone():
            return None
        awake_since = self.waking(runner, now)
        if (awake_since != runner["awake_since"] or not runner["last_seen"]
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY)):
            return None
        if c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id JOIN assignments x ON x.bot=j.bot "
                     "WHERE x.runner_id=? AND j.state='queued' AND m.from_actor=? AND m.kind='notice' "
                     "AND m.body LIKE 'New task from %' LIMIT 1", (who.runner_id, H.KEEPER)).fetchone():
            return None
        why = runner_versions.paused(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        count = c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running')",
                          (who.runner_id,)).fetchone()[0]
        if count >= runner["capacity"] or not self.awake(awake_since, now):
            return {"attempt": None}
        row = self.candidate(c, who, body, runner)
        if row is not None and selected is not None:
            selected.append(row)
        return None if row else {"attempt": None}

    def claim(self, c, who, body, selected=None):
        runner = self.runner(c, who)
        changes = c.total_changes
        self.expire(c)
        if c.total_changes != changes:
            selected = None
        now = H.now()
        self.served_at = now
        # A claim is contact too, and on a machine waking for a few seconds it is the first
        # contact: the heartbeat behind it is up to 15 s away. Recording the waking period here
        # is what keeps a brief wake from taking work on a stale one.
        # A claim is contact, and on a machine waking for seconds it is the only contact: its
        # heartbeat runs on a fifteen-second timer that a wake this short never reaches. Until
        # this was recorded, the next wake compared itself against a heartbeat from before the
        # sleep, read the gap as one long stretch of presence, and took the work.
        awake_since = self.waking(runner, now)
        if awake_since != runner["awake_since"]:
            H.event(c, H.KEEPER, "runner.waking", who.runner_id,
                    {"last_seen": runner["last_seen"], "awake_since": awake_since})
        if awake_since != runner["awake_since"] or not runner["last_seen"] \
                or runner["last_seen"] <= H.shift(now, seconds=-CONTACT_EVERY):
            c.execute("UPDATE runners SET last_seen=?,awake_since=? WHERE id=?",
                      (now, awake_since, who.runner_id))
        # An initial task notice can sit in the queue while another turn finishes
        # that task. Suppress only this obsolete wake, never a later human follow-up.
        obsolete = c.execute("SELECT j.id,j.message_id,t.id AS task_id FROM jobs j "
            "JOIN assignments x ON x.bot=j.bot JOIN messages m ON m.id=j.message_id "
            f"JOIN conversations cv ON cv.id=m.conversation_id JOIN tasks t ON t.id={H.MESSAGE_TASK_SQL} "
            "WHERE x.runner_id=? AND j.state='queued' AND m.from_actor=? AND m.kind='notice' "
            "AND m.body LIKE 'New task from %' AND t.status IN ('done','closed') "
            "AND m.to_actor=t.owner AND NOT EXISTS (SELECT 1 FROM job_recovery r WHERE r.job_id=j.id "
            "AND r.attempt_id=j.attempt_id AND r.decision='resume')", (who.runner_id,H.KEEPER)).fetchall()
        for old in obsolete:
            selected = None
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (old['id'],))
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),old['message_id']))
            H.event(c,H.KEEPER,'job.suppress',old['id'],{'reason':'Task completed before initial notice delivery','task_id':old['task_id']})
        why = runner_versions.paused(c, who.runner_id)
        if why:
            return {"attempt": None, "paused": why}
        count = c.execute("SELECT count(*) FROM attempts WHERE runner_id=? AND state IN ('leased','running')",
                          (who.runner_id,)).fetchone()[0]
        if count >= runner["capacity"]:
            return {"attempt": None}
        # A machine that has only just come back cannot be trusted with a 90 s lease yet.
        # Its work keeps its place in the queue and goes out once the machine is really up.
        if not self.awake(awake_since, now):
            return {"attempt": None}
        row = selected if selected is not None else self.candidate(c, who, body, runner)
        if not row:
            return {"attempt": None}
        msg = H.message(c, row["message_id"])
        if not privacy.message_readable(c, "bot:" + row["bot"], msg):
            c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
            return {"attempt": None}
        conv = H.conversation(c, msg["conversation_id"])
        task = H.task(c, H.message_task_id(msg, conv)) if H.message_task_id(msg, conv) else None
        token = secrets.token_urlsafe(32)
        until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
        turn = H.turn_start(c, H.KEEPER, row["bot"], trigger="task" if task else msg["kind"],
                            message_id=msg["id"], task_id=task["id"] if task else None)
        aid = turn["id"]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) VALUES(?,?,?,?,?,?,'leased',?,?)",
                  (aid, row["id"], row["bot"], who.runner_id, row["generation"], digest(token), until, H.now()))
        c.execute("UPDATE jobs SET state='leased',attempt_id=? WHERE id=?", (aid, row["id"]))
        c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, conv["id"]))
        # A bot that names no runtime or model runs on the company default, resolved here the way
        # runners/assignments resolves it: the runner starts exactly what this says.
        from .shared_bots import follow
        config = providers.fill(providers.load(c, self.store.settings), follow(c, row["bot"], json.loads(
            c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0])))
        # The room's recent page. The runner forwards only what arrived since the bot last
        # answered; the bot reads further back itself with `hub conversation show` when it wants to. The
        # hub keeps no pointer to the bot's session and rebuilds nothing on its behalf.
        epoch = c.execute("SELECT updated FROM session_epochs WHERE conversation_id=?", (conv["id"],)).fetchone()
        bot_who = Identity("bot:" + row["bot"], "bot", attempt_id=aid)
        history = privacy.page(c, bot_who, conv["id"], limit=50, since=epoch["updated"] if epoch else None)["messages"]
        principal = (conv.get("owner_actor") if conv.get("scope") == "personal"
                     else msg["from_actor"] if str(msg["from_actor"]).startswith("human:") else None)
        routine = None
        if task:
            # The routine this task came from, so the bot knows the text is a standing
            # instruction and not a person's ask. What it says is already the task body.
            saved = c.execute('SELECT s.id,s.title FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id '
                              'WHERE o.task_id=?', (task['id'],)).fetchone()
            if saved:
                routine = dict(saved)
        recovery = c.execute("SELECT * FROM job_recovery WHERE job_id=? AND attempt_id=? AND decision='resume'",
                             (row['id'],row['attempt_id'])).fetchone()
        if recovery:
            msg = {**msg, 'body':msg['body'] + '\n\nOperator recovery instructions (' + recovery['actor'] + '):\n' + recovery['note']}
        # Next-run tasks ride along with whatever woke the bot: one run, one prompt, each task
        # still its own task to mark done. Only a runner that puts them in its prompt gets them.
        carried = []
        if getattr(body, "next_run", False):
            if row["bot"] == "librarian":
                from .docs import refresh_generated_docs
                refresh_generated_docs(c)  # Also covers a Librarian enabled after the upgrade.
            for item in H.next_run_tasks(c, row["bot"], exclude=task["id"] if task else None):
                if not H.task_private_readable(c, bot_who.actor, item):
                    continue
                c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (aid, item["id"]))
                carried.append({k: item.get(k) for k in ("id", "title", "body", "requester", "created")})
            if carried:
                H.event(c, H.KEEPER, "task.next-run.carried", aid,
                        {"bot": row["bot"], "tasks": [item["id"] for item in carried]})
        # Quiet notes ride the same way: every note waiting for this bot, each with when it was
        # sent, so the run can tell yesterday's from this morning's.
        notes = []
        if getattr(body, "next_run", False):
            for item in H.notes_waiting(c, row["bot"]):
                c.execute("UPDATE notes SET carried_by=? WHERE id=?", (aid, item["id"]))
                notes.append({"id": item["id"], "from": item["from_actor"], "sent": item["created"],
                              "text": item["body"]})
            if notes:
                H.event(c, H.KEEPER, "note.carried", aid, {"bot": row["bot"], "notes": [n["id"] for n in notes]})
        from .views import roster
        people = roster(c)
        inbox = P.inbox_person(row["bot"], people)
        parked = c.execute("SELECT onboarding_state FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        from .chat_goals import current
        from .subscriptions import effective
        return {"attempt": {"chat_goal": current(c, conv["id"]), "routine": routine, "id": aid,
                            "profile": effective(c, row["bot"])[0],
                            "computer_label": runner["label"],
                            # A starter bot's chat while it is `needs_setup` is its setup (runner prompt).
                            "onboarding": (parked["onboarding_state"] if parked else "") or "",
                            # The mailboxes an inbox bot's turn may ask its runner for mail access to: the one it declares
                            # (its person's email when it declares none), then the people below them.
                            "mailboxes": routines.token_mailboxes(c, row["bot"], people) if inbox else [], "next_run": carried, "notes": notes, "job_id": row["id"], "bot": row["bot"],
                            "credential_vault": c.execute("SELECT 1 FROM credential_grants WHERE subject=? AND revoked IS NULL LIMIT 1",
                                                          ("bot:" + row["bot"],)).fetchone() is not None,
                            "generation": row["generation"], "lease_until": until,
                            "lease_seconds": self.store.settings.lease_seconds, "token": token,
                            "message": msg, "conversation": conv, "task": task,
                            "history": history, "config": config, "principal": principal}}

    def attempt(self, c, who, aid, active=True, final=False):
        """`final` is a runner handing in the result of a turn it has finished. That result
        exists nowhere else, so a lapsed lease must not be grounds for refusing it: ownership
        still decides, the clock does not."""
        self.runner(c, who)
        self.served_at = H.now()
        row = c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                        "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (aid,)).fetchone()
        if not row or row["runner_id"] != who.runner_id:
            raise Problem("forbidden", "This execution attempt is not yours", 403)
        if not privacy.attempt_readable(c, "bot:" + row["bot"], aid):
            raise Problem("stale_lease", "Task access expired; stop this attempt", 409)
        current = self.current_owner(c, who, row)
        if active and current and row["state"] == "expired":
            row = self.restore(c, row) or row
        if final and row["state"] == "expired":
            return row
        if active and (row["state"] not in ("leased", "running") or row["lease_until"] <= H.now() or not current):
            raise Problem("stale_lease", "Execution ownership expired; stop this attempt", 409)
        return row

    @staticmethod
    def current_owner(c, who, row):
        """Whether this runner still owns this bot's work: the assignment names it, at the same
        generation, and the bot is active. A transfer to another Mac ends ownership, and with it
        any say over the job."""
        return bool(row["assigned_runner"] == who.runner_id and row["assigned_generation"] == row["generation"]
                    and H.bot(c, row["bot"])["state"] == "active")

    def restore(self, c, row):
        """A running attempt the server let lapse comes back when its runner reports in within
        STALL_MAX, as long as nothing else touched the job: it is still uncertain and on this
        attempt, no review decided it, and no other attempt runs for the bot. The turn on the
        Mac never stopped, so its result is worth more than a review of a phantom interruption."""
        now = H.now()
        job = c.execute("SELECT state,message_id FROM jobs WHERE id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
        if (not row["finished"] or row["finished"] <= H.shift(now, seconds=-STALL_MAX)
                or not job or job["state"] != "uncertain"
                or c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?", (row["job_id"], row["id"])).fetchone()
                or c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (row["bot"],)).fetchone()):
            return None
        until = H.shift(now, seconds=self.store.settings.lease_seconds)
        c.execute("UPDATE attempts SET state='running',finished=NULL,lease_until=? WHERE id=?", (until, row["id"]))
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        c.execute("UPDATE turns SET finished=NULL,exit=NULL,summary=NULL WHERE id=?", (row["id"],))
        current = H.message(c, job["message_id"])
        H.status_set(c, H.KEEPER, row["bot"], state="running", focus="Responding to queued work",
                     task_id=H.message_task_id(current) or "")
        H.event(c, H.KEEPER, "attempt.lease-restored", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "lapsed": row["finished"], "lease_until": until})
        return c.execute("SELECT a.*,x.runner_id AS assigned_runner,x.generation AS assigned_generation "
                         "FROM attempts a JOIN assignments x ON x.bot=a.bot WHERE a.id=?", (row["id"],)).fetchone()

    def settleable(self, c, who, row):
        """Whether a result that arrives after the lease lapsed may still settle its job. The
        test is the one `restore` uses without the clock: this runner still owns the bot, the job
        is still uncertain and still on this attempt, no review has decided it, and no other
        attempt is running for the bot. A review — a person's or `auto_reconcile`'s — always
        writes job_recovery and moves the job off `uncertain`, so a decision already taken wins
        and a late result can never undo one. Elapsed time is what hands a job from the runner to
        the sweep; it is not what makes settling safe, and the longest turns are exactly the ones
        most likely to outlive a lease."""
        job = c.execute("SELECT state FROM jobs WHERE id=? AND attempt_id=?",
                        (row["job_id"], row["id"])).fetchone()
        return bool(self.current_owner(c, who, row)
                    and job and job["state"] == "uncertain"
                    and not c.execute("SELECT 1 FROM job_recovery WHERE job_id=? AND attempt_id=?",
                                      (row["job_id"], row["id"])).fetchone()
                    and not c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')",
                                      (row["bot"],)).fetchone())

    def run_usage(self, c, row, body):
        """The turn's usage columns from the tokens its runner counted, or None when it counted none.
        The model is the runner's choice for each primary/fallback portion. Unknown values stay
        unknown; the cost is the list-price estimate, empty for a model with no price."""
        used = body.usage
        if not used:
            return None
        def part(item):
            model, runtime = item.model, item.runtime
            catalog = providers.MODEL_BY_ID.get(model) or {}
            provider = catalog.get("provider") or providers.runtime_provider(runtime or catalog.get("runtime"))
            return {"input_tokens": item.input_tokens, "cached_tokens": item.cached_tokens,
                    "output_tokens": item.output_tokens, "model": model or None, "provider": provider or None,
                    "est_cost_usd": providers.estimate_cost(model, item.input_tokens, item.cached_tokens, item.output_tokens),
                    "billing": item.billing, "runtime": runtime or None, "harness": item.harness or None,
                    "effort": item.effort or None, "profile": item.profile_used or None}
        segments = [part(item) for item in (used.segments or [used])
                    if item.input_tokens or item.cached_tokens or item.output_tokens]
        if not segments:
            return None
        result = dict(segments[-1])
        for key in ("input_tokens", "cached_tokens", "output_tokens"):
            result[key] = sum(p[key] for p in segments)
        # Legacy readers of turns see API spend only for a mixed-billing run. Usage and limits
        # consume the immutable segments and keep the subscription equivalent separate.
        billed = [p for p in segments if p["billing"] != "subscription"] or segments
        result["est_cost_usd"] = (sum(p["est_cost_usd"] for p in billed)
                                  if all(p["est_cost_usd"] is not None for p in billed) else None)
        result["billing"] = "subscription" if all(p["billing"] == "subscription" for p in segments) else "api"
        for key in ("model", "provider", "runtime", "harness", "effort", "profile"):
            if len({p[key] for p in segments}) > 1:
                result[key] = None
        result["segments"] = segments
        return result

    def file_result(self, c, row, body):
        """A result whose job this runner no longer speaks for: a review decided it, another
        attempt owns the work now, or the bot moved to another Mac. The run is recorded against
        its own attempt so the text, the tokens and the outcome survive — a reviewer can now read
        what the run actually produced — and the job is left exactly as its owner left it. The
        attempt stays `expired`, which is the true account of its lease and keeps late output
        archivable against it."""
        c.execute("UPDATE attempts SET final_text=?,result_json=? WHERE id=?",
                  (body.text, encode(body.model_dump()), row["id"]))
        H.turn_finish(c, H.KEEPER, row["id"], exit_code=body.outcome, tokens_in=body.tokens_in,
                      tokens_out=body.tokens_out, summary=body.text[:2000], usage=self.run_usage(c, row, body))
        H.event(c, H.KEEPER, "attempt.late-result", row["id"],
                {"bot": row["bot"], "job_id": row["job_id"], "outcome": body.outcome})
        return {"attempt_id": row["id"], "outcome": body.outcome, "message": None, "filed": True}

    def renew(self, c, who, aid):
        self.attempt(c, who, aid)
        until = H.shift(H.now(), seconds=self.store.settings.lease_seconds)
        c.execute("UPDATE attempts SET lease_until=? WHERE id=?", (until, aid))
        return {"lease_until": until, "lease_seconds": self.store.settings.lease_seconds}

    def started(self, c, who, aid, body):
        row = self.attempt(c, who, aid)
        if row["state"] != "leased":
            raise Problem("already_started", "This attempt was already started", 409)
        c.execute("UPDATE attempts SET state='running',started=?,thread_id=? WHERE id=?",
                  (H.now(), body.thread_id, aid))
        c.execute("UPDATE turns SET thread_id=? WHERE id=?", (body.thread_id, aid))
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (row["job_id"],))
        branch = c.execute("SELECT id,phase FROM assignment_branches WHERE bot=?", (row["bot"],)).fetchone()
        if branch and branch["phase"] == "preparing":
            c.execute("UPDATE assignment_branches SET phase='working',revision=revision+1,updated=? WHERE id=?",
                      (H.now(), branch["id"]))
            c.execute("INSERT INTO assignment_branch_events VALUES(?,?,?,?,?,?)",
                      (H.new_id(), branch["id"], H.KEEPER, "working", encode({"reason": "first turn started"}), H.now()))
        from .shared_bots import follow
        config = follow(c, row["bot"], json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()[0]))
        runtime, model = providers.bot_choice(c, self.store.settings, config)
        c.execute("INSERT INTO bot_sessions(bot,runtime,model,thread_id,runner_id,tokens_in,updated) "
                  "VALUES(?,?,?,?,?,0,?) ON CONFLICT(bot,runtime,model) DO UPDATE SET "
                  "thread_id=excluded.thread_id,runner_id=excluded.runner_id,updated=excluded.updated",
                  (row["bot"], runtime, model, body.thread_id, who.runner_id, H.now()))
        msg = c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0]
        H.mark_delivered(c, H.KEEPER, msg)
        H.status_set(c, H.KEEPER, row["bot"], state="running", focus="Responding to queued work",
                     task_id=H.message_task_id(H.message(c, msg)) or "")
        return {"started": True}

    def inputs(self, c, who, aid):
        """Add eligible queued work to a running turn when its runtime can steer.

        A follow-up in the turn's own conversation belongs to that turn. Across conversations,
        only a waited bot question may interrupt it because that has its own explicit answer
        channel. The runner calls this endpoint only for hosts that advertise steering support.
        Re-deliver until acknowledged; a crash remains uncertain, not a new turn.
        """
        attempt = self.attempt(c, who, aid)
        if attempt["state"] != "running":
            return {"messages": []}
        origin = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0])
        origin_task = H.message_task_id(origin, H.conversation(c, origin["conversation_id"]))
        for row in c.execute("SELECT m.* FROM jobs j JOIN messages m ON m.id=j.message_id "
                             "WHERE j.bot=? AND j.state='queued' AND coalesce(json_extract(m.refs_json,'$.command'),0)!=1 "
                             "AND (m.conversation_id=("
                             "SELECT active_message.conversation_id FROM attempts active_attempt "
                             "JOIN jobs active_job ON active_job.id=active_attempt.job_id "
                             "JOIN messages active_message ON active_message.id=active_job.message_id "
                             "WHERE active_attempt.id=?) OR (m.kind='ask' AND m.wait_s>0 "
                             "AND m.from_actor LIKE 'bot:%')) ORDER BY m.rowid LIMIT 10",
                             (attempt["bot"], aid)).fetchall():
            if not privacy.message_readable(c, "bot:" + attempt["bot"], row):
                continue
            incoming_task = H.message_task_id(H.message(c, row["id"]), H.conversation(c, row["conversation_id"]))
            if incoming_task and incoming_task != origin_task and row["kind"] == "notice":
                continue                    # a new task gets its own run, even in the same bot room
            if any(tid != origin_task and H.task_private(c, H.task(c, tid))
                   for tid in privacy.message_tasks(c, row, include_run=False)):
                # Folding another task's private message into this run would make the run private
                # too, shutting out whoever started it. It waits queued and gets its own run.
                continue
            if attempt["bot"] == H.FLEET_MAINTAINER:
                refs = json.loads(row["refs_json"] or "{}")
                origin = c.execute("SELECT j.message_id,m.from_actor FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.id=?", (attempt["job_id"],)).fetchone()
                origin_message = H.message(c, origin["message_id"])
                task_id = H.message_task_id(origin_message)
                task = H.task(c, task_id) if task_id else None
                requester = task["requester"] if task else origin["from_actor"]
                cancelled = bool(task and task["status"] == "closed" and refs.get("task") == task_id)
                related = (row["in_reply_to"] == origin["message_id"] or refs.get("turn_id") == aid
                           or (task and task.get("request_id") and row["in_reply_to"] == task["request_id"])
                           or row["kind"] in ("answer", "steer") or refs.get("credential_saved"))
                if (str(row["from_actor"]).startswith("human:") and (row["from_actor"] != requester or not related)
                        or row["kind"] == "notice" and not cancelled):
                    continue
            # A message keeps one attempt_inputs row (message_id is the key). Its job being queued
            # again means a finished run handed it back on purpose (a requeued or deferred turn, a
            # resumed review), so that old row moves to this run, unacknowledged, and is delivered
            # here once. A row still held by another live run is left alone, and one already on
            # this run keeps its acknowledgement so it is not delivered twice.
            prior = c.execute("SELECT i.attempt_id,a.state FROM attempt_inputs i "
                              "LEFT JOIN attempts a ON a.id=i.attempt_id WHERE i.message_id=?",
                              (row["id"],)).fetchone()
            if prior and prior["attempt_id"] != aid and prior["state"] in ("leased", "running"):
                continue
            if prior and prior["attempt_id"] != aid:
                # The earlier run did read this message, so it keeps the message's tasks for
                # privacy (attempt_tasks reads this event) once the row is no longer its own.
                privacy.record_moved_input(c, prior["attempt_id"], aid, row)
            c.execute("INSERT INTO attempt_inputs VALUES(?,?,NULL) ON CONFLICT(message_id) DO UPDATE SET "
                      "acked_at=CASE WHEN attempt_id=excluded.attempt_id THEN acked_at END,"
                      "attempt_id=excluded.attempt_id", (aid, row["id"]))
            c.execute("INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)", (aid, row["conversation_id"]))
            c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id=?", (aid, row["id"]))
        ids = c.execute("SELECT message_id FROM attempt_inputs WHERE attempt_id=? AND acked_at IS NULL", (aid,)).fetchall()
        # `attempt_id` lets the response check read these as the bot, as for the turn itself:
        # each message was just checked readable by the bot, and a private task's message is
        # never readable by the computer's own identity.
        return {"attempt_id": aid, "messages": [m for row in ids if (m := H.message(c, row[0]))
                             and privacy.message_readable(c, "bot:" + attempt["bot"], m)]}

    def acknowledge_input(self, c, who, aid, mid):
        self.attempt(c, who, aid)
        row = c.execute("SELECT * FROM attempt_inputs WHERE attempt_id=? AND message_id=?", (aid, mid)).fetchone()
        if not row:
            raise Problem("not_found", "This input is not assigned to this execution", 404)
        c.execute("UPDATE attempt_inputs SET acked_at=coalesce(acked_at,?) WHERE attempt_id=? AND message_id=?", (H.now(), aid, mid))
        H.mark_delivered(c, H.KEEPER, mid)
        return {"acknowledged": True}

    def events(self, c, who, aid, body):
        # Late output is archived with its historical attempt, never applied to current work.
        row = self.attempt(c, who, aid, active=False)
        seq = row["last_seq"]
        for event in body.events:
            payload = encode(event.payload)
            existing = c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? AND seq=?",
                                 (aid, event.seq)).fetchone()
            if existing:
                if existing["kind"] != event.kind or existing["payload_json"] != payload:
                    raise Problem("event_conflict", "An event sequence was reused with different content", 409)
                continue
            if event.seq != seq + 1:
                raise Problem("event_gap", f"Expected event {seq + 1}", 409)
            if row["state"] in ("completed", "failed", "interrupted"):
                raise Problem("terminal", "Cannot append new output to a completed attempt", 409)
            c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload_json,created) VALUES(?,?,?,?,?)",
                      (aid, event.seq, event.kind, payload, H.now()))
            if event.kind == "goal" and row["lease_until"] > H.now() and row["state"] != "expired":
                from .chat_goals import report
                report(c, row, event.payload)
            seq = event.seq
        c.execute("UPDATE attempts SET last_seq=? WHERE id=?", (seq, aid))
        return {"ack_seq": seq, "historical": row["lease_until"] <= H.now() or row["state"] == "expired"}

    def complete(self, c, who, aid, body):
        row = self.attempt(c, who, aid, final=True)
        # `attempt` has already given `restore` its chance. What is left is a finished turn whose
        # lease lapsed under it: settle the job with it when the job is still waiting, and file it
        # against the attempt when it is not. Refusing it was how a run that opened three pull
        # requests came back as a phantom interruption.
        late = row["state"] == "expired"
        if not late and row["state"] != "running":
            raise Problem("not_started", "Acknowledge start before completing work", 409)
        if row["last_seq"] != body.last_seq:
            raise Problem("event_gap", "Upload all events before completing the attempt", 409)
        if late and not self.settleable(c, who, row):
            return self.file_result(c, row, body)
        msg = H.message(c, c.execute("SELECT message_id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()[0])
        conv = H.conversation(c, msg["conversation_id"])
        actor = "bot:" + row["bot"]
        reply = None
        if body.text and body.outcome == "completed":
            already = c.execute("SELECT id FROM messages WHERE from_actor=? AND in_reply_to=?",
                                (actor, msg["id"])).fetchone()
            # Tool sends need not name in_reply_to. Their authenticated mutation receipt,
            # unlike caller-supplied refs or matching historical text, proves this attempt sent it.
            same = c.execute(
                "SELECT m.id FROM idempotency i JOIN messages m "
                "ON m.id=coalesce(json_extract(i.response_json,'$.id'),json_extract(i.response_json,'$.message.id')) "
                "WHERE i.actor=? AND i.operation IN (?,?) "
                "AND m.conversation_id=? AND m.from_actor=? AND m.body=?",
                (actor + ":" + aid, "/api/v2/messages", "/api/v2/conversations/" + conv["id"] + "/messages",
                 conv["id"], actor, body.text)).fetchone()
            task = H.task(c, H.message_task_id(msg, conv)) if H.message_task_id(msg, conv) else None
            if not already and not same and (msg["kind"] in ("say", "ask", "steer") or task):
                target = msg["from_actor"]
                replyable = not str(target).startswith("bot:") or msg["kind"] == "ask"
                if target == H.KEEPER and task:
                    humans = [p for p in conv.get("participants") or [] if str(p).startswith("human:")]
                    target = ((conv.get("owner_actor") if conv.get("scope") == "personal" else None)
                              or (humans[0] if humans else None) or task["requester"])
                    # A task in the bot's chat room always delivers. A dedicated task thread
                    # still waits until the owner has marked the work done.
                    if conv.get("kind") == "chat":
                        replyable = target not in (H.KEEPER, actor)
                    else:
                        replyable = actor == task["owner"] and task["status"] == "done"
                if target not in (H.KEEPER, actor) and replyable:
                    refs = {"turn_id": aid, **({"task": task["id"]} if task else {})}
                    privacy.require_destination(c, Identity(actor, "bot", attempt_id=aid), target, conv["id"], refs, msg)
                    reply = H.answer(c, actor, msg["id"], body.text) if msg["kind"] == "ask" else H.say(
                        c, actor, target, body.text, conversation_id=conv["id"],
                        in_reply_to=msg["id"], refs=refs)
        if reply:
            # Which run wrote this and which messages it took in (the one it was started for and
            # what was folded into it), kept in the reply's refs for the frontends to show.
            handled = [msg["id"]] + [r[0] for r in c.execute(
                "SELECT i.message_id FROM attempt_inputs i JOIN messages m ON m.id=i.message_id "
                "WHERE i.attempt_id=? AND m.conversation_id=? ORDER BY m.rowid", (aid, msg["conversation_id"]))]
            refs = {**(H.message(c, reply["id"]).get("refs") or {}),
                    "run": {"job_id": row["job_id"], "attempt_id": aid}, "answers": handled}
            c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode(refs), reply["id"]))
        c.execute("UPDATE attempts SET state=?,finished=?,final_text=?,result_json=? WHERE id=?",
                  (body.outcome, H.now(), body.text, encode(body.model_dump()), aid))
        # A usage-limited turn did nothing and had no effects, so the job goes back in the
        # queue however often it happens; the bot cools off (claim() honours the window) and
        # a third limit in a row waits longer and tells a person in Needs attention. Only
        # interrupted or failed work, which may have acted, is held for review.
        limited = body.outcome != "completed" and body.limited
        # Same reasoning, different cause: the runner reports `retryable` when the runtime was
        # refused before the turn began because it could not renew its own sign-in, and only
        # when that turn produced no message and used no tool. Nothing happened, so requeue it
        # rather than blocking the bot behind an uncertain job until a person clears it.
        retryable = body.outcome != "completed" and body.retryable and not limited
        # The general case of the same thing: a turn that failed having streamed nothing at all,
        # no text, no tool, no token count, only the runtime's own error. When a runtime
        # answered every turn of several bots with "Internal error" at the first event, the
        # jobs it left `uncertain` held those bots until a person cleared each one. Nothing ran,
        # so it goes back in the queue; a job that fails this way NO_EFFECT_TRIES times is not
        # unlucky but broken, and waits for a person as before.
        # Codex and Claude never report their tool calls, so for them silence is only evidence
        # together with speed: the Grok failures came back within a second or two of starting.
        silent = False
        if body.outcome == "failed" and not limited and not retryable:
            kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM attempt_events WHERE attempt_id=?", (aid,))}
            tries = c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (row["job_id"],)).fetchone()[0]
            quick = (row["started"] or row["created"]) >= H.shift(H.now(), seconds=-NO_EFFECT_WITHIN)
            silent = kinds <= SILENT_KINDS and quick and tries < NO_EFFECT_TRIES
        # A refused key or sign-in did nothing either, but only a changed credential fixes it:
        # the job waits queued, and the runner takes no work for that runtime meanwhile, so it
        # neither loops (silent) nor blocks the bot behind a review.
        rejected = body.outcome == "failed" and body.auth_rejected is not None
        if retryable and body.subscription_unavailable:
            from .repositories import save_metadata
            config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
            config = json.loads(config[0]) if config else {}
            primary_runtime = providers.bot_choice(c, self.store.settings, config)[0]
            save_metadata(c, f"subscription-unavailable:{row['bot']}",
                          {**body.subscription_unavailable.model_dump(), "runner_id": row["runner_id"],
                           "primary_runtime": primary_runtime, "primary_harness": config.get("harness")})
        requeue = limited or retryable or silent or rejected
        c.execute("UPDATE jobs SET state=? WHERE id=?",
                  ("completed" if body.outcome == "completed" else "queued" if requeue else "uncertain", row["job_id"]))
        turn_task = H.message_task_id(msg, conv)
        for incoming in c.execute("SELECT i.message_id,m.kind FROM attempt_inputs i "
                                  "JOIN messages m ON m.id=i.message_id WHERE i.attempt_id=?", (aid,)).fetchall():
            answered = incoming["kind"] != "ask" or bool(H.answers_to(c, [incoming["message_id"]]))
            message = H.message(c, incoming["message_id"])
            task_id = H.message_task_id(message, H.conversation(c, message["conversation_id"]))
            deferred = False
            if task_id and task_id != turn_task:
                task = H.task(c, task_id)
                outcome = c.execute("SELECT 1 FROM task_events WHERE task_id=? AND actor=? AND ts>=? "
                                    "AND (field='note' OR (field='status' AND new IN ('done','closed','declined'))) LIMIT 1",
                                    (task_id, actor, row["started"] or row["created"])).fetchone()
                deferred = bool(task and task["status"] in H.ACTIVE_STATUSES and not outcome)
            state = ("queued" if deferred else "completed" if body.outcome == "completed" and answered else
                     "queued" if requeue else "uncertain")
            c.execute("UPDATE jobs SET state=?,attempt_id=CASE WHEN ? THEN NULL ELSE attempt_id END WHERE message_id=?",
                      (state, deferred, incoming["message_id"]))
        if body.outcome == "completed":
            # Notices that queued in this turn's conversation before it was claimed were in the
            # room page it was given, so the turn has read them. Leaving their jobs queued costs
            # one more run each to read the same thing again: a bot could accumulate
            # dozens of Slack digests and hourly occurrences stacked this way. A bot's chat
            # room carries notices for many tasks, so only the ones about this turn's own task
            # (or, for a turn with no task, the ones about none) count as read.
            turn_task = H.message_task_id(msg, conv)
            for extra in c.execute("SELECT j.id,j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                                   "WHERE j.bot=? AND j.state='queued' AND m.kind='notice' "
                                   "AND m.conversation_id=? AND m.created<=? AND j.id<>?",
                                   (row["bot"], conv["id"], row["created"], row["job_id"])).fetchall():
                if H.message_task_id(H.message(c, extra["message_id"]), conv) != turn_task:
                    continue
                c.execute("UPDATE jobs SET state='completed',attempt_id=? WHERE id=?", (aid, extra["id"]))
                H.mark_delivered(c, H.KEEPER, extra["message_id"])
                H.event(c, H.KEEPER, "job.coalesce", extra["id"], {"attempt_id": aid, "job_id": row["job_id"]})
        H.turn_finish(c, H.KEEPER, aid, exit_code=body.outcome, tokens_in=body.tokens_in,
                      tokens_out=body.tokens_out, summary=body.text[:2000], usage=self.run_usage(c, row, body))
        usage_limits.after_run(c, row["bot"], self.store.settings.owner_email)
        config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row["bot"],)).fetchone()
        runtime, model = providers.bot_choice(c, self.store.settings, json.loads(config[0]) if config else {})
        if row["thread_id"] and body.tokens_in is not None:
            c.execute("UPDATE bot_sessions SET tokens_in=?,updated=? WHERE bot=? AND runtime=? AND model=? AND thread_id=?",
                      (int(body.tokens_in), H.now(), row["bot"], runtime, model, row["thread_id"]))
        if rejected:
            self.mark_rejected(c, row["runner_id"], body.auth_rejected.runtime, body.auth_rejected.reason, body.profile_used)
            H.event(c, H.KEEPER, "attempt.auth_rejected", aid,
                    {"bot": row["bot"], "runtime": body.auth_rejected.runtime, "reason": body.auth_rejected.reason})
        if body.fallback:
            # The runner ran this turn on the bot's fallback harness: countable here, and
            # named in Runs, so a primary that keeps failing is noticed.
            H.event(c, H.KEEPER, "attempt.fallback", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "fallback": body.fallback,
                     "outcome": body.outcome})
        if limited:
            streak = limit_streak(c, row["bot"])
            hit = H.now()
            retry = H.shift(hit, seconds=LIMIT_COOLDOWN_LONG if streak >= LIMIT_STRIKES else LIMIT_COOLDOWN)
            focus = (f"{runtime} usage limit at {hit[11:16]}" + (", third in a row" if streak >= LIMIT_STRIKES else "")
                     + f"; retrying after {retry[11:16]} UTC")
            H.status_set(c, H.KEEPER, row["bot"], state="limited", focus=focus)
            # Every limit restarts the cooldown; status_set keeps `since` while the state stays the same.
            c.execute("UPDATE bot_status SET since=? WHERE bot=?", (hit, row["bot"]))
            H.event(c, H.KEEPER, "attempt.limited", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime, "streak": streak,
                     "retry_after": retry})
        elif silent:
            H.status_set(c, H.KEEPER, row["bot"], state="idle",
                         focus=f"{runtime} failed before the run began at {H.now()[11:16]} UTC; the run is back in the queue")
            H.event(c, H.KEEPER, "attempt.no_effect_retry", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime})
        elif retryable:
            # Not a crash and not a limit: the box's sign-in lapsed. Say so plainly, leave the
            # bot claimable, and let the requeued job run again as soon as it is handed out.
            H.status_set(c, H.KEEPER, row["bot"], state="idle",
                         focus=f"{runtime} sign-in could not be renewed at {H.now()[11:16]} UTC; the run is back in the queue")
            H.event(c, H.KEEPER, "attempt.sign_in_retry", aid,
                    {"bot": row["bot"], "job_id": row["job_id"], "runtime": runtime})
        else:
            # Only this run's outcome decides. A run held from earlier keeps its own "saved for
            # later" item in Health until reviewed; counting it here turned every clean run after
            # it into a crash, so a working bot looked broken.
            healthy = body.outcome == "completed"
            H.status_set(c, H.KEEPER, row["bot"], state="idle" if healthy else "crashed",
                         focus="" if healthy else "One run stopped; saved for later")
        return {"attempt_id": aid, "outcome": body.outcome, "message": reply}

    def retry(self, c, who, job_id, body):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not self.auth.operator(c, who, row["bot"]):
            raise Problem("forbidden", "Only this bot's operator can retry uncertain work", 403)
        if not body.acknowledge_uncertain_effects or row["state"] != "uncertain":
            raise Problem("uncertain", "Reconcile any external effects before retrying", 409)
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (job_id,))
        H.event(c, who.actor, "job.retry", job_id)
        return {"job_id": job_id, "state": "queued"}


    def reviewer(self, c, who, bot):
        """The bot's operator, or BotOps. A stopped run is recovered by BotOps,
        not by a person filling in a review form; BotOps' note and decision are audited like theirs."""
        return who.actor == "bot:botops" or self.auth.operator(c, who, bot)

    def review_jobs(self, c, who, bot):
        if not self.reviewer(c, who, bot):
            raise Problem("forbidden", "Only this bot's operator can review interrupted work", 403)
        jobs = []
        for row in c.execute("SELECT * FROM jobs WHERE bot=? AND state='uncertain' ORDER BY created", (bot,)):
            message = H.message(c, row['message_id'])
            if (not privacy.message_readable(c, privacy.actor(who), message)
                    or row['attempt_id'] and not privacy.attempt_readable(c, privacy.actor(who), row['attempt_id'])):
                continue
            conv = H.conversation(c, message['conversation_id'])
            # An operator must be able to unblock their own bot whatever it was doing. That is
            # not a way into a person's conversation: one of those is reconcilable but arrives
            # without its words.
            readable = self.readable(c, who, conv)
            task = H.task(c, H.message_task_id(message, conv)) if H.message_task_id(message, conv) else None
            attempt = c.execute("SELECT id,state,created,started,finished,final_text FROM attempts WHERE id=?",
                                (row['attempt_id'],)).fetchone()
            events = c.execute("SELECT seq,kind,payload_json FROM attempt_events WHERE attempt_id=? "
                               "ORDER BY seq DESC LIMIT 50", (row['attempt_id'],)).fetchall()
            output = [{'seq': e['seq'], 'kind': e['kind'],
                       'text': str(json.loads(e['payload_json']).get('text', ''))[:4000]}
                      for e in reversed(events)]
            if any(e['kind'] == 'tool' for e in events):
                reason = ("The run stopped after using a tool. Tico cannot tell whether the tool "
                          "finished, so retrying automatically could repeat the action.")
            else:
                reason = ("The run stopped before it returned an answer. Its runner did not record "
                          "enough detail for Tico to prove that retrying automatically is safe.")
            jobs.append({'id':row['id'], 'attempt_id':row['attempt_id'], 'readable':readable,
                         'task':{'id':task['id'],'title':task['title'],'status':task['status']} if task and readable else None,
                         'request':message['body'] if readable else '',
                         'attempt':(dict(attempt) if readable else
                                    {**dict(attempt), 'final_text': ''}) if attempt else None,
                         'output':output if readable else [], 'reason':reason})
        return {'jobs':jobs}

    def reconcile_job(self, c, who, job_id, body):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not self.reviewer(c, who, row['bot']):
            raise Problem("forbidden", "Only this bot's operator or BotOps can review interrupted work", 403)
        if (not body.acknowledge_uncertain_effects or row['state'] != 'uncertain'
                or row['attempt_id'] != body.attempt_id):
            raise Problem("uncertain", "This run changed or its external effects have not been reviewed", 409)
        if c.execute("SELECT 1 FROM attempts WHERE bot=? AND state IN ('leased','running')", (row['bot'],)).fetchone():
            raise Problem("active_attempt", "Wait for the active run to finish before reconciling", 409)
        note = body.note.strip()
        if len(note) < 20:
            raise Problem("review_note", "Describe completed work and what should happen next", 422)
        return self._decide(c, row, body.attempt_id, body.decision, note, who.actor)

    def _decide(self, c, row, attempt_id, decision, note, actor, *, action='job.reconcile'):
        """Record one decision on an uncertain job: resume queues it again, dismiss releases it."""
        job_id = row['id']
        state = 'queued' if decision == 'resume' else 'cancelled'
        c.execute("INSERT INTO job_recovery VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                  "attempt_id=excluded.attempt_id,decision=excluded.decision,note=excluded.note,"
                  "actor=excluded.actor,created=excluded.created",
                  (job_id,attempt_id,decision,note,actor,H.now()))
        if decision == 'dismiss':
            c.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?) WHERE id=?", (H.now(),row['message_id']))
        c.execute("UPDATE jobs SET state=? WHERE id=?", (state,job_id))
        H.event(c, actor, action, job_id, {'attempt_id':attempt_id, 'decision':decision, 'note':note})
        if not c.execute("SELECT 1 FROM jobs WHERE bot=? AND state='uncertain'", (row['bot'],)).fetchone():
            H.status_set(c, H.KEEPER, row['bot'], state='idle', focus='Stopped run reviewed')
        return {'job_id':job_id, 'state':state}

    def hold_unrunnable(self, c):
        """Keep one queued notice per conversation for a bot no machine runs.

        A bot with no assignment never claims anything, so every routine occurrence and digest
        for it only lengthens a queue: a bot created before its
        repository existed can have dozens of hourly jobs a day later. The newest stays
        queued for the machine that eventually takes it on, and the older ones are cancelled;
        their messages stay unread in the bot's inbox and conversation, so nothing is lost.
        """
        newest, held = {}, []
        for row in c.execute(
                "SELECT j.id,j.bot,j.message_id FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE j.state='queued' AND m.kind='notice' "
                "AND NOT EXISTS(SELECT 1 FROM assignments a WHERE a.bot=j.bot) "
                "ORDER BY j.created DESC, j.id DESC").fetchall():
            with isolated(c, "hold_unrunnable", row["id"]):
                # A chat room carries notices for many tasks: one kept per task, not per room.
                msg = H.message(c, row["message_id"])
                key = (row["bot"], msg["conversation_id"],
                       H.message_task_id(msg, H.conversation(c, msg["conversation_id"])))
                if key in newest:
                    held.append(row["id"])
                else:
                    newest[key] = row["id"]
        for job_id in held:
            with isolated(c, "hold_unrunnable", job_id):
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job_id,))
                H.event(c, H.KEEPER, "job.suppress", job_id,
                        {"reason": "Bot has no machine; a newer notice about the same task stays queued"})
        # An archived bot never runs again: its queued jobs only sit there. The
        # messages stay in its conversations; the job is let go.
        for row in c.execute("SELECT j.id FROM jobs j JOIN bots b ON b.slug=j.bot "
                             "WHERE j.state='queued' AND b.state='archived'").fetchall():
            with isolated(c, "hold_unrunnable", row["id"]):
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (row["id"],))
                H.event(c, H.KEEPER, "job.suppress", row["id"], {"reason": "Bot is archived"})
                held.append(row["id"])
        return held

    def release_deploy_drains(self, c):
        """Undrain the bots a deploy drained and never gave back.

        Runs from the scheduler's tick. A release's migration can bump several
        bots' config revision mid-deploy, the deploy's restore read that as a person's change and
        kept them drained, and they claimed nothing for eleven hours. A bot is released when its
        last drain was the deploy's, more than DEPLOY_DRAIN_MAX ago, and no person has drained or
        paused it since. A deploy's drain record that old is dropped too, so a retry of that
        release drains afresh instead of reusing a list this sweep has already undone.
        """
        now = H.now()
        cutoff, horizon = H.shift(now, seconds=-DEPLOY_DRAIN_MAX), H.shift(now, days=-7)
        released = []
        for row in c.execute("SELECT b.slug FROM bots b JOIN bot_control ctl ON ctl.bot=b.slug "
                             "WHERE ctl.draining=1 AND b.state='active' ORDER BY b.slug").fetchall():
            with isolated(c, "release_deploy_drains", row["slug"]):
                drained = c.execute("SELECT ts, detail_json FROM events WHERE actor='system:deploy' AND action='bot.drain' "
                                    "AND target=? ORDER BY ts DESC LIMIT 1", (row['slug'],)).fetchone()
                if not drained or drained['ts'] > cutoff or drained['ts'] < horizon:
                    continue
                if c.execute("SELECT 1 FROM events WHERE actor>='human:' AND actor<'human;' "
                             "AND action IN ('bot.drain','bot.pause','bot.resume') AND target=? AND ts>=? LIMIT 1",
                             (row['slug'], drained['ts'])).fetchone():
                    continue
                c.execute("UPDATE bot_control SET draining=0 WHERE bot=?", (row['slug'],))
                c.execute("UPDATE bot_config SET revision=revision+1 WHERE bot=?", (row['slug'],))
                release = (json.loads(drained['detail_json'] or '{}') or {}).get('release')
                H.event(c, H.KEEPER, 'operator.deployment-undrain', row['slug'],
                        {'release': release, 'reason': 'stale deploy drain', 'drained_at': drained['ts']})
                released.append(row['slug'])
        for key, value in c.execute("SELECT key, value_json FROM registry_metadata "
                                    "WHERE key LIKE 'deployment-drain:%'").fetchall():
            with isolated(c, "release_deploy_drains", key):
                stamps = [r.get('at') for r in json.loads(value or '[]') if isinstance(r, dict)]
                if stamps and all(stamps) and max(stamps) <= cutoff:
                    c.execute("DELETE FROM registry_metadata WHERE key=?", (key,))
        return released

    def auto_reconcile(self, c):
        """Settle the interrupted work nobody needs to look at, so a person is only asked about
        a run that actually did something.

        Runs from the scheduler's tick, after `expire`. Two decisions are safe without a person:

        - **Dismiss** when the task the delivery was for is already done or closed. There is
          nothing left to deliver, and rows that held bots were often this.
        - **Resume** when the attempt recorded no tool call and consumed no approval: it read,
          it thought, it was cut off. Running it again repeats nothing. This needs a host that
          reports its tool calls; on claude and codex no `tool` event is ever sent, so silence
          there is not evidence and the run waits for a person.

        A run that used tools stays for a person, because the hub cannot see what a tool did on
        the Mac. The runner's own `restore` gets the first STALL_MAX to reclaim a run that is
        still alive; this sweep only looks after that.

        Resuming is offered once. A job whose second attempt is also uncertain is not a turn
        that was unlucky, it is one that keeps ending the same way, and running it a third time
        is how a bot once opened the same request eighteen times.
        """
        cutoff = H.shift(H.now(), seconds=-STALL_MAX)
        decided = []
        rows = c.execute("SELECT j.*, a.started, a.finished FROM jobs j LEFT JOIN attempts a ON a.id=j.attempt_id "
                         "WHERE j.state='uncertain' AND coalesce(a.finished, j.created) <= ? "
                         "AND NOT EXISTS(SELECT 1 FROM attempts t WHERE t.bot=j.bot AND t.state IN ('leased','running')) "
                         "ORDER BY j.created", (cutoff,)).fetchall()
        for row in rows:
            with isolated(c, "auto_reconcile", row["id"]):
                msg = H.message(c, row['message_id'])
                conv = H.conversation(c, msg['conversation_id']) if msg else None
                task_id = H.message_task_id(msg, conv) if msg else None
                task = H.task(c, task_id) if task_id else None
                if not privacy.message_readable(c, 'bot:' + row['bot'], msg):
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss',
                                               'Task access was revoked.', H.KEEPER, action='job.auto_reconcile'))
                    continue
                if task and task['status'] in ('done', 'closed'):
                    note = (f"Dismissed automatically: the task \"{task['title']}\" was already {task['status']} "
                            "when the interrupted delivery was reviewed, so there was nothing left to deliver.")
                    decided.append(self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                                action='job.auto_reconcile'))
                    continue
                if c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (row['id'],)).fetchone()[0] > 1:
                    # Stopped twice the same way: not bad luck. Let it go and hand the cause to BotOps.
                    note = ("Dismissed automatically: this request stopped partway twice, so it is not retried "
                            "again. BotOps is looking into why.")
                    decision = self._decide(c, row, row['attempt_id'], 'dismiss', note, H.KEEPER,
                                            action='job.auto_reconcile')
                    if privacy.message_readable(c, 'bot:' + H.FLEET_MAINTAINER, msg):
                        self._tell_maintainer(c, row, msg)
                    decided.append(decision)
                    continue
                config = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (row['bot'],)).fetchone()
                declared = json.loads(config[0]) if config else {}
                tools = c.execute("SELECT count(*) FROM attempt_events WHERE attempt_id=? AND kind='tool'",
                                  (row['attempt_id'],)).fetchone()[0]
                window = (row['started'] or row['created'], row['finished'] or H.now())
                consumed = c.execute("SELECT count(*) FROM approvals WHERE requested_by=? AND consumed_at IS NOT NULL "
                                     "AND consumed_at BETWEEN ? AND ?", ('bot:' + row['bot'], *window)).fetchone()[0]
                if reports_tool_calls(declared, declared.get('runtime')) and not tools and not consumed:
                    note = ("Resumed automatically: the interrupted run recorded no tool call and consumed no "
                            "approval before it stopped, so running it again repeats nothing.")
                else:
                    # Never ask a person to reconstruct what a run did. The bot has
                    # the context to check; it gets what the run saved and is told not to repeat an
                    # outside action. Sends and spending still go through their own approval gates.
                    saved = self._saved_text(c, row['attempt_id'])
                    note = ("Your last run on this request stopped partway, and it may already have done some of "
                            "the work. Before doing anything, check what is already done (messages sent, tasks "
                            "changed, commits pushed, bots changed) and finish only what is left. Never repeat an "
                            "outside action that already happened."
                            + (f"\n\nWhat the stopped run saved:\n{saved}" if saved else ""))
                decided.append(self._decide(c, row, row['attempt_id'], 'resume', note, H.KEEPER,
                                            action='job.auto_reconcile'))
        return decided

    def _saved_text(self, c, attempt_id, limit=1500):
        """The whole messages a stopped run produced (not its streaming fragments), newest last."""
        attempt = c.execute("SELECT final_text FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if attempt and attempt["final_text"]:
            return attempt["final_text"][-limit:]
        texts = []
        for event in c.execute("SELECT payload_json FROM attempt_events WHERE attempt_id=? AND kind='message' "
                               "ORDER BY seq", (attempt_id,)):
            text = (json.loads(event["payload_json"] or "{}") or {}).get("text")
            if text and (not texts or texts[-1] != text):
                texts.append(text)
        return "\n".join(texts)[-limit:]

    def _tell_maintainer(self, c, row, msg):
        """One task for BotOps per bot and day when a request keeps stopping."""
        maintainer = H.FLEET_MAINTAINER
        if row['bot'] == maintainer or not H.bot(c, maintainer):
            return
        title = f"Find why {row['bot']}'s runs keep stopping"
        if c.execute("SELECT 1 FROM tasks WHERE title=? AND status IN ('open','doing','waiting','review','ready')",
                     (title,)).fetchone():
            return
        request = ((msg or {}).get('body') or '')[:300]
        try:
            H.task_create(c, H.KEEPER, title,
                          f"Job {row['id']} stopped partway twice and was dismissed. The request was: {request}\n"
                          f"Read its attempts (`hub sql`), fix the cause, and send the request again if it still matters.",
                          H.bot_actor(maintainer))
        except H.Refused:
            pass
