"""The Company page's disclosure core: what decides whether a number may leave the install. Each test here stands for a
way a real number could get out: a missing rule, a raw Finance field, a clock that starts in the wrong place, a replaced
reading, a value worked out from what is shown, or a bot's hand on the switch."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from backend import company_page, hubdb as H, kpis as K
from backend.auth import Identity
from backend.company_page import audiences as A, disclosure as D, release, render, settings as S, stats as ST
from backend.finance import actions as FA, reads as FR
from backend.store import Problem
from backend.tests.test_api import api, headers  # noqa: F401
from backend.tests.test_finance_fixtures import OWNER, account, act, books, finance_template, spend, store  # noqa: F401

STAT_KEYS = {"id", "label", "definition", "chart", "period", "as_of", "display", "stale", "series"}
POINT_KEYS = {"period", "value", "display"}


@pytest.fixture(autouse=True)
def rebuilds(monkeypatch):
    """Records each release rebuild with the transparency it saw, within the step. The real module runs: a stub in
    sys.modules would be bound for good by whichever module imported the release or schedule module first."""
    calls = []
    real = release.rebuild

    def spy(c, reason, at=None):
        calls.append((reason, S.get(c)["transparency"]))
        return real(c, reason, at)
    monkeypatch.setattr(release, "rebuild", spy)
    return calls


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


def by_label(result):
    return {s["label"]: s for s in result["stats"]}


def held_labels(result):
    return {h["label"] for h in result["held"]}


# ------------------------------------------------------------------------------------------- the rule
def test_a_missing_rule_hides_and_a_hidden_rule_hides(store):
    setup(store)
    with store.transaction() as c:
        kpi = make_kpi(c)
        read(c, kpi, 120, "2026-08-31")
        on_board(c, kpi)
        assert D.rule_for(c, "investors", "kpi", kpi["id"])["mode"] == "hidden"
        assert D.rule_for(c, "investors", "no-such-class")["mode"] == "hidden"
        assert ST.project(c, "investors", "2026-10-15") == {"stats": [], "held": []}
        D.set_rule(c, OWNER, "investors", "kpi", mode="show")
        assert len(ST.project(c, "investors", "2026-10-15")["stats"]) == 1
        # One KPI's own cell beats its class, in the closing direction too.
        D.set_rule(c, OWNER, "investors", "kpi", kpi["id"], mode="hidden")
        assert ST.project(c, "investors", "2026-10-15")["stats"] == []
        # An audience with no such row at all, and an unknown audience, show nothing.
        assert ST.project(c, "public", "2026-10-15")["stats"] == []
        assert ST.project(c, "nobody", "2026-10-15")["stats"] == []


# ------------------------------------------------------------------------------------------- the allowlist
def test_a_stat_holds_exactly_the_view_model_and_nothing_of_the_reading(store):
    setup(store, shown("kpi"))
    with store.transaction() as c:
        kpi = K.create(c, OWNER, OWNER, {"name": "Signups inside name", "unit": "signups", "cadence": "monthly",
                                         "source_note": "SECRET-SOURCE-NOTE"})
        read(c, kpi, 120, "2026-08-31", evidence="SECRET-EVIDENCE", note="SECRET-NOTE", source="SECRET-SRC")
        on_board(c, kpi, label="Signups")
        result = ST.project(c, "investors", "2026-10-15")
    stat = result["stats"][0]
    assert set(stat) == STAT_KEYS
    assert all(set(point) == POINT_KEYS for point in stat["series"])
    assert stat["display"] == "120 signups" and stat["as_of"] == "2026-08-31" and stat["period"] == "Aug 2026"
    text = json.dumps(result)
    assert "SECRET" not in text and "inside name" not in text and "human:" not in text


def test_runway_and_cash_pass_on_their_value_only(books, monkeypatch):
    """The raw metric dicts hold accounts, cash and monthly_burn. Sentinels stand for them, and none reaches the JSON."""
    real = FR.metric

    def metric(c, viewer, name, **kw):
        if name == "cash":
            return {"name": name, "value": 500_000, "unit": "minor", "currency": "USD", "stale": False, "view_version": 987_651,
                    "accounts": [{"money_account_id": "SENTINEL-ACCOUNT", "name": "SENTINEL-CHECKING", "balance": 777_777_01,
                                  "provider_balance": 666_666_01}]}
        if name == "runway_months":
            return {"name": name, "value": "12.5", "unit": "months", "cash": 555_555_01, "monthly_burn": 444_444_01,
                    "start": "2026-07-01", "end": "2026-09-30", "stale": False}
        return real(c, viewer, name, **kw)
    monkeypatch.setattr(FR, "metric", metric)
    setup(books, shown("cash_runway"), shown("burn"))
    with books.transaction() as c:
        spend(c, "6000", 12_345_00, day="2026-09-10")
        for ref in ("cash", "runway_months", "burn"):
            ST.create_stat(c, OWNER, "finance_metric", ref)
        result = ST.project(c, "investors", "2026-10-15")
    stats = by_label(result)
    assert result["held"] == [] and set(stats) == {"Cash", "Runway", "Burn"}
    for stat in result["stats"]:
        assert set(stat) == STAT_KEYS and all(set(p) == POINT_KEYS for p in stat["series"])
    assert stats["Cash"]["display"] == "$5,000" and stats["Runway"]["display"] == "12.5 months"
    text = json.dumps(result)
    for leak in ("SENTINEL", "accounts", "monthly_burn", "7777", "6666", "5555", "4444", "view_version", "987651"):
        assert leak not in text


def test_cash_burn_and_runway_read_through_the_real_finance_metric(books):
    """No sentinels: the real metric dict goes through the real read, and only the allowlisted shape comes out."""
    setup(books, shown("cash_runway"), shown("burn"))
    with books.transaction() as c:
        spend(c, "6000", 12_345_00, day="2026-08-10")
        spend(c, "6000", 11_111_00, day="2026-09-10")
        for ref in ("cash", "runway_months", "burn"):
            ST.create_stat(c, OWNER, "finance_metric", ref)
        found = ST.board(c, "investors", "2026-10-15T12:00:00.000000Z")
    for stat in found["stats"]:
        assert set(stat) == STAT_KEYS and all(set(p) == POINT_KEYS for p in stat["series"])
    assert "Burn" in by_label(found) and found["held"] == []
    text = json.dumps(found)
    for field in ("monthly_burn", "accounts", "provider_balance", "view_version"):
        assert field not in text


def test_finance_numbers_come_from_the_books_and_a_private_line_never_shows(books):
    setup(books, shown("revenue"), shown("growth"), shown("pnl_line"), shown("burn"))
    with books.transaction() as c:
        spend(c, "6000", 10_000_00, day="2026-09-10")
        income = account(c, "4000")
        FA.post_entry(c, {"date": "2026-09-12", "source": "usage", "source_id": "sale-1",
                          "lines": [{"account_id": income, "amount": -30_000_00},
                                    {"account_id": account(c, "1000"), "amount": 30_000_00}]}, at="2026-09-12T00:00:00.000000Z")
        ST.create_stat(c, OWNER, "finance_metric", "revenue")
        ST.create_stat(c, OWNER, "finance_metric", "spend")
        made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                "parent_id": account(c, "6000")})
        pay = str(made.result["account_id"])
        assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
        ST.create_stat(c, OWNER, "finance_line", pay, label="Payroll")
        result = ST.project(c, "investors", "2026-10-15")
    stats = by_label(result)
    assert stats["Revenue"]["display"] == "$30,000" and stats["Revenue"]["period"] == "Sep 2026"
    assert stats["Spend"]["display"] == "$10,000"
    assert [s for s in result["held"] if s["label"] == "Payroll"][0]["reason"].startswith("This line is private")
    assert "Payroll" not in stats


# ------------------------------------------------------------------------------------------- the delay clock
def test_the_delay_counts_from_period_end_never_collected_or_created_time(store):
    """A 30-day delay. August is past it on Oct 15, September is inside it. Everything in the last step is entered today,
    so a clock on created or collected time would get both of these wrong."""
    setup(store, ("kpi", "", {"mode": "delayed", "lag_days": 30}))
    with store.transaction() as c:
        kpi = make_kpi(c)
        august = read(c, kpi, 100, "2026-08-31", collected_at="2026-09-01")
        # Collected long before its period ended: it doesn't make the value any earlier.
        september = read(c, kpi, 200, "2026-09-30", collected_at="2026-01-01")
        on_board(c, kpi, label="Signups")
        inside = by_label(ST.project(c, "investors", "2026-10-15"))["Signups"]
        assert inside["display"] == "100" and inside["as_of"] == "2026-08-31"
        assert [p["value"] for p in inside["series"]] == [100]
        # A correction to a period already public shows on the next rebuild; one still inside its delay shows nothing.
        read(c, kpi, 111, "2026-08-31", supersedes=august["id"], note="recount")
        read(c, kpi, 222, "2026-09-30", supersedes=september["id"], note="recount")
        corrected = ST.project(c, "investors", "2026-10-15")
        assert [p["value"] for p in by_label(corrected)["Signups"]["series"]] == [111]
        assert "222" not in json.dumps(corrected) and "200" not in json.dumps(corrected)
        # September comes in at Sep 30 + 30 days, exactly, with its corrected value.
        assert [p["value"] for p in by_label(ST.project(c, "investors", "2026-10-30"))["Signups"]["series"]] == [111]
        later = by_label(ST.project(c, "investors", "2026-10-31"))["Signups"]
        assert [p["value"] for p in later["series"]] == [111, 222] and later["as_of"] == "2026-09-30"


def test_a_period_with_nothing_eligible_is_absent_not_zero(store):
    setup(store, ("kpi", "", {"mode": "delayed", "lag_days": 30}))
    with store.transaction() as c:
        kpi = make_kpi(c)
        read(c, kpi, 100, "2026-09-30")
        on_board(c, kpi)
        found = ST.board(c, "investors", "2026-10-15")
    assert found["stats"] == [] and found["absent"][0]["reason"] == "Nothing eligible yet"


# ------------------------------------------------------------------------------------------- superseded readings
def test_a_replaced_reading_appears_nowhere(store):
    setup(store, shown("kpi"))
    with store.transaction() as c:
        kpi = make_kpi(c)
        wrong = read(c, kpi, 98765, "2026-08-31")
        read(c, kpi, 12000, "2026-08-31", supersedes=wrong["id"], note="misread the export")
        read(c, kpi, 13000, "2026-09-30")
        on_board(c, kpi, label="Signups", chart="line")
        result = ST.project(c, "investors", "2026-10-15")
        facts = ST.facts(c, "investors", "2026-08-01", "2026-08-31", at="2026-10-15")
    assert [p["value"] for p in by_label(result)["Signups"]["series"]] == [12000, 13000]
    assert "98765" not in json.dumps(result) and "98,765" not in json.dumps(result)
    assert "98765" not in json.dumps(facts) and "98,765" not in json.dumps(facts)


def test_facts_are_the_same_filtered_shape_cut_to_the_period(store):
    setup(store, shown("kpi"))
    with store.transaction() as c:
        kpi = make_kpi(c)
        read(c, kpi, 100, "2026-08-31")
        read(c, kpi, 200, "2026-09-30")
        on_board(c, kpi, label="Signups")
        hidden = make_kpi(c, "Churned")
        read(c, hidden, 7, "2026-08-31")
        on_board(c, hidden, cls="customers", label="Churned")
        facts = ST.facts(c, "investors", "2026-08-01", "2026-08-31", at="2026-10-15")
    stat = facts["stats"][0]
    assert len(facts["stats"]) == 1 and set(stat) == STAT_KEYS
    assert stat["display"] == "100" and [p["value"] for p in stat["series"]] == [100]
    assert facts["classes"]["kpi"] == "show" and facts["classes"]["legal"] == "hidden"


def test_the_leak_check_knows_the_recent_values_a_hidden_stat_keeps_back_and_not_the_shown_ones(store):
    setup(store, shown("kpi"))
    with store.transaction() as c:
        paying = make_kpi(c, "Paying customers")
        read(c, paying, 120, "2026-08-31")
        read(c, paying, 148, "2026-09-30")
        on_board(c, paying, cls="customers", label="Paying customers")
        signups = make_kpi(c, "Signups")
        read(c, signups, 500, "2026-09-30")
        on_board(c, signups, label="Signups")
        values = ST.unseen_values(c, "investors", "2026-10-15")
    assert {120.0, 148.0} <= values and 500.0 not in values


def test_a_rounded_or_delayed_value_the_audience_does_not_see_exactly_is_unseen_too(store):
    """A rounded stat shows 150 for 148, and a delayed one shows nothing yet: the exact 148 is still unseen. Only a value
    this audience sees exactly is not."""
    setup(store, shown("kpi"), ("customers", "", {"mode": "rounded"}), ("headcount", "", {"mode": "delayed", "lag_days": 60}))
    with store.transaction() as c:
        rounded = make_kpi(c, "Paying customers")
        read(c, rounded, 148, "2026-09-30")
        on_board(c, rounded, cls="customers", label="Paying customers")
        waiting = make_kpi(c, "Staff")
        read(c, waiting, 37, "2026-09-30")
        on_board(c, waiting, cls="headcount", label="Staff")
        values = ST.unseen_values(c, "investors", "2026-10-15")
    assert {148.0, 37.0} <= values


def test_a_plural_unit_reads_singular_at_exactly_one_and_a_word_ending_in_ss_does_not(store):
    assert ST._text(1, "posts") == "1 post" and ST._text(-1, "posts") == "-1 post"
    assert ST._text(2, "posts") == "2 posts" and ST._text(1, "class") == "1 class"


# ------------------------------------------------------------------------------------------- the derivation check
def numbers(c, *specs):
    """KPIs on the board, each with two months of readings: (label, data class, unit)."""
    for label, cls, unit in specs:
        kpi = make_kpi(c, label, unit)
        read(c, kpi, 12_000, "2026-08-31")
        read(c, kpi, 13_000, "2026-09-30")
        on_board(c, kpi, cls=cls, label=label)


def test_runway_next_to_burn_or_cash_holds_when_the_third_is_kept_back(store):
    setup(store, shown("burn"), shown("cash_runway"), ("cash_runway", "cash", {"mode": "hidden"}))
    with store.transaction() as c:
        numbers(c, ("Runway", "cash_runway", "months"), ("Burn", "burn", "$"))
        c.execute("UPDATE disclosure_rules SET mode='hidden' WHERE data_class='cash_runway' AND ref_id='cash'")
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Runway", "Burn"} and result["stats"] == []
    assert "reveals cash" in result["held"][0]["reason"]
    assert "12,000" not in json.dumps(result) and "13,000" not in json.dumps(result)


def test_runway_next_to_cash_reveals_a_hidden_burn(store):
    setup(store, shown("cash_runway"))
    with store.transaction() as c:
        numbers(c, ("Runway", "cash_runway", "months"), ("Cash", "cash_runway", "$"))
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Runway", "Cash"} and "reveals burn" in " ".join(h["reason"] for h in result["held"])


def test_all_three_shown_is_not_a_leak(store):
    setup(store, shown("cash_runway"), shown("burn"))
    with store.transaction() as c:
        numbers(c, ("Runway", "cash_runway", "months"), ("Cash", "cash_runway", "$"), ("Burn", "burn", "$"))
        result = ST.project(c, "investors", "2026-10-15")
    assert result["held"] == [] and len(result["stats"]) == 3


def test_margin_next_to_revenue_reveals_cost_of_revenue_until_it_is_allowed(store):
    setup(store, shown("revenue"), shown("gross_margin"), shown("growth"))
    with store.transaction() as c:
        numbers(c, ("Revenue", "revenue", "$"), ("Margin", "gross_margin", "%"))
        result = ST.project(c, "investors", "2026-10-15")
        assert held_labels(result) == {"Revenue", "Margin"}
        assert "cost of revenue" in result["held"][0]["reason"]
        # Saying so explicitly resolves it, and that is the way out the reason points to.
        D.set_rule(c, OWNER, "investors", "pnl_line", "cost_of_revenue", mode="show")
        assert ST.project(c, "investors", "2026-10-15")["held"] == []


def test_a_cash_series_reveals_a_hidden_burn_by_differences(store):
    setup(store, shown("cash_runway"), ("cash_runway", "runway_months", {"mode": "hidden"}))
    with store.transaction() as c:
        numbers(c, ("Cash", "cash_runway", "$"))
        result = ST.project(c, "investors", "2026-10-15")
        assert held_labels(result) == {"Cash"} and "Differences between periods" in result["held"][0]["reason"]
        # A Range has no numbers to subtract.
        D.set_rule(c, OWNER, "investors", "cash_runway", mode="range")
        assert ST.project(c, "investors", "2026-10-15")["held"] == []


def test_exact_growth_next_to_rounded_revenue_exposes_its_differences(store):
    setup(store, ("revenue", "", {"mode": "rounded"}), shown("growth"), ("gross_margin", "", {"mode": "rounded"}))
    with store.transaction() as c:
        revenue = make_kpi(c, "Revenue", "$")                  # off the grid, so the rounded series isn't itself exact
        read(c, revenue, 12_345, "2026-08-31")
        read(c, revenue, 13_678, "2026-09-30")
        on_board(c, revenue, cls="revenue", label="Revenue")
        numbers(c, ("Growth", "growth", "%"))
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Growth"} and "exact differences" in result["held"][0]["reason"]
    assert "12,345" not in json.dumps(result)


def test_a_rounded_series_never_shows_exact_values_or_exact_differences(store):
    setup(store, ("kpi", "", {"mode": "rounded", "rounding": "nearest:1000"}), ("customers", "", {"mode": "rounded"}))
    with store.transaction() as c:
        # 1,234 then 2,234 rounds to 1,000 and 2,000, the exact difference.
        same_gap = make_kpi(c, "Same gap")
        read(c, same_gap, 1234, "2026-08-31")
        read(c, same_gap, 2234, "2026-09-30")
        on_board(c, same_gap, label="Same gap")
        # Already on the grid, so rounding changes nothing.
        on_grid = make_kpi(c, "On grid")
        read(c, on_grid, 100_000, "2026-08-31")
        read(c, on_grid, 200_000, "2026-09-30")
        on_board(c, on_grid, label="On grid")
        # A real one: every point on one grid, tile and series alike, and none of the exact values anywhere.
        rounded = make_kpi(c, "Rounded", "$")
        read(c, rounded, 1_234_567, "2026-08-31")
        read(c, rounded, 12_345_678, "2026-09-30")
        on_board(c, rounded, cls="customers", label="Rounded")
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Same gap", "On grid"}
    stat = by_label(result)["Rounded"]
    assert stat["display"] == "~$12,000,000" and [p["value"] for p in stat["series"]] == [1_000_000, 12_000_000]
    text = json.dumps(result)
    assert "234" not in text and "678" not in text


def test_a_range_series_has_no_numbers(store):
    setup(store, ("kpi", "", {"mode": "range"}))
    with store.transaction() as c:
        kpi = make_kpi(c, "Pipeline", "$")
        read(c, kpi, 1_234_567, "2026-08-31")
        read(c, kpi, 3_456_789, "2026-09-30")
        on_board(c, kpi, label="Pipeline")
        result = ST.project(c, "investors", "2026-10-15")
    stat = by_label(result)["Pipeline"]
    assert stat["display"] == "$2,000,000–$5,000,000" and all(p["value"] is None for p in stat["series"])
    assert "234" not in json.dumps(result)


def test_a_delayed_value_cant_be_worked_out_before_its_time(store):
    setup(store, shown("burn"), shown("cash_runway"), ("cash_runway", "cash", {"mode": "delayed", "lag_days": 60}))
    with store.transaction() as c:
        numbers(c, ("Runway", "cash_runway", "months"), ("Burn", "burn", "$"))
        c.execute("UPDATE disclosure_rules SET mode='delayed', lag_days=60 WHERE ref_id='cash'")
        assert held_labels(ST.project(c, "investors", "2026-11-01")) == {"Runway", "Burn"}


def post_income(c, code, amount, day="2026-09-12"):
    made = act(c, OWNER, "account_create", {"code": code, "name": f"Income {code}", "type": "income"})
    line = str(made.result["account_id"])
    FA.post_entry(c, {"date": day, "source": "usage", "source_id": f"sale-{code}",
                      "lines": [{"account_id": line, "amount": -amount},
                                {"account_id": account(c, "1000"), "amount": amount}]}, at="2026-09-12T00:00:00.000000Z")
    return line


def test_an_exact_total_minus_exact_parts_recovers_the_hidden_part(books):
    setup(books, shown("revenue"))
    with books.transaction() as c:
        subscriptions = post_income(c, "4100", 70_000_00)
        services = post_income(c, "4200", 30_000_00)
        ST.create_stat(c, OWNER, "finance_metric", "revenue")
        ST.create_stat(c, OWNER, "finance_line", subscriptions, label="Subscriptions")
        D.set_rule(c, OWNER, "investors", "pnl_line", subscriptions, mode="show")
        D.set_rule(c, OWNER, "investors", "growth", mode="show")
        result = ST.project(c, "investors", "2026-10-15")
        assert held_labels(result) == {"Revenue", "Subscriptions"} and "minus the lines shown" in result["held"][0]["reason"]
        assert "$100,000" not in json.dumps(result)
        # With the other line open too, nothing is left to recover.
        D.set_rule(c, OWNER, "investors", "pnl_line", services, mode="show")
        assert held_labels(ST.project(c, "investors", "2026-10-15")) == set()


def test_parts_that_add_up_to_a_hidden_total_are_held(books):
    setup(books, shown("growth"))
    with books.transaction() as c:
        only = post_income(c, "4100", 70_000_00)
        ST.create_stat(c, OWNER, "finance_line", only, label="Subscriptions")
        D.set_rule(c, OWNER, "investors", "pnl_line", only, mode="show")
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Subscriptions"} and "add up to revenue" in result["held"][0]["reason"]


def test_a_private_stat_is_held_when_the_public_page_and_it_give_away_a_figure_both_keep_back(store):
    """Revenue on Investors next to Gross margin on the Public page gives away cost of revenue, which both keep back. The
    Public page's own check sees only its one stat; the Investors readers see both. The pair the other way round holds
    the Investors margin: the Public revenue is already public, so it holds nothing here."""
    setup(store)
    with store.transaction() as c:
        numbers(c, ("Revenue", "revenue", "$"), ("Margin", "gross_margin", "%"))
        S.set_transparency(c, OWNER, "open")
        # Growth is shown on both, so two exact periods of revenue don't hold it on their own: only the pair is checked.
        for audience in ("investors", "public"):
            D.set_rule(c, OWNER, audience, "growth", mode="show")
        D.set_rule(c, OWNER, "investors", "revenue", mode="show")
        D.set_rule(c, OWNER, "public", "gross_margin", mode="show")
        approve(c, "public")
        assert held_labels(ST.project(c, "investors", "2026-10-15")) == {"Revenue"}
        assert [s["label"] for s in ST.project(c, "public", "2026-10-15")["stats"]] == ["Margin"]
        D.set_rule(c, OWNER, "investors", "revenue", mode="hidden")
        D.set_rule(c, OWNER, "investors", "gross_margin", mode="show")
        D.set_rule(c, OWNER, "public", "gross_margin", mode="hidden")
        D.set_rule(c, OWNER, "public", "revenue", mode="show")
        approve(c, "public")
        assert held_labels(ST.project(c, "investors", "2026-10-15")) == {"Margin"}


def _private_payroll(c, amount):
    """Payroll, a private line, the only expense posted: total spend is Payroll and nothing else."""
    made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                            "parent_id": account(c, "6000")})
    pay = str(made.result["account_id"])
    assert act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"}).status == "committed"
    spend(c, "6200", amount, day="2026-09-10")
    return pay


def test_a_total_alone_is_held_when_its_only_active_account_is_private(books):
    """With no line stat beside it, a total is still the sum of what's posted under it. When that is one private
    account, the total is the private figure and is held with it."""
    setup(books, shown("pnl_line"), shown("burn"))
    with books.transaction() as c:
        _private_payroll(c, 10_000_00)
        ST.create_stat(c, OWNER, "finance_metric", "spend")
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Spend"} and "only active line under total spend" in result["held"][0]["reason"]
    assert "10,000" not in json.dumps(result)


def test_a_total_of_several_active_accounts_is_not_held_for_one_private_part(books):
    """Only a single active account is the total's figure. Two or more are a sum over several, so a private part
    among them doesn't hold the total on its own."""
    setup(books, shown("pnl_line"), shown("burn"))
    with books.transaction() as c:
        _private_payroll(c, 10_000_00)
        spend(c, "6000", 4_000_00, day="2026-09-10")
        ST.create_stat(c, OWNER, "finance_metric", "spend")
        result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == set() and by_label(result)["Spend"]["display"] == "$14,000"


