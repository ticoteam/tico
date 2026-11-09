"""An app built and served again and again in one process (a demo build, every test) gives back what it took: sign-in's
threads end with each run, and nothing module-level keeps the app once it is let go."""

import gc
import threading
import time
import typing
import weakref

from fastapi.dependencies import models as fastapi_models
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_api import api_settings

BUILDS = 3
SIGNED_IN = {"Authorization": "Bearer ana-test"}  # whatever the answer, sign-in ran


def auth_threads():
    return sum(t.name.startswith("tico-auth") for t in threading.enumerate())


def settle(count, at_most=5.0):
    """Sign-in's pool is shut down without waiting; its idle threads end a moment later."""
    deadline = time.monotonic() + at_most
    while auth_threads() != count and time.monotonic() < deadline:
        time.sleep(0.02)
    return auth_threads()


def test_each_run_ends_its_sign_in_threads_and_lets_the_app_go(tmp_path):
    before = auth_threads()  # an app another test still serves keeps its own
    apps = []
    for n in range(BUILDS):
        (tmp_path / str(n)).mkdir()
        app = create_app(api_settings(tmp_path / str(n)))
        with TestClient(app) as client:
            client.get("/api/v2/me", headers=SIGNED_IN)
            assert auth_threads() == before + 1, "sign-in ran on the app's own pool"
        assert settle(before) == before, f"build {n + 1} left sign-in threads behind"
        assert app.state.auth_pool is None
        apps.append(weakref.ref(app))
        del app, client

    # Library caches keep a few of the latest apps' routes and models, up to their own bound; clear them so only an
    # unbounded holder (a module table keyed by store, say) could still keep an earlier one. The newest app may stay:
    # module registries each app's build overwrites (org_rule_check.PREPARE, support_access) hold the latest only.
    for cached in vars(fastapi_models).values():
        getattr(cached, "cache_clear", lambda: None)()
    for clear in getattr(typing, "_cleanups", []):
        clear()
    gc.collect()
    assert [ref() for ref in apps[:-1]] == [None] * (BUILDS - 1), "a built app outlived its run"


def test_an_app_served_twice_gets_a_fresh_pool_each_time(tmp_path):
    app = create_app(api_settings(tmp_path))
    before = auth_threads()
    for _ in range(2):
        with TestClient(app) as client:
            client.get("/api/v2/me", headers=SIGNED_IN)
            assert auth_threads() == before + 1
        assert settle(before) == before


def test_outside_a_run_sign_in_uses_the_default_pool(tmp_path):
    app = create_app(api_settings(tmp_path))
    before = auth_threads()
    app.state.store.initialize()
    client = TestClient(app)  # no lifespan: nothing to shut down, so nothing of its own to leave behind
    try:
        client.get("/api/v2/me", headers=SIGNED_IN)
    finally:
        client.close()
        app.state.store.close()
    assert app.state.auth_pool is None and auth_threads() == before


def test_a_run_inside_another_run_of_the_same_app_ends_cleanly(tmp_path):
    """Two clients on one app (the contract test's fixtures): the inner run ends first and the outer one still ends
    without error, each shutting down only its own pool."""
    app = create_app(api_settings(tmp_path))
    before = auth_threads()
    with TestClient(app) as outer:
        with TestClient(app) as inner:
            inner.get("/api/v2/me", headers=SIGNED_IN)
        outer.get("/api/v2/me", headers=SIGNED_IN)  # the inner run took its pool; the outer's requests still sign in
    assert app.state.auth_pool is None
    assert settle(before) == before
