"""Finance's link to the Tico AI Gateway (spec §3.4, §9): one daemon thread that keeps the Gateway key's self cap in
step with the company AI services budget, and fetches the key's usage meter for the books. Modeled on
backend/webhook_delivery.py: each piece reads or claims in a short transaction, calls the Gateway outside any
transaction, then records what came back; a doorbell wakes it after a commit and a slow sweep covers a missed ring.

The Gateway's caps are on its own list cost, not on Tico's price, so a budget is never turned into a cap. At an AI
services cutoff the self cap is set to the key's spend so far (`spent_usd`), which refuses every further charged call;
when the budget is raised above what it has used, or a new budget month starts, the self cap is cleared. A 404 means
the Gateway does not have the route yet: the worker tries again an hour later and says nothing. A rehearsal copy sends
nothing: what would be sent is recorded as withheld. Nothing runs without a Gateway connection.

A self cap belongs to one key at one address: each result keeps the connection's target fingerprint (`for`: the saved
revision, or a digest of the environment's key and URL, with the address), and a result for another target says nothing about the
current one. After a key or URL change, a rotation or a new environment key, the cap is unknown and is set again: a cut
is made on the new key, and a clear is sent to it once Tico has cut any key here (an earlier key that comes back may
still hold Tico's cut). A key that stops being current keeps its self cap: Tico no longer holds it to change it, and a
cut left on a key nothing uses only refuses spending. A retry's wait (`tried_for`) holds only the target that failed,
for the cap and the meter alike; the meter's months already read are kept across keys."""

import json
import logging
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from . import jobs
from . import usage as U
from .context import is_set_up
from .store import ledger as SL
from .store import tico

log = logging.getLogger(__name__)

SWEEP = 120.0  # seconds the thread sleeps without a ring
FETCH_EVERY_HOURS = 24  # the meter is read once a day per month: postings are daily summaries
NOT_DEPLOYED_RETRY = 3600  # seconds after a 404
RETRY = (60, 300, 1800, 3600)  # seconds after the 1st, 2nd, ... failed try; then hourly
CLAIM_STALE = 120  # seconds a claim may sit before another try may take it
TIMEOUT = 8
FETCH_KEY = "gateway_usage_fetch"
Opener = Callable[[urllib.request.Request, float], Any]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _after(at: str, seconds: float) -> str:
    moment = datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _gateway() -> Any:
    from clients import gateway

    return gateway


# ------------------------------------------------------------------------------------------------ HTTP
def call(
    method: str, base: str, path: str, key: str, body: str | None = None, opener: Opener | None = None
) -> tuple[int | None, dict[str, Any]]:
    """(HTTP status or None when unreachable, the JSON answer with decimals). Never raises."""
    G = _gateway()
    try:
        url = str(G.safe_base(base)) + path
    except ValueError:
        return None, {"error": "bad_url"}
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=body.encode() if body is not None else None, method=method, headers=headers
    )
    send = opener or cast(Opener, G._send)
    try:
        with send(request, TIMEOUT) as response:
            raw = cast(bytes, response.read())
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                raw = exc.read()
            except OSError:
                raw = b""
            return exc.code, _parse(raw)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, {}
    return status, _parse(raw)


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw or b"{}", parse_float=Decimal)
    except ValueError:
        return {}
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


