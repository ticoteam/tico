"""Due dates on rules (docs/dev/rules-plan.md §1, §4): a date, a repeat, an owner and a status for each rule that has one.

- A due date is its own row (`rule_due_dates`), so one rule can have several (quarterly returns and an annual report).
  Its occurrences are worked out from the first date and the repeat; nothing stores them. `rule_due_status` holds each
  occurrence someone marked Satisfied, keyed by its date: by hand, or by its routine's task once a routine links one.
- Status is derived on read. Satisfied when the current occurrence has a status row (a Satisfied "once" stays so).
  Overdue after the end of that day in the due date's zone. Due soon within the linked routine's lead time (its
  trigger's `open_days`), or WINDOW_DAYS with no routine. Otherwise Upcoming.
- Repeats count from the first date, not from the occurrence before, and clamp to the month's end: monthly from the 31st
  falls on the 30th in April, and yearly from 29 February falls on the 28th in other years.
- An edit to the first date or the repeat ends the row (`ended_on`) and starts a new one, which points back at it
  (`replaces`), so the history stays true and reads as one chain. Removing a due date sets `retired_at`. Rows are never
  deleted.
- Obligations (Risk plan, slice 3): a date may carry a title, a basis (where it comes from: law, order, agency, contract,
  ours, asked, theirs) and the one task it belongs to (a matter). A date with a task is shown only to that task's
  readers (`readable`). A mark says how the day was met (`outcome` met, waived or missed), with its proof (a file the
  marker can read, a sent email, or an https link) and a note; a waiver needs the note. `occurrence_state` reads one
  occurrence as the Risk page does: due, met, waived, missed, passed, unconfirmed, or None (theirs, past: only watched).
  A date with no basis (every date made before slice 3) keeps its `status` and every rule above exactly as before.

Unversioned and idempotent (`ensure`), like backend/strategy.py.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import audit_log
from . import automation_due
from . import hubdb as H
from .store import Problem

STATUSES = ("overdue", "due_soon", "upcoming", "satisfied")
BASES = ("law", "order", "agency", "contract", "ours", "asked", "theirs")  # '' is a date that names none
BINDING = ("law", "order", "agency", "contract")  # met only with proof
OUTCOMES = ("met", "waived", "missed")
PROOF_KINDS = ("file", "email", "link")  # what a caller gives; `bank` is the bank feed's own (slice 10)
STATES = ("due", "met", "waived", "missed", "passed", "unconfirmed")
TITLE_MAX = 300
NOTE_MAX = 2000
LINK_MAX = 2000
WINDOW_DAYS = 30  # "due soon" without a routine window
MAX_OCCURRENCES = 5000  # a backstop: a weekly date from decades ago still walks in a few hundred steps
HISTORY = 5  # the last status rows a list shows for a due date
DETAIL_OPEN_DAYS = 20  # the open occurrences a detail lists as open_days (the count is always exact)
DETAIL_MARKS = 100  # the satisfied occurrences a detail shows, per row of its chain
CHAIN_MAX = 50  # the rows of one due date's chain (its edits) a detail walks


def default_zone():
    from .updates import ZONE

    return ZONE.key


# ----------------------------------------------------------------------------- dates
def parse_date(value, what="the date"):
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError:
        raise Problem("validation", f"{what} is a day as YYYY-MM-DD", 422) from None


def parse_zone(value):
    text = str(value or "").strip() or default_zone()
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise Problem("validation", f"{value} is not a time zone (an IANA name such as America/Chicago)", 422) from None
    return text


def parse_repeats(value):
    if value not in automation_due.REPEATS:
        raise Problem("validation", "Repeats is " + ", ".join(automation_due.REPEATS), 422)
    return value


def local_today(zone):
    return datetime.now(ZoneInfo(zone)).date()


def occurrences(first, repeats, until):
    """Ascending occurrences up to `until`, plus the first one after it (that one is the next to fall due). The day of
    the nth occurrence is automation_due.occurrence: one rule for both readers, so a routine's run and its due day agree."""
    out = []
    for n in range(MAX_OCCURRENCES):
        day = automation_due.occurrence(first, repeats, n)
        if day is None:
            break
        out.append(day)
        if day > until:
            break
    return out


