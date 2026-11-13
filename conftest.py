"""Tests read the registry from a fictional company, never from a real environment's."""

import os
import sys
import threading
from pathlib import Path

os.environ["TICO_REGISTRY_DIR"] = str(Path(__file__).parent / "backend/tests/fixtures/registry")
# The update check would otherwise call GitHub from every test that reads /api/v2/config.
os.environ["TICO_UPDATE_CHECK"] = "off"
# A runner readiness call would otherwise start real containers on whoever's computer runs the suite.
os.environ["TICO_RUNNER_CONTAINER_PROBE"] = "off"
# The start jobs a server runs beside itself once it serves (backend/app.py start_jobs) run before it serves instead, so
# a test that starts an app sees their result at once. backend/tests/test_start_jobs.py checks the served way.
os.environ["TICO_START_JOBS"] = "inline"
# Every test's database is thrown away, so no commit waits for the disk (hubdb.synchronous).
os.environ["TICO_SQLITE_SYNC"] = "off"
# Bots start `nearby` (backend/reach.py), and most test companies put their bots on no chart, so tests about
# something else start them open. The reach tests (backend/tests/test_reach*.py) clear this and test the default.
os.environ["TICO_REACH_DEFAULT"] = "open"
# A test that misses a stub must fail, not act in whoever's AWS account runs the suite: one already
# created a real bucket. Fake keys and no profile or config files make any real AWS call unauthorized.
for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
    os.environ.pop(_name, None)
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_CONFIG_FILE": os.devnull,
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_EC2_METADATA_DISABLED": "true",
    }
)

# A test process builds an app per test: every one after the first reuses the route analysis and field validators the
# first built (backend/route_memo.py). Tests only: a server builds one app, and production never patches FastAPI.
from backend import route_memo  # noqa: E402

route_memo.install()

import re  # noqa: E402
import pytest  # noqa: E402

from backend.tests import connection_guard  # noqa: E402

# The per-test check of pooled connections: a test that leaves one open fails at its own teardown.
from backend.tests.connection_guard import pooled_connections_closed  # noqa: E402, F401


def pytest_addoption(parser):
    parser.addoption(
        "--api-database-snapshots",
        action="store_true",
        help="Reuse a worker-local schema snapshot for the shared API fixture (on in pytest.ini)",
    )
    parser.addoption(
        "--fresh-api-databases",
        action="store_true",
        help="Build API fixture databases from empty paths (migration checks and benchmarks)",
    )
    _flake_quarantine().addoption(parser)


def _flake_quarantine():
    """scripts/flake_quarantine.py, loaded by path like scripts/heavy_slot.py (scripts/ is not on every sys.path)."""
    import importlib.util

    name = "tico_flake_quarantine"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "scripts/flake_quarantine.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def pytest_collection_modifyitems(config, items):
    """PR checks and the merge queue pass --quarantine (scripts/pr_checks.py run --quarantine): each test listed in
    ci/quarantine/ runs as a non-strict xfail. Release validation never passes it."""
    _flake_quarantine().modify(config, items)


def unix_socket_path_under_tmp_path(address, basetemp=None):
    """The filesystem path a Unix socket address names, if it lies under pytest's tmp_path; otherwise None.

    A str, bytes or path-like address names a file, relative to the working directory as bind resolves it. Under the
    default basetemp every tmp_path has a pytest-of-<user> directory in it; an explicit --basetemp (xdist gives each
    worker one) is compared by whole directory components. An abstract name (leading NUL, Linux) has no path."""
    if not isinstance(address, (str, bytes, os.PathLike)):
        return None
    path = os.fsdecode(address)
    if not path or path.startswith("\0"):
        return None
    full = Path(os.path.realpath(path))
    root = basetemp and Path(os.path.realpath(basetemp))
    if any(part.startswith("pytest-of-") for part in full.parts) or (root and (full == root or root in full.parents)):
        return str(full)
    return None


def _unix_socket_bind_outside_tmp_path(config):
    """A Unix socket path has a length limit (108 bytes on Linux, 104 on macOS). pytest's tmp_path is short on a laptop
    but long under the hosted release gate's TMPDIR, so a socket under tmp_path passed every local run and failed only
    at release validation (v0.3.50's first candidate). Refuse it on every computer, so it fails at the first run.
    The patch covers every thread of this process and any child it forks; a subprocess that execs a new program (or
    Python) does not inherit it. It is removed when pytest unconfigures."""
    import socket

    bind = socket.socket.bind

    def checked_bind(self, address):
        if self.family == getattr(socket, "AF_UNIX", None):
            path = unix_socket_path_under_tmp_path(address, config.option.basetemp)
            if path:
                raise OSError(
                    f"Unix socket under pytest's tmp_path: {path}. Its length depends on TMPDIR and breaks "
                    "the hosted release gate; bind it in tempfile.mkdtemp(dir='/tmp') instead."
                )
        return bind(self, address)

    socket.socket.bind = checked_bind
    config.add_cleanup(lambda: setattr(socket.socket, "bind", bind))


