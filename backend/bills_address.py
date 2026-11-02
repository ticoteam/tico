"""The bills address (docs/bookkeeper.md): the install's one bills email (Finance's own setting, written only through
`finance.actions.set_bills_email` and read with `finance.reads.bills_email`), and the path that turns a PDF or image
emailed to it into a file the Bookkeeper enters as a bill.

Mail reaches Tico through an Email & Phone inbox (backend/inbound.py). When the bills email is that inbox's address (or
the message was sent to it), each PDF or image attachment is kept once per (delivery, sha256) inside the inbound ingest
transaction, so a re-delivered page never makes a second row. The inbound loop then fetches the bytes from HQ (outside
any transaction), files them in the Bookkeeper's Library and hands them to Finance as an unmatched receipt (`attach`,
subject `unmatched`, as the Bookkeeper, under the stable key `bills-email:<row id>`, so a retry lands once). Finance
lists it in the Bookkeeper's queue until a bill takes the file (`bill_create` with origin `email` and the file in
`attachments`, which raises `finance.bill.received` and takes the file out of the unmatched receipts). A bill needs a
vendor and lines, which only reading the file gives, so the Bookkeeper makes the draft, not this path.

With no bills email set, or one no inbox receives, nothing is kept here and uploads still work. A file Finance did not
take (`needs_entry`) is listed by the Bookkeeper's hourly check until Finance holds it (`reconcile`: attached anywhere,
or kept as an unmatched receipt, makes it `filed`). A fetch that fails is tried again with backoff; after MAX_TRIES it
is `failed`, listed in one check, then `reported`: never listed or woken on again."""
import hashlib
import re
import sqlite3
import urllib.parse
from email.utils import getaddresses
from types import SimpleNamespace
from typing import Any

from fastapi import Request
from pydantic import Field

