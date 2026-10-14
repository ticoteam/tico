"""Signing a model CLI in on a runner from the browser.

The Computer operator, owner or admin asks for a login for one runtime on one computer; the runner starts the CLI and reports
what the CLI itself prints (a link and a one-time code, or a prompt for a pasted code). This module
keeps that relay and nothing else. It never sees the credential the CLI ends up with: the CLI
writes it on the runner's disk, and the runner only reports whether readiness turned green.

What is stored is a link, a one-time code, a few redacted CLI message lines and timestamps, for
at most `LIFETIME` seconds. A code the person pastes back is held only until the runner has
taken it, then dropped.
"""

import json
import re

from .store import H, Problem

RUNTIMES = ("codex", "claude", "musecode")
# Only the runtimes whose CLI asks for a code to be pasted back.
PASTE_RUNTIMES = ("claude",)
LIFETIME = 15 * 60
ACTIVE = ("requested", "starting", "waiting")
TERMINAL = ("signed_in", "failed", "cancelled", "expired")
ORDER = {state: i for i, state in enumerate(("requested", "starting", "waiting", "signed_in", "failed"))}
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
              "WHERE state IN ('requested','starting','waiting') AND expires_at<?", (now, now))
    c.execute("DELETE FROM model_logins WHERE created<?", (H.shift(now, days=-1),))


def view(row):
    state = row["state"]
    return {"id": row["id"], "runner_id": row["runner_id"], "runtime": row["runtime"],
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


def start(c, who, rid, runtime, profile="", auth=None):
    _operator(c, who, rid, auth)
    if runtime not in RUNTIMES:
        raise Problem("kind", "Browser sign-in works for " + ", ".join(RUNTIMES[:-1]) + " and " + RUNTIMES[-1]
                      + "; set other runtimes up from the computer's terminal", 422)
    runner = _runner(c, rid)
    sweep(c)
    running = c.execute("SELECT * FROM model_logins WHERE runner_id=? AND runtime=? AND profile=? "
                        "AND state IN ('requested','starting','waiting')", (rid, runtime, profile)).fetchone()
    if running:
        return view(running)
    if not runner["last_seen"] or runner["last_seen"] < H.shift(H.now(), seconds=-ONLINE_SECONDS):
        raise Problem("offline", "That computer is offline, so it cannot start a sign-in", 409)
    now = H.now()
    lid = H.new_id()
    c.execute("INSERT INTO model_logins(id,runner_id,runtime,profile,state,created,updated,expires_at,requested_by) "
              "VALUES(?,?,?,?,?,?,?,?,?)",
              (lid, rid, runtime, profile, "requested", now, now, H.shift(now, seconds=LIFETIME), who.actor))
    from .subscription_refresh import invalidate_signin
    invalidate_signin(c, rid, profile, runtime, now)
    H.event(c, who.actor, "runner.login.start", rid, {"runtime": runtime})
    return view(_login(c, rid, lid))


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
    if row["state"] in ACTIVE:
        c.execute("UPDATE model_logins SET state='cancelled', url='', user_code='', pending_code=NULL, updated=? "
                  "WHERE id=?", (H.now(), lid))
        H.event(c, who.actor, "runner.login.cancel", rid, {"runtime": row["runtime"]})
    return view(_login(c, rid, lid))


def pending(c, runner_id):
    """What this runner should be running: every live sign-in, and a pasted code not yet taken."""
    sweep(c)
    rows = c.execute("SELECT * FROM model_logins WHERE runner_id=? AND state IN ('requested','starting','waiting') "
                     "ORDER BY created", (runner_id,)).fetchall()
    return [{"id": r["id"], "runtime": r["runtime"], "profile": r["profile"], "expires_at": r["expires_at"],
             **({"code": r["pending_code"]} if r["pending_code"] else {})} for r in rows]


def report(c, runner_id, lid, body):
    row = c.execute("SELECT * FROM model_logins WHERE id=? AND runner_id=?", (lid, runner_id)).fetchone()
    if not row:
        raise Problem("not_found", "That sign-in does not exist", 404)
    sweep(c)
    row = c.execute("SELECT * FROM model_logins WHERE id=?", (lid,)).fetchone()
    if row["state"] in TERMINAL:
        return {"state": row["state"]}
    state = body.state if ORDER[body.state] >= ORDER[row["state"]] else row["state"]
    terminal = state in ("signed_in", "failed")
    url = "" if terminal else clean_url(body.url) or row["url"]
    code = "" if terminal else clean_user_code(body.code) or row["user_code"]
    lines = clean_lines(body.lines) or json.loads(row["lines_json"] or "[]")
    pending_code = None if terminal or body.code_taken else row["pending_code"]
    c.execute("UPDATE model_logins SET state=?, url=?, user_code=?, lines_json=?, message=?, pending_code=?, "
              "updated=? WHERE id=?",
              (state, url, code, json.dumps(lines), redact(body.message), pending_code, H.now(), lid))
    if terminal:
        H.event(c, "runner:" + runner_id, "runner.login." + state, row["runner_id"], {"runtime": row["runtime"]})
    return {"state": state}
