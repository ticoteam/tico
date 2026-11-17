# pyright: strict
"""A Claude plan signed in with a full login, as Orca and t3code sign one in: kind `claude_login` (backend/model_subscriptions.py).

Connect runs `claude auth login --claudeai` on a Linux computer through the sign-in relay, in a home of its own, and the
computer sends back the login (`claudeAiOauth` from `.credentials.json`) with the account it is (`oauthAccount` from
`.claude.json`). The plan is named by its email and is one per account (`account`: lower(email)|organization uuid): a
second Connect of the same account is a Reconnect of the first, and a Reconnect of a setup-token plan (`claude_token`)
turns that same plan into a full login, keeping its owner, its computers and its history (its old readings go).

A turn gets only the short-lived access token, in a `.credentials.json` of its own with no refresh token
(runner-rs/src/model_subscriptions.rs, `CLAUDE_CONFIG_DIR`), never `CLAUDE_CODE_OAUTH_TOKEN`: Claude Code reads the file
before each call, so a rewritten file keeps a running turn signed in when the token rotates.

Refresh: the access token lives eight hours, and a refresh rotates both tokens and revokes the old access token at once;
a refresh token works once. So one computer at a time refreshes a plan (a lease, `claude_logins`), from the revision the
server holds: it refreshes a copy (`claude auth status --json` on a file whose expiry is in the past), rewrites its own
running turns' files, keeps the new login on disk until the server acknowledges it (`refresh_result`, idempotent per
lease), and every other computer with turns on the plan gets the new access token on its next poll (told at once) and
rewrites theirs. A lease that ran out without an answer is uncertain: the login may have rotated there, so only that
computer may take it again until the old access token has expired. A refresh starts when less than an hour is left.

A turn refused ("OAuth token revoked") while the plan moved on, or while a refresh is under way, was caught between a
rotation and its file's rewrite: it is not a failure and never asks for a Reconnect; it goes back in the queue and
continues its session on the new token (`gap`, execution.complete). While the refresh is still under way its job waits
(`claude_login_parks`, execution.queued_jobs_sql) until the plan's revision moves past the one it held or the lease
ends, five minutes at most, so a claim does not hand it the revoked token again; a second refusal of the same job is
an ordinary failure.

A refresh's result counts only from the computer its lease was issued to (`claude_login_leases`), late or repeated.

Usage: computers read `GET /api/oauth/usage` with the access token (never the server: calls to Anthropic run from
computers), every 15 minutes with jitter for a plan on a computer, at most every 5 minutes, backing off from 30 s to
15 minutes on failure and honouring Retry-After (24 hours at most). A refused or failed read never asks for a Reconnect.
The week, the 5-hour window and each model's week are kept as the plan's reading (subscription_refresh.plan_key), which
the Subscriptions row and a person's plan order read; a plan at 95% or more of a window comes after the others in that
order until it is back under 90% or that window has reset (`near`). Only a live refusal holds a plan (login_holds),
and a plan near a limit is still used when every plan of the set is.

The login itself lasts about 29 days from the sign-in and a refresh does not extend it: Settings shows "Reconnect by"
a few days before (`login_expires_at`).
"""

import json
import random
from datetime import datetime, timedelta, timezone
from collections.abc import Callable, Iterable
from typing import Any, Literal, cast

from fastapi import Request
from pydantic import Field, SecretStr

from clients import harness_manifests as _manifests

from . import hubdb as _hubdb
from . import models as M
from .store import Problem

Row = dict[str, Any]
H: Any = _hubdb  # untyped helpers: their rows are `Row`
manifests: Any = _manifests


def _MS() -> Any:
    from . import model_subscriptions

    return model_subscriptions


def _SR() -> Any:
    from . import subscription_refresh

    return subscription_refresh


def _login() -> Any:
    from . import model_login

    return model_login


def _repos() -> Any:
    from . import repositories

    return repositories


KIND = "claude_login"
CAPABILITY = "claude_login_v1"
REFRESH_BEFORE = timedelta(minutes=60)  # a refresh starts when less than this is left of the access token
LEASE_SECONDS = 120
GAP_SECONDS = 90  # a refusal this soon after a refresh was the rotation's
USAGE_EVERY = 15 * 60
USAGE_JITTER = 60
USAGE_GAP = 5 * 60
BACKOFF = (30, 15 * 60)
RETRY_AFTER_CAP = 24 * 60 * 60
USAGE_LIFETIME = 120
NEAR, CLEAR = 95.0, 90.0
RECONNECT_WARN = timedelta(days=5)
MAX_LIVE = 50
PARK_SECONDS = 5 * 60  # the longest a refused turn waits for its plan's refresh
KEEP_SECONDS = 30 * 24 * 60 * 60  # how long an issued lease and a park are remembered
RECENT_SECONDS = 7 * 24 * 60 * 60  # a computer a plan ran on this recently must take a full login before it becomes one


