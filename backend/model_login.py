"""Signing a model CLI in on a runner from the browser.

The Computer operator, owner or admin asks for a login for one runtime on one computer; the runner starts the CLI and reports
what the CLI itself prints (a link and a one-time code, or a prompt for a pasted code). This module
keeps that relay and nothing else. It never sees the credential the CLI ends up with: the CLI
writes it on the runner's disk, and the runner only reports whether readiness turned green.

What is stored is a link, a one-time code, a few redacted CLI message lines and timestamps, for
at most `LIFETIME` seconds. A code the person pastes back is held only until the runner has
taken it, then dropped.

The same relay signs a login out, switches it to another account, and puts the key the computer is given
back (`kind`). A computer's own login that a person signed out stays signed out (`model_login_cleared`):
the server stops handing that computer the key it is given from Settings > Models for it, and the runner keeps its
own mark, until a person signs in again or chooses to use the shared key (`use_shared_key`, the one job that lifts a
v0.6.0-rc.1 runner's own marks). A key deleted in Settings > Models or Credentials clears it the same way
(`mark_key_cleared`).

A model key is a subscription, added and given to computers in Settings > Models (backend/model_subscriptions.py).
The Settings > Computers key jobs (Add, Replace and Clear key, Switch to subscription, Switch to API key) are gone: a
page or client that still asks is told where keys are changed now (`MOVED`).

Remove (`remove`) takes a named subscription's login for one tool off a computer: the runner signs it out there and,
once nothing else is signed in on that subscription, deletes the subscription from the computer. It is refused while
bots run on it; they are moved on the Bots page first, never by this.

Connect (`connect`, Settings > Models) is the one job that does end with a credential: the CLI signs in to a plan, and
the runner sends what that gave it to `backend/model_subscriptions.py`, which seals it as a company subscription. It
leaves the computer's own login as it was. The value never passes through this relay: not in a report, a line or a row.
Only that save ends the job `done`; a report can only fail it.
"""

import json
import re

from clients import harness_manifests as manifests

from . import runner_online
from .store import H, Problem
from . import bot_settings as BS

# The runtimes whose manifest has a [login] the runner can relay, and those whose CLI asks for a code to be pasted
# back. A runner too old for one of them answers that browser sign-in is not available.
RUNTIMES = manifests.with_capability("browser_sign_in")
PASTE_RUNTIMES = manifests.with_capability("paste_code")
SIGN_OUT_RUNTIMES = manifests.with_capability("sign_out")
CONNECT_RUNTIMES = manifests.with_capability("subscription_connect")
# What a row asks the runner to do.
KINDS = ("sign_in", "switch", "sign_out", "use_shared_key", "remove", "connect")
# The kinds that sign in on the side and have a commit point (`finishing`).
SIDE_KINDS = ("switch",)
# A removal has a commit point too: the bots check is made again there, before anything is signed out or deleted.
COMMIT_KINDS = SIDE_KINDS + ("remove",)
LIFETIME = 15 * 60
FINISH_SECONDS = 2 * 60
# `finishing`: a switch signed in on the side and the runner is putting it in place. It can
# no longer be canceled; the runner waits for the server to take that report before it moves anything, so a cancel
# that came first wins.
ACTIVE = ("requested", "starting", "waiting", "finishing")
ACTIVE_SQL = "state IN ('requested','starting','waiting','finishing')"
TERMINAL = ("signed_in", "signed_out", "done", "failed", "canceled", "expired")
# The runner wire keeps its spelling: runners in the field stop on "cancelled" in a report's answer (runner-rs/src/login.rs).
WIRE_STATE = {"canceled": "cancelled"}
ORDER = {state: i for i, state in enumerate(("requested", "starting", "waiting", "finishing", "signed_in", "failed"))}
ORDER.update(signed_out=ORDER["signed_in"], done=ORDER["signed_in"])
# The verdicts each kind may end with; any other is refused as the runner's mistake.
VERDICTS = {
    "sign_in": ("signed_in", "failed"),
    "switch": ("signed_in", "failed"),
    "sign_out": ("signed_out", "failed"),
    "use_shared_key": ("done", "failed"),
    "remove": ("done", "failed"),
    "connect": ("failed",),
}
# Key jobs from before D11-5 that a runner had already taken up when its server was upgraded: the migration
# (20261117085930_drop_computer_keys) leaves them running, since the runner may have changed its login already, and
# their reports are taken as they come (a commit point, then the verdict) and change nothing else here.
LEGACY_VERDICTS = {
    "clear_key": ("signed_out", "failed"),
    "use_key": ("done", "failed"),
    "to_key": ("done", "failed"),
    "to_subscription": ("signed_in", "failed"),
}
VERDICTS.update(LEGACY_VERDICTS)
MAX_LINES, MAX_LINE = 12, 200
# What a page drawn before D11-5, or a client, is told when it asks for a key job from Settings > Computers.
MOVED = (
    "Model keys moved to Settings > Models: add the key there as a subscription and give it to this computer. "
    "To stop a computer using a key, take it off the computer there and sign the computer in"
)
PASTE_RE = re.compile(r"^[A-Za-z0-9_\-#.~%+=/]{6,600}$")

