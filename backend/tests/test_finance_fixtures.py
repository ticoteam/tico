"""Shared set-up for the Finance tests: one initialized hub database built once per worker and copied per test, the
fictional Acme org chart, and helpers to post and act. No tests here."""
import calendar
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from backend import hubdb as H
from backend.credentials import Vault
from backend.finance import actions as FA
from backend.store import Store
from backend.tests.test_api import api_settings

AT = "2026-10-15T12:00:00.000000Z"
OWNER = "human:ana"
# Engineering (led by ben) has Web under it (led by cara); Sales is led by dan. Ana owns the install.
PEOPLE = [{"id": "ana", "email": "ana@acme.example"},
          {"id": "ben", "email": "ben@acme.example", "team": "eng"},
          {"id": "cara", "email": "cara@acme.example", "team": "web", "reports_to": "ben"},
          {"id": "dan", "email": "dan@acme.example", "team": "sales"},
          {"id": "eve", "email": "eve@acme.example", "team": "web", "reports_to": "cara"},
          {"id": "fay", "email": "fay@acme.example", "team": "web", "reports_to": "cara"},
          {"id": "gus", "email": "gus@acme.example", "team": "sales", "reports_to": "dan"}]
GROUPS = {"eng": {"name": "Engineering", "lead": "person:ben"},
          "web": {"name": "Web", "parent": "eng", "lead": "person:cara"},
          "sales": {"name": "Sales", "lead": "person:dan"}}
BOTS = {"coder": ("web", "human:cara"), "seller": ("sales", "human:dan"),
        "bookkeeper": ("eng", "human:ben")}


def _seed(c: sqlite3.Connection) -> None:
    H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in BOTS},
                    {"people": PEOPLE})
    c.execute("INSERT OR REPLACE INTO registry_metadata(key, value_json) VALUES('people', ?)",
              (json.dumps({"people": PEOPLE, "org_groups": GROUPS, "default_user": "ana"}),))
    for slug, (team, boss) in BOTS.items():
        c.execute("INSERT OR REPLACE INTO bot_config(bot, config_json, team, operator, reports_to) VALUES(?,?,?,?,?)",
                  (slug, json.dumps({"team": team}), team, "human:ana", boss))


@pytest.fixture(scope="session")
def finance_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("finance-template")
    store = Store(api_settings(base))
    store.initialize(seed_market=False)
    with store.transaction() as c:
        _seed(c)
    return base / "hub.db"


@pytest.fixture
def store(tmp_path: Path, finance_template: Path) -> Store:
    """A fresh hub with the Acme org and nothing in Finance yet."""
    s = Store(api_settings(tmp_path))
    with closing(sqlite3.connect(finance_template)) as source, closing(sqlite3.connect(s.settings.db_path)) as target:
        source.backup(target)
    FA.use_vault(Vault(s))                 # a local data key beside this test's database
    return s


@pytest.fixture
def books(store: Store) -> Store:
    """The hub with Finance set up for the owner."""
    with store.transaction() as c:
        FA.setup(c, OWNER, AT)
    return store


def account(c: sqlite3.Connection, code: str) -> str:
    return str(c.execute("SELECT id FROM fin_accounts WHERE code=?", (code,)).fetchone()[0])


def budget(c: sqlite3.Connection, name: str) -> str:
    return str(c.execute("SELECT id FROM fin_budgets WHERE name=?", (name,)).fetchone()[0])


_keys = iter(range(10**9))


def act(c: sqlite3.Connection, who: str, kind: str, payload: dict[str, object], key: str | None = None,
        **kw: object) -> FA.ActionResult:
    return FA.act(c, who, kind, payload, key or f"k{next(_keys)}", at=kw.pop("at", AT), **kw)  # type: ignore[arg-type]