def pytest_configure(config):
    """A bot's own run first takes one of the box's heavy-job slots (scripts/heavy_slot.py), before collection.
    A test path that does not exist is an error. Under -n (on by default here) xdist drops it without a word,
    and the run ends as "no tests ran", which a reader piping the output can take for a pass."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("tico_heavy_slot", Path(__file__).parent / "scripts/heavy_slot.py")
    heavy_slot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(heavy_slot)
    heavy_slot.reexec()
    _unix_socket_bind_outside_tmp_path(config)
    from backend.tests import app_build

    app_build.install()  # every app after a worker's first builds in half the time
    if hasattr(config, "workerinput") or config.option.pyargs:
        return  # --pyargs names modules, not paths
    # The page serves the UI's TypeScript as its compiled JavaScript: compiled here once when this checkout can
    # (Node and `npm ci`); otherwise the tests that load the page fail and say to run `npm run build:ui`.
    from backend import ui_build

    try:
        ui_build.prepare()
    except ui_build.StaleBuild:
        pass
    base = Path(config.invocation_params.dir)
    missing = [str(arg) for arg in config.args if not (base / str(arg).split("::")[0]).exists()]
    if missing:
        raise pytest.UsageError("No such test path: " + ", ".join(missing))


PYTEST_WORKERS_MAX = 8


def pytest_xdist_auto_num_workers(config):
    """`-n auto` (pytest.ini): the cores left over, 1 to 8. Several bots run the suite at once on one team box, and
    eight workers each had five suites at a load of 54 on eight cores, next to the hub. Linux counts the load average
    against the cores this process may use; macOS's load average stays high when idle, so a Mac takes its cores.
    A CI VM (CI is set: a remote run, a PR or release job) runs this suite alone: one worker per vCPU, uncapped. The
    cap and the load left a 16-vCPU remote run 8 workers and half its CPU idle (49 minutes for the suite); more workers
    than vCPUs measured slower. PYTEST_XDIST_AUTO_NUM_WORKERS still sets the number, and an explicit -n N still wins."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return int(os.environ["PYTEST_XDIST_AUTO_NUM_WORKERS"])
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    if os.environ.get("CI"):
        return cores
    free = cores - os.getloadavg()[0] if sys.platform.startswith("linux") else cores
    return max(1, min(PYTEST_WORKERS_MAX, int(free)))


def pytest_xdist_make_scheduler(config, log):
    """`--dist load` (xdist's default) with a module's tests kept on one worker when they share a module fixture, and
    the slowest work handed out first (backend/tests/worker_schedule.py). Any other --dist keeps xdist's own."""
    if config.getoption("dist") != "load":
        return None
    from backend.tests.worker_schedule import SharedFixtureScheduling

    return SharedFixtureScheduling(config, log)


