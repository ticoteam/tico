"""Tico Finance: books, budgets and holds, rights and view access, in Tico's one database.

Three layers (reports/2026-10-30-finance-spec.md §3.1):

  core/      no I/O: money, validation, posting invariants, budget math, rights, view resolution, approvers
  store/     every statement that touches a `fin_*` table, and the few reads of core tables Finance needs
  actions    the only way to write (`act`, `setup`, holds, system postings)
  reads      the only way to read business rows, always cut to the viewer's view

Every function takes the caller's open connection `c` and never opens a transaction of its own: the writer gate is
not reentrant. Money is integer minor units; FX rates are Decimal.
"""
