"""Signing a model CLI in on a runner from the browser.

The Computer operator, owner or admin asks for a login for one runtime on one computer; the runner starts the CLI and reports
what the CLI itself prints (a link and a one-time code, or a prompt for a pasted code). This module
keeps that relay and nothing else. It never sees the credential the CLI ends up with: the CLI
writes it on the runner's disk, and the runner only reports whether readiness turned green.

What is stored is a link, a one-time code, a few redacted CLI message lines and timestamps, for
at most `LIFETIME` seconds. A code the person pastes back is held only until the runner has
taken it, then dropped.

The same relay signs a login out, switches it to another account, and puts the company's shared key
back (`kind`). A computer's own login that a person signed out stays signed out (`model_login_cleared`):
the server stops handing that computer the shared "Every computer" key for it, and the runner keeps its
own mark, until a person signs in again or chooses to use the shared key.

Switch to subscription (`to_subscription`) moves the computer's own login off its API key: a switch, signed in on
the side, and only once it has worked does the key go (`model_login_key_off`; the runner deletes its copy and keeps
its own mark). Unlike a sign-out, a later sign-in does not bring the key back; only Use the shared key does.
"""

import json
import re

from clients import harness_manifests as manifests

from .store import H, Problem

# The runtimes whose manifest has a [login] the runner can relay, and those whose CLI asks for a code to be pasted
# back. A runner too old for one of them answers that browser sign-in is not available.
RUNTIMES = manifests.with_capability("browser_sign_in")
PASTE_RUNTIMES = manifests.with_capability("paste_code")
SIGN_OUT_RUNTIMES = manifests.with_capability("sign_out")
# What a row asks the runner to do. A runner without SIGN_OUT_CAPABILITY knows only `sign_in`, so it is never
# handed another kind: it would start a sign-in instead.
KINDS = ("sign_in", "switch", "sign_out", "use_shared_key", "to_subscription")
SIGN_OUT_CAPABILITY = "model_sign_out_v1"
# A runner that can take a computer login off its key once a subscription works there (runner-rs/src/login.rs).
SWITCH_TYPE_CAPABILITY = "model_switch_type_v1"
# The kinds that sign in on the side and have a commit point (`finishing`).
SIDE_KINDS = ("switch", "to_subscription")
LIFETIME = 15 * 60
FINISH_SECONDS = 2 * 60
# `finishing`: a switch signed in on the side and the runner is putting it in place. It can no longer be cancelled;
# the runner waits for the server to take that report before it moves anything, so a cancel that came first wins.
ACTIVE = ("requested", "starting", "waiting", "finishing")
ACTIVE_SQL = "state IN ('requested','starting','waiting','finishing')"
TERMINAL = ("signed_in", "signed_out", "done", "failed", "cancelled", "expired")
ORDER = {state: i for i, state in enumerate(("requested", "starting", "waiting", "finishing", "signed_in", "failed"))}
ORDER.update(signed_out=ORDER["signed_in"], done=ORDER["signed_in"])
# The verdicts each kind may end with; any other is refused as the runner's mistake.
VERDICTS = {"sign_in": ("signed_in", "failed"), "switch": ("signed_in", "failed"),
            "sign_out": ("signed_out", "failed"), "use_shared_key": ("done", "failed"),
            "to_subscription": ("signed_in", "failed")}
MAX_LINES, MAX_LINE = 12, 200
ONLINE_SECONDS = 60
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


def clean_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    if len(url) > 2048 or not url.startswith("https://") or re.search(r"[\s\x00-\x1f\x7f<>\"']", url) \
            or "@" in url.split("/")[2]:
        return ""
    return url


def clean_user_code(code):
    code = str(code or "").strip()
    return code if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]{2,30}", code) else ""


def sweep(c):
    """Expire what ran out of time; forget what nobody needs any more."""
    now = H.now()
    c.execute("UPDATE model_logins SET state='expired', url='', user_code='', pending_code=NULL, updated=? "
              f"WHERE {ACTIVE_SQL} AND expires_at<?", (now, now))
    c.execute("DELETE FROM model_logins WHERE created<?", (H.shift(now, days=-1),))