# ------------------------------------------------------------------------------------------------ values
def _ms(value: object) -> str | None:
    """Milliseconds since the epoch as this database writes a time, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        at = datetime.fromtimestamp(value / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _obj(value: object) -> Row | None:
    return cast(Row, value) if isinstance(value, dict) else None


def parse(text: str) -> Row:
    """A full Claude login as Connect sends it, `{"claudeAiOauth": {...}, "oauthAccount": {...}}`, or a Problem."""
    try:
        value = _obj(json.loads(text))
    except ValueError:
        value = None
    oauth = _obj(value.get("claudeAiOauth")) if value else None
    account = _obj(value.get("oauthAccount")) if value else None
    if not (
        oauth is not None
        and all(isinstance(oauth.get(k), str) and oauth[k] for k in ("accessToken", "refreshToken"))
        and _ms(oauth.get("expiresAt"))
        and account is not None
        and isinstance(account.get("emailAddress"), str)
        and "@" in account["emailAddress"]
        and len(account["emailAddress"]) <= 200
    ):
        raise Problem("secret", "Connect a Claude plan from the page, or paste a setup-token instead", 422)
    return {"claudeAiOauth": oauth, "oauthAccount": account}


def checked(text: str) -> tuple[str, str]:
    """(value to seal, preview: the account's email)."""
    value = parse(text.strip())
    return json.dumps(value, separators=(",", ":")), value["oauthAccount"]["emailAddress"].strip()


def facts(value: Any) -> Row:
    """What is kept of a login beside its plan (`claude_logins`): its account, and when its access token and login end."""
    if isinstance(value, str):
        value = json.loads(value)
    oauth, account = value["claudeAiOauth"], value["oauthAccount"]
    email = account["emailAddress"].strip()
    return {
        "email": email,
        "account": f"{email.lower()}|{account.get('organizationUuid') or ''}",
        "access_expires_at": _ms(oauth.get("expiresAt")),
        "login_expires_at": _ms(oauth.get("refreshTokenExpiresAt")),
    }


def turn_file(text: str) -> str:
    """A turn's `.credentials.json`: the login without its refresh token, so no turn can rotate it."""
    oauth = json.loads(text)["claudeAiOauth"]
    return json.dumps({"claudeAiOauth": {k: v for k, v in oauth.items() if k != "refreshToken"}}, separators=(",", ":"))


def stamp(c: Any, sid: str, value: Any, refreshed: bool = False) -> None:
    """Keep the login's account and times beside the plan (`claude_logins`; model_subscriptions.SUBS reads them)."""
    known = facts(value)
    now = H.now()
    c.execute(
        "INSERT INTO claude_logins(subscription_id,updated_at) VALUES(?,?) ON CONFLICT(subscription_id) DO NOTHING",
        (sid, now),
    )
    c.execute(
        "UPDATE claude_logins SET account=?,access_expires_at=?,login_expires_at=coalesce(?,login_expires_at),"
        "refreshed_at=CASE WHEN ? THEN ? ELSE refreshed_at END,updated_at=? WHERE subscription_id=?",
        (known["account"], known["access_expires_at"], known["login_expires_at"], refreshed, now, now, sid),
    )


def same_account(c: Any, account: str, sid: str | None = None) -> Row | None:
    """The plan already connected as `account` (another than `sid`): its id and name, or None."""
    return H._one(
        c,
        "SELECT s.id,s.name FROM claude_logins l JOIN model_subscriptions s ON s.id=l.subscription_id "
        "WHERE l.account=? AND s.id<>?",
        (account, sid or ""),
    )


def reconnect_by(sub: Any) -> str | None:
    """When to reconnect a full login, shown a few days before its login runs out; None otherwise."""
    ends = H.parse_ts(sub.get("login_expires_at")) if sub.get("kind") == KIND else None
    if ends and ends - RECONNECT_WARN <= datetime.now(timezone.utc):
        return sub["login_expires_at"]
    return None


def expired(sub: Any) -> bool:
    """A full login whose access token has run out (its refresh has failed for an hour): passed over until refreshed."""
    if sub.get("kind") != KIND or not sub.get("access_expires_at"):
        return False
    return sub["access_expires_at"] <= H.shift(H.now(), seconds=60)


# ------------------------------------------------------------------------------------------------ computers
def _caps(runner: Any) -> list[str]:
    return H._json(runner["capabilities"], []) or []


def takes_turns(runner: Any) -> bool:
    """A runner that puts a full login's access token in a turn's own file."""
    return bool(runner) and CAPABILITY in _caps(runner)


def can_sign_in(runner: Any) -> bool:
    """A computer that can run a full login's Connect or refresh: it says it can, and its Claude keeps the login in a
    file (not the system keychain), so a copy can be refreshed beside the one turns use."""
    keychain = manifests.caps("claude").get("switch_unavailable_on", [])
    return takes_turns(runner) and runner["platform"] not in keychain and _online(runner)


def _computers(c: Any, sid: str) -> list[tuple[Any, bool]]:
    """The computers of a plan, each `(runner row, live)`: those with a turn on it now first, then those given it (on
    its own or in its owner's plans), each by id."""
    live = {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT a.runner_id FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
            "WHERE s.subscription_id=? AND a.state IN ('leased','running')",
            (sid,),
        )
    }
    given = {
        r[0]
        for r in c.execute(
            "SELECT g.runner_id FROM computer_subscriptions g WHERE g.runtime='claude' AND (g.subscription_id=? "
            "OR (g.person<>'' AND g.person=(SELECT owner_id FROM model_subscriptions WHERE id=?)))",
            (sid, sid),
        )
    }
    ids = sorted(live | given)
    if not ids:
        return []
    rows = c.execute(
        f"SELECT id,platform,capabilities,last_seen_at FROM runners_with_state WHERE revoked_at IS NULL "
        f"AND id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    return sorted(((r, r["id"] in live) for r in rows), key=lambda x: (not x[1], x[0]["id"]))


def can_convert(c: Any, sid: str) -> None:
    """A setup-token plan becomes a full login only when every computer that runs it can take one: those given it, those
    with a turn on it, and those it ran on in the last week. An older runner would drop the plan (its turns would sign in
    as the computer instead), so the change is refused, naming them, until they are updated."""
    ids = {r["id"] for r, _ in _computers(c, sid)}
    ids |= {
        r[0]
        for r in c.execute(
            "SELECT DISTINCT a.runner_id FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id "
            "WHERE s.subscription_id=? AND a.created_at>?",
            (sid, H.shift(H.now(), seconds=-RECENT_SECONDS)),
        )
    }
    ids.discard(None)
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    behind = [
        r["label"] or r["id"]
        for r in c.execute(
            f"SELECT id,label,capabilities FROM runners_with_state WHERE revoked_at IS NULL AND id IN ({marks}) "
            "ORDER BY lower(label),id",
            sorted(ids),
        )
        if not takes_turns(r)
    ]
    if behind:
        raise Problem("update_computers", f"Update {', '.join(behind)} first: they cannot run a full Claude login", 409)


def _chosen(computers: list[tuple[Any, bool]], ok: Callable[[Any], bool], turn: int) -> str | None:
    """The computer whose turn it is among those `ok` admits: they take it in rotation by `turn` (failures so far), so
    one that keeps failing does not hold a plan forever."""
    ready = [r for r, _ in computers if ok(r)]
    return ready[turn % len(ready)]["id"] if ready else None


def _tell(c: Any, runner_ids: Iterable[str]) -> None:
    """Each computer polls its full logins at once (runner-rs claude_login.rs, channel `subscription_refresh`)."""
    for rid in sorted(set(runner_ids)):
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('runner','subscription_refresh',?,?)",
            (rid, H.now()),
        )