def test_a_shown_total_is_held_when_the_books_cannot_be_read(books):
    """The check can't tell what a total would give away, so the totals shown are held."""
    setup(books, shown("pnl_line"), shown("burn"))
    with books.transaction() as c:
        spend(c, "6000", 4_000_00, day="2026-09-10")
        ST.create_stat(c, OWNER, "finance_metric", "spend")
    with mock.patch.object(ST._Books, "active_accounts", side_effect=ST._Books._errors()("forbidden", "books unreadable")):
        with books.transaction() as c:
            result = ST.project(c, "investors", "2026-10-15")
    assert held_labels(result) == {"Spend"} and "couldn't be read" in result["held"][0]["reason"]


# ------------------------------------------------------------------------------------------- presets and loosening
def modes(c, audience_id):
    rules = D.all_rules(c, audience_id)
    return {cls: D.lookup(rules, cls) for cls in D.DATA_CLASSES}


def test_preset_contents(store):
    setup(store)
    with store.transaction() as c:
        for name in D.PRESETS:
            for audience in ("public", "investors", "board"):
                before = c.execute("SELECT count(*) FROM disclosure_revisions WHERE audience_id=?", (audience,)).fetchone()[0]
                D.apply_preset(c, OWNER, audience, name)
                after = c.execute("SELECT count(*) FROM disclosure_revisions WHERE audience_id=?", (audience,)).fetchone()[0]
                assert after == before + 1
                if name == "radical":
                    found = modes(c, audience)
                    if audience == "public":
                        assert found["revenue"]["mode"] == "delayed" and found["revenue"]["lag_days"] == 30
                        assert found["pnl_line"]["mode"] == "delayed" and found["cash_runway"]["lag_days"] == 30
                        assert found["headcount"]["mode"] == "show" and found["legal"]["mode"] == "show"
                    else:
                        assert all(r["mode"] == "show" for r in found.values())
                elif name == "standard":
                    found = modes(c, audience)
                    if audience == "public":
                        assert {k for k, r in found.items() if r["mode"] != "hidden"} == {"product", "headcount", "mission"}
                    elif audience == "investors":
                        assert all(found[k]["mode"] == "show" for k in D.NUMBER_CLASSES)
                        assert found["customer_names"]["mode"] == "hidden" and found["customers"]["mode"] == "show"
                        assert found["legal"]["mode"] == "hidden" and found["people"]["mode"] == "hidden"
                    else:
                        assert found["legal"]["mode"] == "show" and found["people"]["mode"] == "show"
                else:
                    found = modes(c, audience)
                    if audience == "public":
                        assert all(r["mode"] == "hidden" for r in found.values())
                    elif audience == "investors":
                        assert found["revenue"]["mode"] == "rounded"
                        assert D.parse_rounding(found["revenue"]["rounding"]).quarterly
                        assert found["legal"]["mode"] == "hidden"
                    else:
                        assert all(r["mode"] == "show" for r in found.values())


