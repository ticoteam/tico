"""GET /api/v2/boot: the six reads a page opens with (ui/app/boot.js), in one answer, each exactly as it answers alone."""
from backend.tests.test_api import api  # noqa: F401

READS = ("/api/employees", "/api/me", "/api/humans", "/api/status", "/api/v2/status", "/api/v2/needs-you")
# Health carries ages counted at the moment of the read: the same fields, not the same values.
LIVE = {"/api/status", "/api/v2/status"}


def test_each_read_answers_as_it_does_alone_for_whoever_asks(api):
    for token in ("ana-test", "ben-test", "cara-test"):
        auth = {"Authorization": "Bearer " + token}
        r = api.get("/api/v2/boot", params={"limit": "50"}, headers=auth)
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        reads = r.json()["reads"]
        assert tuple(reads) == READS
        for path in READS:
            alone = api.get(path, params={"limit": "50"} if path == "/api/v2/needs-you" else None, headers=auth)
            part = reads[path]
            assert part["status"] == alone.status_code, (token, path)
            if path in LIVE:
                assert set(part["body"]) == set(alone.json()), (token, path)
            else:
                assert part["body"] == alone.json(), (token, path)
                assert part["etag"] == alone.headers.get("etag", ""), (token, path)
        # Each is its own answer: the roster's tag still answers a later read of it alone with a 304.
        tag = reads["/api/employees"]["etag"]
        assert tag and api.get("/api/employees", headers={**auth, "If-None-Match": tag}).status_code == 304


def test_the_parts_ignore_the_callers_caching_and_compression(api):
    auth = {"Authorization": "Bearer ana-test"}
    tag = api.get("/api/employees", headers=auth).headers["etag"]
    r = api.get("/api/v2/boot", headers={**auth, "If-None-Match": tag, "Accept-Encoding": "gzip"})
    part = r.json()["reads"]["/api/employees"]
    assert part["status"] == 200 and isinstance(part["body"], list) and part["etag"] == tag
    # Needs you without a limit is the whole list, as alone.
    assert r.json()["reads"]["/api/v2/needs-you"]["body"] == api.get("/api/v2/needs-you", headers=auth).json()


def test_signed_out_is_signed_out(api):
    alone = api.get("/api/employees")
    assert api.get("/api/v2/boot").status_code == alone.status_code != 200


def test_a_part_past_its_budget_is_left_out_for_the_page_to_ask_alone(api):
    auth = {"Authorization": "Bearer ana-test"}
    state = api.app.state
    kept, state.boot_part_budget = state.boot_part_budget, 0
    try:
        r = api.get("/api/v2/boot", headers=auth)
    finally:
        state.boot_part_budget = kept
    # Nothing is done in no time: every part is left out, and the answer still comes, at once.
    assert r.status_code == 200 and r.json() == {"reads": {}}
    assert tuple(api.get("/api/v2/boot", headers=auth).json()["reads"]) == READS


def test_a_part_whose_thread_outlives_the_wait_is_left_out(api):
    """A sync read in a worker thread cannot be stopped: still running after the wait, it is left out, not a 500."""
    import time
    route = next(r for r in api.app.routes if getattr(r, "path", "") == "/api/status")
    real = route.dependant.call

    def slow(**values):
        time.sleep(2)
        return real(**values)
    route.dependant.call = slow
    try:
        r = api.get("/api/v2/boot", headers={"Authorization": "Bearer ana-test"})
    finally:
        route.dependant.call = real
    assert r.status_code == 200
    reads = r.json()["reads"]
    assert "/api/status" not in reads and reads["/api/employees"]["status"] == 200