def pytest_sessionfinish(session, exitstatus):
    """Zero collected tests is a failure (exit 5) and says so in an error line, not only in the exit code."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and not hasattr(session.config, "workerinput"):
        session.config.get_terminal_writer().line(
            "ERROR: no tests were collected; check the test paths and the -m / -k filters", red=True, bold=True
        )


# The threads a scheduler tick starts for its passes (backend/scheduler.py): maintenance, thread ranking, stale review.
TICK_PASS_THREADS = ("maintenance-run", "thread-rank", "stale-review")


@pytest.fixture(autouse=True)
def maintenance_runs_end(pooled_connections_closed, tmp_path):
    """A scheduler tick starts its maintenance run, thread ranking and stale review passes on threads that outlive the
    test. Join them here, before the test's tmp_path is removed at teardown: a pass still going would find its database
    gone (tmp_path is the dependency that makes this teardown run first), or open a connection during a later test (a
    thread-rank pass did, and that test's teardown closed it under the pass's statement: a segfaulted xdist worker). It
    takes the pooled-connection guard as a parameter so the guard is set up first and tears down last: a pass this join
    waits for can still hold a connection the guard checks."""
    yield
    for thread in threading.enumerate():
        if thread.name in TICK_PASS_THREADS:
            thread.join(timeout=60)
            if thread.is_alive():
                pytest.fail(f"{thread.name} still going 60 s after the test")


@pytest.fixture(autouse=True)
def store_pools_close(pooled_connections_closed):
    """A store's idle connections stay open until the store is freed, and a store in a reference cycle is freed only by
    the garbage collector, possibly during a later test, which then warns about the connections. Closing every store
    the test made, when the test ends, closes those connections on the spot. A closed store still works: its next read
    opens a new connection. Takes the guard as a parameter, so it tears down before the guard (see maintenance_runs_end)."""
    yield
    connection_guard.close_stores()


def pytest_runtest_logreport(report):
    """Under xdist the workers' teardown reports reach the controller here, so the check's cost is summed over them."""
    if report.when == "teardown":
        connection_guard.add_report(report)


def pytest_terminal_summary(terminalreporter):
    cost = connection_guard.SUMMED
    terminalreporter.write_line(
        f"pooled connection check: {cost['tests']} tests, {cost['seconds']:.1f} s of its own work "
        f"({cost['joined']:.1f} s joining threads); the store's own connects took {cost['connect']:.1f} s, not counted"
    )


@pytest.fixture
def api(tmp_path, api_database):
    """A test client on a new hub database with the fictional company (backend/tests/helpers/api_app.py). A test takes
    it by name and imports nothing for it: only this body imports the app, so the PR test selector does not count every
    change the app can reach as a change to each test that uses the fixture (scripts/pr_checks.py, rule 2)."""
    from backend.tests.helpers.api_app import api_client

    yield from api_client(tmp_path, api_database)


@pytest.fixture
def company(tmp_path, api_database):
    """A store on a new hub database with the fictional company and no app (backend/tests/helpers/company.py): for
    tests of code below the routes, which need a transaction and not a client."""
    from backend.tests.helpers.company import company_store

    store = company_store(tmp_path, api_database)
    yield store
    store.close()


@pytest.fixture
def inside_one_half_hour():
    """Spend windows and daily caps are real half hours, days, weeks and months, each read from the real clock where
    it is used. A test that spends and then reads its window must do both inside one: this starts it clear of the
    next edge (every longer window ends on a half hour), waiting out the few seconds before one."""
    import time
    from datetime import datetime, timezone

    left = 1800 - datetime.now(timezone.utc).timestamp() % 1800
    if left < 20:
        time.sleep(left + 0.05)


@pytest.fixture(scope="session")
def api_database(tmp_path_factory, request):
    """One initialized, fictional database per worker and invocation; never a cache across code changes.

    SQLite's backup API includes committed WAL pages. Only schema, migration markers and the static
    General task type exist here: no bot/person identity, credential, session or stored filesystem path.
    Apps, stores, auth, pools, registry files and all mutable rows remain per-test. Migration tests that
    construct a Store directly still start empty, and lifespan still runs initialize on each restored copy.
    """
    import sqlite3
    from contextlib import closing
    from backend.store import Store
    from backend.tests.helpers.api import api_settings

    if request.config.getoption("--fresh-api-databases") or not request.config.getoption("--api-database-snapshots"):
        return None
    settings = api_settings(tmp_path_factory.mktemp("api-schema"))
    store = Store(settings)
    store.initialize()
    snapshot = settings.db_path.with_name("snapshot.db")
    with closing(sqlite3.connect(settings.db_path)) as source:
        tables = [
            row[0]
            for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if "_fts" not in row[0]
        ]
        # initialize writes system rows (markers, clocks, built-in types and rules, keeper events) that every
        # fresh test database has too. What must never be shared between tests: a person, bot, credential,
        # session, key or computer, and this worker's temporary path.
        identity = re.compile(
            r"human|person|people|bot|cred|secret|session|token|key|auth|member|user|account|"
            r"identit|device|machine|computer|grant|oauth|oidc"
        )
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            if identity.search(table):
                assert not source.execute(f"SELECT 1 FROM {quoted} LIMIT 1").fetchone(), table
            else:
                assert str(settings.db_path.parent) not in repr(source.execute(f"SELECT * FROM {quoted}").fetchall()), (
                    table
                )
        assert {row[0] for row in source.execute("SELECT kind FROM principals")} <= {"system"}
        with closing(sqlite3.connect(snapshot.with_suffix(".building"))) as target:
            source.backup(target)
    snapshot.with_suffix(".building").replace(snapshot)
    return snapshot


@pytest.fixture(scope="session")
def app_snapshots(tmp_path_factory, request):
    """This worker's kept app databases (backend/tests/app_snapshots.py): ({fingerprint: path or None}, their folder),
    or None when --fresh-api-databases or no --api-database-snapshots."""
    if request.config.getoption("--fresh-api-databases") or not request.config.getoption("--api-database-snapshots"):
        return None
    return {}, tmp_path_factory.mktemp("app-snapshots")


@pytest.fixture(autouse=True)
def apps_start_from_snapshot(request, monkeypatch, app_snapshots):
    """An app a test builds on a new database file starts from a copy of the same app's database built earlier in this
    worker, as the `api` fixture's start from its schema snapshot (backend/tests/app_snapshots.py): the helper fixtures
    (tasks_board, onboarding, github_app and others) each migrated an empty file, most of a 3.4 s median test. A test
    that must migrate an empty file through create_app takes @pytest.mark.fresh_database."""
    if app_snapshots is None or request.node.get_closest_marker("fresh_database"):
        return
    import backend.app
    from backend.tests.app_snapshots import snapshot_store

    kept, folder = app_snapshots
    monkeypatch.setattr(
        backend.app, "Store", snapshot_store(backend.app.Store, kept, folder, monkeypatch, (backend.app, "Store"))
    )