# The runner redacts before it reports; this is the same rule again because the server does not
# take a runner's word for it.
_SECRET_PATTERNS = (
    re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}(?:\.[\w-]*)?"),
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|AIza)[-_][A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(?:access|refresh|id)?[_-]?token\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:secret|password|api[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"[A-Za-z0-9+/_\-=]{24,}"),
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def redact(text):
    text = _CONTROL.sub(" ", str(text or ""))
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return " ".join(text.split())[:MAX_LINE]


def clean_lines(lines):
    out = []
    for line in list(lines or [])[-MAX_LINES:]:
        line = redact(line)
        if line and (not out or out[-1] != line):
            out.append(line)
    return out


_WORD = re.compile(r"[a-z][a-z0-9_]{0,39}")
_LINK = re.compile(r"https?://\S*")
# The tail of a code a CLI echoes masked, and a piece of a token a repaint split: too short for `redact`.
_MASKED = re.compile(r"\*{8,}\S*")
_TOKEN_RUN = re.compile(r"[A-Za-z0-9_\-]{20,}")
MAX_PHASES = 60


def clean_trace(trace, hide=""):
    """A failed Connect's trace as it is kept (the `runner.login.failed` event): fixed words for the category and each
    phase, numbers, and the last screen scrubbed here again (links, `hide` (a code still waiting), a masked code's
    tail, anything token-shaped or a piece of it), at most MAX_LINE characters. None when the computer sent none."""
    if trace is None:
        return None
    word = lambda text: text if _WORD.fullmatch(text or "") else ""
    screen = _LINK.sub("[link]", trace.screen)
    if hide:
        screen = screen.replace(hide, "")
    return {
        "category": word(trace.category),
        "phases": [
            {"phase": word(p.phase), "elapsed_ms": min(p.elapsed_ms, 10**9), "exit_code": p.exit_code}
            for p in trace.phases[:MAX_PHASES]
            if word(p.phase)
        ],
        "screen": redact(_TOKEN_RUN.sub("[redacted]", _MASKED.sub("********", screen))),
    }


def clean_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    if (
        len(url) > 2048
        or not url.startswith("https://")
        or re.search(r"[\s\x00-\x1f\x7f<>\"']", url)
        or "@" in url.split("/")[2]
    ):
        return ""
    return url


def clean_user_code(code):
    code = str(code or "").strip()
    return code if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]{2,30}", code) else ""


def sweep(c):
    """Expire what ran out of time; forget what nobody needs any more."""
    now = H.now()
    c.execute(
        "UPDATE model_logins SET state='expired', url='', user_code='', pending_code=NULL, updated_at=? "
        f"WHERE {ACTIVE_SQL} AND expires_at<?",
        (now, now),
    )
    # What a Connect that is over would have become: the job names no value, and nothing reads this once it has ended.
    if H._has_table(c, "subscription_connects"):
        c.execute(
            f"DELETE FROM subscription_connects WHERE login_id NOT IN (SELECT id FROM model_logins WHERE {ACTIVE_SQL})"
        )
    c.execute("DELETE FROM model_logins WHERE created_at<?", (H.shift(now, days=-1),))


def sweep_due(c):
    """Whether `sweep` has anything to change: a poll reads without the write lock when it has not."""
    now = H.now()
    return bool(
        c.execute(
            f"SELECT 1 FROM model_logins WHERE ({ACTIVE_SQL} AND expires_at<?) OR created_at<? LIMIT 1",
            (now, H.shift(now, days=-1)),
        ).fetchone()
    )


