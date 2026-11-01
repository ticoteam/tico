"""Stripe products in the books (plan §7.2, case P2): each product is one item and each of its prices is a ref of its own,
so a monthly and a yearly price both stay. A second read changes nothing; a product or price gone from Stripe is marked,
not removed; a Stripe sale that pays an invoice line naming the item settles that invoice. Runs on the in-memory Stripe
fake from test_finance_stripe.py with a product and price list added; no network."""
import json
from typing import Any

from backend.finance.mercury import Answer
from backend.tests.test_finance_fixtures import AT, OWNER, act, books, finance_template, store  # noqa: F401
from backend.tests.test_finance_stripe import (
    FakeStripe,
    _customer,
    _invoice_row,
    _sum,
    connect_stripe,
    stripe_pass,
)

LATER = "2026-10-15T14:00:00.000000Z"    # past the poll interval, so the pass reads Stripe again
LATEST = "2026-10-15T16:00:00.000000Z"


class CatalogStripe(FakeStripe):
    """The Stripe fake with products and prices, each list served newest first, `page` rows at a time."""

    def __init__(self, page: int = 100) -> None:
        super().__init__(page=page)
        self.product_rows: list[dict[str, Any]] = []
        self.price_rows: list[dict[str, Any]] = []

    def product(self, name: str) -> str:
        pid = f"prod_{len(self.product_rows) + 1:04d}"
        self.product_rows.append({"id": pid, "object": "product", "name": name, "active": True})
        return pid

    def price(self, product: str, amount: int, interval: str = "") -> str:
        prid = f"price_{len(self.price_rows) + 1:04d}"
        self.price_rows.append({"id": prid, "object": "price", "product": product, "active": True, "currency": "usd",
                                "unit_amount": amount, "type": "recurring" if interval else "one_time",
                                "recurring": {"interval": interval, "interval_count": 1} if interval else None})
        return prid

    def rename(self, pid: str, name: str) -> None:
        next(r for r in self.product_rows if r["id"] == pid)["name"] = name

    def remove(self, object_id: str) -> None:
        """Gone from Stripe: a product or a price, whichever `object_id` names."""
        self.product_rows = [r for r in self.product_rows if r["id"] != object_id]
        self.price_rows = [r for r in self.price_rows if r["id"] != object_id]

    def products(self, starting_after: str | None = None, limit: int = 100) -> Answer:
        return _page(self.product_rows, starting_after, min(limit, self.page))

    def prices(self, starting_after: str | None = None, limit: int = 100) -> Answer:
        return _page(self.price_rows, starting_after, min(limit, self.page))


def _page(rows: list[dict[str, Any]], starting_after: str | None, limit: int) -> Answer:
    newest = list(reversed(rows))
    start = [str(r["id"]) for r in newest].index(starting_after) + 1 if starting_after else 0
    page = newest[start:start + limit]
    return Answer(200, {"object": "list", "data": page, "has_more": start + len(page) < len(newest)})


def _items(c: Any) -> list[tuple[Any, ...]]:
    return c.execute("SELECT id, name, kind, archived_at, income_account_id FROM items ORDER BY name").fetchall()


def _refs(c: Any) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Each items ref by its Stripe key ("Product:..." or "Price:..."): (local item id, status, detail)."""
    return {str(r[0]): (str(r[1]), str(r[2]), json.loads(r[3])) for r in c.execute(
        "SELECT external_id, local_id, status, detail FROM fin_external_refs WHERE local_table='items' "
        "ORDER BY external_id").fetchall()}


def _snapshot(c: Any) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Every items row and every items ref, whole: equal before and after a read that changes nothing."""
    return (c.execute("SELECT * FROM items ORDER BY id").fetchall(),
            c.execute("SELECT * FROM fin_external_refs WHERE local_table='items' ORDER BY id").fetchall())


