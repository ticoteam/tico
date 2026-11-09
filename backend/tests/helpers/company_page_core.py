"""Helpers that other tests share, moved out of test_company_page_core.py so they import no test file."""

from backend import kpis as K
from backend.company_page import audiences as A, disclosure as D, stats as ST
from backend.finance import actions as FA
from backend.tests.helpers.finance_fixtures import OWNER, account, act


def make_kpi(c, name="Signups", unit="", cadence="monthly"):
    return K.create(c, OWNER, OWNER, {"name": name, "unit": unit, "cadence": cadence})


def read(c, kpi, value, end, **kw):
    return K.add_reading(c, OWNER, kpi, value, period_end=end, **kw)


def on_board(c, kpi, cls="kpi", label=None, chart="number"):
    return ST.create_stat(c, OWNER, "kpi", kpi["id"], data_class=cls, label=label or kpi["name"], chart=chart)


def setup(store, *rules, audience="investors"):
    with store.transaction() as c:
        A.seed(c)
        for rule in rules:
            D.set_rule(c, OWNER, audience, *rule[:2], **rule[2])
    return audience


def shown(cls, ref="", **kw):
    return (cls, ref, {"mode": "show", **kw})


# ------------------------------------------------------------------------------------------- the derivation check
def numbers(c, *specs):
    """KPIs on the board, each with two months of readings: (label, data class, unit)."""
    for label, cls, unit in specs:
        kpi = make_kpi(c, label, unit)
        read(c, kpi, 12_000, "2026-08-31")
        read(c, kpi, 13_000, "2026-09-30")
        on_board(c, kpi, cls=cls, label=label)


def post_income(c, code, amount, day="2026-09-12"):
    made = act(c, OWNER, "account_create", {"code": code, "name": f"Income {code}", "type": "income"})
    line = str(made.result["account_id"])
    FA.post_entry(
        c,
        {
            "date": day,
            "source": "usage",
            "source_id": f"sale-{code}",
            "lines": [{"account_id": line, "amount": -amount}, {"account_id": account(c, "1000"), "amount": amount}],
        },
        at="2026-09-12T00:00:00.000000Z",
    )
    return line