def view(row):
    state = row["state"]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "runner_id": row["runner_id"],
        "runtime": row["runtime"],
        "profile": row["profile"],
        "state": state,
        "url": row["url"],
        "code": row["user_code"],
        "lines": json.loads(row["lines"] or "[]"),
        "message": row["message"],
        "accepts_code": row["runtime"] in PASTE_RUNTIMES and state == "waiting",
        "code_sent": bool(row["code_sent"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
    }


def _operator(c, who, rid, auth=None):
    runner = _runner(c, rid)
    if not (
        who.role == "owner"
        or auth
        and auth.bot_admin(who)
        or who.role == "human"
        and H.actor_id(who.actor) == runner["owner_id"]
    ):
        raise Problem("forbidden", "You cannot sign a model in on this computer", 403)


def _person_only(who, what):
    """Switching accounts and signing out stop the bots on that login, so only a person does them, never BotOps: not
    as itself, and not with the rights of the person it works for (its delegated identity keeps `via` "botops", even
    on a person's click on a card it proposed). Checked before anything is queued, marked or recorded."""
    from .onboarding import BOTOPS

    if "bot:" + BOTOPS in (who.actor, who.task_actor) or who.via == "botops":
        raise Problem(
            "forbidden",
            f"BotOps cannot {what}: it stops the bots on that login. Ask the person to do it in Settings > Computers",
            403,
        )


def _runner(c, rid):
    row = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
    if not row:
        raise Problem("not_found", "That computer is not registered", 404)
    return row


def _login(c, rid, lid):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, rid)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    return row


def _online(runner):
    return runner_online.is_online(runner["last_seen_at"])


def _queue(c, who, runner, runtime, profile, kind, what):
    """(row, new): a new row for the runner to run, or the one already running for this login. One thing at a time
    per login: a sign-out never races a sign-in on the same files."""
    from .subscriptions import login_upgrade

    if not profile and (problem := login_upgrade(runner, runtime=runtime)):
        raise Problem("runner_version", problem, 409)
    sweep(c)
    running = c.execute(
        f"SELECT * FROM model_logins WHERE runner_id=? AND runtime=? AND profile=? AND {ACTIVE_SQL}",
        (runner["id"], runtime, profile),
    ).fetchone()
    if running and running["kind"] == kind:
        return running, False
    if running:
        raise Problem(
            "state",
            "Something else is running for this login on that computer; cancel it or wait for it to finish",
            409,
        )
    # A removal can delete the subscription's whole folder, so it runs alone on it: no other tool signs in there
    # meanwhile, and it does not start while one is.
    if (
        profile
        and c.execute(
            f"SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND runtime<>? AND {ACTIVE_SQL} "
            "AND (kind='remove' OR ?='remove')",
            (runner["id"], profile, runtime, kind),
        ).fetchone()
    ):
        raise Problem("state", f"Something else is running on {profile} on that computer; wait for it to finish", 409)
    if not _online(runner):
        raise Problem("offline", f"That computer is offline, so it cannot {what}", 409)
    now = H.now()
    lid = H.new_id()
    c.execute(
        "INSERT INTO model_logins(id,runner_id,runtime,profile,state,created_at,updated_at,expires_at,requested_by,kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (lid, runner["id"], runtime, profile, "requested", now, now, H.shift(now, seconds=LIFETIME), who.actor, kind),
    )
    return _login(c, runner["id"], lid), True


def start(c, who, rid, runtime, profile="", auth=None, switch=False, to=""):
    """Sign `runtime` in on a computer: its own login, the only one (a named `profile` is refused). `switch` signs in to another
    account in place: the runner signs in on the side and puts the new login in place only once it has finished, so
    a canceled or failed switch leaves the old login working. `to` (Switch to subscription) is refused: a key comes
    off a computer in Settings > Models now."""
    if to:
        raise Problem("moved", MOVED, 410)
    if profile:
        # One login per AI tool per computer, used by all its bots: a different account means another computer.
        raise Problem(
            "one_login",
            "Each computer has one login per AI tool, used by all its bots. Sign in the "
            "computer's login; for a different account, add another computer",
            422,
        )
    kind = "switch" if switch else "sign_in"
    what = {"switch": "switch accounts", "sign_in": "start a sign-in"}[kind]
    if kind in SIDE_KINDS:
        _person_only(who, what)
    _operator(c, who, rid, auth)
    if runtime not in RUNTIMES:
        raise Problem(
            "kind",
            "Browser sign-in works for "
            + ", ".join(RUNTIMES[:-1])
            + " and "
            + RUNTIMES[-1]
            + "; set other runtimes up from the computer's terminal",
            422,
        )
    if kind in SIDE_KINDS and runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", f"You can't {what} from here for " + runtime, 422)
    runner = _runner(c, rid)
    if kind in SIDE_KINDS and runner["platform"] in manifests.caps(runtime).get("switch_unavailable_on", []):
        raise Problem(
            "kind",
            f"You can't {what} for {runtime} on {runner['label']} from here: it keeps the "
            "login in the system keychain. Sign out, then sign in",
            422,
        )
    row, new = _queue(c, who, runner, runtime, profile, kind, what)
    if new:
        from .subscription_refresh import invalidate_signin

        invalidate_signin(c, rid, profile, runtime, row["created_at"])
        H.event(
            c,
            who.actor,
            {"switch": "runner.login.switch", "sign_in": "runner.login.start"}[kind],
            rid,
            {"runtime": runtime},
        )
    return view(row)