def test_a_preset_board_of_every_finance_metric_doesnt_hold_itself(store):
    """The presets are the normal way in: an all-numbers audience must not end up holding its own numbers."""
    setup(store)
    with store.transaction() as c:
        numbers(c, ("Revenue", "revenue", "$"), ("Growth", "growth", "%"), ("Margin", "gross_margin", "%"),
                ("Burn", "burn", "$"), ("Cash", "cash_runway", "$"), ("Runway", "cash_runway", "months"))
        for name, audience in (("radical", "public"), ("radical", "investors"), ("standard", "investors"),
                               ("standard", "board"), ("quiet", "board")):
            D.apply_preset(c, OWNER, audience, name)
            assert ST.project(c, audience, "2026-11-02")["held"] == [], (name, audience)
        D.apply_preset(c, OWNER, "public", "quiet")
        assert ST.project(c, "public", "2026-11-02") == {"stats": [], "held": []}


def test_quarterly_discloses_whole_quarters_only(store):
    setup(store, ("kpi", "", {"mode": "rounded", "rounding": "sig:2 quarterly"}))
    with store.transaction() as c:
        kpi = make_kpi(c, "Signups")
        read(c, kpi, 1234, "2026-06-30")
        read(c, kpi, 2345, "2026-08-31")
        read(c, kpi, 3456, "2026-09-30")
        on_board(c, kpi, label="Signups")
        early = by_label(ST.project(c, "investors", "2026-09-15"))["Signups"]
        assert early["period"] == "Q2 2026" and early["display"] == "~1,200"
        late = by_label(ST.project(c, "investors", "2026-10-02"))["Signups"]
    assert late["period"] == "Q3 2026" and [p["period"] for p in late["series"]] == ["Q2 2026", "Q3 2026"]
    assert "2345" not in json.dumps(late)


