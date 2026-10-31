"""The bills address (docs/bookkeeper.md): one Email & Phone inbox (backend/inbound.py) whose PDF and image attachments
become draft bills for the Bookkeeper.

An owner or admin names the inbox (`PUT /api/v2/finance/bills-address`); with none named there is no bills address and
uploads still work. Each attachment that reaches it is kept once per (delivery, sha256) inside the inbound ingest
transaction, so a re-delivered page never makes a second row. The inbound loop then fetches the bytes from HQ (outside
any transaction), files them in the Bookkeeper's Library, and asks Finance for a draft bill as the Bookkeeper
(`bill_create`, origin email, the file attached) under the stable key `bills-email:<row id>`, so a retry lands once.
Finance raises `finance.bill.received` for the draft. A bill Finance does not take as it came (no vendor or lines yet,
or bills not live) waits as `needs_entry`, and the Bookkeeper's hourly check lists it with its file to enter by hand."""
import hashlib
import re
import urllib.parse
from types import SimpleNamespace

from fastapi import Request
from pydantic import Field

from .auth import validate_identity
from .models import Contract
from .store import H, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS bills_address(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), inbox_id TEXT NOT NULL, set_by TEXT NOT NULL, set_at TEXT NOT NULL);
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
MAX_TRIES = 5               # a fetch that keeps failing stops at `failed`, listed for the Bookkeeper
STATES = ("waiting", "entered", "needs_entry", "failed")


def _clean(value, limit):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


def inbox_id(c):
    row = c.execute("SELECT inbox_id FROM bills_address WHERE singleton=1").fetchone()
    return row["inbox_id"] if row else None


def view(c):
    row = c.execute("SELECT b.*, i.address FROM bills_address b LEFT JOIN inbound_inboxes i ON i.inbox_id=b.inbox_id "
                    "WHERE b.singleton=1").fetchone()
    if not row:
        return {"bills_address": None}
    return {"bills_address": {"inbox_id": row["inbox_id"], "address": row["address"] or "", "set_by": row["set_by"],
                              "set_at": row["set_at"]}}


def emailed(c, item):
    """An inbound message, inside its ingest transaction: each PDF or image attached to an email that reached the bills
    address is one row, once. Returns how many rows are new."""
    if item.get("kind") != "email" or item.get("spam") or not item.get("inbox_id") or item["inbox_id"] != inbox_id(c):
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


def waiting(c, limit=PER_DRAIN):
    return [dict(r) for r in c.execute("SELECT * FROM bills_inbound WHERE state='waiting' AND tries<? "
                                       "ORDER BY updated_at LIMIT ?", (MAX_TRIES, limit))]


def for_bookkeeper(c, limit=50):
    """Bills from the address the Bookkeeper enters by hand: Finance did not take them as they came, or their bytes
    could not be fetched."""
    return [dict(r) for r in c.execute("SELECT id, received_at, sender, subject, name, file_id, code, state "
                                       "FROM bills_inbound WHERE state IN ('needs_entry','failed') "
                                       "ORDER BY received_at LIMIT ?", (limit,))]


def _bill(c, row, file_id):
    """Ask Finance for the draft bill as the Bookkeeper, under the row's own key: (state, action id, code)."""
    from .finance import actions as FA
    payload = {"origin": "email", "attachments": [file_id], "bill_date": row["received_at"][:10],
               "memo": _clean(f"Emailed by {row['sender']}: {row['subject']}", 200)}
    try:
        done = FA.act(c, ACTOR, "bill_create", payload, "bills-email:" + row["id"])
    except FA.FinanceError as e:
        return "needs_entry", None, e.code
    if done.status == "committed":
        return "entered", done.action_id, ""
    return "needs_entry", done.action_id, done.code or done.status


def file_bill(c, files, row, blob_id, digest, size):
    """File fetched bytes in the Bookkeeper's Library and ask for the draft bill, in the caller's transaction."""
    current = c.execute("SELECT state FROM bills_inbound WHERE id=?", (row["id"],)).fetchone()
    if not current or current["state"] != "waiting":
        return None                              # another tick filed it first
    made, _, _ = files.add_version(c, bot="bookkeeper", actor=ACTOR, scope="bot", task=None, conversation=None,
                                   attempt="", identity="bills-email:" + row["id"], title=row["name"], name=row["name"],
                                   mime=row["mime"], blob_id=blob_id, digest=digest, size=size)
    state, action_id, code = _bill(c, row, made["id"])
    c.execute("UPDATE bills_inbound SET state=?, file_id=?, action_id=?, code=?, updated_at=? WHERE id=?",
              (state, made["id"], action_id, code[:80], H.now(), row["id"]))
    H.event(c, H.KEEPER, "bills.received", row["id"], {"state": state, "file": made["id"]})
    return state


def drain(service, store, blobs, files):
    """Fetch and file waiting bills (the inbound loop, after its sync). Network first, then one short transaction per
    bill; a fetch that fails counts a try and stops at `failed` after MAX_TRIES."""
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
    inbox_id: str | None = Field(default=None, max_length=120)


def install(app, store, auth):
    def admin(who):
        if who.role not in ("owner", "human") or auth.company_role(who) not in ("owner", "admin"):
            raise Problem("forbidden", "Only the owner or an admin sets the bills address", 403)

    @app.get("/api/v2/finance/bills-address")
    def bills_address_read(request: Request):
        """The bills address: the Email & Phone inbox whose PDF and image attachments become draft bills, or null."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            validate_identity(c, who)
            return view(c)

    @app.put("/api/v2/finance/bills-address")
    def bills_address_set(request: Request, body: BillsAddress):
        """Name the email inbox vendors send bills to (`inbox_id`), or stop (`null`). Owner or admin."""
        who = request.state.identity
        auth.domain(who)
        admin(who)
        with store.transaction() as c:
            if body.inbox_id is None:
                c.execute("DELETE FROM bills_address")
            else:
                found = c.execute("SELECT kind FROM inbound_inboxes WHERE inbox_id=?", (body.inbox_id,)).fetchone()
                if not found or found["kind"] != "email":
                    raise Problem("not_found", "No email inbox with that id", 404)
                c.execute("INSERT OR REPLACE INTO bills_address(singleton,inbox_id,set_by,set_at) VALUES(1,?,?,?)",
                          (body.inbox_id, who.actor, H.now()))
            H.event(c, who.actor, "finance.bills_address", body.inbox_id or "", {})
            return view(c)