from .auth import validate_identity
from .models import Contract
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills_inbound(
 id TEXT PRIMARY KEY, delivery TEXT NOT NULL, received_at TEXT NOT NULL, sender TEXT NOT NULL DEFAULT '',
 subject TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', mime TEXT NOT NULL, sha256 TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'waiting', file_id TEXT, action_id TEXT, code TEXT NOT NULL DEFAULT '',
 tries INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bills_inbound_state ON bills_inbound(state, updated_at);
"""
ACTOR = "bot:bookkeeper"
READABLE = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic")
SHA256 = re.compile(r"[0-9a-f]{64}")
PER_MESSAGE = 10            # attachments one email contributes
PER_DRAIN = 5               # bills one inbound tick fetches
MAX_TRIES = 5               # a fetch that keeps failing stops at `failed`, listed for the Bookkeeper once
BACKOFF_MINUTES = (1, 10, 60, 360)   # the wait after the 1st, 2nd, ... failed fetch before the next try
STATES = ("waiting", "filed", "needs_entry", "failed", "reported")


def _clean(value, limit):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


def bills_email(c):
    from .finance import reads as FR
    return FR.bills_email(c)


def inbox_for(c, address):
    """The Email & Phone inbox whose address is the bills email, or None: then mail to it never reaches Tico."""
    if not address:
        return None
    row = c.execute("SELECT inbox_id FROM inbound_inboxes WHERE kind='email' AND lower(address)=? LIMIT 1",
                    (address,)).fetchone()
    return row["inbox_id"] if row else None


def view(c):
    address = bills_email(c)
    return {"bills_email": address, "inbox_id": inbox_for(c, address)}


def recipients(to):
    """The addresses a message was sent to, lower-cased, parsed from its To header (a string or a list of them):
    display names and comments are dropped, so only a real recipient address can match."""
    fields = [str(x) for x in to] if isinstance(to, (list, tuple)) else [str(to or "")]
    return {addr.strip().lower() for _, addr in getaddresses(fields) if addr.strip()}


def emailed(c, item):
    """An inbound message, inside its ingest transaction: each PDF or image attached to an email that reached the bills
    address (its inbox, or the exact address among the parsed recipients) is one row, once. Returns how many rows are
    new."""
    if item.get("kind") != "email" or item.get("spam") or not item.get("inbox_id"):
        return 0
    address = bills_email(c)
    if not address or (item["inbox_id"] != inbox_for(c, address) and address not in recipients(item.get("to"))):
        return 0
    attachments = item.get("attachments") if isinstance(item.get("attachments"), list) else []
    received = item.get("received_at")
    day = H.now()
    if isinstance(received, (int, float)):
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(received, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    made = 0
    for attachment in attachments[:PER_MESSAGE]:
        if not isinstance(attachment, dict):
            continue
        mime = str(attachment.get("type") or "").split(";", 1)[0].strip().lower()
        digest = str(attachment.get("sha256") or "").lower()
        if mime not in READABLE or not SHA256.fullmatch(digest):
            continue
        ident = "bil_" + hashlib.sha256(f"{item['delivery_id']}:{digest}".encode()).hexdigest()[:24]
        made += c.execute("INSERT OR IGNORE INTO bills_inbound(id,delivery,received_at,sender,subject,name,mime,sha256,"
                          "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (ident, str(item["delivery_id"])[:200], day, _clean(item.get("from"), 300),
                           _clean(item.get("subject"), 300), _clean(attachment.get("name"), 200) or "bill",
                           mime, digest, H.now())).rowcount
    return made


def waiting(c, limit=PER_DRAIN, at=None):
    """Waiting bills whose next fetch is due: a row that failed n times waits BACKOFF_MINUTES[n-1] since its last try."""
    at = at or H.now()
    due = " ".join(f"WHEN {n} THEN ?" for n in range(1, len(BACKOFF_MINUTES) + 1))
    cutoffs = [H.shift(at, minutes=-m) for m in BACKOFF_MINUTES]
    return [dict(r) for r in c.execute(f"SELECT * FROM bills_inbound WHERE state='waiting' AND tries<? AND "
                                       f"updated_at <= CASE tries WHEN 0 THEN ? {due} ELSE ? END "
                                       "ORDER BY updated_at LIMIT ?", (MAX_TRIES, at, *cutoffs, cutoffs[-1], limit))]


def reconcile(c):
    """Close intake work Finance has since taken: a `needs_entry` bill whose file is now attached anywhere in the
    books (or kept as an unmatched receipt, which Finance's own queue lists) becomes `filed`. How many closed."""
    from .finance import reads as FR
    rows = {r["file_id"]: r["id"] for r in c.execute("SELECT id, file_id FROM bills_inbound WHERE state='needs_entry' "
                                                     "AND file_id IS NOT NULL ORDER BY updated_at LIMIT 500")}
    held = FR.files_in_books(c, list(rows))
    for file_id in held:
        c.execute("UPDATE bills_inbound SET state='filed', code='', updated_at=? WHERE id=?", (H.now(), rows[file_id]))
    return len(held)


def for_bookkeeper(c: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Bills from the address the Bookkeeper enters by hand: Finance did not take them as they came, or their bytes
    could not be fetched (listed once, see `listed`)."""
    return [dict(r) for r in c.execute("SELECT id, received_at, sender, subject, name, file_id, code, state "
                                       "FROM bills_inbound WHERE state IN ('needs_entry','failed') "
                                       "ORDER BY received_at LIMIT ?", (limit,))]


def listed(c, ids):
    """Rows a delivered Books check listed: a `failed` one is `reported` now, so it is not listed or woken on again.
    How many."""
    ids = [i for i in ids if i][:200]
    if not ids:
        return 0
    return c.execute("UPDATE bills_inbound SET state='reported', updated_at=? WHERE state='failed' AND id IN (%s)"
                     % ",".join("?" * len(ids)), (H.now(), *ids)).rowcount


def labels(c, file_ids):
    """Words for the unmatched receipts that came by the bills address: {file id: "Emailed bill from ...: subject"}."""
    ids = [f for f in file_ids if f][:200]
    if not ids:
        return {}
    rows = c.execute("SELECT file_id, sender, subject, name FROM bills_inbound WHERE file_id IN (%s)"
                     % ",".join("?" * len(ids)), ids)
    return {r["file_id"]: f"Emailed bill from {r['sender'] or 'unknown sender'}: {r['subject'] or r['name']}"[:160]
            for r in rows}


def _hand_over(c, row, file_id):
    """Hand the file to Finance as an unmatched receipt, as the Bookkeeper, under the row's own key:
    (state, action id, code)."""
    from .finance import actions as FA
    try:
        done = FA.act(c, ACTOR, "attach", {"subject_kind": "unmatched", "file_ids": [file_id]}, "bills-email:" + row["id"])
    except FA.FinanceError as e:
        return "needs_entry", None, e.code
    if done.status == "committed":
        return "filed", done.action_id, ""
    return "needs_entry", done.action_id, done.code or done.status


def file_bill(c, files, row, blob_id, digest, size):
    """File fetched bytes in the Bookkeeper's Library and hand them to Finance, in the caller's transaction."""
    current = c.execute("SELECT state FROM bills_inbound WHERE id=?", (row["id"],)).fetchone()
    if not current or current["state"] != "waiting":
        return None                              # another tick filed it first
    made, _, _ = files.add_version(c, bot="bookkeeper", actor=ACTOR, scope="bot", task=None, conversation=None,
                                   attempt="", identity="bills-email:" + row["id"], title=row["name"], name=row["name"],
                                   mime=row["mime"], blob_id=blob_id, digest=digest, size=size)
    state, action_id, code = _hand_over(c, row, made["id"])
    c.execute("UPDATE bills_inbound SET state=?, file_id=?, action_id=?, code=?, updated_at=? WHERE id=?",
              (state, made["id"], action_id, code[:80], H.now(), row["id"]))
    H.event(c, H.KEEPER, "bills.received", row["id"], {"state": state, "file": made["id"]})
    return state


def drain(service, store, blobs, files):
    """Fetch and file waiting bills that are due (the inbound loop, after its sync). Network first, then one short
    transaction per bill; a fetch that fails counts a try, waits out its backoff and stops at `failed` after
    MAX_TRIES."""
    from .postal_mail import Unavailable, _get_bytes
    if blobs is None or files is None or getattr(service, "off", True):
        return 0
    with store.read() as c:
        rows = waiting(c)
    done = 0
    for row in rows:
        message = urllib.parse.quote(row["delivery"], safe="")
        try:
            data, _ = _get_bytes(f"{service.url}/v1/inbound/messages/{message}/attachments/{row['sha256']}",
                                 {"Authorization": "Bearer " + service.key})
        except Unavailable as e:
            with store.transaction() as c:
                c.execute("UPDATE bills_inbound SET tries=tries+1, code=?, state=CASE WHEN tries+1>=? THEN 'failed' "
                          "ELSE state END, updated_at=? WHERE id=? AND state='waiting'",
                          (str(e)[:80], MAX_TRIES, H.now(), row["id"]))
            continue
        digest = blobs.put(data, row["mime"])
        from . import blobs as B
        with store.transaction() as c:
            item = B.register(c, SimpleNamespace(actor=ACTOR), digest, len(data), row["name"], row["mime"])
            if file_bill(c, files, row, item["id"], digest, len(data)):
                done += 1
    return done


class BillsAddress(Contract):
    address: str | None = Field(default=None, max_length=200)


def install(app, store, auth):
    @app.put("/api/v2/finance/bills-address")
    def bills_address_set(request: Request, body: BillsAddress):
        """Set the bills email vendors send bills to (`address`), or clear it (`null`). Owner or admin. `inbox_id` says
        which Email & Phone inbox receives it; null means mail to it never reaches Tico, and uploads still work."""
        from .finance import actions as FA
        who = request.state.identity
        auth.domain(who)
        if who.role not in ("owner", "human") or auth.company_role(who) not in ("owner", "admin"):
            raise Problem("forbidden", "Only the owner or an admin sets the bills address", 403)
        with store.transaction() as c:
            validate_identity(c, who)
            try:
                FA.set_bills_email(c, body.address, by=who.actor)
            except FA.FinanceError as e:
                raise Problem("validation", e.message, 422) from e
            H.event(c, who.actor, "finance.bills_address", bills_email(c) or "", {})
            return view(c)
