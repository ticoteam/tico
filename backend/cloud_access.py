"""Tico Cloud access (phase A, Oct 29): what Tico HQ says this cloud company may do, and what the app does about it.

HQ (tico-services billing/cloud.py `signals`) answers `GET <Gateway URL>/cloud` with the install's gateway key, and puts
the same object under `cloud` on `GET <Gateway URL>/billing` (backend/company_billing.py passes it through):

    {plan, access: open|frozen|locked, reason: null|out_of_credit|trial_over|unpaid, balance_micros, trial_ends,
     trial_days_left, card, auto_recharge, period_end, price_micros, billing_url}

Every other install (self-hosted, EC2-hosted) gets 404 (`not_cloud`), or no `cloud` on its billing answer: nothing is
kept, and nothing here changes anything. What each access does:

    open     working
    frozen   out of credit: no new bot turn on a Tico model or the Gateway (execution.claim_refusal `out_of_credit`;
             a bot on its own subscription or keys still runs), routines' schedules pass (automations.tick), and every
             page shows "Out of credit · Add credit" to `billing_url` (ui/app/cloud-access.ts). Chat, history and
             settings stay usable.
    locked   the trial ended without a card, or the month is unpaid: no bot turn at all (a claim answers `paused`),
             routines pass, and people's API calls are refused 402 `locked` (`gate`) except OPEN_PATHS: who is signed
             in, the status poll, this state, Settings > Billing and Download all files. The app shows Pay to continue.

The state is kept in registry_metadata (KEY), so a restart keeps a lock until HQ answers again, with a copy on the store
for the request gate. Every answer replaces it: paying, or credit landing, opens everything again at the next read
(every SYNC seconds while the company is a cloud one, and whenever Settings > Billing is opened), with no restart. HQ
not answering keeps the last word. A change of access is an audit event (`cloud.access`).

Low credit: HQ's answer carries the balance but not the spend, so each answer keeps an hourly balance sample
(`samples`, the last SPEND_WINDOW) and the spend per day is the fall across them (`spend_day_micros`). Credit landing
(the balance rising) starts the samples again and keeps the last spend. While open with under LOW_DAYS days of spend
left, `view` says `days_left` and every page shows "Low credit · about N days left · Add credit" (one `cloud.low_credit`
audit event as it starts). A machine HQ refused for credit (its 402) is out of credit too, whatever `access` says
(`shown`): HQ's billing stopped both machines on Nov 17 while its answer here still said open.

Until HQ has answered, a Tico Cloud cell (TICO_CELL=1, which `setup cell` sets) is asked every SYNC too, not every
UNKNOWN: its first ask can run before its Gateway key is enrolled. A new or changed key asks at once (`sync_later`).
"""

import json
import logging
import os
import threading
from datetime import timedelta

from .store import H, Problem

log = logging.getLogger(__name__)

KEY = "cloud-access"
ACCESS = ("open", "frozen", "locked")
REASONS = (None, "out_of_credit", "trial_over", "unpaid")
SYNC = timedelta(seconds=60)  # how often HQ is asked while the company is a cloud one
UNKNOWN = timedelta(hours=1)  # and while it is not (or HQ has not said yet, off a Tico Cloud cell)
OPEN_PATHS = ("/api/me", "/api/v2/me", "/api/status", "/api/v2/cloud-access", "/api/v2/system/storage/export")
OPEN_PREFIXES = ("/api/v2/billing",)
LOCKED = "Pay to continue: this company's Tico Cloud plan is locked"
ROUTINES_PAUSED = {"frozen": "out of credit", "locked": "Tico Cloud is locked"}
LOW_DAYS = 2  # under this many days of spend left, every page says so
SPEND_WINDOW = timedelta(hours=24)  # the spend per day is measured over about the last day
SAMPLE_EVERY = timedelta(hours=1)
SPEND_MIN = timedelta(hours=1)  # the shortest stretch a spend is read from
_MISSING = object()
_guard = threading.Lock()
_busy = False  # a sync thread is running
_again = False  # asked while one ran: one more sync after it (the key may have changed meanwhile)


def _url(value):
    """HQ's billing page: https only, or None."""
    return (
        value
        if isinstance(value, str)
        and value.startswith("https://")
        and len(value) <= 500
        and not any(ch.isspace() for ch in value)
        else None
    )


def _int(value):
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("a whole number")
    return value