def cleared(c, rid, runtime, profile=""):
    """Whether a person signed this login out (the shared key stays away from it until they sign in again)."""
    return bool(
        c.execute(
            "SELECT 1 FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?", (rid, runtime, profile)
        ).fetchone()
    )


def cleared_logins(c, rid):
    """{runtime: [profile, ...]} signed out by a person on this computer ("" is the computer's own login)."""
    out = {}
    for row in c.execute(
        "SELECT runtime, profile FROM model_login_cleared WHERE runner_id=? ORDER BY runtime, profile", (rid,)
    ):
        out.setdefault(row["runtime"], []).append(row["profile"])
    return out


def cleared_by_runner(c):
    """{runner_id: cleared_logins(...)} for every computer in one pass (Operations listing)."""
    out = {}
    for row in c.execute("SELECT runner_id, runtime, profile FROM model_login_cleared ORDER BY runtime, profile"):
        out.setdefault(row["runner_id"], {}).setdefault(row["runtime"], []).append(row["profile"])
    return out


def key_cleared_logins(c, rid):
    """[runtime, ...] whose computer login's key was cleared (its key deleted in Settings > Models or Credentials, not a
    Sign out): it reads "No key"."""
    return [
        row["runtime"]
        for row in c.execute(
            "SELECT runtime FROM model_login_cleared WHERE runner_id=? AND profile='' AND clear_key=1 ORDER BY runtime",
            (rid,),
        )
    ]


def key_cleared_by_runner(c):
    """{runner_id: key_cleared_logins(...)} for every computer in one pass (Operations listing)."""
    out = {}
    for row in c.execute(
        "SELECT runner_id, runtime FROM model_login_cleared WHERE profile='' AND clear_key=1 ORDER BY runtime"
    ):
        out.setdefault(row["runner_id"], []).append(row["runtime"])
    return out


def sign_out(c, who, rid, runtime, profile="", auth=None):
    """Sign `runtime` out on a computer: the runner runs the CLI's own logout and checks it took. The login stays
    signed out: the shared key no longer signs it back in, until a person signs in or chooses the shared key."""
    _person_only(who, "sign a login out")
    _operator(c, who, rid, auth)
    if runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", "Signing out from here isn't available for " + runtime, 422)
    runner = _runner(c, rid)
    row, new = _queue(c, who, runner, runtime, profile, "sign_out", "sign out")
    if not new:
        return view(row)
    now = H.now()
    c.execute(
        "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by) VALUES(?,?,?,?,?) "
        "ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET cleared_at=excluded.cleared_at, "
        "cleared_by=excluded.cleared_by",
        (rid, runtime, profile, now, who.actor),
    )
    from .subscription_refresh import invalidate_signin

    invalidate_signin(c, rid, profile, runtime, now)
    H.event(c, who.actor, "runner.login.sign_out", rid, {"runtime": runtime, "profile": profile})
    return view(row)


def use_shared_key(c, who, rid, runtime, auth=None):
    """Let the key this computer is given (Settings > Models) sign a signed-out computer login in again: the sign-out
    mark is lifted here, and on the computer, which keeps a mark of its own and fetches no key while it has it, by
    the Use the shared key job that removes it."""
    _operator(c, who, rid, auth)
    if runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", "Signing out from here isn't available for " + runtime, 422)
    runner = _runner(c, rid)
    if not cleared(c, rid, runtime):
        raise Problem("state", "That login was not signed out from here", 409)
    row, _ = _queue(c, who, runner, runtime, "", "use_shared_key", "use the shared key")
    c.execute("DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=''", (rid, runtime))
    H.event(c, who.actor, "runner.login.use_shared_key", rid, {"runtime": runtime})
    return view(row)