def pushed(c: Any, sid: str) -> None:
    """A plan's login changed (a refresh, or a Reconnect of the same account): every computer with a turn on it is told,
    and rewrites its turns' files on its poll; a turn waiting for it goes."""
    _tell(c, [r["id"] for r, live in _computers(c, sid) if live])
    unpark(c, sid)


def unpark(c: Any, sid: str) -> None:
    """The turns waiting on this plan's refresh (`gap`) go now: their computers claim them at once."""
    now = H.now()
    c.execute(
        "INSERT INTO changes(topic,kind,subject_id,bot,created_at) SELECT DISTINCT 'runner','work',a.runner_id,"
        "a.bot_id,? FROM claude_login_parks p JOIN attempts a ON a.id=p.attempt_id WHERE p.subscription_id=? "
        "AND p.until>?",
        (now, sid, now),
    )
    c.execute("UPDATE claude_login_parks SET until=? WHERE subscription_id=? AND until>?", (now, sid, now))


# ------------------------------------------------------------------------------------------------ refresh
def _lease(c: Any, sid: str) -> Row:
    return H._one(c, "SELECT * FROM claude_logins WHERE subscription_id=?", (sid,)) or {}


def _put_lease(c: Any, sid: str, **fields: Any) -> None:
    now = H.now()
    c.execute(
        "INSERT INTO claude_logins(subscription_id,updated_at) VALUES(?,?) ON CONFLICT(subscription_id) DO NOTHING",
        (sid, now),
    )
    sets = ",".join(f"{k}=?" for k in fields)
    c.execute(
        f"UPDATE claude_logins SET {sets},updated_at=? WHERE subscription_id=?",
        (*fields.values(), now, sid),
    )


