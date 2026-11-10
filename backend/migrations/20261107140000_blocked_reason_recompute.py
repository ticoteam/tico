"""Retired (migrations.SUPERSEDED): it remade the old engine's Blocked reasons from task_holds, which the Pipelines v3
cut-over deleted with the tables it read. Its version stays recorded, so a database that ran it does not run it again,
and a new database records it without running anything."""


def up(conn):
    pass