def is_occurrence(first, repeats, day):
    return any(d == day for d in occurrences(first, repeats, day))


def state(row, satisfied, today, window=WINDOW_DAYS):
    """Where one due date stands on `today` (a day in its zone). `satisfied` is the set of due days already marked, and
    `window` the days before a due day it is Due soon. Returns {due_on, status, open}: `open` counts the unsatisfied
    occurrences inside the window (overdue or due soon)."""
    horizon = today + timedelta(days=window)
    opened, latest, beyond = [], None, None
    for day in occurrences(parse_date(row["first_due_on"]), row["repeats"], horizon):
        if day > horizon:
            beyond = day
            break
        if day.isoformat() in satisfied:
            latest = day
        else:
            opened.append(day)
    if opened:
        current = opened[0]
        status = "overdue" if current < today else "due_soon"
    elif latest is not None and row["repeats"] != "once" and beyond is not None:
        # A repeating date whose occurrences in the window are all marked has moved on: its next one is what is due.
        current, status = beyond, "upcoming"
    elif latest is not None:
        current, status = latest, "satisfied"
    else:
        current, status = beyond or parse_date(row["first_due_on"]), "upcoming"
    return {
        "due_on": current.isoformat(),
        "status": status,
        "open": len(opened),
        "open_days": [day.isoformat() for day in opened[:DETAIL_OPEN_DAYS]],
    }


# ----------------------------------------------------------------------------- reads
def _rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def live_rows(c, rule_ids=None):
    """The due dates in force (not ended, not removed), for these rules or for all of them."""
    if rule_ids is None:
        return _rows(
            c, "SELECT * FROM rule_due_dates WHERE retired_at IS NULL AND ended_on IS NULL ORDER BY created_at"
        )
    ids = list(rule_ids)
    out = []
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        out += _rows(
            c,
            "SELECT * FROM rule_due_dates WHERE retired_at IS NULL AND ended_on IS NULL AND rule_id IN (%s) "
            "ORDER BY created_at" % ",".join("?" * len(part)),
            part,
        )
    return out


def _status_rows(c, due_ids):
    found = {due_id: [] for due_id in due_ids}
    ids = list(due_ids)
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        for r in c.execute(
            "SELECT * FROM rule_due_status WHERE due_date_id IN (%s) ORDER BY satisfied_at DESC"
            % ",".join("?" * len(part)),
            part,
        ):
            found[r["due_date_id"]].append(dict(r))
    return found


def task_days(c, rows):
    """{routine id: its lead time in days} for the rows' linked routines still in place (not deleted)."""
    ids = sorted({r["routine_id"] for r in rows if r.get("routine_id")})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        for r in c.execute(
            "SELECT id, flow FROM automations WHERE deleted_at IS NULL AND id IN (%s)" % ",".join("?" * len(part)), part
        ):
            trigger = json.loads(r["flow"]).get("trigger") or {}
            if trigger.get("type") == "due":
                out[r["id"]] = automation_due.open_days_of(trigger)
    return out


def window(row, days):
    """The days before a due day that it is Due soon: its routine's lead time, else WINDOW_DAYS."""
    return days.get(row.get("routine_id") or "", WINDOW_DAYS)