def _backoff(failures: int) -> int:
    return min(BACKOFF[0] * 2 ** max(failures - 1, 0), BACKOFF[1])


def forget(c: Any, sid: str) -> None:
    """A plan reconnected: its lease goes, and so does any wait on it; its account and times stay. The leases it
    issued are remembered, so a late result is still known for whose it is."""
    c.execute(
        "UPDATE claude_logins SET lease_id='',lease_runner='',lease_revision=0,lease_until=NULL,failures=0,"
        "retry_at=NULL,done_lease='',done_revision=0,updated_at=? WHERE subscription_id=?",
        (H.now(), sid),
    )
    unpark(c, sid)


def drop(c: Any, sid: str) -> None:
    """A plan removed, or given a new value (another account, or no longer a full login): its row goes with its lease,
    and any wait on it ends."""
    c.execute("DELETE FROM claude_logins WHERE subscription_id=?", (sid,))
    unpark(c, sid)


def _refreshes(c: Any, vault: Any, runner_id: str) -> list[Row]:
    """The refreshes this computer is to make now, each with the plan's login: a lease it holds, or one it takes."""
    MS = _MS()

    now = H.now()
    due = H.shift(now, seconds=REFRESH_BEFORE.total_seconds())
    out: list[Row] = []
    for sub in H._rows(
        c.execute(
            f"SELECT * FROM {MS.SUBS} WHERE kind=? AND status<>'needs_reconnect' "
            "AND (access_expires_at IS NULL OR access_expires_at<=?)",
            (KIND, due),
        )
    ):
        lease: Row = _lease(c, sub["id"])
        quiet = ""
        if lease.get("lease_id"):
            mine = lease["lease_runner"] == runner_id
            if (lease["lease_until"] or "") > now:
                if not mine:
                    continue
            elif mine:
                # Uncertain, and back: it may hold the rotated login, so it goes on with the same lease.
                _put_lease(c, sub["id"], lease_until=H.shift(now, seconds=LEASE_SECONDS))
            elif (sub["access_expires_at"] or "") > now:
                continue  # uncertain: no other computer refreshes while the old token may still be the one in use
            else:
                # The old token is gone either way: another computer tries, the quiet one only when none other can.
                quiet = lease["lease_runner"]
                lease = {**lease, "lease_id": ""}
        if not lease.get("lease_id"):
            if (lease.get("retry_at") or "") > now:
                continue
            computers = _computers(c, sub["id"])
            others = [x for x in computers if x[0]["id"] != quiet and can_sign_in(x[0])]
            if _chosen(others or computers, can_sign_in, lease.get("failures") or 0) != runner_id:
                continue
            lease = {"lease_id": H.new_id(), "lease_revision": sub["revision"]}
            c.execute(
                "INSERT INTO claude_login_leases(id,subscription_id,runner_id,revision,issued_at) VALUES(?,?,?,?,?)",
                (lease["lease_id"], sub["id"], runner_id, sub["revision"], now),
            )
            c.execute("DELETE FROM claude_login_leases WHERE issued_at<?", (H.shift(now, seconds=-KEEP_SECONDS),))
            _put_lease(
                c,
                sub["id"],
                lease_id=lease["lease_id"],
                lease_runner=runner_id,
                lease_revision=sub["revision"],
                lease_until=H.shift(now, seconds=LEASE_SECONDS),
            )
            H.event(c, "runner:" + runner_id, "subscription.refresh_leased", sub["id"], {"revision": sub["revision"]})
        if lease["lease_revision"] != sub["revision"]:
            continue  # the plan moved on under it (a Reconnect): the lease ends with its result
        value = MS.value_of(c, vault.cipher, sub)
        if value is None:
            continue
        out.append({"lease": lease["lease_id"], "subscription": sub["id"], "revision": sub["revision"], "value": value})
    return out


