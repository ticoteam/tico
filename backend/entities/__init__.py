"""Shared entity packs (the public places pack) and the Directory (track 3): the store that writes the entity tables, and
the one read rule every read goes through.

store.py is the only module that writes or reads the Directory tables for callers; visible.py decides what a reader sees.
The migration is backend/migrations/20261108020258_directory_tables.py."""
# pyright: strict