def view(row):
    state = row["state"]
    return {"id": row["id"], "kind": row["kind"], "runner_id": row["runner_id"], "runtime": row["runtime"],
            "profile": row["profile"], "state": state, "url": row["url"], "code": row["user_code"],
            "lines": json.loads(row["lines_json"] or "[]"), "message": row["message"],
            "accepts_code": row["runtime"] in PASTE_RUNTIMES and state == "waiting",
            "code_sent": bool(row["code_sent"]), "created": row["created"], "updated": row["updated"],
            "expires_at": row["expires_at"]}


def _operator(c, who, rid, auth=None):
    runner = _runner(c, rid)
    if not (who.role == "owner" or auth and auth.bot_admin(who)
            or who.role == "human" and H.actor_id(who.actor) == runner["operator"]):
        raise Problem("forbidden", "You cannot sign a model in on this computer", 403)


def _person_only(who, what):
    """Switching accounts and signing out stop the bots on that login, so only a person does them, never BotOps: not
    as itself, and not with the rights of the person it works for (its delegated identity keeps `via` "botops", even
    on a person's click on a card it proposed). Checked before anything is queued, marked or recorded."""
    from .onboarding import BOTOPS
    if "bot:" + BOTOPS in (who.actor, who.task_actor) or who.via == "botops":
        raise Problem("forbidden", f"BotOps cannot {what}: it stops the bots on that login. Ask the person to do it "
                      "in Settings > Computers", 403)


def _runner(c, rid):
    row = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
    if not row:
        raise Problem("not_found", "That computer is not registered", 404)
    return row


def _login(c, rid, lid):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, rid)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    return row


def _signs_out(runner):
    return SIGN_OUT_CAPABILITY in set(H._json(runner["capabilities_json"], []) or [])


def _switches_type(runner):
    return SWITCH_TYPE_CAPABILITY in set(H._json(runner["capabilities_json"], []) or [])


def _queue(c, who, runner, runtime, profile, kind, what):
    """(row, new): a new row for the runner to run, or the one already running for this login. One thing at a time
    per login: a sign-out never races a sign-in on the same files."""
    sweep(c)
    running = c.execute(f"SELECT * FROM model_logins WHERE runner_id=? AND runtime=? AND profile=? AND {ACTIVE_SQL}",
                        (runner["id"], runtime, profile)).fetchone()
    if running and running["kind"] == kind:
        return running, False
    if running:
        raise Problem("state", "Something else is running for this login on that computer; cancel it or wait for it "
                      "to finish", 409)
    if not runner["last_seen"] or runner["last_seen"] < H.shift(H.now(), seconds=-ONLINE_SECONDS):
        raise Problem("offline", f"That computer is offline, so it cannot {what}", 409)
    if kind != "sign_in" and not _signs_out(runner) or kind == "to_subscription" and not _switches_type(runner):
        raise Problem("runner_version", f"Update {runner['label']} to {what} from here", 409)
    now = H.now()
    lid = H.new_id()
    c.execute("INSERT INTO model_logins(id,runner_id,runtime,profile,state,created,updated,expires_at,requested_by,kind) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (lid, runner["id"], runtime, profile, "requested", now, now, H.shift(now, seconds=LIFETIME), who.actor,
               kind))
    return _login(c, runner["id"], lid), True


def start(c, who, rid, runtime, profile="", auth=None, switch=False, to=""):
    """Sign `runtime` in on a computer (`profile` "" is the computer's own login). `switch` signs in to another
    account in place: the runner signs in on the side and puts the new login in place only once it has finished, so
    a cancelled or failed switch leaves the old login working. `to="subscription"` is the same switch for the
    computer's own login running on an API key: once the subscription is in place the key goes, and stays off."""
    if to and (switch or profile):
        raise Problem("kind", "Switch to subscription is for the computer's own login, on its own", 422)
    kind = "to_subscription" if to else "switch" if switch else "sign_in"
    what = {"to_subscription": "switch to a subscription", "switch": "switch accounts", "sign_in": "start a sign-in"}[kind]
    if kind in SIDE_KINDS:
        _person_only(who, what)
    _operator(c, who, rid, auth)
    if runtime not in RUNTIMES:
        raise Problem("kind", "Browser sign-in works for " + ", ".join(RUNTIMES[:-1]) + " and " + RUNTIMES[-1]
                      + "; set other runtimes up from the computer's terminal", 422)
    if kind in SIDE_KINDS and runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", f"You can't {what} from here for " + runtime, 422)
    runner = _runner(c, rid)
    if kind in SIDE_KINDS and runner["platform"] in manifests.caps(runtime).get("switch_unavailable_on", []):
        raise Problem("kind", f"You can't {what} for {runtime} on {runner['label']} from here: it keeps the "
                      "login in the system keychain. Sign out, then sign in", 422)
    row, new = _queue(c, who, runner, runtime, profile, kind, what)
    if new:
        from .subscription_refresh import invalidate_signin
        invalidate_signin(c, rid, profile, runtime, row["created"])
        H.event(c, who.actor, {"to_subscription": "runner.login.to_subscription", "switch": "runner.login.switch",
                               "sign_in": "runner.login.start"}[kind], rid, {"runtime": runtime})
    return view(row)


