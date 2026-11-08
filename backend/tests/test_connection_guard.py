"""The per-test pooled connection check (backend/tests/connection_guard.py) fails the test that leaves a connection open."""

from pathlib import Path

pytest_plugins = ["pytester"]

REPO = Path(__file__).resolve().parents[2]


def test_a_test_that_leaves_a_pooled_connection_open_fails_at_its_teardown_and_names_itself(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    pytester.makepyfile(
        test_leaks="""
        from backend.config import Settings
        from backend.store import Store

        HELD = []


        def test_the_one_that_leaks(tmp_path):
            store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
            # The context manager is kept: were it freed, its generator would give the connection back to the pool.
            checkout = store.read()
            HELD.append((checkout, checkout.__enter__()))  # checked out and never returned


        def test_a_clean_one_after_it(tmp_path):
            store = Store(Settings(db_path=tmp_path / "hub.sqlite"))
            with store.read():
                pass
        """
    )
    result = pytester.runpytest_subprocess("-p", "backend.tests.connection_guard")
    # The leaking test passes its call and errors at its teardown; the clean one passes.
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(
        [
            "*ERROR at teardown of test_the_one_that_leaks*",
            "*left 1 pooled database connection*",
            "*test_the_one_that_leaks*",
        ]
    )