# ------------------------------------------------------------------------------------------------ the cap
def cap_step(
    store: Any,
    vault: Any,
    settings: Any,
    *,
    rehearsal: bool = False,
    opener: Opener | None = None,
    now: str | None = None,
) -> str:
    """Bring the self cap in step once if it is out of step. What happened: `none` (no Gateway connection or no
    Finance), `steady`, `waiting`, `withheld`, `pushed`, `not_deployed` or `failed`."""
    now = now or _now()
    with store.read() as c:
        if not is_set_up(c) or not tico.gateway_connected(c, settings):
            return "none"
        want, month = jobs.cap_wanted(c, now)
        state = jobs.cap_state(c)
        target = tico.gateway_fingerprint(c, settings)
    if not _needs(state, want, month, target):
        return "steady"
    if _waits(state, target, now):
        return "waiting"
    with store.transaction() as c:
        state = jobs.cap_state(c)
        target = tico.gateway_fingerprint(c, settings)
        if not _needs(state, want, month, target) or str(state.get("claimed_at") or "") > _after(now, -CLAIM_STALE):
            return "steady"
        if rehearsal:
            jobs.cap_record(
                c,
                {
                    **state,
                    "status": "withheld",
                    "want": want,
                    "month_wanted": month,
                    "next_try_at": _after(now, NOT_DEPLOYED_RETRY),
                    "tried_for": target,
                    "at": now,
                },
                now,
            )
            return "withheld"
        claim = secrets.token_hex(8)
        jobs.cap_record(c, {**state, "claimed_at": now, "claim": claim}, now)
    with store.read() as c:
        key, base, target = tico.gateway_target(c, vault, settings)
    status, answer, spent = _push(want, key, base, opener)
    with store.transaction() as c:
        current = jobs.cap_state(c)
        if current.get("claim") != claim:
            # This claim went stale and a newer one took over: its result stands, whatever this late reply says.
            log.info("Set aside a late Gateway self cap reply (claim %s, %s)", claim, status or "unreachable")
            return "stale"
        state = {k: v for k, v in current.items() if k not in ("claimed_at", "claim")}
        if status is not None and 200 <= status < 300:
            standing = {
                k: str(answer[k])
                for k in ("spent_usd", "cap_usd", "self_cap_usd", "effective_cap_usd")
                if k in answer and answer[k] is not None
            }
            jobs.cap_record(
                c,
                {
                    "pushed": want,
                    "month": month,
                    "for": target,
                    "status": "ok",
                    "at": now,
                    "tries": 0,
                    "standing": standing,
                    **({"spent_usd": str(spent)} if spent is not None else {}),
                },
                now,
            )
            return "pushed"
        if status == 404:
            jobs.cap_record(
                c,
                {
                    **state,
                    "status": "not_deployed",
                    "next_try_at": _after(now, NOT_DEPLOYED_RETRY),
                    "tried_for": target,
                    "at": now,
                },
                now,
            )
            return "not_deployed"
        tries = _tries(state, target) + 1
        if tries == 1:
            log.warning("Could not set the Gateway self cap (%s)", status or "unreachable")
        jobs.cap_record(
            c,
            {
                **state,
                "status": "failed",
                "error": str(status or "unreachable"),
                "tries": tries,
                "next_try_at": _after(now, RETRY[min(tries, len(RETRY)) - 1]),
                "tried_for": target,
                "at": now,
            },
            now,
        )
        return "failed"


def _needs(state: Mapping[str, object], want: str, month: str, target: str) -> bool:
    """Whether the current target's self cap may be out of step. A result made for another target is no evidence
    (module doc): a cut is made again, and a clear is sent once any cut was ever pushed here (`pushed` set)."""
    pushed, pushed_month = state.get("pushed"), state.get("month")
    if state.get("for") != target:
        return want == "cut" or pushed is not None
    if want == "cut":
        return not (pushed == "cut" and pushed_month == month)
    return pushed == "cut"


def _waits(state: Mapping[str, object], target: str | None, now: str) -> bool:
    """A retry's wait holds the target that failed, not one that replaced it. `target` None: whichever."""
    return str(state.get("next_try_at") or "") > now and (target is None or state.get("tried_for") == target)


def _tries(state: Mapping[str, object], target: str) -> int:
    """Failed tries in a row on this target; another target starts again from none."""
    return int(str(state.get("tries") or 0)) if state.get("tried_for") == target else 0


def _push(want: str, key: str, base: str, opener: Opener | None) -> tuple[int | None, dict[str, Any], Decimal | None]:
    """Set the self cap to the key's spend so far (`cut`) or clear it. (status, answer, the spend it was set to)."""
    if not key:
        return None, {}, None
    if want == "clear":
        status, answer = call("POST", base, "/key/cap", key, '{"self_cap_usd": null}', opener)
        return status, answer, None
    status, standing = call("GET", base, "/key", key, None, opener)
    if status is None or not 200 <= status < 300:
        return status, standing, None
    spent = _decimal(standing.get("spent_usd"))
    if spent is None:
        return None, {}, None
    staff = _decimal(standing.get("cap_usd"))
    value = min(spent, staff) if staff is not None else spent
    status, answer = call("POST", base, "/key/cap", key, '{"self_cap_usd": %s}' % format(value, "f"), opener)
    return status, answer, value


# ------------------------------------------------------------------------------------------------ the meter
def due_months(c: Any, at: str, target: str | None = None) -> list[str]:
    """The months whose meter was last read more than FETCH_EVERY_HOURS ago, unless a 404 or failure on `target` (the
    connection's fingerprint; None: any) says wait."""
    state = fetch_state(c)
    if _waits(state, target, at):
        return []
    last = cast(dict[str, str], state.get("months") or {})
    cutoff = _after(at, -FETCH_EVERY_HOURS * 3600)
    return [m for m in U.months_to_fetch(c, datetime.fromisoformat(at[:10]).date()) if last.get(m, "") <= cutoff]


def fetch_state(c: Any) -> dict[str, object]:
    stored = SL.setting(c, FETCH_KEY)
    return dict(cast(Mapping[str, object], stored)) if isinstance(stored, Mapping) else {}