def views(c, rows, today=None):
    """Each due date's view, with its status today: {id, rule_id, first_due_on, repeats, timezone, owner, routine_id,
    task_days, ended_on, retired_at, due_on, status, open, history}. `rows` are rule_due_dates rows. `task_days` is the
    linked routine's lead time (None with no routine). A list reads this; a detail reads `detail`."""
    statuses = _status_rows(c, [r["id"] for r in rows])
    days = task_days(c, rows)
    out = []
    for row in rows:
        marks = statuses.get(row["id"], [])
        satisfied = {m["due_on"] for m in marks}
        now = today or local_today(row["timezone"])
        current = state(row, satisfied, now, window(row, days))
        out.append(
            {
                "id": row["id"],
                "rule_id": row["rule_id"],
                "title": row.get("title") or "",
                "basis": row.get("basis") or "",
                "task_id": row.get("task_id"),
                "first_due_on": row["first_due_on"],
                "repeats": row["repeats"],
                "timezone": row["timezone"],
                "owner": row["owner"],
                "routine_id": row["routine_id"],
                "task_days": days.get(row["routine_id"] or ""),
                "ended_on": row["ended_on"],
                "retired_at": row["retired_at"],
                "due_on": current["due_on"],
                "status": current["status"],
                "open": current["open"],
                "history": [
                    {
                        "due_on": m["due_on"],
                        "satisfied_by": m["satisfied_by"],
                        "via": m["via"],
                        "at": m["satisfied_at"],
                        "task_id": m["task_id"],
                        **mark_fields(m),
                    }
                    for m in marks[:HISTORY]
                ],
            }
        )
    return out


def due_rows(c, rule_ids=None):
    """Views of the due dates in force, grouped by rule id."""
    grouped = {}
    for found in views(c, live_rows(c, rule_ids)):
        grouped.setdefault(found["rule_id"], []).append(found)
    return grouped


def one(c, due_id):
    row = c.execute("SELECT * FROM rule_due_dates WHERE id=?", (due_id,)).fetchone()
    return dict(row) if row else None


def detail(c, row, today=None):
    """One due date in full, for the side panel: its view with the open occurrences named, and its chain, oldest first.
    The chain runs back through the rows it replaced (ended by an edit) and on to the one that replaced it, ended or
    removed rows included, so the marks an edit left behind stay in reach by id."""
    chain = [dict(row)]
    seen = {row["id"]}
    while chain[0]["replaces"] and chain[0]["replaces"] not in seen and len(chain) < CHAIN_MAX:
        earlier = one(c, chain[0]["replaces"])
        if not earlier:
            break
        seen.add(earlier["id"])
        chain.insert(0, dict(earlier))
    while len(chain) < CHAIN_MAX:
        later = c.execute("SELECT * FROM rule_due_dates WHERE replaces=?", (chain[-1]["id"],)).fetchone()
        if not later or later["id"] in seen:
            break
        seen.add(later["id"])
        chain.append(dict(later))
    current = views(c, [row], today)[0]
    current["open_days"] = state(
        row, satisfied_days(c, row["id"]), today or local_today(row["timezone"]), window(row, task_days(c, [row]))
    )["open_days"]
    rows = []
    for link in chain:
        marks = [
            dict(m)
            for m in c.execute(
                "SELECT * FROM rule_due_status WHERE due_date_id=? ORDER BY due_on DESC LIMIT ?",
                (link["id"], DETAIL_MARKS),
            )
        ]
        rows.append(
            {
                "id": link["id"],
                "title": link.get("title") or "",
                "basis": link.get("basis") or "",
                "task_id": link.get("task_id"),
                "first_due_on": link["first_due_on"],
                "repeats": link["repeats"],
                "timezone": link["timezone"],
                "owner": link["owner"],
                "routine_id": link["routine_id"],
                "replaces": link["replaces"],
                "replaced_by": _replaced_by(c, link["id"]),
                "ended_on": link["ended_on"],
                "retired_at": link["retired_at"],
                "satisfied": [
                    {
                        "due_on": m["due_on"],
                        "satisfied_by": m["satisfied_by"],
                        "via": m["via"],
                        "satisfied_at": m["satisfied_at"],
                        "task_id": m["task_id"],
                        **mark_fields(m),
                    }
                    for m in marks
                ],
            }
        )
    return {**current, "replaces": row["replaces"], "replaced_by": _replaced_by(c, row["id"]), "chain": rows}


