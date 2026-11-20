"""Stored ids are made by backend/slugs.py, each caller in its own style (limit, fallback, what it does first). These
outputs are what the callers stored before they shared the rule; a change here moves ids already in databases. The
lambdas repeat a call made inside a larger function; test_docs, test_triggers, test_pipeline_settings and
test_worktrees cover those paths end to end."""

import sqlite3

import pytest

from backend import groups, kpis, market, observability, risk, shared_bots, task_documents, tool_services
from backend.company_file import draft
from backend.entities import store as entities
from backend.slugs import slugify

A39 = "a" * 39 + " b"


def _kpi_slug(name):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE kpis (slug TEXT)")
    return kpis._unique_slug(c, name)


def _entity_id(name):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE entities (id TEXT)")
    return entities._new_entity_id(c, "company", name)


CASES = [
    # docs.py, a doc's path from its title: casefold first, so ß is ss
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "Straße / ﬁle", "strasse-file"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "AT&T's 2024 Plan!", "at-t-s-2024-plan"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "🙂", "untitled"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), None, "none"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "x" * 79 + " y", "x" * 79),
    (_kpi_slug, "Weekly Active Users (WAU)", "weekly-active-users-wau"),
    (_kpi_slug, "", "kpi"),
    (_kpi_slug, None, "kpi"),
    (_kpi_slug, "a" * 59 + " b", "a" * 59),
    (market.slug, "AT&T Wireless", "at-and-t-wireless"),
    (market.slug, "  Café & Co.  ", "caf-and-co"),
    (market.slug, None, "untitled"),
    (risk.slug, "risk:Customer Theft Claims", "customer-theft-claims"),
    (risk.slug, "RISK: data loss", "data-loss"),
    (risk.slug, "", ""),
    (risk.slug, "a" * 34 + " b", "a" * 34),
    (tool_services.slug, "Google Workspace", "google-workspace"),
    (tool_services.slug, "!!!", "tool"),
    (tool_services.slug, "a" * 47 + " b", "a" * 47),
    (tool_services.slug, None, "tool"),
    # pipeline_settings.py, a new stage's id
    (lambda x: slugify(x, fallback="stage"), "QA / Fixes 2", "qa-fixes-2"),
    (lambda x: slugify(x, fallback="stage"), "—", "stage"),
    (_entity_id, "Acme, Inc.", "company/acme-inc"),
    (_entity_id, None, "company/none"),
    (_entity_id, "日本", "company/entity"),
    # company_file/draft.py keeps its own copy (the package is copied as is); it must agree
    (draft.slugify, "-Head of Sales-", "head-of-sales"),
    (draft.slugify, "", "x"),
    (draft.slugify, None, "none"),
    # groups.py cuts after the rule, so a cut can end on a hyphen
    (groups.slug_of, "Sales & Marketing", "sales-marketing"),
    (groups.slug_of, None, ""),
    (groups.slug_of, A39, "a" * 39 + "-"),
    # triggers.py, a trigger's id from its name
    (lambda x: slugify(x, limit=40, fallback="hook"), "Stripe: payment failed", "stripe-payment-failed"),
    (lambda x: slugify(x, limit=40, fallback="hook"), "???", "hook"),
    (lambda x: slugify(x, limit=40, fallback="hook"), A39, "a" * 39),
    (observability.instance, "Acme Co. (staging)", "acme-co-staging"),
    (observability.instance, None, ""),
    (observability.instance, "a" * 63 + " b", "a" * 63 + "-"),
    (task_documents.file_name, "QA Report", "qa-report.html"),
    (task_documents.file_name, "--", "document.html"),
    # worktrees.py, a task branch's slug
    (lambda x: slugify(x)[:50] or "task", "Fix: login fails on Safari", "fix-login-fails-on-safari"),
    (lambda x: slugify(x)[:50] or "task", "", "task"),
    (lambda x: slugify(x)[:50] or "task", "a" * 49 + " b", "a" * 49 + "-"),
]


@pytest.mark.parametrize(("make", "text", "slug"), CASES)
def test_each_caller_keeps_its_stored_slugs(make, text, slug):
    assert make(text) == slug


def test_branch_copy_slug():
    assert shared_bots.copy_slug("Sales Bot", "ana@acme.example") == "sales-bot-ana-acme-example"