def refresh_result(c: Any, vault: Any, runner_id: str, sid: str, body: "RefreshResult") -> Row:
    """A computer's answer to its lease. `succeeded`: the refreshed login, kept when the plan is still at the revision
    the refresh began from, whatever became of the lease (the old refresh token is spent, so it is the newest login
    there is); a result already kept for this lease is acknowledged again. `failed`: nothing rotated; tried again after
    a backoff, on the next computer in turn. `rejected`: the login is gone (Claude Code signed it out), so the plan
    needs reconnecting, unless the plan moved on since. Any of them only from the computer the lease was issued to,
    for the plan and the revision it was issued for: anything else is not kept."""
    MS = _MS()

    sub = MS.row(c, sid)
    if not sub or sub["kind"] != KIND:
        raise Problem("not_found", "That plan is not a full Claude login", 404)
    issued = H._one(c, "SELECT * FROM claude_login_leases WHERE id=?", (body.lease,))
    if (
        not issued
        or issued["runner_id"] != runner_id
        or issued["subscription_id"] != sid
        or issued["revision"] != body.revision
    ):
        return {"stored": False}
    lease = _lease(c, sid)
    if lease.get("done_lease") == body.lease:
        return {"stored": True, "revision": lease["done_revision"]}
    now = H.now()
    holds = lease.get("lease_id") == body.lease and lease.get("lease_runner") == runner_id
    if body.state == "succeeded":
        if body.value is None:
            raise Problem("value", "A refreshed login carries its value", 422)
        if body.revision != sub["revision"]:
            return {"stored": False}
        value, preview = MS.checked(KIND, body.value.get_secret_value())
        if facts(value)["account"] != sub["account"]:
            return {"stored": False}
        MS.store_refreshed(c, vault, sub, value, preview, "runner:" + runner_id, now)
        stamp(c, sid, value, refreshed=True)
        if sub["status"] == "needs_reconnect":
            # A late result after a lost lease brings the login back.
            c.execute("UPDATE model_subscriptions SET status='new',status_detail='',status_at=? WHERE id=?", (now, sid))
        _put_lease(
            c,
            sid,
            lease_id="",
            lease_runner="",
            lease_until=None,
            failures=0,
            retry_at=None,
            done_lease=body.lease,
            done_revision=sub["revision"] + 1,
        )
        H.event(c, "runner:" + runner_id, "subscription.refreshed", sid, {"revision": sub["revision"] + 1})
        pushed(c, sid)
        return {"stored": True, "revision": sub["revision"] + 1}
    if not holds:
        return {"stored": False}
    failures = (lease.get("failures") or 0) + 1
    _put_lease(
        c,
        sid,
        lease_id="",
        lease_runner="",
        lease_until=None,
        failures=failures,
        retry_at=H.shift(now, seconds=_backoff(failures)) if body.state == "failed" else None,
    )
    if body.state == "rejected" and body.revision == sub["revision"]:
        MS._status(c, sid, "needs_reconnect", "Signed out: reconnect this plan")
    H.event(c, "runner:" + runner_id, "subscription.refresh_" + body.state, sid, {"revision": body.revision})
    unpark(c, sid)  # the lease ended without a new login: a waiting turn runs, and fails if it is refused again
    return {"stored": False}


def gap(c: Any, attempt_id: str, runtime: str) -> bool:
    """Whether a turn refused its sign-in on a full login was caught by a rotation: the plan moved on since it was
    handed the turn, a refresh is under way (a lease, held or uncertain), or one was kept moments ago. Once per job: its
    second refusal is the login's own. While the refresh is under way the job waits for it (`PARK_SECONDS` at most),
    so its next claim is handed the new token, not the revoked one again."""
    MS = _MS()

    handed = MS.handed(c, attempt_id, runtime)
    sub = MS.row(c, handed["subscription_id"]) if handed else None
    if not handed or not sub or sub["kind"] != KIND:
        return False
    attempt: Row = H._one(c, "SELECT job_id FROM attempts WHERE id=?", (attempt_id,)) or {}
    job: str = attempt.get("job_id") or ""
    if (
        job
        and c.execute("SELECT 1 FROM claude_login_parks WHERE job_id=? AND attempt_id<>?", (job, attempt_id)).fetchone()
    ):
        return False
    now = H.now()
    moved = sub["revision"] > handed["revision"]
    leased = bool(_lease(c, sub["id"]).get("lease_id"))
    recent = bool(sub["refreshed_at"]) and sub["refreshed_at"] > H.shift(now, seconds=-GAP_SECONDS)
    if not (moved or leased or recent):
        return False
    if job:
        c.execute("DELETE FROM claude_login_parks WHERE created_at<?", (H.shift(now, seconds=-KEEP_SECONDS),))
        c.execute(
            "INSERT OR REPLACE INTO claude_login_parks(job_id,attempt_id,subscription_id,revision,until,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                job,
                attempt_id,
                sub["id"],
                handed["revision"],
                H.shift(now, seconds=PARK_SECONDS) if leased and not moved else now,
                now,
            ),
        )
    return True


