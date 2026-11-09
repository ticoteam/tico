"""Helpers that other tests share, moved out of test_model_login.py so they import no test file."""


def stored(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM model_logins")]
