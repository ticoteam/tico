"""Tico HQ's side of the setup page on tico.team/setup: the `/v1/onboarding` routes and what they keep.

This part is the page's download count, the health check the page asks first, and the browser rules every
`/v1/onboarding` route shares. The brain's `/turn` comes later, uses the same rules and turns `/health` to ok.

    GET /v1/onboarding/health -> 200 {"ok": false} while there is no brain to talk to; the page then plans in the
                                 browser (and still sends its download count, which it does only after this answers JSON)
    POST /v1/onboarding/event {"session_id": "<the page's random id>", "kind": "download"}
                              {"session_id": "...", "kind": "message"}   (one per visitor message; a count only)
                              {"session_id": "...", "kind": "step", "step": 2}
    -> 204, with no body, whatever is kept

- **Works with the brain off.** The route needs no model and no key, so the page can count downloads while it plans in
  the browser. That gives the browser-only baseline before the brain launches.
- **Counts are off until switched on.** Nothing is stored unless `HQ_ONBOARDING_COUNTS=on`; the answer is the same
  204 either way, so the page cannot tell. Switching it on follows the owner's yes on what we keep.
- **What is kept** (one row per session, in `onboarding_sessions`): a salted hash of the session id, the UTC day it was
  first seen, the furthest step reached, whether the file was downloaded, how many messages the visitor sent, and the
  turns and cost the brain adds later.
  No message text, no address, no user agent: the table has no column for them. Rows go after 13 months, like installs.
- **Strict input.** Only those fields; a session id of 8 to 64 letters, digits, `-` or `_`; kind `download`, `message` or
  `step`; `step` a whole number from 0 to 9, and only with kind `step`. Anything else is a 422 with no detail and nothing kept.
- **Browsers.** Only the origins in `HQ_ONBOARDING_ORIGINS` (default `https://tico.team`) may call these routes from a
  page. A preflight is answered here, before the request limit, so it does not use up the visitor's requests, and every
  answer on these routes carries the origin back, including the limit's 429, so the page can read it.
- **Staff read.** `GET /v1/staff/onboarding?days=14` (staff key): totals per UTC day for the Customer Insights
  Analyst's report: sessions, sessions with 2 or more visitor messages (in either mode), downloads and cost. No session rows, no text.

Merging into hq/app.py: `create_app` calls `onboarding.install(app, counts, staff_only)` after the routes and the request
limit, so this module's browser rules sit outside that limit. The daily purge calls `counts.purge()`.
"""
import hashlib
import json
import os
import re
from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse, Response