# A queued job a full login's refresh holds (`gap`): it waits while its plan is still at the revision it was refused on
# and a lease is out, until its time is up. SQL over a queued job `j` (execution.queued_jobs_sql).
PARKED_SQL = (
    "EXISTS(SELECT 1 FROM claude_login_parks p JOIN model_subscriptions ps ON ps.id=p.subscription_id "
    "JOIN claude_logins pl ON pl.subscription_id=p.subscription_id WHERE p.job_id=j.id "
    "AND p.until>strftime('%Y-%m-%dT%H:%M:%fZ','now') AND ps.revision=p.revision AND pl.lease_id<>'')"
)


# ------------------------------------------------------------------------------------------------ tokens
def _tokens(c: Any, runner_id: str, live: "list[LiveToken]") -> list[Row]:
    """The new access token for each plan this computer runs turns on whose revision it has is behind."""
    out: list[Row] = []
    for item in live[:MAX_LIVE]:
        sub = H._one(c, "SELECT * FROM model_subscriptions WHERE id=?", (item.subscription,))
        if not sub or sub["kind"] != KIND or sub["status"] == "needs_reconnect" or sub["revision"] <= item.revision:
            continue
        if not c.execute(
            "SELECT 1 FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id WHERE s.subscription_id=? "
            "AND a.runner_id=? AND a.state IN ('leased','running') LIMIT 1",
            (sub["id"], runner_id),
        ).fetchone():
            continue
        out.append(sub)
    return out


# ------------------------------------------------------------------------------------------------ usage
def _usage_meta(c: Any, sid: str) -> Row:
    return _repos().metadata(c, _SR().plan_key(sid)) or {}


def _save_usage_meta(c: Any, sid: str, value: Row) -> None:
    _repos().save_metadata(c, _SR().plan_key(sid), value)


def ask_usage(c: Any, sid: str, actor: str) -> Any:
    """Read the plan's usage now (↻ on its row): as soon as a computer can, and not within five minutes of the last
    read."""
    SR = _SR()

    sub = H._one(c, "SELECT * FROM model_subscriptions WHERE id=?", (sid,))
    if not sub:
        raise Problem("not_found", "Subscription not found", 404)
    ready = [r["id"] for r, _ in _computers(c, sid) if takes_turns(r) and _online(r)]
    if not ready:
        raise Problem("offline", "Its computers are offline or not yet updated to read usage", 409)
    value = _usage_meta(c, sid)
    old: Row = value.get("request") or {}
    now = H.now()
    # Never two reads of the same plan less than USAGE_GAP apart, whoever asked.
    schedule: Row = value.get("schedule") or {}
    last: str = max(old.get("requested_at", ""), schedule.get("read_at") or "")
    if (old.get("state") == "requested" and old.get("expires_at", "") > now) or last > H.shift(
        now, seconds=-max(SR.COOLDOWN, USAGE_GAP)
    ):
        return SR.plan_view(value)
    runner = ready[0]
    value["request"] = _request(runner, now)
    _save_usage_meta(c, sid, value)
    H.event(c, actor, "subscription.usage_requested", sid, {"runner_id": runner, "revision": sub["revision"]})
    return SR.plan_view(value)


def _online(runner: Any) -> bool:
    return bool(_login()._online(runner))


def _request(runner_id: str, now: str) -> Row:
    return {
        "id": H.new_id(),
        "kind": "oauth_usage",
        "runner_id": runner_id,
        "requested_at": now,
        "updated_at": now,
        "expires_at": H.shift(now, seconds=USAGE_LIFETIME),
        "state": "requested",
    }


def _wanted(c: Any, sid: str, schedule: Row) -> bool:
    """Whether a plan's usage is read on its schedule: never read yet, in use (a turn runs on it, or a computer signs in
    with it now), or next in line in a person's set. Others wait for ↻ or their turn to come up."""
    MS = _MS()

    if not schedule.get("read_at"):
        return True
    if c.execute(
        "SELECT 1 FROM attempt_subscriptions s JOIN attempts a ON a.id=s.attempt_id WHERE s.subscription_id=? "
        "AND a.state IN ('leased','running') LIMIT 1",
        (sid,),
    ).fetchone():
        return True
    given: dict[tuple[str, str], Row] = MS._given(c) or {}
    for (_, runtime), entry in given.items():
        if not entry.get("person"):
            if entry.get("id") == sid:
                return True
            continue
        if entry.get("id") == sid:
            return True
        members: list[Row] = entry.get("members") or []
        ready = [m for m in members if MS.available(c, m, runtime)]
        if sid in [m["id"] for m in MS._ordered(c, ready, runtime)[:2]]:
            return True
    return False