def rule(mode, lag=0, rounding=None):
    return {"mode": mode, "lag_days": lag, "rounding": rounding}


@pytest.mark.parametrize("old, new, loosens", [
    (None, rule("show"), True),                                             # missing is hidden
    (rule("hidden"), rule("range"), True),
    (rule("show"), rule("hidden"), False),
    (rule("show"), rule("rounded", rounding="sig:2"), False),
    (rule("rounded", rounding="sig:2"), rule("show"), True),
    (rule("delayed", 30), rule("delayed", 60), False),
    (rule("delayed", 60), rule("delayed", 30), True),
    (rule("delayed", 30), rule("show"), True),
    (rule("show"), rule("delayed", 30), False),
    (rule("delayed", 30), rule("rounded"), True),                           # a current value appears: can't call it tighter
    (rule("rounded", rounding="sig:2"), rule("rounded", rounding="sig:1"), False),
    (rule("rounded", rounding="sig:1"), rule("rounded", rounding="sig:2"), True),
    (rule("rounded", rounding="nearest:100"), rule("rounded", rounding="nearest:1000"), False),
    (rule("rounded", rounding="nearest:700"), rule("rounded", rounding="nearest:1000"), True),   # not a multiple: unsure
    (rule("rounded", rounding="nearest:100"), rule("rounded", rounding="sig:1"), True),          # unlike methods: unsure
    (rule("rounded", rounding="sig:2 quarterly"), rule("rounded", rounding="sig:2"), True),
    (rule("rounded", rounding="sig:2"), rule("rounded", rounding="sig:2 quarterly"), False),
    (rule("rounded", rounding="sig:2"), rule("range", rounding="bands"), False),
    (rule("range", rounding="bands"), rule("rounded", rounding="sig:1"), True),
    (rule("rounded", rounding="nearest:1000"), rule("range", rounding="bands"), True),           # unsure
    (rule("range", rounding="step:100"), rule("range", rounding="step:50"), True),
    (rule("show"), rule("no-such-mode"), True),
    (rule("rounded", rounding="made up"), rule("rounded"), True),
    ("not a rule", rule("hidden"), True),
])
def test_is_loosening_is_conservative(old, new, loosens):
    assert D.is_loosening(old, new) is loosens


