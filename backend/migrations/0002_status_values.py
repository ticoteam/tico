"""The status and state lists (backend/status_values.py): refuse stored values outside them, naming each, before anything
changes; rewrite the retired spellings (one spelling per meaning); install the triggers and CHECKs on the tables this file
has. A table a module creates later gets its constraints from status_values.enforce at the end of Store.initialize.

A CHECK goes in by rebuilding the table, which must not cascade into child rows, so foreign keys are off for this file.
"""
from backend import status_values

FOREIGN_KEYS_OFF = True


def up(conn):
    status_values.upgrade(conn)
