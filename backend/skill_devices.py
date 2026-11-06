"""The skills helper's devices: a person's own Claude Code, enrolled with a device code, holding a token that reaches
the skills marketplace and the two reports back, and nothing else (spec: reports/2026-11-05-company-skills §4, §5, §7).

Enrolling. `clients/skill_helper.mjs setup` asks for a code without signing in (POST skills/devices/codes, capped
per address the way agent pairings are), prints the address and the short code, and polls POST skills/devices/token
with its secret device code. The person opens the address signed in, sees the label, ticks "Share which skills I
use" or not, and approves (or denies). The next poll mints a `tico_sd_` token, stored here only as its sha256, and
hands it over exactly once.

The token. `Auth.authenticate` turns it into that person (scope `skills-device`, via_token) on the routes in ROUTES
and refuses it with 403 everywhere else; a revoked token is refused like an unknown one (401). A device may also
revoke itself (`remove`), and no other device.

Reports. POST skills/devices/receipt says which skill versions the helper staged; POST skills/usage says which
skills were used, only while the person's reporting is on. A usage row holds the skill, who, where (the hashed
session) and when: never a prompt, an argument or a file. Unknown names are dropped and a repeat of one (session,
tool use) pair is ignored.
"""

import re
import secrets
from datetime import timezone

from fastapi import Request
from pydantic import Field

from .models import Contract
from .store import H, Problem, digest

PREFIX = "tico_sd_"
SCOPE = "skills-device"
CODE_TTL = 600
POLL_EVERY = 5
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODES_PER_CLIENT_HOUR = 10
CODES_PENDING_MAX = 50
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
EVENTS_MAX = 100
USAGE_MAX_AGE_DAYS = 90
TRIGGERS = ("model", "person")
CODES_PATH = "/api/v2/skills/devices/codes"
TOKEN_PATH = "/api/v2/skills/devices/token"
# The (method, path) pairs a device token reaches. DELETE of a device is limited to its own id in `revoke`.
ROUTES = (
    ("GET", re.compile(r"/api/v2/skills/marketplace\.json")),
    ("GET", re.compile(r"/api/v2/skills/bundles/[^/]+")),
    ("GET", re.compile(r"/api/v2/skills/plugins/[^/]+/[^/]+\.zip")),
    ("POST", re.compile(r"/api/v2/skills/usage")),
    ("POST", re.compile(r"/api/v2/skills/devices/receipt")),
    ("DELETE", re.compile(r"/api/v2/skills/devices/[^/]+")),
)
REFUSAL = "A skills helper token only reads the skills marketplace and reports what it staged and used"
FIELDS = ("id", "label", "reporting_enabled", "claude_version", "last_seen_at", "created_at")


def allowed(method, path):
    return any(method == m and pattern.fullmatch(path or "") for m, pattern in ROUTES)


def public(request):
    """The two calls the helper makes before it has a token. Everything else under skills/ needs a sign-in or a token."""
    return request.method == "POST" and request.url.path in (CODES_PATH, TOKEN_PATH)


class SkillDeviceCodeCreate(Contract):
    label: str = Field("", max_length=120)


class SkillDeviceDecision(Contract):
    reporting_enabled: bool = False


class SkillDeviceTokenExchange(Contract):
    device_code: str = Field(..., min_length=1, max_length=200)


class SkillDevicePatch(Contract):
    reporting_enabled: bool


class SkillStaged(Contract):
    name: str = Field(..., max_length=200)
    sha256: str = Field(..., max_length=200)


class SkillReceipt(Contract):
    staged: list[SkillStaged] = Field(default_factory=list, max_length=500)
    claude_version: str = Field("", max_length=80)


class SkillUsageEvent(Contract):
    name: str = Field(..., max_length=200)
    trigger: str = Field(..., max_length=20)
    session_key: str = Field(..., min_length=1, max_length=128)
    tool_use_id: str = Field(..., min_length=1, max_length=128)
    used_at: str = Field("", max_length=40)


class SkillUsage(Contract):
    events: list[SkillUsageEvent] = Field(..., max_length=EVENTS_MAX)


