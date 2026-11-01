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
the side, and only once it has worked does the key go (`model_login_key_off`; the computer's own key leaves the vault,
and the runner deletes its copies and keeps its own mark). Unlike a sign-out, a later sign-in does not bring a key
back; Add key or Use the shared key does.

Clear key and Replace key (backend/computer_keys.py) ride it too, as `clear_key` and `use_key`, and never carry the
key: Replace stores it in the vault for that computer and the runner fetches it from there.

Switch to API key (`to_key`) is the way back from a subscription: a `use_key` job whose key is checked while the
subscription stays signed in and in use, and only once it is in place is the subscription signed out (unless the CLI
keeps both in one login, where the key's login already took its place). Its key becomes this computer's, and the
switch's marks are lifted, only when the runner says it is done; a cancel, a failed check or one that runs out leaves
the subscription, the marks and the vault as they were. A key the company shares
with every computer asks "this computer only" or "all of them" first (`key_info` lists the computers it serves).

Remove (`remove`) takes a named subscription's login for one tool off a computer: the runner signs it out there and,
once nothing else is signed in on that subscription, deletes the subscription from the computer. It is refused while
bots run on it; they are moved on the Bots page first, never by this.
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
KINDS = ("sign_in", "switch", "sign_out", "use_shared_key", "clear_key", "use_key", "to_subscription", "remove", "to_key")
SIGN_OUT_CAPABILITY = "model_sign_out_v1"
# A runner that clears and uses a computer's key (runner-rs/src/login.rs); an older one is asked to update first.
KEYS_CAPABILITY = "model_keys_v1"
KEY_KINDS = ("clear_key", "use_key", "to_key")
# The kinds that check a key staged in the vault for them (backend/computer_keys.py store/confirm/discard).
KEY_JOBS = ("use_key", "to_key")
KEY_JOBS_SQL = "kind IN ('use_key','to_key')"
# A runner that can take a computer login off its key once a subscription works there (runner-rs/src/login.rs).
SWITCH_TYPE_CAPABILITY = "model_switch_type_v1"
# A runner that can take a named subscription off the computer (runner-rs/src/login.rs run_remove).
REMOVE_CAPABILITY = "model_remove_v1"
# A runner that can move a computer login off its subscription onto a key (`to_key`, runner-rs/src/login.rs).
SWITCH_TO_KEY_CAPABILITY = "model_switch_to_key_v1"
# The kinds that sign in on the side and have a commit point (`finishing`).
SIDE_KINDS = ("switch", "to_subscription")
# A removal has a commit point too: the bots check is made again there, before anything is signed out or deleted.
COMMIT_KINDS = SIDE_KINDS + ("remove",)
LIFETIME = 15 * 60
FINISH_SECONDS = 2 * 60
# `finishing`: a switch signed in on the side (or a new key was checked) and the runner is putting it in place. It can
# no longer be cancelled; the runner waits for the server to take that report before it moves anything, so a cancel
# that came first wins.
ACTIVE = ("requested", "starting", "waiting", "finishing")
ACTIVE_SQL = "state IN ('requested','starting','waiting','finishing')"
TERMINAL = ("signed_in", "signed_out", "done", "failed", "cancelled", "expired")
ORDER = {state: i for i, state in enumerate(("requested", "starting", "waiting", "finishing", "signed_in", "failed"))}
ORDER.update(signed_out=ORDER["signed_in"], done=ORDER["signed_in"])
# The verdicts each kind may end with; any other is refused as the runner's mistake.
VERDICTS = {"sign_in": ("signed_in", "failed"), "switch": ("signed_in", "failed"),
            "sign_out": ("signed_out", "failed"), "use_shared_key": ("done", "failed"),
            "clear_key": ("signed_out", "failed"), "use_key": ("done", "failed"),
            "to_subscription": ("signed_in", "failed"), "remove": ("done", "failed"),
            "to_key": ("done", "failed")}
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
    from . import computer_keys
    for row in c.execute(f"SELECT id FROM model_logins WHERE {KEY_JOBS_SQL} AND {ACTIVE_SQL} AND expires_at<?",
                         (now,)).fetchall():
        computer_keys.discard(c, row["id"])
    c.execute("UPDATE model_logins SET state='expired', url='', user_code='', pending_code=NULL, updated_at=? "
              f"WHERE {ACTIVE_SQL} AND expires_at<?", (now, now))
    c.execute("DELETE FROM model_logins WHERE created_at<?", (H.shift(now, days=-1),))


def sweep_due(c):
    """Whether `sweep` has anything to change: a poll reads without the write lock when it has not."""
    now = H.now()
    return bool(c.execute(f"SELECT 1 FROM model_logins WHERE ({ACTIVE_SQL} AND expires_at<?) OR created_at<? LIMIT 1",
                          (now, H.shift(now, days=-1))).fetchone())


def view(row):
    state = row["state"]
    return {"id": row["id"], "kind": row["kind"], "runner_id": row["runner_id"], "runtime": row["runtime"],
            "profile": row["profile"], "state": state, "url": row["url"], "code": row["user_code"],
            "lines": json.loads(row["lines"] or "[]"), "message": row["message"],
            "accepts_code": row["runtime"] in PASTE_RUNTIMES and state == "waiting",
            "code_sent": bool(row["code_sent"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
            "expires_at": row["expires_at"]}


def _operator(c, who, rid, auth=None):
    runner = _runner(c, rid)
    if not (who.role == "owner" or auth and auth.bot_admin(who)
            or who.role == "human" and H.actor_id(who.actor) == runner["owner_id"]):
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
    return SIGN_OUT_CAPABILITY in set(H._json(runner["capabilities"], []) or [])


def _switches_type(runner):
    return SWITCH_TYPE_CAPABILITY in set(H._json(runner["capabilities"], []) or [])


def _switches_to_key(runner):
    return SWITCH_TO_KEY_CAPABILITY in set(H._json(runner["capabilities"], []) or [])


def _handles_keys(runner):
    return KEYS_CAPABILITY in set(H._json(runner["capabilities"], []) or [])


def _online(runner):
    return bool(runner["last_seen_at"]) and runner["last_seen_at"] >= H.shift(H.now(), seconds=-ONLINE_SECONDS)


def _removes(runner):
    return REMOVE_CAPABILITY in set(H._json(runner["capabilities"], []) or [])


def _queue(c, who, runner, runtime, profile, kind, what):
    """(row, new): a new row for the runner to run, or the one already running for this login. One thing at a time
    per login: a sign-out never races a sign-in on the same files."""
    from .subscriptions import login_upgrade
    if not profile and (problem := login_upgrade(runner, runtime=runtime)):
        raise Problem("runner_version", problem, 409)
    sweep(c)
    running = c.execute(f"SELECT * FROM model_logins WHERE runner_id=? AND runtime=? AND profile=? AND {ACTIVE_SQL}",
                        (runner["id"], runtime, profile)).fetchone()
    if running and running["kind"] == kind:
        return running, False
    if running:
        raise Problem("state", "Something else is running for this login on that computer; cancel it or wait for it "
                      "to finish", 409)
    # A removal can delete the subscription's whole folder, so it runs alone on it: no other tool signs in there
    # meanwhile, and it does not start while one is.
    if profile and c.execute(f"SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND runtime<>? AND {ACTIVE_SQL} "
                             "AND (kind='remove' OR ?='remove')", (runner["id"], profile, runtime, kind)).fetchone():
        raise Problem("state", f"Something else is running on {profile} on that computer; wait for it to finish", 409)
    if not _online(runner):
        raise Problem("offline", f"That computer is offline, so it cannot {what}", 409)
    if (kind != "sign_in" and not _signs_out(runner) or kind in KEY_KINDS and not _handles_keys(runner)
            or kind == "to_subscription" and not _switches_type(runner) or kind == "remove" and not _removes(runner)
            or kind == "to_key" and not _switches_to_key(runner)):
        raise Problem("runner_version", f"Update {runner['label']} to {what} from here", 409)
    now = H.now()
    lid = H.new_id()
    c.execute("INSERT INTO model_logins(id,runner_id,runtime,profile,state,created_at,updated_at,expires_at,requested_by,kind) "
              "VALUES(?,?,?,?,?,?,?,?,?,?)",
              (lid, runner["id"], runtime, profile, "requested", now, now, H.shift(now, seconds=LIFETIME), who.actor,
               kind))
    return _login(c, runner["id"], lid), True


def start(c, who, rid, runtime, profile="", auth=None, switch=False, to=""):
    """Sign `runtime` in on a computer: its own login, the only one (a named `profile` is refused). `switch` signs in to another
    account in place: the runner signs in on the side and puts the new login in place only once it has finished, so
    a cancelled or failed switch leaves the old login working. `to="subscription"` is the same switch for the
    computer's own login running on an API key: once the subscription is in place the key goes, and stays off."""
    if to and (switch or profile):
        raise Problem("kind", "Switch to subscription is for the computer's own login, on its own", 422)
    if profile:
        # One login per AI tool per computer, used by all its bots: a different account means another computer.
        raise Problem("one_login", "Each computer has one login per AI tool, used by all its bots. Sign in the "
                      "computer's login; for a different account, add another computer", 422)
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
    # Switch to subscription keeps the API key in use until the subscription is in place. A login a person cleared
    # the key of, or signed out, has no key to keep, so the switch would be a plain sign-in whose cancel leaves no
    # login while it reads "The API key is still in use" (a page drawn before the Clear key, #1298).
    if kind == "to_subscription" and cleared(c, rid, runtime):
        gone = "its key was cleared" if runtime in key_cleared_logins(c, rid) else "it is signed out"
        raise Problem("state", f"{runtime} on {runner['label']} has no API key to switch from: {gone}. Sign in "
                      "instead", 409)
    if kind in SIDE_KINDS and runner["platform"] in manifests.caps(runtime).get("switch_unavailable_on", []):
        raise Problem("kind", f"You can't {what} for {runtime} on {runner['label']} from here: it keeps the "
                      "login in the system keychain. Sign out, then sign in", 422)
    row, new = _queue(c, who, runner, runtime, profile, kind, what)
    if new:
        from .subscription_refresh import invalidate_signin
        invalidate_signin(c, rid, profile, runtime, row["created_at"])
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


def cleared_by_runner(c):
    """{runner_id: cleared_logins(...)} for every computer in one pass (Operations listing)."""
    out = {}
    for row in c.execute("SELECT runner_id, runtime, profile FROM model_login_cleared ORDER BY runtime, profile"):
        out.setdefault(row["runner_id"], {}).setdefault(row["runtime"], []).append(row["profile"])
    return out


def key_cleared_logins(c, rid):
    """[runtime, ...] whose computer login a person cleared the key of (Clear key, not Sign out): "No key · Add key"."""
    return [row["runtime"] for row in c.execute(
        "SELECT runtime FROM model_login_cleared WHERE runner_id=? AND profile='' AND clear_key=1 ORDER BY runtime", (rid,))]


def key_cleared_by_runner(c):
    """{runner_id: key_cleared_logins(...)} for every computer in one pass (Operations listing)."""
    out = {}
    for row in c.execute(
            "SELECT runner_id, runtime FROM model_login_cleared WHERE profile='' AND clear_key=1 ORDER BY runtime"):
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


def _share_with(c, who, runner_ids, credential_id, here, runtime):
    """Grant `credential_id` to each computer in `runner_ids` by name ("Shared with: [computers]"): on each, the key
    `runtime` runs on from now. A computer listed is one a person just gave this key, so another key shared with it by
    name for `runtime` is taken off it (the choice decides, not which key is older), and a Clear key or a Switch to
    subscription there no longer holds it back (`_resume`). The grant rows themselves tell every runner to fetch its
    keys again (backend/events.py)."""
    from .computer_keys import SHARED_WITH, names
    rows = []
    for rid in dict.fromkeys([here, *runner_ids]):
        row = c.execute("SELECT * FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone()
        if not row:
            raise Problem("computers", "Share it only with computers that are connected", 422)
        rows.append(row)
    marked = [row for row in rows if cleared(c, row["id"], runtime) or key_off(c, row["id"], runtime)]
    for row in marked:
        if not _online(row):
            raise Problem("offline", f"{row['label']} is offline, and its key was cleared or switched to a "
                          "subscription from here, so it cannot take the shared key until it is back", 409)
        if not _signs_out(row):
            raise Problem("runner_version", f"Update {row['label']} to use the shared key from here", 409)
    now = H.now()
    allowed = names(runtime)
    for row in rows:
        subject = SHARED_WITH + row["id"]
        for other in c.execute("SELECT g.id FROM credential_grants g JOIN credentials cr ON cr.id=g.credential_id "
                               "WHERE g.subject=? AND g.revoked_at IS NULL AND g.credential_id<>? AND cr.env IN (%s)"
                               % ",".join("?" * len(allowed)), (subject, credential_id, *allowed)).fetchall():
            c.execute("UPDATE credential_grants SET revoked_at=?, revoked_by=? WHERE id=?", (now, who.actor, other["id"]))
        if not c.execute("SELECT 1 FROM credential_grants WHERE credential_id=? AND subject=? AND revoked_at IS NULL",
                         (credential_id, subject)).fetchone():
            c.execute("INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
                      (H.new_id(), credential_id, subject, who.actor, now))
    for row in marked:
        _resume(c, who, row, runtime)
    # In the order every list of computers here uses.
    return sorted(rows, key=lambda row: (row["label"].lower(), row["id"]))


def _resume(c, who, runner, runtime):
    """Lift a Clear key or Switch to subscription on `runner` for `runtime`: here, and on the computer, which keeps a
    mark of its own and does not fetch a key while it has it, through the Use the shared key job that removes it."""
    _queue(c, who, runner, runtime, "", "use_shared_key", "use the shared key")
    for table in ("model_login_cleared", "model_login_key_off"):
        c.execute(f"DELETE FROM {table} WHERE runner_id=? AND runtime=? AND profile=''", (runner["id"], runtime))


def share_key(c, who, rid, runtime, key, env="", share=(), auth=None, vault=None):
    """Add connection > API key > "Shared with: [computers]": the pasted key is stored once, as a shared key, and
    granted to this computer and each one listed. Like an "Every computer" key in Credentials, it widens a company
    secret to more computers, so it is a credential administrator's change; it is used at each computer's next key
    fetch, as a new "Every computer" key is (no per-computer check: that queue is one computer's)."""
    from . import computer_keys
    from .credentials import require_admin
    _person_only(who, "share a key")
    _operator(c, who, rid, auth)
    _key_runtime(runtime)
    require_admin(c, who, vault.admins)
    runner = _runner(c, rid)
    allowed = computer_keys.names(runtime)
    env = env or allowed[0]
    if env not in allowed:
        raise Problem("env", f"A {runtime} key is stored as " + " or ".join(allowed), 422)
    value = str(key or "").strip()
    if not value or len(value) > 4000 or any(ch in value for ch in "\r\n\0"):
        raise Problem("credential", "Paste the key as one line", 422)
    if not vault.configured:
        raise Problem("vault_unavailable", "Credentials are not set up on this server, so a key cannot be stored", 503)
    cid, now = H.new_id(), H.now()
    ciphertext, nonce = vault.cipher.encrypt(c, cid, value)
    preview = value[:3] + "…" + value[-3:] if len(value) >= 12 else "••••••"
    c.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,updated_by,"
              "created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (cid, f"{env} shared from {runner['label']}", "api_key", env, preview, ciphertext, nonce,
               "computers:" + rid, now, now, who.actor, who.actor))
    rows = _share_with(c, who, share, cid, rid, runtime)
    H.event(c, who.actor, "computer_key.shared", rid, {"runtime": runtime, "env": env, "credential": cid,
                                                       "computers": [row["id"] for row in rows]})
    return {"shared": {"id": cid, "env": env, "preview": preview},
            "computers": [{"id": row["id"], "label": row["label"], "online": _online(row)} for row in rows]}


def attach_shared_key(c, who, rid, runtime, credential_id, auth=None, vault=None):
    """Add connection > API key > "Use a shared key": this computer runs `runtime` on a key already shared (with every
    computer, or with others by name). A credential administrator's change, as sharing it was."""
    from . import computer_keys
    from .credentials import COMPUTERS, require_admin
    _person_only(who, "use a shared key")
    _operator(c, who, rid, auth)
    _key_runtime(runtime)
    require_admin(c, who, vault.admins)
    runner = _runner(c, rid)
    row = c.execute("SELECT * FROM credentials WHERE id=? AND ciphertext IS NOT NULL", (credential_id,)).fetchone()
    shared = row and c.execute("SELECT 1 FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL AND "
                               "(subject=? OR subject LIKE ?)", (credential_id, COMPUTERS,
                                                                 computer_keys.SHARED_WITH + "%")).fetchone()
    if not shared or row["env"] not in computer_keys.names(runtime):
        raise Problem("credential", f"Choose a shared {runtime} key", 422)
    _share_with(c, who, (), credential_id, rid, runtime)
    H.event(c, who.actor, "computer_key.attached", rid, {"runtime": runtime, "credential": credential_id})
    return {"shared": {"id": row["id"], "env": row["env"], "preview": row["preview"]},
            "computers": [{"id": r["id"], "label": r["label"]} for r in _shared_users(c, runner, runtime)]}


def shared_choices(c, runtime):
    """The shared keys a credential administrator can attach to a computer for `runtime`: [{id, name, env, preview,
    every}], never a value."""
    from . import computer_keys
    from .credentials import COMPUTERS
    out = []
    for row in c.execute("SELECT cr.id, cr.name, cr.env, cr.preview, max(g.subject=?) AS every FROM credentials cr "
                         "JOIN credential_grants g ON g.credential_id=cr.id AND g.revoked_at IS NULL "
                         "WHERE cr.ciphertext IS NOT NULL AND (g.subject=? OR g.subject LIKE ?) GROUP BY cr.id "
                         "ORDER BY lower(cr.name), cr.id", (COMPUTERS, COMPUTERS, computer_keys.SHARED_WITH + "%")):
        if row["env"] in computer_keys.names(runtime):
            out.append({"id": row["id"], "name": row["name"], "env": row["env"], "preview": row["preview"],
                        "every": bool(row["every"])})
    return out


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
    runner = c.execute('SELECT readiness,capabilities FROM runners WHERE id=?', (rid,)).fetchone()
    ready = H._json(runner['readiness'], {}) or {}
    current = computer_logins(runner, ready)
    if profile and current:
        # A named default is the same login, so cleanup must still refuse it while bots use it.
        if profile != ready['computer_profile']:
            return []
    record = providers.load(c, settings)
    out = []
    active = "" if every else "AND b.state='active' "
    for row in c.execute("SELECT a.bot, b.display_name, bc.config FROM assignments a JOIN bots b ON b.id=a.bot "
                         f"{active}LEFT JOIN bot_config bc ON bc.bot=a.bot WHERE a.runner_id=? "
                         "ORDER BY a.bot", (rid,)):
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
    return (f"{_names([b['name'] for b in bots])} run on it. Move them to another connection on the Bots page "
            "first")


def hold_removals(c, settings, bots=None):
    """Refuse a change that makes a bot (any of `bots`, or any bot) run on a subscription being taken off its
    computer: a placement, a model or tool change, the company's default, a shared bot's original. Called after the
    change is written, inside its transaction, so the refusal undoes it. A removal is held from its ask to its end,
    the commit point and the deletion after it included."""
    for row in c.execute(f"SELECT runner_id, runtime, profile FROM model_logins WHERE kind='remove' AND {ACTIVE_SQL}").fetchall():
        on = [b for b in _bots_on(c, row["runner_id"], row["runtime"], row["profile"], settings, True)
              if bots is None or b["bot"] in bots]
        if on:
            label = (c.execute("SELECT label FROM runners WHERE id=?", (row["runner_id"],)).fetchone() or ["the computer"])[0]
            raise Problem("removing", f"{row['profile']} is being removed from {label}, and {_names([b['name'] for b in on])} "
                          "would run on it. Wait until it is removed, or choose another connection on the Bots page", 409,
                          extra={"bots": on})


def removing(c, rid, profile):
    """Whether `profile` is being taken off computer `rid` (a removal not yet ended): no bot chooses it meanwhile."""
    return bool(profile) and c.execute(f"SELECT 1 FROM model_logins WHERE runner_id=? AND profile=? AND kind='remove' "
                                       f"AND {ACTIVE_SQL}", (rid, profile)).fetchone() is not None


def remove(c, who, rid, runtime, profile, settings, auth=None):
    """Take a named subscription's login for `runtime` off a computer. Refused while any bot here runs on it, naming
    them: a person moves them on the Bots page first."""
    _person_only(who, "remove a subscription")
    _operator(c, who, rid, auth)
    if not profile:
        raise Problem("kind", "Only a named subscription can be removed; sign the computer's own login out instead", 422)
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
    c.execute("UPDATE model_logins SET pending_code=?, code_sent=1, updated_at=? WHERE id=?",
              (code, H.now(), lid))
    return view(_login(c, rid, lid))


def cancel(c, who, rid, lid, auth=None):
    _operator(c, who, rid, auth)
    sweep(c)
    row = _login(c, rid, lid)
    if row["state"] == "finishing":
        if row["kind"] == "remove":
            raise Problem("state", "It is already being removed, so it can't be cancelled now", 409)
        what = "key" if row["kind"] in KEY_JOBS else "account"
        raise Problem("state", f"The new {what} is already being put in place, so it can't be cancelled now", 409)
    if row["kind"] in KEY_JOBS and row["state"] in ACTIVE:
        from .computer_keys import discard
        discard(c, lid)
    if row["state"] in ACTIVE:
        c.execute("UPDATE model_logins SET state='cancelled', url='', user_code='', pending_code=NULL, updated_at=? "
                  "WHERE id=?", (H.now(), lid))
        H.event(c, who.actor, "runner.login.cancel", rid, {"runtime": row["runtime"]})
    return view(_login(c, rid, lid))


def pending(c, runner_id, swept=True):
    """What this runner should be running: every live sign-in, and a pasted code not yet taken."""
    if swept:
        sweep(c)
    rows = c.execute(f"SELECT * FROM model_logins WHERE runner_id=? AND {ACTIVE_SQL} ORDER BY created_at",
                     (runner_id,)).fetchall()
    return [{"id": r["id"], "runtime": r["runtime"], "profile": r["profile"], "expires_at": r["expires_at"],
             **({"kind": r["kind"]} if r["kind"] != "sign_in" else {}),
             **({"code": r["pending_code"]} if r["pending_code"] else {})} for r in rows]


def report(c, runner_id, lid, body, settings=None):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    sweep(c)
    row = c.execute("SELECT * FROM model_logins WHERE id=?", (lid,)).fetchone()
    if row["state"] in TERMINAL:
        return {"state": row["state"]}
    if body.state in ("signed_in", "signed_out", "done") and body.state not in VERDICTS[row["kind"]]:
        raise Problem("state", f"A {row['kind'].replace('_', ' ')} does not end {body.state.replace('_', ' ')}", 422)
    if body.state == "finishing" and row["kind"] not in (*COMMIT_KINDS, *KEY_JOBS):
        raise Problem("state", "Only a switch or a new key puts a new login in place", 422)
    # A removal's commit point: a bot that came to run on the subscription since it was asked for (moved to this
    # computer, or onto this tool) stops it here, before the runner signs anything out or deletes anything.
    if body.state == "finishing" and row["kind"] == "remove" and row["state"] != "finishing":
        bots = _bots_on(c, runner_id, row["runtime"], row["profile"], settings, True)
        if bots:
            c.execute("UPDATE model_logins SET state='failed', message=?, updated_at=? WHERE id=?",
                      (redact(_in_use(bots)), H.now(), lid))
            H.event(c, "runner:" + runner_id, "runner.login.failed", runner_id, {"runtime": row["runtime"]})
            return {"state": "failed"}
    if body.state == "finishing" and row["state"] != "finishing":
        # From here it cannot be cancelled, and it has a little time of its own to finish in.
        c.execute("UPDATE model_logins SET expires_at=MAX(expires_at, ?) WHERE id=?",
                  (H.shift(H.now(), seconds=FINISH_SECONDS), lid))
        if row["kind"] == "use_key":
            # The computer checked the new key and puts it in place only once this is taken: it is this computer's
            # key from now, so a key fetch made meanwhile hands it out, not the one it replaces.
            from . import computer_keys
            computer_keys.confirm(c, row["runner_id"], lid)
    state = body.state if ORDER[body.state] >= ORDER[row["state"]] else row["state"]
    terminal = state in ("signed_in", "signed_out", "done", "failed")
    # Finishing, the person has nothing left to open or enter.
    done_with_page = terminal or state == "finishing"
    url = "" if done_with_page else clean_url(body.url) or row["url"]
    code = "" if done_with_page else clean_user_code(body.code) or row["user_code"]
    lines = clean_lines(body.lines) or json.loads(row["lines"] or "[]")
    pending_code = None if terminal or body.code_taken else row["pending_code"]
    c.execute("UPDATE model_logins SET state=?, url=?, user_code=?, lines=?, message=?, pending_code=?, "
              "updated_at=? WHERE id=?",
              (state, url, code, json.dumps(lines), redact(body.message), pending_code, H.now(), lid))
    if terminal:
        H.event(c, "runner:" + runner_id, "runner.login." + state, row["runner_id"], {"runtime": row["runtime"]})
    # Switch to API key: only now is the key this computer's and the switch's marks lifted (confirm). Until the runner
    # says so the subscription is what the login runs on, so a failure after its commit point, which the runner undoes
    # on the computer, has nothing to undo here.
    if terminal and row["kind"] in KEY_JOBS:
        from . import computer_keys
        if state == "done":
            computer_keys.confirm(c, row["runner_id"], lid)
        else:
            computer_keys.discard(c, lid)
    # A person signed this login in again: it is no longer signed out on purpose. A sign-out that did not take
    # leaves the login as it was, so it is not marked either.
    if (state == "signed_in" or state == "failed" and row["kind"] == "sign_out") and terminal:
        c.execute("DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
                  (row["runner_id"], row["runtime"], row["profile"]))
    # Removed: nothing of it stays here to fill a line again (its sign-out mark, its manual weekly reading).
    if state == "done" and row["kind"] == "remove":
        from .subscriptions import weekly_key
        c.execute("DELETE FROM model_login_cleared WHERE runner_id=? AND runtime=? AND profile=?",
                  (row["runner_id"], row["runtime"], row["profile"]))
        c.execute("DELETE FROM registry_metadata WHERE key=?",
                  (weekly_key(row["runner_id"], row["profile"], row["runtime"]),))
    # Switched to a subscription: the key is off this login from now on (the runner has deleted its copies), and the
    # computer's own key is deleted from the vault, as Clear key does.
    if state == "signed_in" and row["kind"] == "to_subscription":
        from . import computer_keys
        computer_keys.delete(c, row["runner_id"], row["runtime"])
        c.execute("INSERT INTO model_login_key_off(runner_id,runtime,profile,set_at,set_by) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET set_at=excluded.set_at, set_by=excluded.set_by",
                  (row["runner_id"], row["runtime"], row["profile"], H.now(), row["requested_by"]))
    # A login switched to another account or key (or the key put back): a usage limit held on the old one is not the
    # new one's, so its bots are freed and one probe turn tests it (execution.lift_after_login_switch).
    if settings is not None and terminal and state in ("signed_in", "done") and row["kind"] in ("switch", "to_subscription", "to_key", "use_key"):
        from .execution import lift_after_login_switch
        lift_after_login_switch(c, settings, H.KEEPER, runner_id, row["runtime"])
    return {"state": state}


def _key_runtime(runtime):
    from .computer_keys import RUNTIMES as KEY_RUNTIMES
    if runtime not in KEY_RUNTIMES:
        raise Problem("kind", "A key signs in only " + ", ".join(KEY_RUNTIMES) + " on a computer", 422)


def _key_source(runner, runtime):
    """What the computer said signs `runtime` in: {name, source} ("computer", "terminal" or "shared"), or {}."""
    found = H._json(runner["readiness"], {}) or {}
    key = ((found.get("runtimes") or {}).get(runtime) or {}).get("key")
    return key if isinstance(key, dict) else {}


def _shared_users(c, runner, runtime):
    """The computers the shared key `runner` runs `runtime` on serves (`computer_keys.users`), it included; [] when it
    runs on no shared key."""
    from . import computer_keys
    return computer_keys.users(c, runner, runtime)


def _may(c, who, row, auth):
    try:
        _operator(c, who, row["id"], auth)
    except Problem:
        return False
    return True


def key_info(c, who, rid, runtime, auth=None, vault=None):
    """For Clear key / Replace key: which key signs `runtime` in on this computer, and, when it is the company's
    shared key, every computer it serves (the "this computer only / all of them" ask), each with whether it is online
    and whether this person may change it there. Previews only, never a value."""
    from . import computer_keys
    from .credentials import COMPUTERS, administrator, granted
    _operator(c, who, rid, auth)
    _key_runtime(runtime)
    runner = _runner(c, rid)
    own = computer_keys.active(c, rid, runtime)
    reported = _key_source(runner, runtime)
    shared = None if own else computer_keys.uses_shared(c, runner, runtime)
    # The computer's last report can still say "shared" after the key stopped serving it (cleared here, its grant
    # revoked, the key deleted) until its next heartbeat: then no shared key is described.
    source = ("computer" if own else "shared" if shared else
              "" if reported.get("source") == "shared" else reported.get("source") or "")
    # A company key's name and preview are a credential administrator's to read, as in Credentials; the computer's
    # operator still reads its own key's.
    key_admin = bool(vault and administrator(c, who, vault.admins))
    out = {"runtime": runtime, "source": source, "name": own["env"] if own else reported.get("name", "") if source else "",
           "preview": own["preview"] if own else shared["preview"] if shared and key_admin else "",
           "cleared": cleared(c, rid, runtime), "names": list(computer_keys.names(runtime)), "computers": []}
    if source == "shared":
        out["computers"] = [{"id": row["id"], "label": row["label"], "online": _online(row),
                             "updated": _handles_keys(row), "may": _may(c, who, row, auth)}
                            for row in _shared_users(c, runner, runtime)]
        out["shared"] = {"env": shared["env"], "every": shared["id"] in granted(c, COMPUTERS)}
        if key_admin:
            out["shared"].update(id=shared["id"], name=shared["name"])
        out["can_replace_all"] = key_admin
    # Add connection > API key: the shared keys an administrator may attach here instead of pasting one.
    if key_admin:
        out["can_share"], out["shared_choices"] = True, shared_choices(c, runtime)
    return out


def _clear_here(c, who, runner, runtime, queue):
    """Clear `runtime`'s key on one computer: its own vault key is deleted, it is marked so no key (the shared one
    included) signs it in again, and the runner is told to delete its copies, at once (`queue`) or, offline, at its
    next key fetch (`clear_key` on the mark). Returns the job, or None when it waits for the computer."""
    from . import computer_keys
    rid = runner["id"]
    row = None
    if queue:
        row, new = _queue(c, who, runner, runtime, "", "clear_key", "clear its key")
        if not new:
            return row
    computer_keys.delete(c, rid, runtime)
    mark_key_cleared(c, who, rid, runtime)
    H.event(c, who.actor, "computer_key.cleared", rid, {"runtime": runtime, "queued": bool(row)})
    return row


def mark_key_cleared(c, who, rid, runtime):
    """No key signs `runtime` in on this computer again until a person adds one or chooses the shared key, and its next
    key fetch tells it to delete its copies and log out a login a key made (`clear_key`)."""
    now = H.now()
    c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
              "VALUES(?,?,?,?,?,1) ON CONFLICT(runner_id,runtime,profile) DO UPDATE SET cleared_at=excluded.cleared_at, "
              "cleared_by=excluded.cleared_by, clear_key=1", (rid, runtime, "", now, who.actor))
    from .subscription_refresh import invalidate_signin
    invalidate_signin(c, rid, "", runtime, now)


def clear_key(c, who, rid, runtime, scope="this", auth=None):
    """Clear key: the key that signs `runtime` in on this computer is removed and stays removed (the shared key does
    not sign it back in) until a person adds a key or chooses the shared key; bots and assignments stay.
    `scope` "all", for the company's shared key: the same on every computer it serves, each on a current runner. One
    that is offline clears its copy when it is next online, at its first key check."""
    _person_only(who, "clear a key")
    _operator(c, who, rid, auth)
    _key_runtime(runtime)
    runner = _runner(c, rid)
    if scope != "all":
        return {"computers": [{"id": rid, "label": runner["label"], "login": view(_clear_here(c, who, runner, runtime, True)),
                               "waiting": False}]}
    rows = _shared_users(c, runner, runtime)
    if not rows:
        raise Problem("state", "This computer does not use a shared key", 409)
    refused = [row["label"] for row in rows if not _may(c, who, row, auth)]
    if refused:
        raise Problem("forbidden", "You cannot change the key on " + ", ".join(refused) + "; clear it on this computer "
                      "only, or ask an admin", 403)
    # Offline too: an older runner has no clear_key handler, so a mark left for it would never clear its copy.
    stale = [row["label"] for row in rows if not _handles_keys(row)]
    if stale:
        raise Problem("runner_version", "Update " + ", ".join(stale) + " to clear its key from here, or delete the "
                      "shared key in Credentials", 409)
    out = []
    for row in rows:
        job = _clear_here(c, who, row, runtime, _online(row))
        out.append({"id": row["id"], "label": row["label"], "login": view(job) if job else None, "waiting": job is None})
    return {"computers": out}


def replace_key(c, who, rid, runtime, key, env="", scope="this", auth=None, vault=None, switch=False):
    """Replace key (Add key on a computer with none): the new key is stored in the vault for this computer and the
    runner is told to fetch it, check it and use it; only then is the old one deleted. A failed check leaves the old
    key working. `scope` "all", for the company's shared key: its value is replaced for every computer it serves, which
    is a credential administrator's change as in Credentials; each fetches it at once, or when next online.
    `switch` is Switch to API key for the computer's own login on a subscription (`to_key`): the same check, and once
    the key is in place the subscription is signed out."""
    from . import computer_keys
    from .credentials import require_admin
    _person_only(who, "switch to an API key" if switch else "change a key")
    _operator(c, who, rid, auth)
    _key_runtime(runtime)
    runner = _runner(c, rid)
    if switch:
        if scope != "this":
            raise Problem("scope", "Switch to API key is for this computer's own login", 422)
        caps = manifests.caps(runtime)
        # The key is checked before the subscription is signed out, by the CLI's key login or a key check of its own.
        if runtime not in SIGN_OUT_RUNTIMES or not (caps.get("api_key_login") or caps.get("api_key_check")):
            raise Problem("kind", "You can't switch to an API key from here for " + runtime, 422)
        if runner["platform"] in caps.get("switch_unavailable_on", []):
            raise Problem("kind", f"You can't switch {runtime} to an API key on {runner['label']} from here: it keeps "
                          "the login in the system keychain. Sign out, then add a key", 422)
        row, new = _queue(c, who, runner, runtime, "", "to_key", "switch to an API key")
        if not new:
            raise Problem("state", "A switch to an API key is already running on that computer; wait for it to finish",
                          409)
        stored = computer_keys.store(c, vault, who, runner, runtime, key, row["id"], env)
        H.event(c, who.actor, "runner.login.to_key", rid, {"runtime": runtime})
        return {**view(row), "key": stored}
    if scope == "all":
        require_admin(c, who, vault.admins)
        shared = computer_keys.shared(c, runtime, rid)
        reported = _key_source(runner, runtime)
        served = _shared_users(c, runner, runtime)
        if not shared or not served:
            raise Problem("state", "This computer does not use a shared key", 409)
        # The kind the person chose (Claude takes an OAuth token or an API key), else the one this computer uses:
        # each kind is its own credential, and only the one that kind names is replaced.
        env = env or (reported.get("name") if reported.get("name") in shared else next(iter(shared)))
        if env not in computer_keys.names(runtime):
            raise Problem("env", f"A {runtime} key is stored as " + " or ".join(computer_keys.names(runtime)), 422)
        if env not in shared:
            raise Problem("env", "The shared key is " + " and ".join(shared) + ", not " + env + "; replace it as that "
                          "kind, or add a " + env + " in Credentials", 409)
        row = shared[env]
        value = str(key or "").strip()
        if not value or len(value) > 4000 or any(ch in value for ch in "\r\n\0"):
            raise Problem("credential", "Paste the key as one line", 422)
        ciphertext, nonce = vault.cipher.encrypt(c, row["id"], value)
        preview = value[:3] + "…" + value[-3:] if len(value) >= 12 else "••••••"
        c.execute("UPDATE credentials SET ciphertext=?, nonce=?, preview=?, revision=revision+1, updated_at=?, updated_by=? "
                  "WHERE id=?", (ciphertext, nonce, preview, H.now(), who.actor, row["id"]))
        H.event(c, who.actor, "credential.updated", row["id"], {"secret_changed": True, "via": "computers"})
        return {"shared": {"id": row["id"], "env": row["env"], "preview": preview},
                "computers": [{"id": r["id"], "label": r["label"], "online": _online(r)} for r in served]}
    row, new = _queue(c, who, runner, runtime, "", "use_key", "use a new key")
    if not new:
        raise Problem("state", "A new key is already being checked on that computer; wait for it to finish", 409)
    stored = computer_keys.store(c, vault, who, runner, runtime, key, row["id"], env)
    return {**view(row), "key": stored}