def _replaced_by(c, due_id):
    found = c.execute("SELECT id FROM rule_due_dates WHERE replaces=?", (due_id,)).fetchone()
    return found["id"] if found else None


def satisfied_days(c, due_id):
    return {r["due_on"] for r in c.execute("SELECT due_on FROM rule_due_status WHERE due_date_id=?", (due_id,))}


# ----------------------------------------------------------------------------- obligations (Risk plan, slice 3)
SOON_DAYS = 14  # a binding date this close makes its risk live
PAST_DAYS = 30  # a settled occurrence stays in the list this long
LOOKBACK_DAYS = 366  # how far back an unproved occurrence is still listed
NEXT_MAX_DAYS = 3660  # how far ahead a risk's next date is looked for


def mark_fields(mark):
    """How a marked occurrence ended, from its rule_due_status row: outcome, proof {kind, ref} (null with none), note,
    and the second confirmation (slice 9). Every mark made before slice 3 reads as met with no proof."""
    kind = mark.get("proof_kind") or ""
    return {
        "outcome": mark.get("outcome") or "met",
        "proof": {"kind": kind, "ref": mark.get("proof_ref") or ""} if kind else None,
        "note": mark.get("note") or "",
        "confirmed_by_id": mark.get("confirmed_by_id"),
        "confirmed_at": mark.get("confirmed_at"),
    }


def occurrence_state(basis, due_on, today, mark):
    """One occurrence as the Risk page reads it. Before or on its day: due. After it with no mark: passed when the other
    side only asked for it, None when it is theirs to meet (we only watch it), else unconfirmed. Marked met: met, unless
    the basis binds us (law, order, agency, contract) and there is no proof, then unconfirmed. Waived and missed stay
    so. A date with no basis reads like one of ours."""
    if mark:
        outcome = mark.get("outcome") or "met"
        if outcome != "met":
            return outcome
        return "unconfirmed" if basis in BINDING and not mark.get("proof_kind") else "met"
    if due_on >= today:
        return "due"
    if basis == "asked":
        return "passed"
    if basis == "theirs":
        return None
    return "unconfirmed"


def occurrence_group(found, due_on, today, mark):
    """Where an occurrence sits on the Obligations tab: unconfirmed; next (due within SOON_DAYS); later; past (settled,
    passed or watched, its day or its mark within PAST_DAYS). None when it is out of the list."""
    if found == "unconfirmed":
        return "unconfirmed"
    if found == "due":
        return "next" if due_on <= today + timedelta(days=SOON_DAYS) else "later"
    marked_on = str((mark or {}).get("satisfied_at") or "")[:10]
    recent = max(due_on.isoformat(), marked_on)
    return "past" if recent >= (today - timedelta(days=PAST_DAYS)).isoformat() else None


def obligations(c, rows, days=90, today=None, only_next=False):
    """The occurrences of these rule_due_dates rows the Risk page lists, each as {row, due_on, state, group, mark}:
    every unconfirmed one back to LOOKBACK_DAYS, every due one up to `days` ahead, and every other one settled or
    passed in the last PAST_DAYS (an older day marked lately included). `today` is each row's own day in its zone
    unless given. `only_next` reads each row's occurrences only up to its first due one, however far ahead (up to
    NEXT_MAX_DAYS), and then the days marked ahead of it: all a risk's level and next date need."""
    marks = _status_rows(c, [r["id"] for r in rows])
    out = []
    for row in rows:
        now = today or local_today(row["timezone"])
        basis = row.get("basis") or ""
        found = {m["due_on"]: m for m in marks.get(row["id"], [])}
        start = now - timedelta(days=LOOKBACK_DAYS)
        horizon = now + timedelta(days=NEXT_MAX_DAYS if only_next else days)

        for day in _each_occurrence(parse_date(row["first_due_on"]), row["repeats"]):
            if day > horizon:
                break
            mark = found.get(day.isoformat())
            if day < start and mark is None:
                continue
            if _emit(out, row, basis, now, day, mark) == "due" and only_next:
                # A day marked ahead of it still counts: a binding one marked early without proof is unconfirmed.
                for due_on in sorted(d for d in found if d > day.isoformat()):
                    _emit(out, row, basis, now, parse_date(due_on), found[due_on])
                break
    return out