PREFIX = "/v1/onboarding"
ORIGINS = ("https://tico.team",)
BODY_LIMIT = 512
FIELDS = {"session_id", "kind", "step"}
KINDS = ("download", "message", "step")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_STEP = 9
RETENTION_DAYS = 395          # 13 months, as installs
NEW_SESSIONS_PER_DAY = 20_000  # a flood cap: past it, a new session is not counted that day
STAFF_DAYS = 14
STAFF_MAX_DAYS = 90
NO_STORE = {"Cache-Control": "no-store"}
ON = {"1", "on", "true", "yes"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS onboarding_salt(salt BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS onboarding_sessions(
 session_hash BLOB PRIMARY KEY,
 day TEXT NOT NULL,
 step INTEGER NOT NULL DEFAULT 0,
 downloaded INTEGER NOT NULL DEFAULT 0,
 messages INTEGER NOT NULL DEFAULT 0,
 turns INTEGER NOT NULL DEFAULT 0,
 cost_micro_usd INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS onboarding_sessions_day ON onboarding_sessions(day);
"""


def switched_on(value):
    return str(value or "").strip().lower() in ON


def origins_from_env():
    raw = os.environ.get("HQ_ONBOARDING_ORIGINS", "").strip()
    return tuple(o.strip().rstrip("/") for o in raw.split(",") if o.strip()) if raw else ORIGINS


class Counts:
    """The per-session counts. Shares the installs database's connection, lock and clock (db.py)."""

    def __init__(self, db, on=None):
        self.db = db
        self.on = switched_on(os.environ.get("HQ_ONBOARDING_COUNTS")) if on is None else bool(on)
        with db.lock:
            db.conn.executescript(SCHEMA)
            row = db.conn.execute("SELECT salt FROM onboarding_salt").fetchone()
            if row is None:
                db.conn.execute("INSERT INTO onboarding_salt(salt) VALUES(?)", (os.urandom(16),))
                row = db.conn.execute("SELECT salt FROM onboarding_salt").fetchone()
        # The salt stays in the database so a session keeps one row across a restart.
        self.salt = bytes(row[0])

    def key(self, session_id):
        return hashlib.blake2b(session_id.encode(), key=self.salt, digest_size=16).digest()

    def event(self, session_id, kind, step=None):
        """Count one page event. Returns whether anything was written."""
        if not self.on:
            return False
        day = self.db.clock().isoformat()
        key = self.key(session_id)
        with self.db.lock:
            conn = self.db.conn
            if conn.execute("SELECT 1 FROM onboarding_sessions WHERE session_hash=?", (key,)).fetchone() is None:
                started = conn.execute("SELECT count(*) FROM onboarding_sessions WHERE day=?", (day,)).fetchone()[0]
                if started >= NEW_SESSIONS_PER_DAY:
                    return False
                conn.execute("INSERT INTO onboarding_sessions(session_hash, day) VALUES(?, ?)", (key, day))
            if kind == "download":
                conn.execute("UPDATE onboarding_sessions SET downloaded=1 WHERE session_hash=?", (key,))
            elif kind == "message":
                conn.execute("UPDATE onboarding_sessions SET messages=messages+1 WHERE session_hash=?", (key,))
            else:
                conn.execute("UPDATE onboarding_sessions SET step=max(step, ?) WHERE session_hash=?", (step, key))
        return True

    def purge(self):
        """Forget sessions first seen more than 13 months ago. Returns how many."""
        cutoff = (self.db.clock() - timedelta(days=RETENTION_DAYS)).isoformat()
        with self.db.lock:
            return self.db.conn.execute("DELETE FROM onboarding_sessions WHERE day<?", (cutoff,)).rowcount

    def daily(self, days=STAFF_DAYS):
        """Totals for each of the last `days` UTC days, today included, oldest first; a day with no sessions is zeros."""
        today = self.db.clock()
        first = today - timedelta(days=days - 1)
        with self.db.lock:
            rows = {r[0]: r for r in self.db.conn.execute(
                "SELECT day, count(*), sum(messages>=2), sum(downloaded), sum(cost_micro_usd) FROM onboarding_sessions "
                "WHERE day>=? GROUP BY day", (first.isoformat(),))}
        out = []
        for n in range(days):
            day = (first + timedelta(days=n)).isoformat()
            r = rows.get(day)
            out.append({"day": day, "sessions": r[1] if r else 0, "sessions_2plus_messages": r[2] if r else 0,
                        "downloads": r[3] if r else 0, "cost_usd": round((r[4] if r else 0) / 1_000_000, 6)})
        return out


def parse(raw):
    """The event, strictly, as (session_id, kind, step), or None."""
    if len(raw) > BODY_LIMIT:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict) or not set(body) <= FIELDS:
        return None
    session_id, kind, step = body.get("session_id"), body.get("kind"), body.get("step")
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id) or kind not in KINDS:
        return None
    if kind == "step":
        if type(step) is not int or not 0 <= step <= MAX_STEP:
            return None
    elif "step" in body:
        return None
    return session_id, kind, step


def install(app, counts, staff_only=None, origins=None, brain_ready=lambda: False):
    """Add the `/v1/onboarding` routes and their browser rules to an HQ app. `brain_ready` says whether `/turn` can
    answer now; until the brain is built there is none, and `/health` says so without a model call or a key."""
    allowed = set(origins_from_env() if origins is None else origins)

    def ours(path):
        return path == PREFIX or path.startswith(PREFIX + "/")

    @app.middleware("http")
    async def browser_rules(request: Request, call_next):
        if not ours(request.url.path):
            return await call_next(request)
        origin = request.headers.get("origin", "")
        allow = {"Access-Control-Allow-Origin": origin} if origin in allowed else {}
        if request.method == "OPTIONS":
            # A preflight: answered here, never counted against the visitor's requests.
            asked = request.headers.get("access-control-request-headers", "")
            wants = {h.strip().lower() for h in asked.split(",") if h.strip()}
            if allow and request.headers.get("access-control-request-method", "") in ("GET", "POST") \
                    and wants <= {"content-type"}:
                allow.update({"Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type",
                              "Access-Control-Max-Age": "600"})
            else:
                allow = {}
            return Response(status_code=204, headers={**allow, "Vary": "Origin", **NO_STORE})
        response = await call_next(request)
        response.headers.update(allow)
        response.headers["Vary"] = "Origin"
        return response

    @app.get(PREFIX + "/health")
    def health():
        return JSONResponse({"ok": bool(brain_ready())}, headers=NO_STORE)

    @app.post(PREFIX + "/event")
    async def event(request: Request):
        raw = b""
        async for chunk in request.stream():         # read no more than a valid request can be
            raw += chunk
            if len(raw) > BODY_LIMIT:
                break
        found = parse(raw)
        if found is None:
            return JSONResponse({"error": "invalid"}, status_code=422, headers=NO_STORE)
        counts.event(*found)
        return Response(status_code=204, headers=NO_STORE)

    if staff_only is not None:
        @app.get("/v1/staff/onboarding")
        def staff_onboarding(request: Request):
            denied = staff_only(request)
            if denied is not None:
                return denied
            raw = request.query_params.get("days", "")
            days = int(raw) if raw.isdigit() else STAFF_DAYS
            if (raw and not raw.isdigit()) or not 1 <= days <= STAFF_MAX_DAYS:
                return JSONResponse({"error": "invalid"}, status_code=422, headers=NO_STORE)
            return JSONResponse({"counting": counts.on, "days": counts.daily(days)}, headers=NO_STORE)

    return app