def parse(value):
    """HQ's `cloud` object, field by field, or None for a company that is not a cloud one. Raises ValueError for any
    other shape, so nothing odd decides whether people can work."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("access") not in ACCESS or value.get("reason") not in REASONS:
        raise ValueError("not a cloud access answer")
    trial_ends = value.get("trial_ends")
    return {
        "access": value["access"],
        "reason": value["reason"],
        "plan": str(value.get("plan") or "")[:20],
        "balance_micros": _int(value.get("balance_micros")) or 0,
        "trial_ends": trial_ends if isinstance(trial_ends, str) and len(trial_ends) <= 40 else None,
        "trial_days_left": _int(value.get("trial_days_left")),
        "billing_url": _url(value.get("billing_url")),
    }


def state(c):
    """The kept state, or None (not a cloud company, or HQ has not said)."""
    from .read_cache import metadata

    row = metadata(c, KEY)
    try:
        found = json.loads(row[0]) if row else None
    except (ValueError, TypeError):
        return None
    return found if isinstance(found, dict) and found.get("access") in ACCESS else None


def access(c):
    found = state(c)
    return found["access"] if found else None


_UNREAD = object()


def frozen(c, value=_UNREAD):
    """Whether new turns that would spend usage wait: out of credit, or locked. `value`: access(c), when read already."""
    value = access(c) if value is _UNREAD else value
    return value in ("frozen", "locked")


def locked(c, value=_UNREAD):
    value = access(c) if value is _UNREAD else value
    return value == "locked"


def routines_paused(c):
    """Why routines' scheduled runs pass over now (automations.tick), or None."""
    return ROUTINES_PAUSED.get(access(c))


def cell():
    """Whether this install is a Tico Cloud cell company: TICO_CELL=1, which `setup cell` writes (docs/cell.md). A .env
    an older core wrote has only TICO_UPDATES=tico, until that company's next `cell update`; it still counts."""
    from . import releases

    return strict_cell() or releases.managed()


def strict_cell():
    """Only TICO_CELL=1 says a cell. TICO_UPDATES=tico alone is the general externally-managed-update flag, which other
    installs carry too, so Sleep when idle uses this one: its cell behaviour must not turn on for them."""
    return os.environ.get("TICO_CELL", "").strip() == "1"


def current(store):
    """The state the request gate reads: the store's copy, loaded once."""
    found = getattr(store, "_cloud_access", _MISSING)
    if found is _MISSING:
        with store.read() as c:
            found = state(c)
        store._cloud_access = found
    return found


def spend(before, balance, at):
    """(samples, spend_day_micros) after an answer with `balance` at `at` (ISO): an hourly sample is added, those older
    than SPEND_WINDOW dropped, and the spend per day is the fall from the oldest to now once SPEND_MIN apart. A rise
    (credit landed) starts again from this one and keeps the last spend; a fall too short to read keeps it as well."""
    timed = []  # (sample, when) for each readable one
    for sample in (before or {}).get("samples") or []:
        when = H.parse_ts(sample.get("at")) if isinstance(sample, dict) else None
        if when is not None and type(sample.get("balance_micros")) is int:
            timed.append((sample, when))
    rate = (before or {}).get("spend_day_micros")
    rate = rate if type(rate) is int and rate > 0 else None
    now = H.parse_ts(at)
    if now is None:
        return [sample for sample, _ in timed], rate
    if timed and balance > timed[-1][0]["balance_micros"]:
        timed = []
    timed = [(sample, when) for sample, when in timed if now - when <= SPEND_WINDOW]
    if not timed or now - timed[-1][1] >= SAMPLE_EVERY:
        timed.append(({"balance_micros": balance, "at": at}, now))
    oldest, since = timed[0]
    elapsed = now - since
    if elapsed >= SPEND_MIN:
        fall = oldest["balance_micros"] - balance
        rate = int(fall * (timedelta(days=1) / elapsed)) if fall > 0 else None
    kept = [sample for sample, _ in timed]
    return kept, rate


