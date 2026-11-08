"""Shared set-up for the Finance tests: one initialized hub database built once per worker and copied per test, the
fictional Acme org chart, and helpers to post and act. No tests here."""

from backend.tests.helpers.finance_fixtures import act, books, finance_template, owner_only_books, store