def affected_bots(c, who, rid, runtime, profile, settings, auth=None, every=False):
    """The active bots on this computer that run `runtime` on this login: the ones a sign-out stops. `every` counts
    paused bots too: what a removal would leave with nothing to run on when they start again."""
    _operator(c, who, rid, auth)
    runner = _runner(c, rid)
    if not profile:
        from .subscriptions import login_upgrade

        if problem := login_upgrade(runner, runtime=runtime):
            raise Problem("runner_version", problem, 409)
    return {"runtime": runtime, "profile": profile, "bots": _bots_on(c, rid, runtime, profile, settings, every)}


def _bots_on(c, rid, runtime, profile, settings, every):
    """Bots affected by this login: one computer login after acknowledgement, reported homes during an upgrade."""
    from . import providers
    from .shared_bots import follow
    from .subscriptions import computer_logins

    runner = c.execute('SELECT readiness,capabilities FROM runners_with_state WHERE id=?', (rid,)).fetchone()
    ready = H._json(runner['readiness'], {}) or {}
    current = computer_logins(runner, ready)
    if profile and current:
        # A named default is the same login, so cleanup must still refuse it while bots use it.
        if profile != ready['computer_profile']:
            return []
    record = providers.load(c, settings)
    out = []
    active = "" if every else "AND b.state='active' "
    for row in c.execute(
        f"SELECT a.bot, b.display_name, {BS.CONFIG} AS config FROM assignments a JOIN bots b ON b.id=a.bot "
        f"{active}LEFT JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} WHERE a.runner_id=? "
        "ORDER BY a.bot",
        (rid,),
    ):
        try:
            config = providers.fill(record, follow(c, row["bot"], json.loads(row["config"] or "{}")))
        except ValueError:
            continue
        reported = ((ready.get('bots') or {}).get(row['bot']) or {}).get('profile') or ''
        if config.get("runtime") == runtime and (current or reported == profile):
            out.append({"bot": row["bot"], "name": row["display_name"] or row["bot"]})
    return out


def _names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _in_use(bots):
    return f"{_names([b['name'] for b in bots])} run on it. Move them to another connection on the Bots page first"


def hold_removals(c, settings, bots=None):
    """Refuse a change that makes a bot (any of `bots`, or any bot) run on a subscription being taken off its
    computer: a placement, a model or tool change, the company's default, a shared bot's original. Called after the
    change is written, inside its transaction, so the refusal undoes it. A removal is held from its ask to its end,
    the commit point and the deletion after it included."""
    for row in c.execute(
        f"SELECT runner_id, runtime, profile FROM model_logins WHERE kind='remove' AND {ACTIVE_SQL}"
    ).fetchall():
        on = [
            b
            for b in _bots_on(c, row["runner_id"], row["runtime"], row["profile"], settings, True)
            if bots is None or b["bot"] in bots
        ]
        if on:
            label = (
                c.execute("SELECT label FROM runners WHERE id=?", (row["runner_id"],)).fetchone() or ["the computer"]
            )[0]
            raise Problem(
                "removing",
                f"{row['profile']} is being removed from {label}, and {_names([b['name'] for b in on])} "
                "would run on it. Wait until it is removed, or choose another connection on the Bots page",
                409,
                extra={"bots": on},
            )


def removing(c, rid, profile):
    """Whether `profile` is being taken off computer `rid` (a removal not yet ended): no bot chooses it meanwhile."""
    return (
        bool(profile)
        and c.execute(
            f"SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND kind='remove' AND {ACTIVE_SQL}",
            (rid, profile),
        ).fetchone()
        is not None
    )