def _each_occurrence(first, repeats):
    """The occurrences in order, one at a time (`occurrences` lists them all up to a day first)."""
    for n in range(MAX_OCCURRENCES):
        day = automation_due.occurrence(first, repeats, n)
        if day is None:
            return
        yield day


def _emit(out, row, basis, now, day, mark):
    """Append one occurrence to `out` when it is in the list; its state either way."""
    current = occurrence_state(basis, day, now, mark)
    group = occurrence_group(current, day, now, mark)
    if group:
        out.append({"row": row, "due_on": day.isoformat(), "state": current, "group": group, "mark": mark})
    return current


def readable(c, auth, who, rows):
    """The rows `who` may be shown: a date that belongs to a task only to that task's readers."""
    ids = sorted({r["task_id"] for r in rows if r.get("task_id")})
    if not ids:
        return list(rows)
    seen = set()
    for start in range(0, len(ids), 500):
        part = ids[start : start + 500]
        seen.update(
            r[0]
            for r in c.execute(
                f"SELECT id FROM (SELECT id FROM tasks WHERE ({auth.task_sql(c, who)})) WHERE id IN "
                f"({','.join('?' * len(part))})",
                part,
            )
        )
    return [r for r in rows if not r.get("task_id") or r["task_id"] in seen]


def _file_row(c, ref):
    return c.execute("SELECT * FROM bot_files WHERE id=? AND state<>'deleted'", (ref,)).fetchone()


def _email_readable(c, who, row):
    from . import mailbox_access

    return row["actor"] == who.actor or bool(mailbox_access.may(c, who, row["mailbox"])["read"])


def check_proof(c, auth, who, kind, ref):
    """The proof a mark may carry, as (kind, ref): a file `who` can read, an email that was sent from a mailbox they
    read (or that they sent), or an https link. Anything else is refused."""
    from urllib.parse import urlsplit

    ref = str(ref or "").strip()
    if not ref:
        raise Problem("validation", "Proof names its file, email or link (ref)", 422)
    if kind == "file":
        from . import files

        row = _file_row(c, ref)
        if not row or not files.file_visible(c, auth, who, row, {}):
            raise Problem("not_found", "That file is not one you can read", 404)
        return kind, ref
    if kind == "email":
        row = c.execute("SELECT id, mailbox, actor, state FROM outbound_email WHERE id=?", (ref,)).fetchone()
        if not row or not _email_readable(c, who, row):
            raise Problem("not_found", "That email is not one you can read", 404)
        if row["state"] != "sent":
            raise Problem("validation", f"That email was not sent (it is {row['state']}); a draft is not proof", 422)
        return kind, ref
    if kind == "link":
        try:
            parts = urlsplit(ref)
            scheme, host = parts.scheme, parts.hostname
        except ValueError:  # such as an unclosed [ in the host
            scheme, host = "", None
        if scheme != "https" or not host or len(ref) > LINK_MAX or any(ch.isspace() for ch in ref):
            raise Problem("validation", "A link is an https address", 422)
        return kind, ref
    if kind == "bank":
        raise Problem("validation", "Bank proof comes from the bank feed, not by hand", 422)
    raise Problem("validation", "Proof is a file, an email or a link", 422)


