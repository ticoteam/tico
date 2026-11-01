"""Shared records: the locations, items and projects that more than one system works from. Finance's lines tag and
name them now; Operations will use the same tables later. Writes go through Finance's action pipeline
(backend/finance/catalog.py), so gates, idempotency and the audit log apply to every change."""
