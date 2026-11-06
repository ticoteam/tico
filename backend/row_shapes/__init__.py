"""Row shapes by area: each module holds the TypedDicts of its tables and a TABLES map from each one to its table.

`backend/rows.py` re-exports every name, so `from backend.rows import TaskRow` keeps working. Adding a table's shape
means adding it to its area module; a name that code imports from `backend.rows` also needs an import and an `__all__`
entry there."""