def proof_view(c, auth, who, proof, memo):
    """A mark's proof as `who` reads it: {kind, ref, name}. A file or email they cannot open keeps its kind and loses
    its ref and name; a link is shown as it is."""
    if not proof:
        return None
    kind, ref = proof["kind"], proof["ref"]
    key = (kind, ref)
    if key not in memo:
        name, shown = None, kind not in ("file", "email")
        if kind == "file":
            from . import files

            row = _file_row(c, ref)
            shown = bool(row) and files.file_visible(c, auth, who, row, memo.setdefault("files", {}))
            name = row["title"] if shown else None
        elif kind == "email":
            row = c.execute("SELECT mailbox, actor, subject FROM outbound_email WHERE id=?", (ref,)).fetchone()
            shown = bool(row) and _email_readable(c, who, row)
            name = row["subject"] if shown else None
        memo[key] = {"kind": kind, "ref": ref if shown else None, "name": name}
    return dict(memo[key])


# ----------------------------------------------------------------------------- writes
def add(
    c, actor, rule_id, first_due, repeats, zone, owner, routine_id=None, replaces=None, title="", basis="", task_id=None
):
    row_id = H.new_id()
    c.execute(
        "INSERT INTO rule_due_dates (id, rule_id, first_due_on, repeats, timezone, owner, routine_id, replaces, "
        "ended_on, retired_at, created_by, created_at, title, basis, task_id) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?)",
        (row_id, rule_id, first_due, repeats, zone, owner, routine_id, replaces, actor, H.now(), title, basis, task_id),
    )
    return row_id


def restart(c, actor, row, changes):
    """An edit to the first date or the repeat: the row ends today in its zone and a new one takes over, so the marks it
    already has stay with it. Returns the new row's id."""
    ended = local_today(row["timezone"]).isoformat()
    c.execute("UPDATE rule_due_dates SET ended_on=? WHERE id=?", (ended, row["id"]))
    fresh = {**row, **changes}
    return add(
        c,
        actor,
        row["rule_id"],
        fresh["first_due_on"],
        fresh["repeats"],
        fresh["timezone"],
        fresh["owner"],
        fresh["routine_id"],
        replaces=row["id"],
        title=fresh.get("title") or "",
        basis=fresh.get("basis") or "",
        task_id=fresh.get("task_id"),
    )


def update_in_place(c, row_id, changes):
    if not changes:
        return
    sets = ", ".join(f"{k}=?" for k in changes)
    c.execute(f"UPDATE rule_due_dates SET {sets} WHERE id=?", (*changes.values(), row_id))


def remove(c, row_id):
    c.execute("UPDATE rule_due_dates SET retired_at=? WHERE id=?", (H.now(), row_id))


def satisfy(c, actor, row, due_on=None, via="person", task_id=None, outcome="met", proof=None, note=""):
    """Mark one occurrence Satisfied. With more than one open occurrence, `due_on` says which; otherwise the current one.
    `outcome` says how it ended (met, waived or missed); `proof` is a checked (kind, ref) (`check_proof`), for a met
    day; a waiver needs a `note`. A day marked already is marked again only to settle a mark that has no proof: proof
    added to it (its doer kept), or a waiver or a miss in its place. Returns the day marked."""
    if outcome not in OUTCOMES:
        raise Problem("validation", "outcome is " + ", ".join(OUTCOMES), 422)
    note = str(note or "").strip()
    if len(note) > NOTE_MAX:
        raise Problem("validation", f"A note is at most {NOTE_MAX} characters", 422)
    if outcome == "waived" and not note:
        raise Problem("validation", "Say why it is waived: a waiver needs a note", 422)
    if proof is not None and outcome != "met":
        raise Problem("validation", "Proof is for a met date; a waiver or a miss takes a note", 422)
    settles = proof is not None or outcome != "met"
    satisfied = satisfied_days(c, row["id"])
    now = local_today(row["timezone"])
    current = state(row, satisfied, now, window(row, task_days(c, [row])))
    if due_on is None:
        if current["open"] > 1:
            raise Problem("validation", f"{current['open']} occurrences are open: say which one with due_on", 422)
        target = parse_date(current["due_on"])
    else:
        target = parse_date(due_on, "due_on")
        if not is_occurrence(parse_date(row["first_due_on"]), row["repeats"], target):
            raise Problem("validation", f"{target.isoformat()} is not an occurrence of this due date", 422)
    day = target.isoformat()
    if day in satisfied:
        if not settles or not _settle(c, actor, row, day, via, outcome, proof, note):
            raise Problem("conflict", f"{day} is already satisfied", 409)
        return day
    try:
        c.execute(
            "INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, satisfied_at, outcome, "
            "proof_kind, proof_ref, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["id"], day, actor, via, task_id, H.now(), outcome, *(proof or ("", "")), note),
        )
    except sqlite3.IntegrityError:
        # Two marks of the same day raced: the second is the same answer as marking a satisfied day.
        raise Problem("conflict", f"{day} is already satisfied", 409) from None
    return day