def test_p2_each_product_is_one_item_and_each_price_is_its_own_ref(books):
    stripe = CatalogStripe(page=2)
    connect_stripe(books, stripe)
    support = stripe.product("Acme Support")
    hosting = stripe.product("Acme Hosting")
    monthly = stripe.price(support, 1_000, "month")
    yearly = stripe.price(support, 10_000, "year")
    hosted = stripe.price(hosting, 2_500, "month")
    assert stripe_pass(books, stripe) == "ok"
    with books.read() as c:
        items = _items(c)
        refs = _refs(c)
        code = c.execute("SELECT code FROM fin_accounts WHERE id=?", (items[0][4],)).fetchone()[0]
    assert [(row[1], row[2], row[3]) for row in items] == [("Acme Hosting", "service", None),
                                                           ("Acme Support", "service", None)]
    assert code == "4000"                                    # the books' income account, which a Stripe sale posts to
    assert set(refs) == {f"Product:{support}", f"Product:{hosting}", f"Price:{monthly}", f"Price:{yearly}",
                         f"Price:{hosted}"}
    support_item, hosting_item = items[1][0], items[0][0]
    assert {refs[f"Price:{p}"][0] for p in (monthly, yearly)} == {support_item}
    assert {(refs[f"Price:{p}"][2]["interval"], refs[f"Price:{p}"][2]["amount"]) for p in (monthly, yearly)} == {
        ("month", 1_000), ("year", 10_000)}
    assert refs[f"Price:{hosted}"][0] == hosting_item


def test_p2_a_second_read_changes_nothing_and_a_price_gone_from_stripe_is_marked(books):
    stripe = CatalogStripe()
    connect_stripe(books, stripe)
    support = stripe.product("Acme Support")
    monthly = stripe.price(support, 1_000, "month")
    yearly = stripe.price(support, 10_000, "year")
    stripe_pass(books, stripe)
    with books.read() as c:
        before = _snapshot(c)
    assert stripe_pass(books, stripe, at=LATER) == "ok"
    with books.read() as c:
        assert _snapshot(c) == before                        # the same catalog again: no item, ref or write
    stripe.rename(support, "Acme Support Plus")
    stripe.remove(yearly)
    assert stripe_pass(books, stripe, at=LATEST) == "ok"
    with books.read() as c:
        items = _items(c)
        refs = _refs(c)
    assert [(row[1], row[3]) for row in items] == [("Acme Support Plus", None)]    # renamed in place, not doubled
    assert items[0][0] == before[0][0][0]
    assert {key: status for key, (_, status, _) in refs.items()} == {
        f"Product:{support}": "active", f"Price:{monthly}": "active", f"Price:{yearly}": "deleted"}


def test_p2_a_product_gone_from_stripe_archives_its_item_and_keeps_the_row(books):
    stripe = CatalogStripe()
    connect_stripe(books, stripe)
    support = stripe.product("Acme Support")
    monthly = stripe.price(support, 1_000, "month")
    stripe_pass(books, stripe)
    stripe.remove(monthly)
    stripe.remove(support)
    assert stripe_pass(books, stripe, at=LATER) == "ok"
    with books.read() as c:
        items = _items(c)
        refs = _refs(c)
    assert len(items) == 1 and items[0][3] is not None       # still there, archived
    assert {key: status for key, (_, status, _) in refs.items()} == {f"Price:{monthly}": "deleted",
                                                                     f"Product:{support}": "deleted"}


def test_p2_a_stripe_sale_that_pays_an_invoice_line_naming_the_item_settles_it(books):
    stripe = CatalogStripe()
    connect_stripe(books, stripe)
    support = stripe.product("Acme Support")
    stripe.price(support, 1_000, "month")
    stripe_pass(books, stripe)
    with books.read() as c:
        item = str(_items(c)[0][0])
    customer = _customer(books, "Acme Buyer")
    with books.transaction() as c:
        made = act(c, OWNER, "invoice_create", {"customer_id": customer, "invoice_date": "2026-10-01",
                                                "lines": [{"item_id": item, "amount": 1_000}]})
        assert made.status == "committed", made
        iid = str(made.result["invoice_id"])
        assert act(c, OWNER, "invoice_send", {"invoice_id": iid}).status == "committed"
    stripe.add("charge", 1_000, 0, "Acme Buyer")
    assert stripe_pass(books, stripe, at=LATER) == "ok"
    assert _invoice_row(books, iid) == (1_000, "paid")
    with books.read() as c:
        line = c.execute("SELECT item_id, account_id FROM fin_invoice_lines WHERE invoice_id=?", (iid,)).fetchone()
        revenue = c.execute("SELECT COALESCE(SUM(l.amount),0) FROM fin_invoice_lines l "
                            "JOIN fin_invoices i ON i.id=l.invoice_id WHERE l.item_id=? AND i.status='paid'",
                            (item,)).fetchone()[0]
        assert (_sum(c, "4000"), _sum(c, "1200")) == (-1_000, 0)   # income once, by the invoice; the sale settles AR
    assert str(line[0]) == item
    assert int(revenue) == 1_000                             # revenue by item: the paid line names the item