def remove(c, who, rid, runtime, profile, settings, auth=None):
    """Take a named subscription's login for `runtime` off a computer. Refused while any bot here runs on it, naming
    them: a person moves them on the Bots page first."""
    _person_only(who, "remove a subscription")
    _operator(c, who, rid, auth)
    if not profile:
        raise Problem(
            "kind", "Only a named subscription can be removed; sign the computer's own login out instead", 422
        )
    if runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", "Removing a subscription from here isn't available for " + runtime, 422)
    runner = _runner(c, rid)
    row = c.execute("SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?", (rid, profile)).fetchone()
    if not row or runtime not in json.loads(row[0] or "{}"):
        raise Problem("not_found", "That subscription is not on this computer", 404)
    bots = affected_bots(c, who, rid, runtime, profile, settings, auth=auth, every=True)["bots"]
    if bots:
        raise Problem("in_use", _in_use(bots), 409, extra={"bots": bots})
    row, new = _queue(c, who, runner, runtime, profile, "remove", "remove a subscription")
    if new:
        H.event(c, who.actor, "runner.login.remove", rid, {"runtime": runtime, "profile": profile})
    return view(row)


def read(c, who, rid, lid, auth=None, swept=True):
    _operator(c, who, rid, auth)
    if swept:
        sweep(c)
    return view(_login(c, rid, lid))


def submit_code(c, who, rid, lid, code, auth=None):
    _operator(c, who, rid, auth)
    sweep(c)
    row = _login(c, rid, lid)
    if row["runtime"] not in PASTE_RUNTIMES:
        raise Problem("kind", "This sign-in does not take a pasted code", 422)
    if row["state"] != "waiting":
        raise Problem("state", "This sign-in is not waiting for a code", 409)
    code = str(code or "").strip()
    if not PASTE_RE.fullmatch(code):
        raise Problem("kind", "That does not look like the code the sign-in page showed", 422)
    if row["code_sent"]:
        raise Problem("state", "A code was already sent; start a new sign-in to try again", 409)
    c.execute("UPDATE model_logins SET pending_code=?, code_sent=1, updated_at=? WHERE id=?", (code, H.now(), lid))
    return view(_login(c, rid, lid))


def cancel(c, who, rid, lid, auth=None):
    _operator(c, who, rid, auth)
    sweep(c)
    row = _login(c, rid, lid)
    if row["state"] == "finishing":
        if row["kind"] == "remove":
            raise Problem("state", "It is already being removed, so it can't be canceled now", 409)
        raise Problem("state", "The new account is already being put in place, so it can't be canceled now", 409)
    if row["state"] in ACTIVE:
        c.execute(
            "UPDATE model_logins SET state='canceled', url='', user_code='', pending_code=NULL, updated_at=? "
            "WHERE id=?",
            (H.now(), lid),
        )
        H.event(c, who.actor, "runner.login.cancel", rid, {"runtime": row["runtime"]})
    return view(_login(c, rid, lid))


def pending(c, runner_id, swept=True):
    """What this runner should be running: every live sign-in, and a pasted code not yet taken."""
    if swept:
        sweep(c)
    rows = c.execute(
        f"SELECT * FROM model_logins WHERE runner_id=? AND {ACTIVE_SQL} ORDER BY created_at", (runner_id,)
    ).fetchall()
    return [
        {
            "id": r["id"],
            "runtime": r["runtime"],
            "profile": r["profile"],
            "expires_at": r["expires_at"],
            **({"kind": r["kind"]} if r["kind"] != "sign_in" else {}),
            **({"code": r["pending_code"]} if r["pending_code"] else {}),
        }
        for r in rows
    ]