def cleared(c, rid, runtime, profile=""):
    """Whether a person signed this login out (the shared key stays away from it until they sign in again)."""
    return bool(c.execute("SELECT 1 FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
                          (rid, runtime, profile)).fetchone())


def key_off(c, rid, runtime, profile=""):
    """Whether this login was switched to a subscription: the shared key stays away until Use the shared key."""
    return bool(c.execute("SELECT 1 FROM model_login_key_off WHERE runner_id=? AND runtime=? AND profile=?",
                          (rid, runtime, profile)).fetchone())


def cleared_logins(c, rid):
    """{runtime: [profile, ...]} signed out by a person on this computer ("" is the computer's own login)."""
    out = {}
    for row in c.execute("SELECT runtime, profile FROM model_login_cleared WHERE runner_id=? ORDER BY runtime, profile",
                         (rid,)):
        out.setdefault(row["runtime"], []).append(row["profile"])
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
    c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by) VALUES(?,?,?,?,?) "
              "ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET cleared_at=excluded.cleared_at, "
              "cleared_by=excluded.cleared_by", (rid, runtime, profile, now, who.actor))
    from .subscription_refresh import invalidate_signin
    invalidate_signin(c, rid, profile, runtime, now)
    H.event(c, who.actor, "runner.login.sign_out", rid, {"runtime": runtime, "profile": profile})
    return view(row)


def use_shared_key(c, who, rid, runtime, auth=None):
    """Let the company's shared key sign a signed-out computer login in again."""
    _operator(c, who, rid, auth)
    if runtime not in SIGN_OUT_RUNTIMES:
        raise Problem("kind", "Signing out from here isn't available for " + runtime, 422)
    runner = _runner(c, rid)
    if not cleared(c, rid, runtime) and not key_off(c, rid, runtime):
        raise Problem("state", "That login was not signed out or switched to a subscription from here", 409)
    row, _ = _queue(c, who, runner, runtime, "", "use_shared_key", "use the shared key")
    c.execute("DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=''", (rid, runtime))
    c.execute("DELETE FROM model_login_key_off WHERE runner_id=? AND runtime=? AND profile=''", (rid, runtime))
    H.event(c, who.actor, "runner.login.use_shared_key", rid, {"runtime": runtime})
    return view(row)


def affected_bots(c, who, rid, runtime, profile, settings, auth=None):
    """The active bots on this computer that run `runtime` on this login: the ones a sign-out stops."""
    _operator(c, who, rid, auth)
    _runner(c, rid)
    from . import providers, subscriptions
    from .shared_bots import follow
    ctx = subscriptions.context(c, settings)
    out = []
    for row in c.execute("SELECT a.bot, b.display_name, bc.config_json FROM assignments a JOIN bots b ON b.slug=a.bot "
                         "AND b.state='active' LEFT JOIN bot_config bc ON bc.bot=a.bot WHERE a.runner_id=? "
                         "ORDER BY a.bot", (rid,)):
        try:
            config = providers.fill(ctx["providers"], follow(c, row["bot"], json.loads(row["config_json"] or "{}")))
        except ValueError:
            continue
        if config.get("runtime") != runtime:
            continue
        assigned, _ = subscriptions.effective(c, row["bot"], ctx)
        if (assigned or "") == profile:
            out.append({"bot": row["bot"], "name": row["display_name"] or row["bot"]})
    return {"runtime": runtime, "profile": profile, "bots": out}