def test_every_change_writes_a_revision_and_the_same_rule_again_writes_none(store):
    setup(store)
    with store.transaction() as c:
        first = D.set_rule(c, OWNER, "investors", "revenue", mode="show")["revision"]
        assert first == D.current_revision(c, "investors")
        assert D.set_rule(c, OWNER, "investors", "revenue", mode="show") == {"revision": first, "loosened": False,
                                                                             "tightened": False}
        second = D.set_rule(c, OWNER, "investors", "revenue", mode="hidden")["revision"]
        assert second != first
        count = c.execute("SELECT count(*) FROM disclosure_revisions WHERE audience_id='investors'").fetchone()[0]
    assert count == 2


def test_a_change_that_tightens_one_cell_says_so_even_when_another_loosens(store):
    """Hiding revenue while showing customers is still a tightening: the published editions it leaves up need saying."""
    setup(store, shown("revenue"))
    with store.transaction() as c:
        mixed = D.set_rules(c, OWNER, "investors", [{"data_class": "revenue", "mode": "hidden"},
                                                    {"data_class": "customers", "mode": "show"}])
        assert mixed["loosened"] and mixed["tightened"]
        pure = D.set_rules(c, OWNER, "investors", [{"data_class": "revenue", "mode": "show"}])
        assert pure["loosened"] and not pure["tightened"]


def test_a_preset_that_narrows_says_so_and_one_that_only_widens_does_not(store):
    setup(store)
    with store.transaction() as c:
        D.apply_preset(c, OWNER, "investors", "standard")
        D.set_rule(c, OWNER, "investors", "revenue", mode="hidden")
        # Revenue opens to rounded while the other numbers close to it: one loosening and one tightening in one preset.
        mixed = D.apply_preset(c, OWNER, "investors", "quiet")
        assert mixed["loosened"] and mixed["tightened"]
        D.apply_preset(c, OWNER, "investors", "radical")
        narrower = D.apply_preset(c, OWNER, "investors", "quiet")
        assert narrower["tightened"] and not narrower["loosened"]
        wider = D.apply_preset(c, OWNER, "investors", "radical")
        assert wider["loosened"] and not wider["tightened"]


