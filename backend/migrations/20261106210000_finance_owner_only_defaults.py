"""Finance is the owner's alone by default (backend/finance/core/view.py Defaults): managers and everyone else see
nothing until the owner grants it or opens the defaults up again. Existing books move to that default now, and the view
version moves so cached views and briefings follow. Grants stay; grants a manager made stop counting, since a manager
grants only from a team default (view.standing)."""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fin_view_defaults'").fetchone():
        return
    at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now')").fetchone()[0]
    moved = conn.execute(
        "UPDATE fin_view_defaults SET policy='none', updated_at=? WHERE role IN ('manager','member') AND policy<>'none'",
        (at,),
    ).rowcount
    if moved and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fin_view_versions'").fetchone():
        conn.execute(
            "INSERT INTO fin_view_versions(id, version, reason, updated_at) VALUES(1, 1, 'defaults', ?) "
            "ON CONFLICT(id) DO UPDATE SET version=fin_view_versions.version+1, reason='defaults', "
            "updated_at=excluded.updated_at",
            (at,),
        )