def report(c, runner_id, lid, body, settings=None):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    sweep(c)
    row = c.execute("SELECT * FROM model_logins WHERE id=?", (lid,)).fetchone()
    if row["state"] in TERMINAL:
        return {"state": WIRE_STATE.get(row["state"], row["state"])}
    if body.state in ("signed_in", "signed_out", "done") and body.state not in VERDICTS.get(row["kind"], ()):
        raise Problem("state", f"A {row['kind'].replace('_', ' ')} does not end {body.state.replace('_', ' ')}", 422)
    if body.state == "finishing" and row["kind"] not in (*COMMIT_KINDS, *LEGACY_VERDICTS):
        raise Problem("state", "Only a switch puts a new login in place", 422)
    # A removal's commit point: a bot that came to run on the subscription since it was asked for (moved to this
    # computer, or onto this tool) stops it here, before the runner signs anything out or deletes anything.
    if body.state == "finishing" and row["kind"] == "remove" and row["state"] != "finishing":
        bots = _bots_on(c, runner_id, row["runtime"], row["profile"], settings, True)
        if bots:
            c.execute(
                "UPDATE model_logins SET state='failed', message=?, updated_at=? WHERE id=?",
                (redact(_in_use(bots)), H.now(), lid),
            )
            H.event(
                c,
                "runner:" + runner_id,
                "runner.login.failed",
                runner_id,
                {"runtime": row["runtime"], "login": lid, "kind": row["kind"], "category": "in_use"},
            )
            return {"state": "failed"}
    if body.state == "finishing" and row["state"] != "finishing":
        # From here it cannot be canceled, and it has a little time of its own to finish in.
        c.execute(
            "UPDATE model_logins SET expires_at=MAX(expires_at, ?) WHERE id=?",
            (H.shift(H.now(), seconds=FINISH_SECONDS), lid),
        )
    state = body.state if ORDER[body.state] >= ORDER[row["state"]] else row["state"]
    terminal = state in ("signed_in", "signed_out", "done", "failed")
    # Finishing, the person has nothing left to open or enter.
    done_with_page = terminal or state == "finishing"
    url = "" if done_with_page else clean_url(body.url) or row["url"]
    code = "" if done_with_page else clean_user_code(body.code) or row["user_code"]
    # Connect keeps no lines: what it prints ends with the credential itself.
    lines = [] if row["kind"] == "connect" else clean_lines(body.lines) or json.loads(row["lines"] or "[]")
    pending_code = None if terminal or body.code_taken else row["pending_code"]
    c.execute(
        "UPDATE model_logins SET state=?, url=?, user_code=?, lines=?, message=?, pending_code=?, "
        "updated_at=? WHERE id=?",
        (state, url, code, json.dumps(lines), redact(body.message), pending_code, H.now(), lid),
    )
    if terminal:
        detail = {"runtime": row["runtime"]}
        if state == "failed":
            # Which sign-in failed and why stay with its audit row (the login's own row is gone after a day), so a
            # list of failures can show it (the support bundle's reads these keys) and nobody has to read the
            # computer's own log: the login, its kind, the category ("" when the computer named none) and, for a
            # Connect, the step its command ended on with its exit code, the scrubbed last screen and the whole trace.
            trace = clean_trace(body.trace, row["pending_code"] or "")
            detail.update(login=lid, kind=row["kind"], category=(trace or {}).get("category", ""))
            if trace and row["kind"] == "connect":
                ended = [p for p in trace["phases"] if p["exit_code"] is not None][-1:] or trace["phases"][-1:]
                for step in ended:
                    detail.update(step=step["phase"], elapsed_ms=step["elapsed_ms"])
                    if step["exit_code"] is not None:
                        detail["exit_code"] = step["exit_code"]
                if trace["screen"]:
                    detail["lines"] = [trace["screen"]]
                detail["trace"] = trace
        H.event(c, "runner:" + runner_id, "runner.login." + state, row["runner_id"], detail)
    # A person signed this login in again: it is no longer signed out on purpose. A sign-out that did not take
    # leaves the login as it was, so it is not marked either.
    if (state == "signed_in" or state == "failed" and row["kind"] == "sign_out") and terminal:
        c.execute(
            "DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
            (row["runner_id"], row["runtime"], row["profile"]),
        )
    # Removed: nothing of it stays here to fill a line again (its sign-out mark, its manual weekly reading).
    if state == "done" and row["kind"] == "remove":
        from .subscriptions import weekly_key

        c.execute(
            "DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
            (row["runner_id"], row["runtime"], row["profile"]),
        )
        c.execute(
            "DELETE FROM registry_metadata WHERE key=?", (weekly_key(row["runner_id"], row["profile"], row["runtime"]),)
        )
    # A login switched to another account: a usage limit held on the old one is not the new one's, so its bots are
    # freed and one probe turn tests it (execution.lift_after_login_switch).
    if settings is not None and terminal and state == "signed_in" and row["kind"] == "switch":
        from .execution import lift_after_login_switch

        lift_after_login_switch(c, settings, H.KEEPER, runner_id, row["runtime"])
    return {"state": state}


def mark_key_cleared(c, who, rid, runtime):
    """No key signs `runtime` in on this computer again until a person signs it in or chooses Use the shared key, and
    its next key fetch tells it to delete its copies and log out a login a key made (`clear_key`)."""
    now = H.now()
    c.execute(
        "INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
        "VALUES(?,?,?,?,?,1) ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET cleared_at=excluded.cleared_at, "
        "cleared_by=excluded.cleared_by, clear_key=1",
        (rid, runtime, "", now, who.actor),
    )
    from .subscription_refresh import invalidate_signin

    invalidate_signin(c, rid, "", runtime, now)