# ------------------------------------------------------------------------------------------- the stats board's approval
def approve(c, audience="investors"):
    c.execute("INSERT INTO stat_board_approvals (id, audience_id, config_hash, disclosure_revision_id, approval_id, "
              "approved_by, created_at) VALUES (?,?,?,?,?,?,?)",
              (H.new_id(), audience, ST.board_config_hash(c, audience), D.current_revision(c, audience), "appr-1", OWNER,
               H.now()))


def test_loosening_or_adding_asks_again_and_tightening_or_removing_does_not(store):
    setup(store, ("kpi", "", {"mode": "rounded"}))
    with store.transaction() as c:
        kept = make_kpi(c, "Kept")
        extra = make_kpi(c, "Extra")
        stat = on_board(c, kept, label="Kept")
        assert not ST.board_approved(c, "investors")           # no approval yet
        approve(c)
        assert ST.board_approved(c, "investors")
        # A value refreshing is not a change to the board.
        read(c, kept, 5, "2026-08-31")
        assert ST.board_approved(c, "investors")
        # Tightening applies at once and keeps the approval.
        D.set_rule(c, OWNER, "investors", "kpi", mode="range")
        assert ST.board_approved(c, "investors")
        # Loosening does not.
        D.set_rule(c, OWNER, "investors", "kpi", mode="show")
        assert not ST.board_approved(c, "investors")
        approve(c)
        # A new stat this audience sees asks again; so do its words, chart and order.
        second = on_board(c, extra, label="Extra")
        assert not ST.board_approved(c, "investors")
        approve(c)
        ST.update_stat(c, OWNER, stat["id"], chart="line")
        assert not ST.board_approved(c, "investors")
        approve(c)
        ST.update_stat(c, OWNER, stat["id"], position=9)
        assert not ST.board_approved(c, "investors")
        approve(c)
        # Taking one off applies at once and keeps it.
        ST.delete_stat(c, OWNER, second["id"])
        assert ST.board_approved(c, "investors")


def test_a_bot_renaming_its_kpi_changes_nothing_shown_and_its_unit_voids_the_approval(store):
    setup(store, shown("kpi"))
    with store.transaction() as c:
        kpi = make_kpi(c, "Signups", "signups")
        read(c, kpi, 5, "2026-08-31")
        on_board(c, kpi, label="Signups")
        approve(c)
        K.update(c, "bot:coder", kpi["id"], {"name": "Signups from the Acme deal we are losing"})
        assert ST.board_approved(c, "investors")
        assert "Acme" not in json.dumps(ST.project(c, "investors", "2026-10-15"))
        K.update(c, "bot:coder", kpi["id"], {"unit": "lost deals"})
        assert not ST.board_approved(c, "investors")


# ------------------------------------------------------------------------------------------- the switch and audiences
def test_changing_transparency_rebuilds_in_the_same_step(store, rebuilds):
    with store.transaction() as c:
        assert S.get(c)["transparency"] == "closed"            # the default, with no row at all
        S.set_transparency(c, OWNER, "open")
        S.set_transparency(c, OWNER, "private")
        S.set_transparency(c, OWNER, "closed")
        S.set_transparency(c, OWNER, "closed")                 # no change, no rebuild
    # Each rebuild saw the new state: it reads the change it's taking effect for.
    assert [state for _, state in rebuilds] == ["open", "private", "closed"]
    with pytest.raises(Problem), store.transaction() as c:
        S.set_transparency(c, OWNER, "wide open")


def test_a_failed_rebuild_leaves_the_switch_where_it_was(store, monkeypatch):
    """A page still being served mustn't read as closed. The owner sees the failure and the state stays true."""
    with store.transaction() as c:
        S.set_transparency(c, OWNER, "open")

    def broken(c, reason):
        raise RuntimeError("disk full")
    monkeypatch.setattr(company_page.release, "rebuild", broken)
    with pytest.raises(RuntimeError), store.transaction() as c:
        S.set_transparency(c, OWNER, "closed")
    with store.read() as c:
        assert S.get(c)["transparency"] == "open"


def test_taking_things_away_from_a_live_page_rebuilds(store, rebuilds):
    setup(store)
    with store.transaction() as c:
        A.update(c, OWNER, "public", enabled=True)
        D.set_rule(c, OWNER, "public", "revenue", mode="show")
        assert rebuilds == []                                  # closed: nothing is served
        S.set_transparency(c, OWNER, "open")
        rebuilds.clear()
        D.set_rule(c, OWNER, "public", "revenue", mode="hidden")
        assert len(rebuilds) == 1
        A.update(c, OWNER, "public", enabled=False)
        assert len(rebuilds) == 2
        D.apply_preset(c, OWNER, "public", "quiet")
        assert len(rebuilds) == 3
        # Another audience isn't on the public files; it is checked per request.
        D.set_rule(c, OWNER, "investors", "revenue", mode="hidden")
        assert len(rebuilds) == 3


def test_the_mission_is_the_published_vision_and_only_where_a_rule_allows_it(store):
    setup(store)
    with store.transaction() as c:
        c.execute("UPDATE strategy_versions SET vision='UNPUBLISHED DRAFT', summary='INSIDE SUMMARY' WHERE status='draft'")
        c.execute("INSERT INTO strategy_versions (id, n, status, vision, summary) VALUES ('v1', 999, 'published', "
                  "'Every landlord runs on Acme.', 'INSIDE SUMMARY')")
        assert S.get(c)["mission"] == "Every landlord runs on Acme."
        assert S.mission_for(c, "public") == ""
        D.set_rule(c, OWNER, "public", "mission", mode="show")
        assert S.mission_for(c, "public") == "Every landlord runs on Acme."
        # With no vision written, the strategy's summary stays inside.
        c.execute("UPDATE strategy_versions SET vision=''")
        assert "SUMMARY" not in S.mission_for(c, "public")


def test_slug_is_one_dns_label(store):
    with store.transaction() as c:
        assert S.update(c, OWNER, slug="Acme-Co")["slug"] == "acme-co"
        for bad in ("a.b", "-acme", "acme-", "a" * 64, "acme co", "xn--acme", "www", "acme/../x"):
            with pytest.raises(Problem):
                S.update(c, OWNER, slug=bad)
        with pytest.raises(Problem):
            S.update(c, OWNER, accent="red;background:url(x)")


def test_a_logo_is_an_image_in_the_store(store):
    with store.transaction() as c:
        # The page's own upload is named for it; a library picture is an image too but never the logo.
        for blob, name, kind in (("logo-1", S.LOGO_NAME + ".png", "image/png"), ("doc-1", "doc.pdf", "application/pdf"),
                                 ("pic-1", "holiday.png", "image/png")):
            c.execute("INSERT INTO blobs (id, owner, digest, size, name, content_type, created) VALUES (?,?,?,?,?,?,?)",
                      (blob, OWNER, blob, 1, name, kind, H.now()))
        assert S.update(c, OWNER, logo_file_id="logo-1")["logo_file_id"] == "logo-1"
        for bad in ("doc-1", "pic-1", "no-such-file", "../etc/passwd"):
            with pytest.raises(Problem):
                S.update(c, OWNER, logo_file_id=bad)
        assert set(S.get(c)) >= {"transparency", "accent", "slug", "logo_file_id"}