def _settle(c, actor, row, day, via, outcome, proof, note):
    """Settle a mark that has no proof yet. Proof on a met mark keeps who did it and when; a waiver or a miss (or a met
    with proof in place of either) is the actor's own decision, now. False when the mark has proof (or is gone)."""
    found = c.execute(
        "SELECT outcome FROM rule_due_status WHERE due_date_id=? AND due_on=? AND proof_kind=''", (row["id"], day)
    ).fetchone()
    if not found:
        return False
    kind, ref = proof or ("", "")
    if found["outcome"] == outcome == "met":
        done = c.execute(
            "UPDATE rule_due_status SET proof_kind=?, proof_ref=?, note=CASE WHEN ?='' THEN note ELSE ? END "
            "WHERE due_date_id=? AND due_on=? AND proof_kind=''",
            (kind, ref, note, note, row["id"], day),
        )
    else:
        done = c.execute(
            "UPDATE rule_due_status SET outcome=?, proof_kind=?, proof_ref=?, note=?, satisfied_by=?, via=?, "
            "satisfied_at=? WHERE due_date_id=? AND due_on=? AND proof_kind=''",
            (outcome, kind, ref, note, actor, via, H.now(), row["id"], day),
        )
    return done.rowcount == 1


def marked(c, due_id, due_on):
    """Whether that occurrence of the due date is already marked Satisfied (its routine then skips the day)."""
    return bool(
        c.execute("SELECT 1 FROM rule_due_status WHERE due_date_id=? AND due_on=?", (due_id, due_on)).fetchone()
    )


def on_task_status(c, actor, task, old, new):
    """Every status change of a routine's occurrence task (the engine's effects call this on done, closed and reopened,
    in the move's transaction, with the actor whose act caused it): a move into done or closed that completes it
    (`_completes`) marks its due date's occurrence Satisfied, whatever the status before (the owner's done after a
    close by Tico counts); a reopen takes the mark away."""
    if not task.get("occurrence") or not H._has_table(c, "rule_due_dates") or new == old:
        return
    if new in ("done", "closed"):
        on_due_task_completed(c, {**task, "status": new}, actor)
    elif new == "open":  # back in play, from done or closed
        on_due_task_reopened(c, task, actor)


def _completes(task, actor):
    """Completed (docs/dev/rules-plan.md §1): marked done by its owner, or closed by a person. A mover's or manager's done
    and a close by Tico (cleanup, a task closed with the one above it) never are."""
    if not actor or actor == H.TICO_ACTOR:
        return False
    if task.get("status") == "done":
        return actor == task.get("owner_id")
    return task.get("status") == "closed" and H.is_human(actor)


def links(c, due_id, routine_id):
    """Whether that automation is the routine the due date row links now (`routine_id`, set only by
    backend/rule_due_routine.py): the one automation that may carry the row's id in its trigger, and stamp it on a task."""
    row = one(c, due_id) if due_id and routine_id else None
    return bool(row) and row["routine_id"] == routine_id