def read(c, who, rid, lid, auth=None):
    _operator(c, who, rid, auth)
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
    c.execute("UPDATE model_logins SET pending_code=?, code_sent=1, updated=? WHERE id=?",
              (code, H.now(), lid))
    return view(_login(c, rid, lid))


def cancel(c, who, rid, lid, auth=None):
    _operator(c, who, rid, auth)
    sweep(c)
    row = _login(c, rid, lid)
    if row["state"] == "finishing":
        raise Problem("state", "The new account is already being put in place, so it can't be cancelled now", 409)
    if row["state"] in ACTIVE:
        c.execute("UPDATE model_logins SET state='cancelled', url='', user_code='', pending_code=NULL, updated=? "
                  "WHERE id=?", (H.now(), lid))
        H.event(c, who.actor, "runner.login.cancel", rid, {"runtime": row["runtime"]})
    return view(_login(c, rid, lid))


def pending(c, runner_id):
    """What this runner should be running: every live sign-in, and a pasted code not yet taken."""
    sweep(c)
    rows = c.execute(f"SELECT * FROM model_logins WHERE runner_id=? AND {ACTIVE_SQL} ORDER BY created",
                     (runner_id,)).fetchall()
    return [{"id": r["id"], "runtime": r["runtime"], "profile": r["profile"], "expires_at": r["expires_at"],
             **({"kind": r["kind"]} if r["kind"] != "sign_in" else {}),
             **({"code": r["pending_code"]} if r["pending_code"] else {})} for r in rows]


def report(c, runner_id, lid, body):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    sweep(c)
    row = c.execute("SELECT * FROM model_logins WHERE id=?", (lid,)).fetchone()
    if row["state"] in TERMINAL:
        return {"state": row["state"]}
    if body.state in ("signed_in", "signed_out", "done") and body.state not in VERDICTS[row["kind"]]:
        raise Problem("state", f"A {row['kind'].replace('_', ' ')} does not end {body.state.replace('_', ' ')}", 422)
    if body.state == "finishing" and row["kind"] not in SIDE_KINDS:
        raise Problem("state", "Only a switch puts a new login in place", 422)
    if body.state == "finishing" and row["state"] != "finishing":
        # From here it cannot be cancelled, and it has a little time of its own to finish in.
        c.execute("UPDATE model_logins SET expires_at=MAX(expires_at, ?) WHERE id=?",
                  (H.shift(H.now(), seconds=FINISH_SECONDS), lid))
    state = body.state if ORDER[body.state] >= ORDER[row["state"]] else row["state"]
    terminal = state in ("signed_in", "signed_out", "done", "failed")
    # Finishing, the person has nothing left to open or enter.
    done_with_page = terminal or state == "finishing"
    url = "" if done_with_page else clean_url(body.url) or row["url"]
    code = "" if done_with_page else clean_user_code(body.code) or row["user_code"]
    lines = clean_lines(body.lines) or json.loads(row["lines_json"] or "[]")
    pending_code = None if terminal or body.code_taken else row["pending_code"]
    c.execute("UPDATE model_logins SET state=?, url=?, user_code=?, lines_json=?, message=?, pending_code=?, "
              "updated=? WHERE id=?",
              (state, url, code, json.dumps(lines), redact(body.message), pending_code, H.now(), lid))
    if terminal:
        H.event(c, "runner:" + runner_id, "runner.login." + state, row["runner_id"], {"runtime": row["runtime"]})
    # A person signed this login in again: it is no longer signed out on purpose. A sign-out that did not take
    # leaves the login as it was, so it is not marked either.
    if (state == "signed_in" or state == "failed" and row["kind"] == "sign_out") and terminal:
        c.execute("DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
                  (row["runner_id"], row["runtime"], row["profile"]))
    # Switched to a subscription: the key is off this login from now on (the runner has deleted its copy).
    if state == "signed_in" and row["kind"] == "to_subscription":
        c.execute("INSERT INTO model_login_key_off(runner_id,runtime,profile,set_at,set_by) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET set_at=excluded.set_at, set_by=excluded.set_by",
                  (row["runner_id"], row["runtime"], row["profile"], H.now(), row["requested_by"]))
    return {"state": state}