def days_left(found):
    """Whole days of spend left while open and under LOW_DAYS, else None."""
    if not found or found.get("access") != "open":
        return None
    rate, balance = found.get("spend_day_micros"), found.get("balance_micros") or 0
    if type(rate) is not int or rate <= 0 or balance >= LOW_DAYS * rate:
        return None
    return max(0, balance // rate)


def apply(store, cloud, at=None):
    """Keep HQ's word (`parse`d, or None for not a cloud company), with the spend read from its balances (`spend`).
    `at`: when HQ answered (ISO), now by default. Returns the state."""
    at = at or H.now()
    with store.transaction() as c:
        before = state(c)
        kept = None
        if cloud is None:
            c.execute("DELETE FROM registry_metadata WHERE key=?", (KEY,))
        else:
            samples, rate = spend(before, cloud.get("balance_micros") or 0, at)
            kept = {**cloud, "checked": at, "samples": samples, "spend_day_micros": rate}
            c.execute(
                "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (KEY, json.dumps(kept)),
            )
        was, now = (before or {}).get("access"), (cloud or {}).get("access")
        if was != now:
            H.event(
                c,
                H.TICO_ACTOR,
                "cloud.access",
                "company",
                {"from": was, "to": now, "reason": (cloud or {}).get("reason")},
            )
        low = days_left(kept)
        if kept is not None and low is not None and days_left(before) is None:
            H.event(
                c,
                H.TICO_ACTOR,
                "cloud.low_credit",
                "company",
                {
                    "days_left": low,
                    "balance_micros": kept["balance_micros"],
                    "spend_day_micros": kept["spend_day_micros"],
                },
            )
    store._cloud_access = kept
    return store._cloud_access


def from_billing(store, answer):
    """Settings > Billing's answer from HQ carries the same word: keep it, so the page and the app agree at once. Only
    a cloud company's: one without `cloud` changes nothing here (the poll's 404 is what says "not a cloud company")."""
    if isinstance(answer, dict) and answer.get("cloud") is not None:
        apply(store, answer["cloud"])


def sync(store, vault, settings):
    """Ask HQ and keep its answer. 404 (`not_cloud`, or an HQ without the route) is not a cloud company; HQ refusing
    the key or not answering keeps the last word."""
    from . import company_billing, gateway_connection

    with store.read() as c:
        key, base = gateway_connection.target(c, vault, settings)[:2]
    status, payload = company_billing.cloud(key, base)
    if status == 404:
        return apply(store, None)
    if status != 200:
        return current(store)
    try:
        return apply(store, parse(payload))
    except ValueError:
        log.warning("Tico HQ's cloud access answer had an unusable shape; keeping the last one")
        return current(store)


def due(store, at, last):
    """Whether the scheduler asks HQ now: every SYNC while the company is a cloud one, or while a Tico Cloud cell has no
    answer yet; else every UNKNOWN (self-hosted and EC2-hosted installs)."""
    if last is None:
        return True
    return at - last >= (SYNC if current(store) is not None or cell() else UNKNOWN)


def sync_later(store, vault, settings):
    """`sync` on its own thread, one at a time: a tick never waits on HQ. Asked while one runs (say, a key was just
    enrolled under it), it runs once more after, so the request is never lost."""
    global _busy, _again
    with _guard:
        if _busy:
            _again = True
            return
        _busy = True
    threading.Thread(target=_run, args=(store, vault, settings), name="cloud-access", daemon=True).start()


def _run(store, vault, settings):
    global _busy, _again
    while True:
        try:
            sync(store, vault, settings)
        except Exception:  # the next tick asks again
            log.exception("Tico Cloud access check failed")
        with _guard:
            if not _again:
                _busy = False
                return
            _again = False


def view(found):
    """What every page reads (GET /api/v2/cloud-access, /api/status): access, why, where to pay, and the days of spend
    left while that is under LOW_DAYS (`days_left`, else None). None off cloud."""
    if not found:
        return None
    out = {key: found.get(key) for key in ("access", "reason", "plan", "trial_days_left", "billing_url")}
    return {**out, "days_left": days_left(found)}


def shown(c, found):
    """`view`, out of credit as well while HQ has refused one of the company's machines for credit (its 402), even
    when its company-wide word is still open: the machines are stopped, so every page says so, with Add credit."""
    from . import cloud_machines

    out = view(found)
    if (out is None or out["access"] == "open") and cloud_machines.credit_refused(c):
        base = out or {"plan": "", "trial_days_left": None, "billing_url": None}
        return {**base, "access": "frozen", "reason": "out_of_credit", "days_left": None}
    return out


def gate(store, who, path):
    """Refuse a person's API call while the company is locked (402 `locked`, with the billing page), except OPEN_PATHS.
    Runners (whose claims answer `paused`) and Tico support pass."""
    if not path.startswith(("/api/", "/mcp")) or path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return
    if getattr(who, "role", "") == "runner" or getattr(who, "support", False):
        return
    # Revoking machine access stays available to the owner; the route gates enabling separately.
    if path == "/api/v2/cloud-machines/support" and getattr(who, "role", "") == "owner":
        return
    require_unlocked(store)


def require_unlocked(store):
    """Refuse an action that needs an unlocked company, even on a route that also permits revocation."""
    found = current(store)
    if found and found.get("access") == "locked":
        raise Problem("locked", LOCKED, 402, extra={"cloud": view(found)})
