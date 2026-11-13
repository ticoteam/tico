"""Growth's first run on the web (backend/growth_setup.py, ui/app/growth-start.ts): `growth_setup.done_shown_at`, when
the Get started panel last said "you're set up". It says so once per team, then never comes back."""


def up(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(growth_setup)")}
    if cols and "done_shown_at" not in cols:
        conn.execute("ALTER TABLE growth_setup ADD COLUMN done_shown_at TEXT")