def close_month(c: sqlite3.Connection, month: str, who: str = OWNER, **kw: object) -> FA.ActionResult:
    """Close a month the way the books do: every money account with a settled feed row by the month's end is reconciled
    through that end at its books' balance first (the close gate asks for it), then close_period. Returns the close."""
    year, mon = (int(x) for x in month.split("-"))
    end = date(year, mon, calendar.monthrange(year, mon)[1]).isoformat()
    accounts = c.execute("SELECT m.id, m.kind, m.account_id FROM fin_money_accounts m WHERE m.status='active' AND "
                         "(SELECT MIN(f.txn_date) FROM fin_feed_txns f WHERE f.money_account_id=m.id AND f.pending=0) "
                         "<= ?", (end,)).fetchall()
    for mid, kind, gl in accounts:
        last = c.execute("SELECT MAX(statement_end) FROM fin_reconciliations WHERE money_account_id=?", (mid,)).fetchone()[0]
        if last is not None and str(last) >= end:
            continue
        balance = int(c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
                                "WHERE l.account_id=? AND e.entry_date<=?", (gl, end)).fetchone()[0])     # reversed pairs net to 0
        shown = -balance if kind == "card_program" else balance      # a card statement shows what is owed
        done = act(c, who, "reconcile", {"money_account_id": mid, "statement_end": end, "statement_balance": shown}, **kw)
        assert done.status == "committed" and done.result["status"] == "done", (mid, kind, shown, done.result)
    return act(c, who, "close_period", {"month": month}, **kw)


def reopen_rows(c: sqlite3.Connection, **kw: object) -> None:
    """Undo each money account's latest done reconciliation, so the rows it cleared (and a closed month's close keeps)
    are editable again: a reconciled row changed by the bank asks a person instead of reposting itself."""
    for (mid,) in c.execute("SELECT DISTINCT money_account_id FROM fin_reconciliations WHERE status='done' AND "
                            "undone_at IS NULL").fetchall():
        last = c.execute("SELECT id FROM fin_reconciliations WHERE money_account_id=? AND status='done' AND "
                         "undone_at IS NULL ORDER BY statement_end DESC LIMIT 1", (mid,)).fetchone()
        assert act(c, OWNER, "reconcile_undo", {"reconciliation_id": str(last[0])}, **kw).status == "committed"


def spend(c: sqlite3.Connection, code: str, amount: int, *, day: str = "2026-10-10", team: str = "",
          teammate: str = "", vendor: str = "", source_id: str = "") -> dict[str, object]:
    """A system-posted expense paid from the bank."""
    tags = {k: v for k, v in (("team", team), ("teammate", teammate)) if v}
    line: dict[str, object] = {"account_id": account(c, code), "amount": amount, "tags": tags}
    if vendor:
        line["contact_id"] = vendor
    return FA.post_entry(c, {"date": day, "source": "usage", "source_id": source_id or f"s{next(_keys)}",
                             "lines": [line, {"account_id": account(c, "1000"), "amount": -amount}]}, at=AT)


def set_lead(c: sqlite3.Connection, team: str, lead: str) -> None:
    """Change who leads a team on the org chart ("" for nobody)."""
    groups = {k: dict(v) for k, v in GROUPS.items()}
    if lead:
        groups[team]["lead"] = lead
    else:
        groups[team].pop("lead", None)
    c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'",
              (json.dumps({"people": PEOPLE, "org_groups": groups, "default_user": "ana"}),))


def set_manager(c: sqlite3.Connection, person: str, manager: str) -> None:
    """Change who a person reports to on the org chart ("" for nobody)."""
    people = [{**p, "reports_to": manager} if p["id"] == person else p for p in PEOPLE]
    c.execute("UPDATE registry_metadata SET value_json=? WHERE key='people'",
              (json.dumps({"people": people, "org_groups": GROUPS, "default_user": "ana"}),))


# ------------------------------------------------------------------------------------------------ a fake Mercury
MERCURY = json.loads((Path(__file__).parent / "fixtures" / "mercury" / "responses.json").read_text())