def usage_step(
    store: Any,
    vault: Any,
    settings: Any,
    *,
    rehearsal: bool = False,
    opener: Opener | None = None,
    now: str | None = None,
) -> str:
    """Read the meter for each month that is due and keep it as a snapshot. `none`, `steady`, `withheld`, `fetched`,
    `not_deployed` or `failed`."""
    now = now or _now()
    with store.read() as c:
        if not is_set_up(c) or not tico.gateway_connected(c, settings):
            return "none"
        key, base, target = tico.gateway_target(c, vault, settings)
        months = due_months(c, now, target)
    if not months:
        return "steady"
    if rehearsal or not key:
        with store.transaction() as c:
            state = fetch_state(c)
            SL.set_setting(
                c,
                FETCH_KEY,
                {
                    **state,
                    "status": "withheld" if rehearsal else "no_key",
                    "next_try_at": _after(now, NOT_DEPLOYED_RETRY),
                    "tried_for": target,
                },
                "keeper",
                now,
            )
        return "withheld" if rehearsal else "none"
    outcome = "fetched"
    for month in months:
        status, answer = call("GET", base, "/key/usage?" + urllib.parse.urlencode({"month": month}), key, None, opener)
        with store.transaction() as c:
            state = fetch_state(c)
            done = dict(cast(dict[str, str], state.get("months") or {}))
            if status is not None and 200 <= status < 300 and isinstance(answer.get("rows"), list):
                U.snapshot(c, {**answer, "month": answer.get("month") or month}, fetched_at=now)
                done[month] = now
                SL.set_setting(c, FETCH_KEY, {"months": done, "status": "ok", "at": now, "tries": 0}, "keeper", now)
                continue
            if status == 404:
                SL.set_setting(
                    c,
                    FETCH_KEY,
                    {
                        **state,
                        "status": "not_deployed",
                        "next_try_at": _after(now, NOT_DEPLOYED_RETRY),
                        "tried_for": target,
                    },
                    "keeper",
                    now,
                )
                return "not_deployed"
            tries = _tries(state, target) + 1
            if tries == 1:
                log.warning("Could not read the Gateway usage meter (%s)", status or "unreachable")
            SL.set_setting(
                c,
                FETCH_KEY,
                {
                    **state,
                    "status": "failed",
                    "error": str(status or "unreachable"),
                    "tries": tries,
                    "next_try_at": _after(now, RETRY[min(tries, len(RETRY)) - 1]),
                    "tried_for": target,
                },
                "keeper",
                now,
            )
            outcome = "failed"
            break
    return outcome


# ------------------------------------------------------------------------------------------------ the thread
class Bell:
    """A doorbell: `ring` wakes the worker, and a ring while it was busy is not slept through."""

    def __init__(self) -> None:
        self.cond, self.rung = threading.Condition(), 0

    def ring(self) -> None:
        with self.cond:
            self.rung += 1
            self.cond.notify_all()

    def wait(self, seen: int, timeout: float) -> int:
        with self.cond:
            if self.rung == seen:
                self.cond.wait(timeout)
            return self.rung


BELL = Bell()
_worker: dict[str, Any] = {"thread": None, "stop": threading.Event(), "lock": threading.Lock(), "args": None}


def ring() -> None:
    BELL.ring()


def start(store: Any, vault: Any, settings: Any, *, rehearsal: bool = False, opener: Opener | None = None) -> None:
    """Start the thread (or restart it if it died). Safe to call again on every scheduler tick."""
    with _worker["lock"]:
        _worker["args"] = (store, vault, settings, rehearsal, opener)
        thread = cast(threading.Thread | None, _worker["thread"])
        if thread is not None and thread.is_alive():
            return
        if cast(threading.Event, _worker["stop"]).is_set():
            _worker["stop"] = threading.Event()
        made = threading.Thread(
            target=_loop,
            args=(store, vault, settings, rehearsal, opener, _worker["stop"]),
            name="finance-gateway",
            daemon=True,
        )
        made.start()
        _worker["thread"] = made


def ensure() -> None:
    """Restart a worker that died; nothing before the first `start`."""
    args = cast(tuple[Any, Any, Any, bool, Opener | None] | None, _worker["args"])
    if args and not cast(threading.Event, _worker["stop"]).is_set():
        store, vault, settings, rehearsal, opener = args
        start(store, vault, settings, rehearsal=rehearsal, opener=opener)


def stop() -> None:
    cast(threading.Event, _worker["stop"]).set()
    BELL.ring()
    thread = cast(threading.Thread | None, _worker["thread"])
    if thread is not None:
        thread.join(5)
    _worker["thread"] = None


def step(store: Any, vault: Any, settings: Any, *, rehearsal: bool = False, opener: Opener | None = None) -> None:
    for piece in (cap_step, usage_step):
        try:
            piece(store, vault, settings, rehearsal=rehearsal, opener=opener)
        except Exception:
            log.exception("Finance Gateway step failed")


def _loop(store: Any, vault: Any, settings: Any, rehearsal: bool, opener: Opener | None, halt: threading.Event) -> None:
    while not halt.is_set():
        seen = BELL.rung
        step(store, vault, settings, rehearsal=rehearsal, opener=opener)
        BELL.wait(seen, SWEEP)