def test_the_address_is_the_reader_origin_else_the_slug_under_the_page_domain():
    def app(origin="", domain=""):
        return SimpleNamespace(reader_origin=origin, page_domain=domain)
    assert S.address("acme", app()) is None
    assert S.address("", app(domain="pages.example")) is None
    assert S.address("acme", app(domain="pages.example")) == "https://acme.pages.example"
    assert S.address("acme", app("https://updates.acme.example/", "pages.example")) == "https://updates.acme.example"


def test_what_can_go_on_the_board_is_names_only_and_never_a_private_line(books):
    with books.transaction() as c:
        kept = make_kpi(c, "Signups")
        read(c, kept, 4242, "2026-08-31")
        K.set_archived(c, OWNER, make_kpi(c, "Old one")["id"], True)
        spend(c, "6000", 42_424_00, day="2026-09-10")
        made = act(c, OWNER, "account_create", {"code": "6200", "name": "Payroll", "type": "expense",
                                                "parent_id": account(c, "6000")})
        pay = str(made.result["account_id"])
        act(c, OWNER, "private_lines_set", {"account_id": pay, "others_see": "totals"})
        found = ST.available(c)
    assert all(set(item) == {"source_kind", "ref_id", "label"} for item in found)
    kinds = {(item["source_kind"], item["label"]) for item in found}
    assert ("kpi", "Signups") in kinds and ("finance_metric", "Revenue") in kinds and ("finance_metric", "Runway") in kinds
    lines = [item for item in found if item["source_kind"] == "finance_line"]
    assert account(c, "6000") in {item["ref_id"] for item in lines}
    assert pay not in {item["ref_id"] for item in lines} and "Payroll" not in json.dumps(found)
    assert "Old one" not in json.dumps(found) and "4242" not in json.dumps(found) and "42424" not in json.dumps(found)


def test_built_ins_are_seeded_once_disabled_and_cant_be_deleted(store):
    with store.transaction() as c:
        A.seed(c)
        A.update(c, OWNER, "board", name="The board", enabled=True)
        A.seed(c)
        found = {a["kind"]: a for a in A.list_all(c)}
        assert set(found) == {"board", "investors", "public"}
        assert found["board"]["name"] == "The board" and found["board"]["enabled"] is True     # seed left it alone
        assert found["investors"]["enabled"] is False and found["public"]["enabled"] is False
        assert found["investors"]["due_rule"] == {"kind": "business_day", "n": 10}
        assert found["public"]["due_rule"] == {"kind": "days_after_period_end", "n": 30}
        due = {"kind": "days_after_period_end", "n": 3}
        assert A.update(c, OWNER, "board", due_rule=due)["due_rule"] == due
        stored = c.execute("SELECT due_rule FROM audiences WHERE id='board'").fetchone()[0]
        assert json.loads(stored) == due
        for bad in ({"business_day": 3}, {"kind": "business_day", "n": 0}, {"kind": "business_day", "n": 99},
                    {"kind": "days_after_period_end", "n": True}, {"kind": "someday", "n": 1}, {}):
            with pytest.raises(Problem):
                A.update(c, OWNER, "board", due_rule=bad)
        with pytest.raises(Problem) as refused:
            A.delete(c, OWNER, "public")
        assert refused.value.status == 409
        custom = A.create(c, OWNER, "Advisors")
        assert custom["kind"] == "custom" and custom["enabled"] is False
        source = A.add_source(c, OWNER, custom["id"], "bot:coder")
        assert source["author"] == "coder" and A.add_source(c, OWNER, custom["id"], "coder")["id"] == source["id"]
        with pytest.raises(Problem):
            A.add_source(c, OWNER, custom["id"], "bot:no-such-bot")
        with pytest.raises(Problem):
            A.update(c, OWNER, custom["id"], approver="bot:coder")     # an approval is a person's own click
        D.set_rule(c, OWNER, custom["id"], "revenue", mode="show")
        A.delete(c, OWNER, custom["id"])
        assert not c.execute("SELECT 1 FROM disclosure_rules WHERE audience_id=?", (custom["id"],)).fetchone()


def test_a_bot_is_refused_every_write_at_the_module_too(store):
    """The route checks first; this holds for any caller that reaches the module another way."""
    setup(store)
    with store.transaction() as c:
        kpi = make_kpi(c)
        for write in (lambda: S.set_transparency(c, "bot:coder", "open"),
                      lambda: S.update(c, "bot:coder", slug="acme"),
                      lambda: A.create(c, "bot:coder", "Advisors"),
                      lambda: A.update(c, "bot:coder", "public", enabled=True),
                      lambda: D.set_rule(c, "bot:coder", "public", "revenue", mode="show"),
                      lambda: D.apply_preset(c, "bot:coder", "public", "radical"),
                      lambda: ST.create_stat(c, "bot:coder", "kpi", kpi["id"])):
            with pytest.raises(Problem) as refused:
                write()
            assert refused.value.status == 403
        assert S.get(c)["transparency"] == "closed"