class FakeMercury:
    """A Mercury that answers from the recorded responses and remembers what Finance sent. `txns` is what
    /transactions holds (by id), `events` the Events API in order, `queue[name]` answers to give `name` first."""

    def __init__(self) -> None:
        from backend.finance.mercury import Answer
        self.Answer = Answer
        self.txns: dict[str, dict[str, object]] = {}
        self.events_list: list[dict[str, object]] = []
        self.requests: dict[str, dict[str, object]] = {}
        self.queue: dict[str, list[object]] = {}
        self.calls: list[tuple[str, object]] = []
        self.cards_list: list[dict[str, object]] = []
        self.balances: dict[str, float] = {}       # account id -> currentBalance, in place of the recorded one
        self.card_page = 500

    def _answer(self, name: str, arg: object, default: object):
        self.calls.append((name, arg))
        waiting = self.queue.get(name)
        found = waiting.pop(0) if waiting else default
        return found if isinstance(found, self.Answer) else self.Answer(200, dict(found))  # type: ignore[arg-type]

    def sent(self, name: str) -> list[object]:
        return [arg for n, arg in self.calls if n == name]

    def _balanced(self, recorded: dict[str, object]) -> dict[str, object]:
        listed = cast(list[dict[str, object]], recorded["accounts"])
        return {"accounts": [{**a, "currentBalance": self.balances[str(a["id"])]} if str(a["id"]) in self.balances
                             else a for a in listed]}

    def accounts(self):
        return self._answer("accounts", None, self._balanced(MERCURY["accounts"]))

    def credit(self):
        return self._answer("credit", None, self._balanced(MERCURY["credit"]))

    def users(self):
        return self._answer("users", None, MERCURY["users"])

    def transactions(self, start_after, limit=500):
        """Mercury's transaction list a page at a time: the rows after `start_after`, and page.nextPage naming the
        last row of a page that has more after it (a list of up to `limit` rows answers in one page, as before)."""
        rows = list(self.txns.values())
        ids = [str(t["id"]) for t in rows]
        start = ids.index(start_after) + 1 if start_after in ids else 0
        chunk = rows[start:start + limit]
        more = start + limit < len(rows)
        page = {"nextPage": str(chunk[-1]["id"])} if more and chunk else {}
        return self._answer("transactions", start_after, {"transactions": chunk, "page": page})

    def transaction(self, txn_id):
        raw = self.txns.get(txn_id)
        return self._answer("transaction", txn_id, raw if raw is not None else self.Answer(404, {}))

    def account_transactions(self, account_id, search=None, start=None, end=None, start_after=None, limit=500):
        """Mercury's search over one account's transactions: `search` matches a transaction's note, description or
        counterparty; `start` and `end` (dates, YYYY-MM-DD) bound its created day. The whole match comes in one page."""
        def hit(txn: dict) -> bool:
            if str(txn.get("accountId")) != account_id:
                return False
            if search and not any(search.lower() in str(txn.get(f) or "").lower()
                                  for f in ("note", "bankDescription", "counterpartyName")):
                return False
            day = str(txn.get("createdAt") or "")[:10]
            return (not start or day >= start) and (not end or day <= end)
        hits = [t for t in self.txns.values() if hit(t)]
        return self._answer("account_transactions", (account_id, search, start, end),
                            {"transactions": hits, "page": {}})

    def events(self, start_after, limit=500, newest=False):
        found = list(reversed(self.events_list))[:limit] if newest else self.events_list
        if start_after and not newest:
            ids = [str(e["id"]) for e in found]
            found = found[ids.index(start_after) + 1:] if start_after in ids else found
        return self._answer("events", start_after, {"events": found})

    def statements(self, account_id, start, end):
        return self._answer("statements", account_id, {"statements": [MERCURY["bank_statement"]]})

    def credit_statements(self, account_id):
        return self._answer("credit_statements", account_id, {"statements": [MERCURY["credit_statement_new"]]})

    def cards(self, user_id, start_after=None):
        """One page of `card_page` cards (nextPage names the last card when more follow)."""
        mine = [k for k in self.cards_list if k.get("userId") == user_id]
        ids = [str(k["id"]) for k in mine]
        start = ids.index(start_after) + 1 if start_after in ids else 0
        page = mine[start:start + self.card_page]
        more = start + self.card_page < len(mine)
        return self._answer("cards", (user_id, start_after),
                            {"cards": page, "page": {"nextPage": str(page[-1]["id"]) if more and page else None}})

    def card(self, card_id):
        found = next((k for k in self.cards_list if k.get("id") == card_id), None)
        return self._answer("card", card_id, found if found is not None else self.Answer(404, {}))

    def create_card(self, user_id, nickname, monthly_cents):
        return self._answer("create_card", (user_id, monthly_cents), MERCURY["card_created"])

    def card_limit(self, card_id, monthly_cents):
        return self._answer("card_limit", (card_id, monthly_cents), {})

    def card_state(self, card_id, op):
        return self._answer("card_state", (card_id, op), {})

    def create_recipient(self, body):
        return self._answer("create_recipient", dict(body), {"id": f"rcp-{len(self.sent('create_recipient')) + 1}"})

    def recipient(self, recipient_id):
        return self._answer("recipient", recipient_id, {"id": recipient_id})

    def request_send_money(self, account_id, body):
        answer = self._answer("request_send_money", dict(body), MERCURY["send_request_created"])
        if answer.ok:
            self.land(body, str(answer.body.get("requestId")), str(answer.body.get("status") or "pendingApproval"))
        return answer

    def create_transfer(self, body):
        """An internal transfer, numbered in the order sent (xfer-1, ...). Mercury answers with both legs, each a
        transaction: the debit (xfer-N, the out-leg) and the credit (xfer-N-credit), each carrying the body's note. Like
        the others, a leg reaches `txns` only when a test lands it."""
        number = len(self.sent("create_transfer")) + 1
        amount = float(body["amount"])

        def leg(txn_id: str, account: object, value: float) -> dict[str, object]:
            return {"id": txn_id, "accountId": str(account), "amount": value, "createdAt": "2026-10-15T12:00:00Z",
                    "postedAt": None, "status": "pending", "kind": "internalTransfer", "note": body.get("note"),
                    "externalMemo": None, "bankDescription": "Internal transfer",
                    "counterpartyName": "Internal transfer", "requestId": None, "cardId": None}
        return self._answer("create_transfer", dict(body), {
            "debitTransaction": leg(f"xfer-{number}", body["sourceAccountId"], -amount),
            "creditTransaction": leg(f"xfer-{number}-credit", body["destinationAccountId"], amount),
            "status": "pending"})

    def create_transaction(self, account_id, body):
        """A direct send: Mercury's transaction at once, numbered in the order sent (txn-direct-1, ...)."""
        number = len(self.sent("create_transaction")) + 1
        return self._answer("create_transaction", dict(body), {"id": f"txn-direct-{number}", "status": "pending"})

    def land(self, body, request_id: str, status: str = "pendingApproval") -> None:
        """A request as Mercury keeps it: only the documented list fields. Which request field Mercury shows as
        `memo` is not documented; Finance's key travels in `note` and leads `externalMemo`, so the fake shows the
        external memo (the recipient-facing one, the likelier echo)."""
        self.requests[request_id] = {"requestId": request_id, "status": status, "amount": body.get("amount"),
                                     "recipientId": body.get("recipientId"), "memo": body.get("externalMemo")}

    def send_request(self, request_id):
        return self._answer("send_request", request_id, self.requests.get(request_id) or self.Answer(404, {}))

    page_size = 1000

    def send_requests(self, account_id, start_after=None):
        rows = list(self.requests.values())
        ids = [str(r["requestId"]) for r in rows]
        rows = rows[ids.index(start_after) + 1:] if start_after in ids else rows
        page, more = rows[:self.page_size], len(rows) > self.page_size
        return self._answer("send_requests", (account_id, start_after),
                            {"requests": page, "page": {"nextPage": str(page[-1]["requestId"]) if more else None}})


def connect(store: Store, fake: FakeMercury, *, approver: str = "ben@acme.example",
            creator: str = "ana@acme.example") -> dict[str, object]:
    """Connect Acme's Mercury (checking, savings and IO) as the owner; the connection_create result."""
    from backend.finance import sync
    found, why = sync.probe(fake)  # type: ignore[arg-type]
    assert not why, why
    with store.transaction() as c:
        done = act(c, OWNER, "connection_create", {"provider": "mercury", "credential_id": "cred-mercury",
                                                   "probe": found, "approver_email": approver,
                                                   "token_creator_email": creator})
    assert done.status == "committed", done
    return dict(done.result)


def sync_once(store: Store, fake: FakeMercury, at: str = AT, **kw: object) -> str:
    """One pass of the bank worker over every connection, with `fake` as Mercury."""
    from backend.finance import sync
    with store.read() as c:
        ids = [str(r[0]) for r in c.execute("SELECT id FROM fin_connections")]
    out = ""
    for cid in ids:
        out = sync.connection(store, None, cid, make=lambda _token: fake, at=at, **kw)  # type: ignore[arg-type]
    return out