def linked(c, row, routine_id):
    """Whether that automation is, or was, the due date row's routine: its `routine_id` now, or one the rule's audit trail
    recorded for this row (every add and edit of a due date writes its view, routine included). A routine turned off or
    deleted after it opened a task still stands for that task; an automation that only names the row never does."""
    if not routine_id:
        return False
    if row["routine_id"] == routine_id:
        return True
    for found in c.execute("SELECT before, after FROM audit_log WHERE object=?", ("org_rule:" + row["rule_id"],)):
        for raw in found:
            due = (json.loads(raw) or {}).get("due") if raw else None
            if isinstance(due, dict) and due.get("id") == row["id"] and due.get("routine_id") == routine_id:
                return True
    return False


def _occurrence_row(c, task):
    """The due date and the day a routine's task stands for: the routine and the day from its occurrence marker
    (`<automation>:<due_on>`, the server's), the row from `tasks.rule_due_id`, which the run that opened the task stamped
    only while that automation was the row's routine (automation_actions `_occurrence`), scheduled or Run now. The
    routine must be (or have been) the row's (`linked`). Later edits to the routine or the due date never change it, so
    a task opened before an edit marks the ended row and a task outliving its routine still marks its day. (None, None)
    when it is not a rule's routine or that due date was removed."""
    routine, _, due_on = str(task.get("occurrence") or "").rpartition(":")
    try:
        day = date.fromisoformat(due_on)
    except ValueError:
        return None, None
    row = one(c, task.get("rule_due_id")) if task.get("rule_due_id") else None
    if not row or row["retired_at"] or not is_occurrence(parse_date(row["first_due_on"]), row["repeats"], day):
        return None, None
    if not linked(c, row, routine):
        return None, None
    return row, due_on


def on_due_task_completed(c, task, closed_by=None):
    """A routine's task completed (`_completes`): its occurrence of the linked due date is marked Satisfied by whoever
    completed it, via routine, with the task's id and an audit row on the rule. Returns the due date ids marked."""
    if not _completes(task, closed_by):
        return []
    row, due_on = _occurrence_row(c, task)
    if not row or due_on in satisfied_days(c, row["id"]):
        return []
    try:
        c.execute(
            "INSERT INTO rule_due_status (due_date_id, due_on, satisfied_by, via, task_id, satisfied_at) "
            "VALUES (?,?,?,?,?,?)",
            (row["id"], due_on, closed_by, "routine", task.get("id"), H.now()),
        )
    except sqlite3.IntegrityError:
        return []
    audit_log.write(
        c,
        closed_by,
        "org_rule:" + row["rule_id"],
        None,
        {"due": row["id"], "satisfied": due_on, "task": task.get("id")},
        "routine",
    )
    return [row["id"]]


def on_due_task_reopened(c, task, reopened_by=None):
    """A routine's task reopened: the occurrence it marked is no longer satisfied. Removes the routine's mark for this
    task, unless it has proof, and writes an audit row on each rule. Returns the due date ids unmarked."""
    actor = reopened_by or task.get("owner_id") or H.TICO_ACTOR
    unmarked = []
    # A mark someone has since proved stays: the evidence is not undone by reopening the task.
    for mark in _rows(
        c, "SELECT * FROM rule_due_status WHERE task_id=? AND via='routine' AND proof_kind=''", (task.get("id"),)
    ):
        due = one(c, mark["due_date_id"])
        c.execute("DELETE FROM rule_due_status WHERE due_date_id=? AND due_on=?", (mark["due_date_id"], mark["due_on"]))
        if due:
            audit_log.write(
                c,
                actor,
                "org_rule:" + due["rule_id"],
                {"due": due["id"], "satisfied": mark["due_on"], "by": mark["satisfied_by"]},
                None,
                "routine",
            )
        unmarked.append(mark["due_date_id"])
    return unmarked