# ------------------------------------------------------------------------------------------- the routes
def call(api, method, path, body=None, token="ana-test", expected=200, key=None):
    r = api.request(method, "/api/v2/" + path, json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def test_routes_owners_and_admins_write_bots_and_members_dont(api, monkeypatch, rebuilds):  # noqa: F811
    identities = api.app.state.store.settings.test_identities
    identities["bot-test"] = Identity("bot:ops", "bot", agent="hermes")
    identities["botops-for-ana"] = Identity("human:ana", "owner", "ana@acme.example", via="botops", confirmed=True)
    identities["botops-for-cara"] = Identity("human:cara", "human", "cara@acme.example", via="botops", confirmed=True)
    identities["manager-bot"] = Identity("bot:ops", "bot", agent="hermes", via="botops", confirmed=True)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created,created_by) "
                  "VALUES('ops','hermes','x-ops',?,'human:ana')", (H.now(),))

    # Seeded and listed, all closed.
    audiences = call(api, "GET", "audiences")["audiences"]
    assert {a["kind"] for a in audiences} == {"board", "investors", "public"}
    assert call(api, "GET", "company-page")["transparency"] == "closed"

    writes = [("PATCH", "company-page", {"transparency": "open", "slug": "acme"}),
              ("POST", "audiences", {"name": "Advisors"}),
              ("PATCH", "audiences/public", {"enabled": True}),
              ("PUT", "audiences/public/disclosure", {"rules": [{"data_class": "revenue", "mode": "show"}]}),
              ("POST", "audiences/public/disclosure/preset", {"name": "radical"}),
              ("POST", "audiences/public/sources", {"author": "company"}),
              ("POST", "company-page/stats", {"source_kind": "finance_metric", "ref_id": "revenue"})]
    for token in ("bot-test", "manager-bot", "cara-test", "botops-for-cara"):
        for method, path, body in writes:
            call(api, method, path, body, token=token, expected=403)
        for path in ("company-page", "audiences", "audiences/public/disclosure", "audiences/public/stats",
                     "company-page/stats", "company-page/preview?audience=public"):
            r = api.get("/api/v2/" + path, headers=headers(token))
            assert r.status_code == 403, (token, path, r.text)
    assert rebuilds == []
    assert call(api, "GET", "company-page")["transparency"] == "closed"

    # No address, no opening: it would publish to nowhere. A slug alone isn't one until there's a domain to put it under.
    call(api, "PATCH", "company-page", {"transparency": "open"}, expected=422)
    assert call(api, "PATCH", "company-page", {"slug": "not one.label"}, expected=422)
    assert call(api, "PATCH", "company-page", {"slug": "acme"})["address"] is None
    call(api, "PATCH", "company-page", {"transparency": "open"}, expected=422)
    monkeypatch.setattr(api.app.state.store.settings, "page_domain", "pages.example")
    assert call(api, "GET", "company-page")["address"] == "https://acme.pages.example"
    assert call(api, "PATCH", "company-page", {"slug": ""})["address"] is None
    # An admin may, and BotOps for the owner may, and a retry replays rather than writes twice.
    key = "same-key"
    made = call(api, "POST", "audiences", {"name": "Advisors"}, token="ben-test", key=key)
    again = call(api, "POST", "audiences", {"name": "Advisors"}, token="ben-test", key=key)
    assert made["id"] == again["id"]
    assert call(api, "PATCH", "company-page", {"slug": "acme"}, token="botops-for-ana")["slug"] == "acme"
    assert call(api, "PATCH", "company-page", {"transparency": "open"})["transparency"] == "open"
    assert [state for _, state in rebuilds] == ["open"]
    call(api, "PATCH", "company-page", {"slug": ""}, expected=422)     # the live page keeps its address
    assert call(api, "PATCH", "company-page", {"transparency": "closed"})["transparency"] == "closed"
    assert [state for _, state in rebuilds] == ["open", "closed"]
    # A write with no key is refused, not guessed at.
    assert api.post("/api/v2/audiences", json={"name": "No key"},
                    headers={"Authorization": "Bearer ana-test"}).status_code == 422

    # The grid and the board.
    grid = call(api, "PUT", "audiences/investors/disclosure",
                {"rules": [{"data_class": "kpi", "mode": "show"}, {"data_class": "legal", "mode": "hidden"}]})
    assert {(r["data_class"], r["mode"]) for r in grid["rules"]} == {("kpi", "show"), ("legal", "hidden")}
    call(api, "PUT", "audiences/investors/disclosure", {"rules": [{"data_class": "nope", "mode": "show"}]}, expected=422)
    call(api, "POST", "audiences/investors/disclosure/preset", {"name": "standard"})
    assert call(api, "GET", "audiences/investors/disclosure")["revision"]
    with api.app.state.store.transaction() as c:
        kpi = make_kpi(c)
        read(c, kpi, 321, "2026-08-31")
    stat = call(api, "POST", "company-page/stats", {"source_kind": "kpi", "ref_id": kpi["id"], "label": "Signups"})
    call(api, "POST", "company-page/stats", {"source_kind": "kpi", "ref_id": kpi["id"]}, expected=409)
    assert call(api, "PATCH", f"company-page/stats/{stat['id']}", {"chart": "bars"})["chart"] == "bars"
    board = call(api, "GET", "audiences/investors/stats")
    assert [s["display"] for s in board["stats"]] == ["321"] and board["approved"] is False
    assert call(api, "GET", "company-page/stats")["stats"][0]["id"] == stat["id"]
    call(api, "DELETE", f"company-page/stats/{stat['id']}")
    assert call(api, "GET", "company-page/stats")["stats"] == []

    # What can go on the board comes with it.
    choices = call(api, "GET", "company-page/stats")["available"]
    assert {"source_kind": "kpi", "ref_id": kpi["id"], "label": "Signups"} in choices
    assert {"source_kind": "finance_metric", "ref_id": "cash", "label": "Cash"} in choices

    # The next due day: only for an audience the schedule would write for, as the day it is in the company's zone.
    def dues():
        # Frozen on a Monday, 2 November 2026, so the business days below are fixed.
        with mock.patch.object(H, "now", return_value="2026-11-02T12:00:00.000000Z"):
            return {a["id"]: a["next_due"] for a in call(api, "GET", "audiences")["audiences"]}
    call(api, "PATCH", "audiences/board", {"enabled": True})
    assert dues()["board"] is None                                     # no sources
    board_source = call(api, "POST", "audiences/board/sources", {"author": "company"})
    assert dues()["board"] is None                                     # the page is Closed
    call(api, "PATCH", "company-page", {"transparency": "private"})
    assert dues()["board"] == "2026-11-06" and dues()["investors"] is None
    call(api, "PATCH", "audiences/public", {"enabled": True})
    call(api, "POST", "audiences/public/sources", {"author": "company"})
    assert dues()["public"] is None                                    # no readers until it is Open
    call(api, "PATCH", "company-page", {"transparency": "open"})
    assert dues()["public"] == "2026-11-30"  # October ends the 31st; 30 days after it
    call(api, "PATCH", "company-page", {"transparency": "closed"})
    call(api, "PATCH", "audiences/public", {"enabled": False})
    call(api, "DELETE", f"audiences/board/sources/{board_source['id']}")
    for leftover in call(api, "GET", "audiences/public/sources")["sources"]:
        call(api, "DELETE", f"audiences/public/sources/{leftover['id']}")

    # Built-ins stay; a source comes and goes.
    call(api, "DELETE", "audiences/public", expected=409)
    source = call(api, "POST", "audiences/public/sources", {"author": "company"})
    assert call(api, "GET", "audiences/public/sources")["sources"][0]["id"] == source["id"]
    call(api, "DELETE", f"audiences/public/sources/{source['id']}")
    assert call(api, "GET", "audiences/public/sources")["sources"] == []

    # The preview renders the audience's view, on an opaque origin.
    seen = {}
    real_page = render.page

    def page(view, **kw):
        seen.update(view)
        return real_page(view, **kw)
    monkeypatch.setattr(render, "page", page)
    r = api.get("/api/v2/company-page/preview?audience=investors", headers=headers("ben-test"))
    assert r.status_code == 200 and r.text.startswith("<!doctype html>")
    assert "sandbox" in r.headers["content-security-policy"] and "no-store" in r.headers["cache-control"]
    assert "font-src data:" in r.headers["content-security-policy"]     # the fonts ride in the page, as its logo does
    assert seen["audience"] == {"kind": "investors", "name": "Investors"}
    assert api.get("/api/v2/company-page/preview?audience=nobody", headers=headers()).status_code == 404
