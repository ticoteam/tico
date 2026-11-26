"""scripts/landmines.py: each rule finds its bad case and not its good one, and the counts only go down."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import landmines  # noqa: E402

PY = "backend/tests/test_thing.py"
UI = "ui/tests/thing.cts"


def hits(path, text):
    return {rule: len(lines) for rule, lines in landmines.hits_in(path, text, landmines.RULES).items()}


@pytest.mark.parametrize(
    ("rule", "path", "bad", "good"),
    [
        ("no-retry", PY, "@pytest.mark.flaky(reruns=3)\ndef test_x(): pass\n", "def test_x(): pass\n"),
        ("no-retry", "pytest.ini", "[pytest]\naddopts = --reruns 2\n", "[pytest]\naddopts = -q\n"),
        (
            "no-retry",
            "ui/playwright.config.ts",
            "export default {\n  retries: 2,\n};\n",
            "export default {\n  workers: 2,\n};\n",
        ),
        ("no-fixed-sleep-py", PY, "import time\ntime.sleep(0.5)\n", "import asyncio\nawait asyncio.sleep(0)\n"),
        ("no-fixed-sleep-py", PY, "await asyncio.sleep(1)\n", "assert done.wait(10), 'the worker never finished'\n"),
        ("no-fixed-wait-ui", UI, "await page.waitForTimeout(200);\n", "await until(() => drawn(), 'the row');\n"),
        ("no-fixed-wait-ui", UI, "await new Promise(r => setTimeout(r, 50));\n", "await page.waitForResponse(url);\n"),
        ("no-raised-timeout", UI, "await x.waitFor({timeout: 10000});\n", "await x.waitFor({timeout: t(5000)});\n"),
        ("no-raised-timeout", UI, "await x.waitFor({timeout: t(60000)});\n", "await x.waitFor({timeout: 500});\n"),
        ("no-unittest-mock", PY, "from unittest import mock\n", "from backend.tests.helpers import fake_github\n"),
        (
            "no-unittest-mock",
            PY,
            "with mock.patch.object(H, 'which', which):\n    pass\n",
            "monkeypatch.setattr(H, 'now', at)\n",
        ),
        ("no-unittest-mock", PY, "m = MagicMock()\n", "m = FakeGitHub()\n"),
        (
            "no-unittest-mock",
            PY,
            "with mock.patch.dict(os.environ, {}), mock.patch.object(H, 'run'):\n    pass\n",
            "with mock.patch.dict(os.environ, {'PATH': ''}):\n    pass\n",
        ),
        (
            "no-global-time-patch",
            PY,
            "monkeypatch.setattr(apps.time, 'sleep', lambda s: None)\n",
            "monkeypatch.setattr(store, '_sleep', lambda s: None)\n",
        ),
        ("no-global-time-patch", PY, "monkeypatch.setattr('clients.tico.time.sleep', f)\n", "x = 1\n"),
        (
            "no-global-time-patch",
            PY,
            "with mock.patch.object(H.time, 'time', lambda: 1):\n    pass\n",
            "with mock.patch.object(H, '_now', lambda: 1):\n    pass\n",
        ),
        (
            "no-global-time-patch",
            PY,
            "with mock.patch('clients.tico.time.monotonic'):\n    pass\n",
            "with time_machine.travel(0, tick=False):\n    pass\n",
        ),
        (
            "timing-assert",
            PY,
            "took = time.monotonic() - began\nassert took < 2.0\n",
            "while not done():\n    assert time.monotonic() < deadline, 'never done'\n",
        ),
        (
            "timing-assert",
            UI,
            "assert.ok(Date.now() - started < 500);\n",
            "assert.equal(draws, 1);\n",
        ),
        (
            "no-assigned-clock-seam",
            "backend/thing.py",
            "import time\n_clock = time.monotonic  # tests' clock\n",
            "import time\n\n\ndef _clock():\n    return time.monotonic()\n",
        ),
    ],
)
def test_each_rule_finds_its_bad_case_and_not_its_good_one(rule, path, bad, good):
    assert hits(path, bad).get(rule) == 1
    assert rule not in hits(path, good)


CORE = "backend/tasks.py"


@pytest.mark.parametrize(
    ("rule", "path", "bad", "good"),
    [
        (
            "no-runner-support-import",
            "clients/thing.py",
            "from runner_support.op import resolve_op_refs\n",
            '"""Runners fetch it per turn (runner_support/git_credentials.py)."""\nfrom . import op\n',
        ),
        (
            "no-runner-support-import",
            "backend/thing.py",
            "def f():\n    import runner_support.engine as engine\n",
            "def f():\n    import runner_rs_bridge\n",
        ),
        ("core-imports-app", CORE, "from .mail import send\n", "from .people import roster\n"),
        ("core-imports-app", CORE, "from .finance.reads import bills_email\n", "from .finance import actions as FA\n"),
        ("core-imports-app", CORE, "from .finance import reads as FR\n", "from .finance.actions import run_hold\n"),
        ("core-imports-app", CORE, "from .mail_lib.parse import parts\n", "from .tasks_lib import parts\n"),
        ("core-imports-app", CORE, "from backend import calls\n", "from backend import cards\n"),
        ("core-imports-app", CORE, "import backend.slack_app\n", "import backend.slack\n"),
        ("core-imports-app", "backend/app.py", "from . import live_meetings\n", "from . import routing\n"),
        ("core-imports-app", "backend/entities/person.py", "from .. import bookkeeper\n", "from . import bookkeeper\n"),
    ],
)
def test_each_architecture_rule_finds_its_bad_case_and_not_its_good_one(rule, path, bad, good):
    assert hits(path, bad).get(rule) == 1
    assert rule not in hits(path, good)


HUB_SQL = "backend/migrations/20261201000000_thing.sql"
HUB_PY = "backend/migrations/20261201000000_thing.py"
APP = "backend/app.py"
ROUTES = "backend/thing_routes.py"


@pytest.mark.parametrize(
    ("path", "bad", "good"),
    [
        (HUB_SQL, "CREATE TABLE flight_events(id INTEGER PRIMARY KEY);\n", "DROP TABLE IF EXISTS flight_events;\n"),
        (HUB_SQL, 'create table if not exists "flight_sql"(n INTEGER);\n', "CREATE TABLE flights(id INTEGER);\n"),
        (HUB_SQL, "CREATE TEMP TABLE main.flight_db(bytes INTEGER);\n", "SELECT bytes FROM flight_db;\n"),
        (
            HUB_SQL,
            "ALTER TABLE flight_slow ADD COLUMN parts TEXT;\n",
            "ALTER TABLE tasks ADD COLUMN flight_note TEXT;\n",
        ),
        (HUB_SQL, "ALTER TABLE perf RENAME TO flight_requests;\n", "ALTER TABLE perf RENAME TO perf_old;\n"),
        (
            HUB_SQL,
            "CREATE INDEX flight_events_kind ON flight_events(kind);\n",
            "DROP INDEX IF EXISTS flight_events_kind;\n",
        ),
        (
            HUB_SQL,
            "CREATE TRIGGER t AFTER INSERT ON flight_web BEGIN SELECT 1; END;\n",
            "-- CREATE TABLE flight_web moved\n",
        ),
        (
            HUB_PY,
            'c.execute("CREATE TABLE flight_process(cpu REAL)")\n',
            'c.execute("DROP TABLE IF EXISTS flight_process")\n',
        ),
        (
            "backend/hubdb.py",
            'SQL = "ALTER TABLE flight_db ADD COLUMN x"\n',
            "# ALTER TABLE flight_db went to hub.flight.db\n",
        ),
        ("deploy/thing.sql", "CREATE TABLE flight_web(span INTEGER);\n", "CREATE TABLE web(span INTEGER);\n"),
        (HUB_SQL, 'CREATE TABLE "main"."flight_db"(x INTEGER);\n', 'CREATE TABLE "main"."db_flight"(x INTEGER);\n'),
        (
            HUB_SQL,
            "CREATE VIEW IF NOT EXISTS flight_recent AS SELECT 1;\n",
            "CREATE VIEW recent AS SELECT * FROM flight_db;\n",
        ),
        (
            HUB_SQL,
            "CREATE UNIQUE INDEX IF NOT EXISTS k ON `main`.`flight_sql`(n);\n",
            "CREATE INDEX k ON sql_log(n);\n",
        ),
        (
            HUB_SQL,
            "CREATE TABLE tasks2(id INTEGER); -- then\nALTER TABLE\n  flight_slow ADD COLUMN x;\n",
            "CREATE TABLE tasks2(id INTEGER); -- CREATE TABLE flight_slow(x) moved to hub.flight.db\n",
        ),
        (
            HUB_SQL,
            "CREATE INDEX k ON tasks(id);\nCREATE TRIGGER t AFTER UPDATE OF kind\n  ON flight_events BEGIN SELECT 1; END;\n",
            "CREATE INDEX k ON tasks(id);\nSELECT * FROM a JOIN b ON flight_events.id = b.id;\n",
        ),
        (
            HUB_PY,
            'SQL = """\nCREATE TABLE IF NOT EXISTS\n    flight_requests (span INTEGER)\n"""\n',
            'SQL = """\nDROP TABLE IF EXISTS\n    flight_requests\n"""  # CREATE TABLE flight_requests moved\n',
        ),
        (
            HUB_SQL,
            "CREATE /* legacy */ TABLE flight_events(id INTEGER);\n",
            "/* CREATE TABLE flight_events(id INTEGER);\n   moved to hub.flight.db */\nSELECT 1;\n",
        ),
        (HUB_SQL, "ALTER TABLE 'flight_events' ADD COLUMN x;\n", "ALTER TABLE 'events' ADD COLUMN flight_x;\n"),
        (HUB_SQL, 'CREATE INDEX "by kind-2" ON flight_events(kind);\n', 'CREATE INDEX "by kind-2" ON events(kind);\n'),
        (
            HUB_PY,
            'c.execute("-- setup\\nCREATE TABLE flight_events(id INTEGER)")\n',
            'c.execute("-- CREATE TABLE flight_events(id) moved")\n',
        ),
        (
            HUB_PY,
            'c.execute("SELECT 1 -- x", "CREATE TABLE flight_events(id INTEGER)")\n',
            'c.execute("SELECT 1 -- x", "CREATE TABLE events(id INTEGER)")\n',
        ),
        (
            HUB_PY,
            'SQL = "CREATE /* old */ TABLE \'main\'.\'flight_db\'(x)"\n',
            'SQL = "/* CREATE TABLE flight_db(x) */ SELECT 1"\n',
        ),
    ],
)
def test_no_flight_in_hub_bans_a_flight_table_made_or_changed_and_allows_its_drop(path, bad, good):
    assert hits(path, bad).get("no-flight-in-hub") == 1
    assert "no-flight-in-hub" not in hits(path, good)


def test_no_flight_in_hub_names_the_line_a_statement_starts_on():
    text = "-- one\nCREATE TABLE\n  flight_events(id INTEGER);\nCREATE INDEX k\n  ON flight_events(id);\n"
    found = landmines.hits_in(HUB_SQL, text, [landmines.BY_ID["no-flight-in-hub"]])["no-flight-in-hub"]
    assert found == [(2, "CREATE TABLE"), (4, "CREATE INDEX k")]


def test_no_flight_in_hub_keeps_line_numbers_past_a_block_comment_and_sees_past_globs():
    text = "/* one\n   two */\nCREATE TABLE flight_events(id INTEGER);\n"
    found = landmines.hits_in(HUB_SQL, text, [landmines.BY_ID["no-flight-in-hub"]])["no-flight-in-hub"]
    assert [number for number, _ in found] == [3]
    # A glob's /* in one Python string and */ in a later one are no comment hiding the DDL between them.
    py = 'A = "docs/*.md"\nB = "CREATE TABLE flight_events(id INTEGER)"\nC = "src/**/x"\n'
    assert hits(HUB_PY, py).get("no-flight-in-hub") == 1


def test_no_flight_in_hub_leaves_the_flight_file_the_shipped_migrations_and_tests_alone():
    sql = "CREATE TABLE flight_events(id INTEGER);\nALTER TABLE flight_slow ADD COLUMN parts TEXT;\n"
    py = 'A = "CREATE TABLE flight_events(id INTEGER)"\nB = "ALTER TABLE flight_slow ADD COLUMN parts TEXT"\n'
    rule = [landmines.BY_ID["no-flight-in-hub"]]
    for path in (
        "backend/flight.py",
        "backend/migrations/baseline.sql",
        "backend/migrations/20261126013526_flight_slow_parts.sql",
        "backend/tests/fixtures/schema_floor.sql",
        "backend/tests/test_flight_db.py",
        "clients/thing.py",
        "docs/observability.md",
    ):
        assert landmines.hits_in(path, py if path.endswith(".py") else sql, rule) == {}, path
    for path in (HUB_SQL, HUB_PY, "backend/hubdb.py", "backend/flight_file.py"):
        found = landmines.hits_in(path, py if path.endswith(".py") else sql, rule)
        assert len(found["no-flight-in-hub"]) == 2, path


@pytest.mark.parametrize(
    ("path", "bad", "good"),
    [
        ("desktop/package.json", '    "@tauri-apps/api": "^2.0.0",\n', '    "electron-updater": "^6.6.2",\n'),
        ("runner-rs/Cargo.toml", 'tauri = { version = "2", features = [] }\n', 'serde = "1"\n'),
        ("app/Cargo.toml", 'tauri-plugin-updater = "2"\n', 'tokio = "1"\n'),
        ("app/Cargo.toml", "[dependencies.tauri]\n", "[dependencies.serde]\n"),
        ("scripts/build.sh", "cargo tauri build --target universal-apple-darwin\n", "npm run pack:mac\n"),
        (
            ".github/workflows/app.yml",
            "      - uses: tauri-apps/tauri-action@v0\n",
            "      - run: npx electron-builder\n",
        ),
        (".github/workflows/app.yml", "name: Tauri app\n", "# The Tauri app (app/, app.yml) was built beside it.\n"),
        ("backend/downloads.py", 'CONF = "app/src-tauri/tauri.conf.json"\n', 'FEED = "latest-mac.yml"\n'),
        (
            "docs/desktop.md",
            'config = json.loads(Path("app/tauri.conf.json").read_text())\n',
            "Icons: `desktop/build/`.\n",
        ),
        ("docs/desktop.md", 'OVERRIDE="$BUILD_ROOT/tauri.override.json"\n', 'OUT="$BUILD_ROOT/app"\n'),
        ("README.md", "One Rust crate (`app/`, Tauri 2) builds the app.\n", "The Electron app (`desktop/`).\n"),
        ("CLAUDE.md", "- `app/` (Tauri) is the older desktop shell.\n", "- `desktop/` (Electron) is the shell.\n"),
        ("docs/releasing.md", "`TAURI_SIGNING_PRIVATE_KEY` signs the updater.\n", "`DESKTOP_MAC_CERT` signs it.\n"),
        (
            "backend/assets/tico/README.md",
            "- `app/icons/*`: the PNGs `tauri.conf.json` lists.\n",
            "- Alpha Centauri.\n",
        ),
        (
            "web/src/desktop/index.ts",
            "import { invoke } from '@tauri-apps/api/core';\n",
            "import { bridge } from './bridge';\n",
        ),
        ("clients/tico.py", 'HINT = "run cargo-tauri"\n', 'HINT = "run npm"\n'),
        ("runner-rs/Cargo.toml", '"tauri" = "2"\n', '"serde" = "1"\n'),
        (
            "runner-rs/Cargo.toml",
            'shell = { package = "tauri", version = "2" }\n',
            'json = { package = "serde_json" }\n',
        ),
        ("runner-rs/Cargo.toml", '[target.x.dependencies."tauri"]\n', '[target.x.dependencies."tokio"]\n'),
        ("app/bootstrap.sh", "cargo tauri build\n", "npm run build\n"),
        ("README-build.md", "Build the Tauri shell first.\n", "Build the Electron app first.\n"),
        ("scripts/build.sh", "cargo   tauri build\n", "cargo   build\n"),
        ("app/Cargo.toml", "[dependencies . tauri]\n", "[dependencies . serde]\n"),
        (
            "app/Cargo.toml",
            "[target.'cfg(target_os = \"macos\")'.dependencies.tauri]\n",
            "[target.'cfg(target_os = \"macos\")'.dependencies.objc2]\n",
        ),
        (
            "app/Cargo.toml",
            '[target."cfg(unix)".build-dependencies."tauri"]\n',
            '[target."cfg(unix)".dependencies.tokio]\n',
        ),
    ],
)
def test_no_tauri_bans_the_tauri_app_coming_back(path, bad, good):
    assert hits(path, bad).get("no-tauri") == 1
    assert "no-tauri" not in hits(path, good)


def test_no_tauri_bans_a_file_named_for_tauri_whatever_it_holds():
    for path in (
        "app/src-tauri/src/main.rs",
        ".github/workflows/tauri.yml",
        "app/Tauri.toml",
        "scripts/tauri_build.py",
    ):
        assert hits(path, "x = 1\n").get("no-tauri") == 1, path


def test_no_tauri_counts_a_binary_file_at_a_tauri_path(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    icon = tmp_path / "app/src-tauri/icons/icon.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    git(tmp_path, "add", "-A")
    monkeypatch.setattr(landmines, "ROOT", tmp_path)
    tracked = landmines.tracked_files
    monkeypatch.setattr(landmines, "tracked_files", lambda: tracked(tmp_path))
    found = landmines.scan([landmines.BY_ID["no-tauri"]])
    assert list(found) == ["app/src-tauri/icons/icon.png"]


def test_no_tauri_leaves_history_tests_of_its_removal_and_customer_repositories_alone():
    text = "Tauri 2 built app/ with cargo tauri build and app/tauri.conf.json.\n"
    for path in (
        "skills/honest-code/LESSONS.md",
        "CHANGELOG.md",
        "desktop/CHANGELOG.md",
        "docs/plans/2026-11-core-simplification.md",
        "runner-rs/src/worktrees.rs",
        "templates/catalog/builder/README.md",
    ):
        assert "no-tauri" not in hits(path, text), path
    # A name that only contains the letters is not named for Tauri.
    for path in ("docs/alpha-centauri.md", "backend/centauri.py"):
        assert "no-tauri" not in hits(path, "Alpha Centauri is the nearest star.\n"), path
    # A Python comment or a test that names the retired app is not the app coming back.
    removed = '# GLib left with app/ (the Tauri shell)\nTAURI = ["latest.json"]\ndef test_only_the_tauri_app(): pass\n'
    assert "no-tauri" not in hits("backend/tests/test_downloads.py", removed)
    assert "no-tauri" not in hits("scripts/tests/test_osv_gate.py", removed)
        (APP, "import asyncio\nasyncio.create_task(loop())\n", "workers.add('loop', loop)\n"),
        (APP, "import asyncio as aio\naio.create_task(loop())\n", "aio.sleep(0)\n"),
        (APP, "from asyncio import create_task\ncreate_task(loop())\n", "create_task(request, body)\n"),
        (APP, "from asyncio import create_task as spawn\nspawn(loop())\n", "spawn = 1\n"),
        (
            APP,
            "asyncio.get_running_loop().create_task(loop())\n",
            "asyncio.get_running_loop().run_in_executor(None, f)\n",
        ),
        (APP, "asyncio.ensure_future(loop())\n", "await asyncio.wait_for(stop.wait(), timeout=60)\n"),
        (
            APP,
            "asyncio.create_task(\n    loop(),\n    name='x',\n)\n",
            "cap, create_task, preflight = register_task_routes(app)\ntasks.install(app, create_task)\n",
        ),
    ],
)
def test_no_lifespan_task_finds_a_raw_task_in_the_app_and_not_the_task_helper(path, bad, good):
    assert hits(path, bad).get("no-lifespan-task") == 1
    assert "no-lifespan-task" not in hits(path, good)


def test_no_lifespan_task_reads_only_the_app_and_only_code():
    assert hits(APP, '# asyncio.create_task(loop()) would die silently\nHELP = "asyncio.create_task(x)"\n') == {}
    for path in ("backend/lifespan_workers.py", "backend/tasks.py", "backend/tests/test_app.py", "clients/x.py"):
        assert "no-lifespan-task" not in hits(path, "import asyncio\nasyncio.create_task(loop())\n"), path


@pytest.mark.parametrize(
    ("bad", "good"),
    [
        ("def install(app, store, auth):\n    pass\n", "def install_id(c):\n    pass\n"),
        ("def register_people(app, store):\n    pass\n", "def register_people(admin_only, app, store):\n    pass\n"),
        (
            "def install(app: FastAPI, store: Any) -> None:\n    pass\n",
            "def install(c: Any, app: FastAPI) -> None:\n    pass\n",
        ),
        ("def register_foo(app: 'FastAPI', store):\n    pass\n", "def register(c, who, digest):\n    pass\n"),
        ("def install(\n    app,\n    store,\n):\n    pass\n", "def install(\n    store,\n    app,\n):\n    pass\n"),
        (
            "def register(  # the old way\n    app: Any, store: Any\n) -> None:\n    pass\n",
            "def installer(self, os_name):\n    pass\n",
        ),
        ("async def install_calls(app, store):\n    pass\n", "def install_routes(application, store):\n    pass\n"),
        (
            "class Mount:\n    def install(self, app, store):\n        pass\n",
            "class Mount:\n    def install(self, store, app):\n        pass\n",
        ),
        ("def install(app):\n    pass\n", "def add(app, store):\n    pass\n"),
        (
            "def f():\n    def register_routes(app=None):\n        pass\n",
            "def router():\n    return api_routing.router()\n",
        ),
    ],
)
def test_no_install_closure_finds_each_spelling_of_an_app_installer(bad, good):
    assert hits(ROUTES, bad).get("no-install-closure") == 1
    assert "no-install-closure" not in hits(ROUTES, good)


def test_no_install_closure_reports_the_def_line_and_reads_only_shipped_backend_code():
    text = "x = 1\n\n\ndef install(\n    app,\n    store,\n):\n    pass\n"
    assert landmines.hits_in(ROUTES, text, landmines.RULES)["no-install-closure"] == [(4, "def install(")]
    assert hits(ROUTES, '"""def install(app, store): the old way."""\n# def register(app):\n') == {}
    for path in ("backend/tests/test_thing.py", "backend/tests/helpers/app.py", "clients/x.py", "scripts/x.py"):
        assert "no-install-closure" not in hits(path, "def install(app, store):\n    pass\n"), path


@pytest.mark.parametrize(
    ("bad", "good"),
    [
        ("from fastapi import APIRouter\nr = APIRouter()\n", "from . import api_routing\nr = api_routing.router()\n"),
        ("import fastapi\nr = fastapi.APIRouter(prefix='/x')\n", "def router() -> fastapi.APIRouter:\n    pass\n"),
        ("from fastapi import APIRouter as R\nr = R()\n", "from fastapi import APIRouter as R\nr: R\n"),
        ("from fastapi.routing import APIRouter as Rt\nr = Rt(tags=['x'])\n", "from fastapi.routing import APIRoute\n"),
        ("app.include_router(r)\n", "api_routing.include(app, r)\n"),
        ("app.include_router(\n    r,\n    prefix='/x',\n)\n", "include_router = None\n"),
    ],
)
def test_no_bare_apirouter_finds_a_bare_router_and_include_router(bad, good):
    assert hits(ROUTES, bad).get("no-bare-apirouter") == 1
    assert "no-bare-apirouter" not in hits(ROUTES, good)


def test_no_bare_apirouter_allows_api_routing_tests_and_text():
    bare = "from fastapi import APIRouter\nr = APIRouter()\napp.include_router(r)\n"
    for path in ("backend/api_routing.py", "backend/tests/test_api_routing.py", "clients/x.py"):
        assert "no-bare-apirouter" not in hits(path, bare), path
    assert hits(ROUTES, '# never app.include_router(r)\nHELP = "APIRouter()"\n') == {}


def test_core_imports_app_counts_each_non_core_module_of_a_multi_line_import():
    text = "from . import (\n    mail,\n    tasks,  # Core\n    calendar_api,\n    growth_core as growth,\n)\n"
    found = landmines.hits_in(CORE, text, landmines.RULES)["core-imports-app"]
    assert found == [(1, "from . import ("), (1, "from . import ("), (1, "from . import (")]


def test_core_imports_app_names_every_non_core_family():
    families = (
        "finance bookkeeper bills_address growth growth_sync market marketing_tasks market_sources company_page lenses "
        "calendar_api call_notes calls live_meetings meetings meeting_items mail mail_lib mailboxes mailbox_access "
        "email_sending letters postal_mail slack_app granola_mcp finance_routes finance_ads_routes call_ring callbacks"
    ).split()
    assert hits(CORE, "".join(f"from . import {name}\n" for name in families)) == {"core-imports-app": len(families)}
    core = "tasks people entities turn_budgets calendar emails settings_admin".split()
    assert hits(CORE, "".join(f"from . import {name}\n" for name in core)) == {}


def test_core_imports_app_reads_only_core_backend_code():
    text = "from .mail import send\nfrom . import calls\n"
    for path in (
        "backend/mail_compose.py",
        "backend/finance/actions.py",
        "backend/tests/test_tasks.py",
        "clients/x.py",
    ):
        assert "core-imports-app" not in hits(path, text), path


def test_a_production_module_named_like_a_test_is_read_and_a_test_folder_is_not():
    text = "from .mail import send\nimport runner_support.op\n"
    assert hits("backend/test_receipts.py", text) == {"core-imports-app": 1, "no-runner-support-import": 1}
    assert hits("backend/preconftest.py", text) == {"core-imports-app": 1, "no-runner-support-import": 1}
    assert hits("clients/preconftest.py", text) == {"no-runner-support-import": 1}
    for path in ("backend/tests/helpers/fake.py", "backend/conftest.py", "clients/tests/test_x.py"):
        assert hits(path, text) == {}, path


def test_each_statement_on_one_line_is_its_own_hit():
    text = "import runner_support.engine; import runner_support.op\nfrom . import mail; from . import mailboxes\n"
    assert hits(CORE, text) == {"no-runner-support-import": 2, "core-imports-app": 2}


def test_an_import_inside_a_string_is_not_an_import():
    text = 'HELP = """\nfrom .mail import send\nimport runner_support\n"""\n'
    assert hits(CORE, text) == {}


@pytest.fixture(autouse=True)
def _git_finds_the_repository_by_its_folder(monkeypatch):
    """A hook exports GIT_DIR and GIT_INDEX_FILE; unset, every git call here (landmines' own included) reads the scratch
    repository its cwd names."""
    for name in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(name, raising=False)


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_a_new_file_under_runner_support_is_banned_and_an_old_or_test_file_is_not(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "runner_support").mkdir()
    (tmp_path / "runner_support/engine.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "branch", "base")
    (tmp_path / "runner_support/tests").mkdir()
    for name in ("runner_support/new_job.py", "runner_support/shell_init/zsh-x", "runner_support/tests/test_job.py"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("x = 2\n")
    (tmp_path / "runner_support/engine.py").write_text("x = 3\n")
    git(tmp_path, "add", "-A")

    found = landmines.new_files(tmp_path, "base")
    assert sorted(found) == ["runner_support/new_job.py", "runner_support/shell_init/zsh-x"]
    problems = landmines.compare(found, {})
    assert len(problems) == 2
    assert all("no-new-runner-support-module" in p and "runner-rs" in p for p in problems)


def test_a_file_main_deleted_after_the_branch_point_is_not_new(tmp_path):
    git(tmp_path, "init", "-q", "-b", "base")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "runner_support").mkdir()
    for name in ("engine.py", "old_job.py"):
        (tmp_path / "runner_support" / name).write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "branch point")
    git(tmp_path, "checkout", "-qb", "work")
    git(tmp_path, "checkout", "-q", "base")
    git(tmp_path, "rm", "-q", "runner_support/old_job.py")
    git(tmp_path, "commit", "-qm", "main deletes a module")
    git(tmp_path, "checkout", "-q", "work")
    (tmp_path / "runner_support/new_job.py").write_text("x = 2\n")
    git(tmp_path, "add", "-A")

    assert sorted(landmines.new_files(tmp_path, "base")) == ["runner_support/new_job.py"]


def test_a_pattern_inside_a_string_or_a_comment_is_not_a_hit():
    text = 'WORKER = "import time\\ntime.sleep(60)\\n"\n# time.sleep(1) would race here\nx = f"{time}"\n'
    assert hits(PY, text) == {}


def test_a_rule_only_reads_the_files_in_its_scope():
    assert hits("backend/store.py", "time.sleep(1)\n") == {}
    assert hits("ui/app/chat.ts", "await page.waitForTimeout(5);\n") == {}


def test_a_count_above_its_baseline_fails_and_prints_every_hit():
    found = {PY: {"no-fixed-sleep-py": [(3, "time.sleep(1)"), (9, "time.sleep(2)")]}}
    problems = landmines.compare(found, {PY: {"no-fixed-sleep-py": 1}})
    assert len(problems) == 1
    assert "has 2, its baseline allows 1" in problems[0]
    assert f"{PY}:3: time.sleep(1)" in problems[0] and f"{PY}:9: time.sleep(2)" in problems[0]
    assert "Stream.until" in problems[0]


def test_a_count_below_its_baseline_fails_as_stale():
    problems = landmines.compare({}, {PY: {"no-fixed-sleep-py": 2}})
    assert problems == [
        f"no-fixed-sleep-py: {PY} now has 0; lower its baseline to 0 with "
        "`python3 scripts/landmines.py --write` in this commit"
    ]


def test_an_equal_count_passes():
    found = {PY: {"no-unittest-mock": [(1, "from unittest import mock")]}}
    assert landmines.compare(found, {PY: {"no-unittest-mock": 1}}) == []


def test_a_ban_fails_on_one_hit_whatever_the_baseline_says():
    found = {"pytest.ini": {"no-retry": [(2, "addopts = --reruns 2")]}}
    problems = landmines.compare(found, {"pytest.ini": {"no-retry": 1}})
    assert any("is a ban and has no baseline" in p for p in problems)
    assert any("has 1, its baseline allows 0" in p for p in problems)


def test_write_never_records_a_ban():
    found = {"pytest.ini": {"no-retry": [(2, "x")]}, PY: {"no-fixed-sleep-py": [(1, "y")]}}
    assert landmines.counts_of(found) == {"pytest.ini": {}, PY: {"no-fixed-sleep-py": 1}}


def test_the_shared_wait_helper_is_the_one_file_whose_poll_may_sleep():
    poll = "import time\ntime.sleep(0.01)\n"
    assert "no-fixed-sleep-py" not in hits(landmines.WAIT_HELPER, poll)
    assert hits("backend/tests/helpers/other.py", poll).get("no-fixed-sleep-py") == 1


def test_a_baseline_that_grew_against_the_base_fails():
    problems = landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {PY: {"no-fixed-sleep-py": 2}}, {"no-fixed-sleep-py"})
    assert len(problems) == 1 and "grew from 2 to 3" in problems[0]


def test_a_rule_the_base_does_not_have_brings_its_own_baseline():
    assert landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {}, {"no-retry"}) == []
    assert landmines.growth({PY: {"no-fixed-sleep-py": 3}}, {}, None) == []


def test_rules_at_reads_the_rule_ids_from_a_copy_of_this_script():
    source = Path(landmines.__file__).read_text(encoding="utf-8")
    ids = set(__import__("re").findall(r'^\s*Rule\(\s*\n\s*"([a-z0-9-]+)"', source, __import__("re").M))
    assert ids == set(landmines.BY_ID)


ROW = "| {id} | Do the thing. | {incidents} | {cls} | {seen} | {status} | {where} |\n"


def lessons(**row):
    fields = {
        "id": "L01",
        "incidents": "2026-11-03 #2294",
        "cls": "script",
        "seen": "1",
        "status": "open",
        "where": "-",
    }
    fields.update(row)
    return "| id | lesson | incidents | class | seen | status | where |\n|---|---|---|---|---|---|---|\n" + ROW.format(
        **fields
    )


def lesson_problems(text, exists=lambda path: True):
    return landmines.lesson_problems(text, set(landmines.BY_ID), exists)


def test_a_true_lessons_table_passes():
    text = lessons(
        incidents="2026-11-03 #2294; 2026-11-12 e9dece9583 (an empty commit; a rerun)",
        seen="2",
        status="encoded no-retry",
    )
    cited_elsewhere = (
        "L02",
        "L03",
        "L26",
        "L31",
        "L35",
        "L36",
        "L37",
        "L38",
        "L39",
        "L40",
        "L41",
        "L42",
    )  # the real rules cite rows this one-row table does not have
    assert [p for p in lesson_problems(text) if not any(i in p for i in cited_elsewhere)] == []


def test_seen_must_match_the_incidents():
    assert any("seen is 3 but it lists 1" in p for p in lesson_problems(lessons(seen="3")))


def test_an_incident_needs_a_pr_or_a_sha():
    assert any("needs a date and a PR number" in p for p in lesson_problems(lessons(incidents="2026-11-03 a rerun")))


def test_a_script_lesson_seen_twice_cannot_stay_open():
    text = lessons(incidents="2026-11-03 #1; 2026-11-04 #2", seen="2")
    assert any("still open" in p for p in lesson_problems(text))


def test_an_encoded_row_must_name_a_real_rule_or_script():
    assert any("is not in scripts/landmines.py" in p for p in lesson_problems(lessons(status="encoded no-such-rule")))
    missing = lessons(status="encoded `scripts/gone.py`")
    assert any("does not exist" in p for p in lesson_problems(missing, exists=lambda path: False))


def test_a_rule_must_cite_a_lesson_that_exists():
    assert any("cites L02, which is not in" in p for p in lesson_problems(lessons()))


def test_the_real_lessons_file_passes():
    assert landmines.check_lessons() == []