def _code(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _shown(code):
    return code[:4] + "-" + code[4:]


def _signed_in(who, what):
    """A person, as themselves in a browser or with their own API token: never a bot, the Assistant, support, or a
    device token."""
    if who.role not in ("owner", "human") or who.via or who.support or who.scope:
        raise Problem("forbidden", what + " is for a signed-in person", 403)


def _approver(who):
    _signed_in(who, "Connecting Claude Code")
    if who.via_token:
        raise Problem("forbidden", "Approve from a signed-in browser: an API token cannot connect a device", 403)


def create_code(c, body, client, public_url):
    """The helper asks to be connected. Returns its secret device code (kept by the helper) and the short code."""
    now = H.now()
    c.execute("DELETE FROM skill_device_codes WHERE created_at<?", (H.shift(now, hours=-24),))
    c.execute("UPDATE skill_device_codes SET status='expired',updated_at=? WHERE status IN ('pending','approved') "
              "AND expires_at<=?", (now, now))
    client_hash = digest("skills-device-client:" + client)
    if c.execute("SELECT count(*) FROM skill_device_codes WHERE client_hash=? AND created_at>?",
                 (client_hash, H.shift(now, hours=-1))).fetchone()[0] >= CODES_PER_CLIENT_HOUR:
        raise Problem("rate_limited", "Too many connect requests from this address; try again in an hour", 429,
                      retryable=True)
    if c.execute("SELECT count(*) FROM skill_device_codes WHERE status='pending'").fetchone()[0] >= CODES_PENDING_MAX:
        raise Problem("rate_limited", "Too many connect requests are waiting; try again in a few minutes", 429,
                      retryable=True)
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not c.execute("SELECT 1 FROM skill_device_codes WHERE user_code=?", (code,)).fetchone():
            break
    else:
        raise Problem("unavailable", "Could not make a code; try again", 503, retryable=True)
    device_code = secrets.token_urlsafe(32)
    c.execute("INSERT INTO skill_device_codes(device_code_hash,user_code,label,status,expires_at,created_at,updated_at,"
              "client_hash) VALUES(?,?,?,'pending',?,?,?,?)",
              (digest(device_code), code, body.label, H.shift(now, seconds=CODE_TTL), now, now, client_hash))
    return {"device_code": device_code, "user_code": _shown(code),
            "verification_url": public_url + "/#/skills/connect?code=" + _shown(code),
            "expires_in": CODE_TTL, "interval": POLL_EVERY}


def _pending(c, user_code):
    code = _code(user_code)
    row = c.execute("SELECT * FROM skill_device_codes WHERE user_code=? AND status='pending' AND expires_at>?",
                    (code, H.now())).fetchone() if len(code) == 8 else None
    if not row:
        raise Problem("not_found", "That code is not valid or has expired. Run setup again for a new one", 404)
    return row


def preview(c, who, user_code):
    _approver(who)
    row = _pending(c, user_code)
    return {"user_code": _shown(row["user_code"]), "label": row["label"], "expires_at": row["expires_at"]}


def decide(c, who, user_code, approve, reporting_enabled=False):
    _approver(who)
    row = _pending(c, user_code)
    status = "approved" if approve else "denied"
    c.execute("UPDATE skill_device_codes SET status=?,person_id=?,reporting_enabled=?,updated_at=? WHERE user_code=?",
              (status, H.actor_id(who.actor), int(bool(approve and reporting_enabled)), H.now(), row["user_code"]))
    H.event(c, who.actor, "skill_device." + status, _shown(row["user_code"]), {"label": row["label"]})
    return {status: True, "label": row["label"]}


def exchange(c, device_code):
    """("pending" | "gone" | "token", value). The caller raises after the transaction, so an expiry it marks stays."""
    row = c.execute("SELECT * FROM skill_device_codes WHERE device_code_hash=?", (digest(device_code),)).fetchone()
    if not row:
        return "unknown", None
    now = H.now()
    if row["status"] in ("pending", "approved") and row["expires_at"] <= now:
        c.execute("UPDATE skill_device_codes SET status='expired',updated_at=? WHERE device_code_hash=?",
                  (now, row["device_code_hash"]))
        return "gone", "expired"
    if row["status"] == "pending":
        return "pending", None
    if row["status"] != "approved":
        return "gone", row["status"]
    token, device_id = PREFIX + secrets.token_urlsafe(32), "skd_" + secrets.token_hex(10)
    c.execute("INSERT INTO skill_devices(id,person_id,label,token_hash,reporting_enabled,created_at,updated_at) "
              "VALUES(?,?,?,?,?,?,?)", (device_id, row["person_id"], row["label"], digest(token),
                                        row["reporting_enabled"], now, now))
    c.execute("UPDATE skill_device_codes SET status='used',device_id=?,updated_at=? WHERE device_code_hash=?",
              (device_id, now, row["device_code_hash"]))
    H.event(c, H.human_actor(row["person_id"]), "skill_device.enrolled", device_id, {"label": row["label"]})
    return "token", {"token": token, "device_id": device_id, "reporting_enabled": bool(row["reporting_enabled"])}


def listing(c, who):
    _signed_in(who, "Your Claude Code devices")
    rows = c.execute("SELECT " + ",".join(FIELDS) + " FROM skill_devices WHERE person_id=? AND revoked_at IS NULL "
                     "ORDER BY created_at DESC", (H.actor_id(who.actor),)).fetchall()
    return [{**dict(r), "reporting_enabled": bool(r["reporting_enabled"])} for r in rows]


def _own(c, who, device_id):
    row = c.execute("SELECT * FROM skill_devices WHERE id=? AND revoked_at IS NULL", (device_id,)).fetchone()
    if not row or row["person_id"] != H.actor_id(who.actor):
        raise Problem("not_found", "Device not found", 404)
    return row


def set_reporting(c, who, device_id, on):
    _signed_in(who, "Reporting")
    row = _own(c, who, device_id)
    c.execute("UPDATE skill_devices SET reporting_enabled=?,updated_at=? WHERE id=?", (int(on), H.now(), row["id"]))
    H.event(c, who.actor, "skill_device.reporting", row["id"], {"on": bool(on)})
    return {"id": row["id"], "reporting_enabled": bool(on)}


def revoke(c, auth, who, device_id):
    """A person revokes their own device; an admin any; a device only itself."""
    if who.scope == SCOPE:
        if device_id != who.token_id:
            raise Problem("forbidden", "A device revokes only itself", 403)
        row = c.execute("SELECT * FROM skill_devices WHERE id=?", (device_id,)).fetchone()
    else:
        _signed_in(who, "Revoking a device")
        row = c.execute("SELECT * FROM skill_devices WHERE id=?", (device_id,)).fetchone()
        if row and row["person_id"] != H.actor_id(who.actor) and not auth.bot_admin(who):
            row = None
    if not row:
        raise Problem("not_found", "Device not found", 404)
    if row["revoked_at"]:
        raise Problem("revoked", "This device is already revoked", 409)
    now = H.now()
    c.execute("UPDATE skill_devices SET revoked_at=?,updated_at=? WHERE id=?", (now, now, device_id))
    H.event(c, who.actor, "skill_device.revoked", device_id, {"label": row["label"]})
    return {"id": device_id, "revoked": True}


def _device(c, who):
    if who.scope != SCOPE:
        raise Problem("forbidden", "This route takes a skills helper token", 403)
    row = c.execute("SELECT * FROM skill_devices WHERE id=? AND revoked_at IS NULL", (who.token_id,)).fetchone()
    if not row:
        raise Problem("identity", "Invalid credential", 401)
    return row


def _skills_by_name(c, names):
    names = sorted({n for n in names if NAME.fullmatch(n)})
    if not names:
        return {}
    marks = ",".join("?" * len(names))
    return {r["name"]: r["id"] for r in c.execute(
        "SELECT id,name FROM skills WHERE deleted_at IS NULL AND name IN (" + marks + ")", names)}


def receipt(c, who, body):
    """What the helper staged now replaces what it staged before, for this person."""
    device = _device(c, who)
    person, now = device["person_id"], H.now()
    staged = {s.name: s.sha256.lower() for s in body.staged if SHA.fullmatch(s.sha256.lower())}
    known = _skills_by_name(c, staged)
    for name, skill_id in known.items():
        c.execute("INSERT INTO skill_receipts(target_type,target_id,skill_id,sha256,source,staged_at) "
                  "VALUES('Human',?,?,?,'helper',?) ON CONFLICT(target_type,target_id,skill_id) DO UPDATE SET "
                  "sha256=excluded.sha256,source=excluded.source,staged_at=excluded.staged_at",
                  (person, skill_id, staged[name], now))
    keep = sorted(known.values())
    c.execute("DELETE FROM skill_receipts WHERE target_type='Human' AND target_id=? AND source='helper'"
              + (" AND skill_id NOT IN (" + ",".join("?" * len(keep)) + ")" if keep else ""), (person, *keep))
    c.execute("UPDATE skill_devices SET claude_version=?,last_seen_at=?,updated_at=? WHERE id=?",
              (body.claude_version or device["claude_version"], now, now, device["id"]))
    return {"staged": len(known), "dropped": len(body.staged) - len(known)}


def _when(value, now):
    """An event's time as this server writes times: now when missing, unreadable or ahead of now."""
    at = H.parse_ts(value) if value else None
    if at is None or at.tzinfo is None:
        return now
    stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return min(stamp, now)


def usage(c, who, body):
    device = _device(c, who)
    if not device["reporting_enabled"]:
        raise Problem("reporting_off", "Reporting is off for this device", 403)
    person, now = device["person_id"], H.now()
    oldest = H.shift(now, days=-USAGE_MAX_AGE_DAYS)
    known = _skills_by_name(c, [e.name for e in body.events])
    staged = {r["skill_id"]: r["sha256"] for r in c.execute(
        "SELECT skill_id,sha256 FROM skill_receipts WHERE target_type='Human' AND target_id=?", (person,))}
    recorded = duplicates = dropped = 0
    for e in body.events:
        skill_id, used_at = known.get(e.name), _when(e.used_at, now)
        if not skill_id or e.trigger not in TRIGGERS or used_at < oldest:
            dropped += 1
            continue
        inserted = c.execute("INSERT OR IGNORE INTO skill_usages(skill_id,sha256,actor_type,actor_id,trigger,source,"
                             "session_key,tool_use_id,used_at,created_at) VALUES(?,?,'Human',?,?,'helper',?,?,?,?)",
                             (skill_id, staged.get(skill_id), person, e.trigger, e.session_key, e.tool_use_id,
                              used_at, now)).rowcount
        recorded += inserted
        duplicates += 1 - inserted
    c.execute("UPDATE skill_devices SET last_seen_at=? WHERE id=?", (now, device["id"]))
    return {"recorded": recorded, "duplicates": duplicates, "dropped": dropped}


def install(app, store, auth, mutate, settings):
    def client_address(request):
        from .client_ip import client_ip, ip_bucket
        return ip_bucket(client_ip(request, settings))

    def refuse_in_rehearsal():
        if getattr(settings, "rehearsal", False):
            raise Problem("rehearsal", "Connecting devices is off in a rehearsal: this server is a copy of real data",
                          409)

    @app.post(CODES_PATH, status_code=201)
    def skill_device_code(request: Request, body: SkillDeviceCodeCreate):
        """No sign-in: the helper has nothing yet. Capped per address and in total."""
        refuse_in_rehearsal()
        with store.transaction() as c:
            return create_code(c, body, client_address(request), settings.public_url)

    @app.post(TOKEN_PATH)
    def skill_device_token(request: Request, body: SkillDeviceTokenExchange):
        """428 while the person has not decided, the token exactly once, 410 after (expired, denied or used)."""
        with store.transaction() as c:
            state, value = exchange(c, body.device_code)
        if state == "unknown":
            raise Problem("not_found", "No such device code", 404)
        if state == "pending":
            raise Problem("authorization_pending", "Waiting for approval in the browser", 428, retryable=True)
        if state == "gone":
            raise Problem(value, "This code was " + value + ". Run setup again", 410)
        return value

    @app.get("/api/v2/skills/devices/codes/{user_code}")
    def skill_device_code_preview(request: Request, user_code: str):
        with store.read() as c:
            return preview(c, request.state.identity, user_code)

    @app.post("/api/v2/skills/devices/codes/{user_code}/approve")
    def skill_device_approve(request: Request, user_code: str, body: SkillDeviceDecision):
        who = request.state.identity
        _approver(who)
        refuse_in_rehearsal()
        return mutate(request, body, lambda c: decide(c, who, user_code, True, body.reporting_enabled))

    @app.post("/api/v2/skills/devices/codes/{user_code}/deny")
    def skill_device_deny(request: Request, user_code: str, body: SkillDeviceDecision):
        who = request.state.identity
        _approver(who)
        return mutate(request, body, lambda c: decide(c, who, user_code, False))

    @app.get("/api/v2/skills/devices")
    def skill_devices_list(request: Request):
        with store.read() as c:
            return {"devices": listing(c, request.state.identity)}

    @app.patch("/api/v2/skills/devices/{device_id}")
    def skill_device_patch(request: Request, device_id: str, body: SkillDevicePatch):
        who = request.state.identity
        return mutate(request, body, lambda c: set_reporting(c, who, device_id, body.reporting_enabled))

    @app.delete("/api/v2/skills/devices/{device_id}")
    def skill_device_revoke(request: Request, device_id: str):
        # The helper's `remove` sends no Idempotency-Key, and a second revoke is a 409 anyway.
        who = request.state.identity
        return store.write(who, lambda c: revoke(c, auth, who, device_id))

    @app.post("/api/v2/skills/devices/receipt")
    def skill_device_receipt(request: Request, body: SkillReceipt):
        who = request.state.identity
        return store.write(who, lambda c: receipt(c, who, body))

    @app.post("/api/v2/skills/usage")
    def skill_usage(request: Request, body: SkillUsage):
        who = request.state.identity
        return store.write(who, lambda c: usage(c, who, body))