def _usage_jobs(c: Any, vault: Any, runner_id: str) -> list[Row]:
    """The usage reads this computer is to make now, each with the plan's access token for this read only: one asked of
    it, or one due on the plan's schedule whose turn is this computer's."""
    MS = _MS()

    now = H.now()
    fresh = H.shift(now, seconds=120)
    out: list[Row] = []
    for sub in H._rows(
        c.execute(
            f"SELECT * FROM {MS.SUBS} WHERE kind=? AND status<>'needs_reconnect' AND access_expires_at>?",
            (KIND, fresh),
        )
    ):
        lease = _lease(c, sub["id"])
        if lease.get("lease_id") and (lease.get("lease_until") or "") > now:
            continue  # its token is about to change
        value = _usage_meta(c, sub["id"])
        request: Row = value.get("request") or {}
        live = request.get("state") == "requested" and request.get("expires_at", "") > now
        if not live:
            schedule: Row = value.get("schedule") or {}
            if (schedule.get("next_at") or "") > now or not _wanted(c, sub["id"], schedule):
                continue
            computers = _computers(c, sub["id"])
            if _chosen(computers, lambda r: takes_turns(r) and _online(r), schedule.get("failures") or 0) != runner_id:
                continue
            request = value["request"] = _request(runner_id, now)
            _save_usage_meta(c, sub["id"], value)
        if request.get("kind") != "oauth_usage" or request.get("runner_id") != runner_id:
            continue
        secret = MS.value_of(c, vault.cipher, sub)
        if secret is None:
            continue
        out.append(
            {
                "id": request["id"],
                "subscription": sub["id"],
                "revision": sub["revision"],
                "token": json.loads(secret)["claudeAiOauth"]["accessToken"],
            }
        )
    return out


def _instant(text: object, within: timedelta) -> str | None:
    """An ISO instant with its offset, in UTC, no further off than `within`; None for anything else."""
    try:
        at = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if not at.tzinfo:
        return None
    now = datetime.now(timezone.utc)
    at = at.astimezone(timezone.utc)
    return at.isoformat() if now < at <= now + within else None


def _window(w: "Window | None", within: timedelta) -> Row | None:
    if w is None:
        return None
    resets = _instant(w.resets_at, within)
    return {"used_percent": w.used_percent, "resets_at": resets} if resets else None


def near(meta: Row) -> bool:
    """Whether the plan's reading (`meta`, as `usage_result` keeps it) still puts it near a limit: it was near when
    read, and a window that has not reset since is still at or over CLEAR. Each window counts only until its own reset,
    so a plan near its 5-hour limit is not ordered last once that window has reset, read again or not."""
    if not meta.get("near"):
        return False
    now = datetime.now(timezone.utc)
    windows = [meta.get("reading"), meta.get("five_hour"), *(meta.get("models") or [])]
    for w in windows:
        if not isinstance(w, dict):
            continue
        percent = cast(Row, w).get("used_percent")
        resets = H.parse_ts(cast(Row, w).get("resets_at"))
        if isinstance(percent, (int, float)) and percent >= CLEAR and resets and resets > now:
            return True
    return False


def usage_result(c: Any, runner_id: str, sid: str, body: "UsageResult") -> Any:
    """A computer's answer to a usage read. A read kept for its plan replaces the plan's reading (the week, the 5-hour
    window, each model's week), unless the plan's account changed since it was asked; a refused read (429) waits as long
    as it was told; any other failure backs off. Never a Reconnect."""
    sub = H._one(c, "SELECT * FROM model_subscriptions WHERE id=?", (sid,))
    value = _usage_meta(c, sid)
    request: Row = value.get("request") or {}
    if not sub or request.get("id") != body.id or request.get("runner_id") != runner_id:
        raise Problem("not_found", "Usage request not found", 404)
    now = H.now()
    schedule: Row = dict(value.get("schedule") or {})
    if body.state == "succeeded":
        week = _window(body.seven_day, timedelta(days=8))
        if not week:
            raise Problem("invalid", "Usage has no current valid week", 422)
        superseded = (sub["credential_at"] or "") > request["requested_at"]
        if not superseded:
            five = _window(body.five_hour, timedelta(hours=6))
            models = [
                {"model": m.model, **w} for m in body.models[:10] if (w := _window(m, timedelta(days=8))) is not None
            ]
            top = max(
                [week["used_percent"]] + ([five["used_percent"]] if five else []) + [m["used_percent"] for m in models]
            )
            mark = {"reported_at": now, "status": None, "origin": "subscription", "subscription_id": sid}
            # The week as a plan read keeps it (subscription_refresh.plan_report), so `_plan_usage` reads it as before.
            value["reading"] = {**week, **mark}
            value["five_hour"] = {**five, **mark} if five else None
            value["models"] = models
            value["near"] = top >= NEAR or (bool(value.get("near")) and top >= CLEAR)
        schedule.update(
            failures=0, read_at=now, next_at=H.shift(now, seconds=USAGE_EVERY + random.randint(0, USAGE_JITTER))
        )
    else:
        failures = (schedule.get("failures") or 0) + 1
        wait = _backoff(failures)
        if body.state == "limited" and body.retry_after:
            wait = min(max(body.retry_after, USAGE_GAP), RETRY_AFTER_CAP)
        schedule.update(failures=failures, next_at=H.shift(now, seconds=wait))
    value["schedule"] = schedule
    value["request"] = {**request, "state": "succeeded" if body.state == "succeeded" else "failed", "updated_at": now}
    _save_usage_meta(c, sid, value)
    SR = _SR()

    return SR.plan_view(value)


