"""Tests read the registry from a fictional company, never from a real environment's."""
import os
import sys
from pathlib import Path

os.environ["TICO_REGISTRY_DIR"] = str(Path(__file__).parent / "backend/tests/fixtures/registry")
# The update check would otherwise call GitHub from every test that reads /api/v2/config.
os.environ["TICO_UPDATE_CHECK"] = "off"
# A runner readiness call would otherwise start real containers on whoever's computer runs the suite.
os.environ["TICO_RUNNER_CONTAINER_PROBE"] = "off"
# Every test's database is thrown away, so no commit waits for the disk (hubdb.synchronous).
os.environ["TICO_SQLITE_SYNC"] = "off"
# A test that misses a stub must fail, not act in whoever's AWS account runs the suite: one already
# created a real bucket. Fake keys and no profile or config files make any real AWS call unauthorized.
for _name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
    os.environ.pop(_name, None)
os.environ.update({"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
                   "AWS_EC2_METADATA_DISABLED": "true"})


import pytest  # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--api-database-snapshots", action="store_true",
                     help="Reuse a worker-local schema snapshot for the shared API fixture")
    parser.addoption("--fresh-api-databases", action="store_true",
                     help="Build API fixture databases from empty paths (migration checks and benchmarks)")


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
                raise OSError(f"Unix socket under pytest's tmp_path: {path}. Its length depends on TMPDIR and breaks "
                              "the hosted release gate; bind it in tempfile.mkdtemp(dir='/tmp') instead.")
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
    if hasattr(config, "workerinput") or config.option.pyargs:
        return          # --pyargs names modules, not paths
    base = Path(config.invocation_params.dir)
    missing = [str(arg) for arg in config.args if not (base / str(arg).split("::")[0]).exists()]
    if missing:
        raise pytest.UsageError("No such test path: " + ", ".join(missing))


PYTEST_WORKERS_MAX = 8


def pytest_xdist_auto_num_workers(config):
    """`-n auto` (pytest.ini): the cores left over, 1 to 8. Several bots run the suite at once on one team box, and
    eight workers each had five suites at a load of 54 on eight cores, next to the hub. Linux counts the load average
    against the cores this process may use; macOS's load average stays high when idle, so a Mac takes its cores.
    PYTEST_XDIST_AUTO_NUM_WORKERS still sets the number, and an explicit -n N still wins."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return int(os.environ["PYTEST_XDIST_AUTO_NUM_WORKERS"])
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    free = cores - os.getloadavg()[0] if sys.platform.startswith("linux") else cores
    return max(1, min(PYTEST_WORKERS_MAX, int(free)))


def pytest_sessionfinish(session, exitstatus):
    """Zero collected tests is a failure (exit 5) and says so in an error line, not only in the exit code."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and not hasattr(session.config, "workerinput"):
        session.config.get_terminal_writer().line(
            "ERROR: no tests were collected; check the test paths and the -m / -k filters", red=True, bold=True)


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
    from backend.tests.test_api import api_settings

    if request.config.getoption("--fresh-api-databases") or not request.config.getoption("--api-database-snapshots"):
        return None
    settings = api_settings(tmp_path_factory.mktemp("api-schema"))
    store = Store(settings)
    store.initialize()
    snapshot = settings.db_path.with_name("snapshot.db")
    with closing(sqlite3.connect(settings.db_path)) as source:
        tables = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        allowed = {"cloud_migrations", "registry_metadata", "sqlite_sequence", "task_types", "task_steps", "task_rules", "provenance_clock"}
        for table in tables:
            # FTS5 has internal configuration rows but no user document before fixture seeding.
            if table in allowed or "_fts_" in table:
                continue
            assert not source.execute('SELECT 1 FROM "' + table.replace('"', '""') + '" LIMIT 1').fetchone(), table
        assert source.execute("SELECT id FROM task_rules").fetchall() == [("task-rule-close-after-done",)]
        keys = {row[0] for row in source.execute("SELECT key FROM registry_metadata")}
        assert keys <= {"access", "owner", "access_bot_limit", "bot_access", "docs_migrated",
                        "credential-file-migration-v1", "credential-file-migration-v2-hub", "message_links.ready",
                        "librarian_wording35", "librarian_wording36", "release-history",
                        "repositories-access-migrated", "task_session_default"}
        assert source.execute("SELECT id FROM task_types").fetchall() == [("general",)]
        assert all(row[0].startswith("general-") for row in source.execute("SELECT id FROM task_steps"))
        # Inspect the stored values directly; dumping every empty table and trigger dominates startup
        # when many workers build their templates at once.
        for table in allowed:
            assert str(settings.db_path.parent) not in repr(source.execute(f'SELECT * FROM "{table}"').fetchall())
        with closing(sqlite3.connect(snapshot.with_suffix(".building"))) as target:
            source.backup(target)
    snapshot.with_suffix(".building").replace(snapshot)
    return snapshot
import _scan_rows  # noqa
_scan_rows.patch()
