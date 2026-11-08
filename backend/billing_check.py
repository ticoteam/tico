"""Tico HQ's nightly billing check on the Health page, for the Tico team's own install (Billing 1b).

HQ (ticoteam/tico-services, billing/ledger.py) checks every company's ledger at 02:00 UTC and keeps what disagrees,
append-only, at `GET /v1/staff/billing/check`: when the newest check ran, how many accounts it read, and each finding
(the company's install id, what disagreed, which row, by how many micro-dollars). This module reads that and puts it
on the owner's Health page. It never fixes anything and never writes billing data: one GET with HQ's read-only staff
key, which HQ refuses for every write.

Only a server whose environment holds that key (`TICO_HQ_STAFF_READ_KEY`, the same value as HQ's `HQ_STAFF_READ_KEY`)
asks; on every other install nothing is sent and nothing is shown. It asks hourly (backend/scheduler.py), on its own
thread, never inside a transaction, at `<TICO_HQ_URL>` (backend/census.py `hq_url`), and keeps HQ's last answer here.

The Health page then says, each red (`bad`):
- every mismatch: the company, what disagreed and the difference in dollars, written from the integer micro-dollars;
- no check at all, or a newest one older than STALE_HOURS: a check that did not run must not look clean;
- HQ not read for UNREAD_HOURS (key refused, HQ down, an answer of the wrong shape).
A clean, fresh check shows nothing, so a mismatch fixed later is gone after the next clean check (HQ keeps its record).
Nothing from HQ's answer is logged, and the key never leaves this module but in the request's header.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone

import httpx

from .census import hq_url
from .store import H

ENV_KEY = "TICO_HQ_STAFF_READ_KEY"
KEY = "hq-billing-check"
PATH = "/v1/staff/billing/check"
STALE_HOURS = 26             # the check runs at 02:00 UTC every night; two hours late counts as missed
UNREAD_HOURS = 3             # three hourly reads in a row failed: HQ's word is unknown, which is not clean
SHOWN = 20                   # mismatches listed one by one; past that, one more item counts the rest
WAIT = 10                    # seconds for HQ's answer
TRANSPORT = None             # tests replace it with an httpx.MockTransport: nothing reaches the network
KINDS = {"balance": "its cached balance", "debit": "debit", "credit": "credit",
         "cross_account": "cross-company allocation"}
FAILURES = {"refused": "HQ refused the read-only key", "unreachable": "HQ did not answer",
            "invalid": "HQ's answer was not a billing check"}
ACCOUNT = re.compile(r"[A-Za-z0-9-]{1,64}")
KIND = re.compile(r"[a-z_]{1,40}")
SUBJECT = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|\+00:00)")
_running = threading.Lock()


def env_key(settings=None):
    """HQ's read-only staff key from the server's environment, or ''."""
    return str(getattr(settings, "hq_staff_read_key", "") or os.environ.get(ENV_KEY, "")).strip()


def dollars(micros, sign=False):
    """Whole micro-dollars as dollars, in integers only: 12_500_000 -> $12.50, 1 -> $0.000001, -400_000 -> -$0.40.
    `sign` writes + on a positive amount, for a difference."""
    if type(micros) is not int:
        raise TypeError("micro-dollars are an int")
    mark = "-" if micros < 0 else "+" if sign and micros > 0 else ""
    whole, rest = divmod(abs(micros), 1_000_000)
    cents = f"{rest:06d}".rstrip("0").ljust(2, "0") if rest % 10_000 else f"{rest // 10_000:02d}"
    return f"{mark}${whole:,}.{cents}"


def _parse_at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse(body):
    """HQ's answer as {checked_at, accounts, findings} (or None before HQ's first check). Raises ValueError for
    anything else: no float, no bool for money, every field in its shape, so nothing odd reaches the page."""
    if not isinstance(body, dict) or "check" not in body:
        raise ValueError("no check")
    check = body["check"]
    if check is None:
        return None
    if not isinstance(check, dict) or not isinstance(check.get("checked_at"), str) \
            or not STAMP.fullmatch(check["checked_at"]) or type(check.get("accounts")) is not int \
            or not isinstance(check.get("findings"), list):
        raise ValueError("check shape")
    _parse_at(check["checked_at"])
    findings = []
    for item in check["findings"]:
        if not isinstance(item, dict) or not isinstance(item.get("account_id"), str) \
                or not ACCOUNT.fullmatch(item["account_id"]) or not isinstance(item.get("kind"), str) \
                or not KIND.fullmatch(item["kind"]) or type(item.get("difference_micros")) is not int:
            raise ValueError("finding shape")
        subject = item.get("subject_id")
        if subject is not None and not (isinstance(subject, str) and SUBJECT.fullmatch(subject)):
            raise ValueError("finding subject")
        findings.append({"account_id": item["account_id"], "kind": item["kind"], "subject_id": subject,
                         "difference_micros": item["difference_micros"]})
    return {"checked_at": check["checked_at"], "accounts": check["accounts"], "findings": findings}


def fetch(key, base=None):
    """Ask HQ once: ("ok", check or None) or (failure, None), failure one of FAILURES. Read-only: a GET."""
    headers = {"Accept": "application/json", "User-Agent": "tico-billing-check", "Authorization": "Bearer " + key}
    try:
        with httpx.Client(timeout=WAIT, transport=TRANSPORT, follow_redirects=False) as http:
            response = http.get((base or hq_url()) + PATH, headers=headers)
    except httpx.HTTPError:
        return "unreachable", None
    if response.status_code in (401, 403, 404):         # HQ answers 404 on staff routes when no staff key is set
        return "refused", None
    if response.status_code != 200:
        return "unreachable", None
    try:
        return "ok", parse(response.json())
    except ValueError:
        return "invalid", None


def _row(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def record(c, state, check, now):
    """Keep HQ's answer. A good read replaces the check; a failed one keeps the last good check and notes since when
    reads fail, so a blip shows nothing and a mismatch already shown stays shown."""
    stored = _row(c)
    if state == "ok":
        stored = {"read_at": now, "check": check}
    else:
        stored = {**stored, "failure": state, "failing_since": stored.get("failing_since") or now, "tried_at": now}
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(stored, sort_keys=True)))


def read_now(store, settings=None):
    """Read HQ and keep the answer: the network call outside any transaction. Returns the state, or None with no key
    or while another read is running."""
    key = env_key(settings)
    if not key or not _running.acquire(blocking=False):
        return None
    try:
        state, check = fetch(key)
        with store.transaction() as c:
            record(c, state, check, H.now())
        return state
    finally:
        _running.release()


def read_later(store, settings=None):
    """`read_now` on a background thread, for the hourly sweep: a tick never waits on HQ."""
    def run():
        try:
            read_now(store, settings)
        except Exception:
            pass                                          # the next hour tries again; the last answer stays
    thread = threading.Thread(target=run, name="billing-check", daemon=True)
    thread.start()
    return thread


def _hours(since, now):
    return (now - _parse_at(since)).total_seconds() / 3600


def _bad(key, summary):
    return {"id": "billing_check:" + key, "label": "Billing check", "status": "bad", "summary": summary, "fixes": []}


def health(c, settings=None, now=None):
    """The Health page's billing items, for the owner: [] when this server holds no key or the check is clean."""
    if not env_key(settings):
        return []
    stored, now = _row(c), now or datetime.now(timezone.utc)
    if not stored:
        return [{"id": "billing_check", "label": "Billing check", "status": "unknown",
                 "summary": "Tico HQ's nightly billing check has not been read yet.", "fixes": []}]
    out = []
    if stored.get("failure") and _hours(stored["failing_since"], now) >= UNREAD_HOURS:
        out.append(_bad("unread", f"Could not read Tico HQ's nightly billing check since "
                                  f"{stored['failing_since'][:16].replace('T', ' ')} UTC: "
                                  f"{FAILURES.get(stored['failure'], 'HQ did not answer')}. Until it is read again, "
                                  "a billing mismatch would not show here."))
    if "check" not in stored:
        return out
    check = stored["check"]
    if check is None:
        return out + [_bad("missed", "Tico HQ has no nightly billing check yet: no company's ledger has been checked.")]
    at = check["checked_at"][:16].replace("T", " ")
    if _hours(check["checked_at"], now) >= STALE_HOURS:
        out.append(_bad("missed", f"Tico HQ's nightly billing check last ran {at} UTC: it has missed a night. "
                                  "The mismatches shown are from that check."
                                  if check["findings"] else f"Tico HQ's nightly billing check last ran {at} UTC: it "
                                  "has missed a night, so the ledgers are not known to be clean."))
    for item in check["findings"][:SHOWN]:
        what = KINDS.get(item["kind"], item["kind"].replace("_", " "))
        row = f" {item['subject_id']}" if item["subject_id"] else ""
        out.append(_bad(f"{item['account_id']}:{item['kind']}:{item['subject_id'] or ''}",
                        f"Company {item['account_id']}: {what}{row} is off by {dollars(item['difference_micros'], True)} "
                        f"in the {at} UTC check. Nothing was changed; HQ keeps the record."))
    if len(check["findings"]) > SHOWN:
        out.append(_bad("more", f"{len(check['findings']) - SHOWN} more billing mismatches in the {at} UTC check "
                                "(GET /v1/staff/billing/check on HQ lists them all)."))
    return out
