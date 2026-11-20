"""Stored ids are made by backend/slugs.py, each caller in its own style (limit, fallback, what it does first). These
outputs are what the callers stored before they shared the rule; a change here moves ids already in databases."""

import pytest

from backend import groups, market, observability, risk, shared_bots, task_documents, tool_services
from backend.company_file import draft
from backend.slugs import slugify

A39 = "a" * 39 + " b"

CASES = [
    # docs.py, a doc's path from its title: casefold first, so ß is ss
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "Straße / ﬁle", "strasse-file"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "AT&T's 2024 Plan!", "at-t-s-2024-plan"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "🙂", "untitled"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), None, "none"),
    (lambda x: slugify(str(x).casefold(), limit=80, fallback="untitled"), "x" * 79 + " y", "x" * 79),
    # kpis.py, a KPI's slug
    (lambda x: slugify(x, limit=60, fallback="kpi"), "Weekly Active Users (WAU)", "weekly-active-users-wau"),
    (lambda x: slugify(x, limit=60, fallback="kpi"), "", "kpi"),
    (lambda x: slugify(x, limit=60, fallback="kpi"), "a" * 59 + " b", "a" * 59),
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
    # pipeline_settings.py, a new stage's id
    (lambda x: slugify(x, fallback="stage"), "QA / Fixes 2", "qa-fixes-2"),
    (lambda x: slugify(x, fallback="stage"), "—", "stage"),
    # entities/store.py, a new entity's id
    (lambda x: slugify(str(x), fallback="entity"), "Acme, Inc.", "acme-inc"),
    (lambda x: slugify(str(x), fallback="entity"), None, "none"),
    (lambda x: slugify(str(x), fallback="entity"), "日本", "entity"),
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