def reset_usage(c: Any, sid: str) -> None:
    """A plan whose account may have changed (a conversion): its old readings and asked reads go."""
    SR = _SR()

    c.execute("DELETE FROM registry_metadata WHERE key=?", (SR.plan_key(sid),))


# ------------------------------------------------------------------------------------------------ routes
class LiveToken(M.Contract):
    subscription: M.ID
    revision: int = Field(ge=0)


class Poll(M.Contract):
    """What a computer holds: the revision of each full login its running turns are on."""

    live: list[LiveToken] = Field(default_factory=list, max_length=MAX_LIVE)


class RefreshResult(M.Contract):
    lease: M.ID
    revision: int = Field(ge=1)
    state: Literal["succeeded", "failed", "rejected"]
    value: SecretStr | None = Field(default=None, max_length=64 * 1024)


class Window(M.Contract):
    used_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    resets_at: str = Field(max_length=40)


class ModelWindow(Window):
    model: str = Field(min_length=1, max_length=80)


class UsageResult(M.Contract):
    id: M.ID
    state: Literal["succeeded", "failed", "limited"]
    seven_day: Window | None = None
    five_hour: Window | None = None
    models: list[ModelWindow] = Field(default_factory=list, max_length=20)
    retry_after: int | None = Field(default=None, ge=0, le=10 * RETRY_AFTER_CAP)


def poll(c: Any, vault: Any, runner_id: str, body: "Poll") -> Row:
    """A computer's poll: the access tokens its running turns need, the refreshes and usage reads it is to make."""
    MS = _MS()

    runner = c.execute("SELECT * FROM runners_with_state WHERE id=? AND revoked_at IS NULL", (runner_id,)).fetchone()
    if not takes_turns(runner):
        return {"tokens": [], "refreshes": [], "usage": []}
    # What the computer's turns hold now: their record moves with it, so a later refusal is judged against the token
    # they really had (`gap`).
    for item in body.live[:MAX_LIVE]:
        c.execute(
            "UPDATE attempt_subscriptions SET revision=min(?,(SELECT revision FROM model_subscriptions WHERE id=?)) "
            "WHERE subscription_id=? AND revision<? AND attempt_id IN "
            "(SELECT id FROM attempts WHERE runner_id=? AND state IN ('leased','running'))",
            (item.revision, item.subscription, item.subscription, item.revision, runner_id),
        )
    tokens: list[Row] = []
    for sub in _tokens(c, runner_id, body.live):
        value = MS.value_of(c, vault.cipher, sub)
        if value is not None:
            tokens.append({"subscription": sub["id"], "revision": sub["revision"], "value": turn_file(value)})
    return {
        "tokens": tokens,
        "refreshes": _refreshes(c, vault, runner_id) if can_sign_in(runner) else [],
        "usage": _usage_jobs(c, vault, runner_id),
    }


def install(app: Any, store: Any) -> None:
    """Runner routes. Not through `mutate`: a login or token is kept in no request receipt."""

    def runner_only(c: Any, who: Any) -> None:
        if (
            who.role != "runner"
            or not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (who.runner_id,)).fetchone()
        ):
            raise Problem("forbidden", "Only a registered computer may send this", 403)

    @app.post("/api/v2/runner-claude-logins")
    def runner_poll(request: Request, body: Poll) -> Row:
        who = request.state.identity
        with store.transaction() as c:
            runner_only(c, who)
            return poll(c, app.state.vault, who.runner_id, body)

    @app.post("/api/v2/runner-claude-logins/{sid}/refreshed")
    def runner_refreshed(request: Request, sid: str, body: RefreshResult) -> Row:
        who = request.state.identity
        with store.transaction() as c:
            runner_only(c, who)
            return refresh_result(c, app.state.vault, who.runner_id, sid, body)

    @app.post("/api/v2/runner-claude-logins/{sid}/usage")
    def runner_usage(request: Request, sid: str, body: UsageResult) -> Any:
        who = request.state.identity
        with store.transaction() as c:
            runner_only(c, who)
            return usage_result(c, who.runner_id, sid, body)
