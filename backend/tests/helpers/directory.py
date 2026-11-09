"""Helpers that other tests share, moved out of test_directory.py so they import no test file."""


# ------------------------------------------------------------------------------------- the routes
def events(api, action):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events WHERE action=? ORDER BY created_at, id", (action,))]


def roster_of(api):
    with api.app.state.store.read() as c:
        from backend.views import roster

        return {p["email"]: p for p in roster(c)["people"]}
