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


APP = "backend/app.py"


@pytest.mark.parametrize(
    "bad",
    [
        "import asyncio\n\n\nasync def lifespan(app):\n    task = asyncio.create_task(loop())\n",
        "import asyncio as aio\nt = aio.create_task(loop())\n",
        "from asyncio import create_task\nt = create_task(loop())\n",
        "from asyncio import create_task as spawn\nt = spawn(loop())\n",
        "import asyncio\nt = asyncio.get_running_loop().create_task(loop())\n",
        "import asyncio\nt = asyncio.ensure_future(loop())\n",
        "import asyncio\nt = asyncio.create_task(\n    loop(),\n    name='x',\n)\n",
    ],
)
def test_no_lifespan_task_finds_each_way_app_starts_a_bare_task(bad):
    assert hits(APP, bad) == {"no-lifespan-task": 1}


@pytest.mark.parametrize(
    "good",
    [
        # app.py's own Tico-task helper, handed back by register_task_routes, has the same name
        "_, create_task, _ = register_task_routes(app)\nthread_tasks.install(app, store, auth, create_task, url)\n",
        "t = create_task(c, who, body)\n",
        "# asyncio.create_task(loop()) would die silently\nx = 1\n",
        "HELP = 'never asyncio.create_task(loop()) here'\n",
        "workers.start('digest', digest_loop)\n",
        "import asyncio\nawait asyncio.sleep(0)\n",
    ],
)
def test_no_lifespan_task_leaves_app_s_task_helper_comments_and_strings_alone(good):
    assert "no-lifespan-task" not in hits(APP, good)


def test_no_lifespan_task_reads_only_backend_app():
    text = "import asyncio\nt = asyncio.create_task(loop())\n"
    for path in ("backend/lifespan_workers.py", "backend/store.py", "backend/tests/test_app.py", "clients/x.py"):
        assert "no-lifespan-task" not in hits(path, text), path


INSTALLER = "backend/thing_routes.py"


@pytest.mark.parametrize(
    "bad",
    [
        "def install(app, store, auth):\n    @app.get('/x')\n    def x():\n        return {}\n",
        "def install(\n    app,\n    store,\n):\n    pass\n",
        "def register_foo(app: FastAPI, store: Any) -> None:\n    pass\n",
        "def install_files(app, store, auth, blobs, mutate):\n    pass\n",
        "def register(\n    app: Any,  # the FastAPI app\n    store: Any,\n) -> tuple:\n    pass\n",
        "def install(app):\n    pass\n",
        "def install(app=None):\n    pass\n",
        "async def register_hooks(app, store):\n    pass\n",
        "class Routes:\n    def install(self, app, store):\n        pass\n",
        "def register_x(\n    self,\n    app,\n):\n    pass\n",
    ],
)
def test_no_install_closure_finds_each_route_installer_taking_the_app(bad):
    assert hits(INSTALLER, bad) == {"no-install-closure": 1}


@pytest.mark.parametrize(
    "good",
    [
        "def install(store: Any) -> None:\n    pass\n",  # stage_rules.install
        "def register(store, vault, settings, tool):\n    pass\n",
        "def register_people(admin_only, app, auth):\n    pass\n",  # app is not the first parameter
        "def install_id(c):\n    pass\n",
        "class A:\n    def install_id(self):\n        pass\n",
        "class A:\n    def register(self, c, who, body):\n        pass\n",
        "def install(application, store):\n    pass\n",
        "def install(app_id, store):\n    pass\n",
        "def build(app, store):\n    pass\n",
        "def preinstall(app):\n    pass\n",
        "# def install(app, store):\nx = 1\n",
        "DOC = '''\ndef install(app, store):\n'''\n",
        "router = api_routing.router()\napi_routing.include(app, router)\n",
        "install(app, store, auth)\n",  # a call, not a definition
    ],
)
def test_no_install_closure_leaves_other_functions_comments_and_strings_alone(good):
    assert "no-install-closure" not in hits(INSTALLER, good)


def test_no_install_closure_counts_each_installer_and_reads_only_shipped_backend_code():
    text = "def install(app, store):\n    pass\n\n\ndef register_more(\n    app,\n):\n    pass\n"
    assert landmines.hits_in(INSTALLER, text, landmines.RULES)["no-install-closure"] == [
        (1, "def install(app, store):"),
        (5, "def register_more("),
    ]
    for path in ("backend/tests/test_routes.py", "backend/conftest.py", "clients/thing.py", "scripts/x.py"):
        assert "no-install-closure" not in hits(path, text), path


ROUTES = "backend/thing_routes.py"


@pytest.mark.parametrize(
    "bad",
    [
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
        "import fastapi\nrouter = fastapi.APIRouter(prefix='/x')\n",
        "from fastapi.routing import APIRouter\nrouter = APIRouter(\n    prefix='/x',\n)\n",
        "from fastapi import APIRouter as R\nrouter = R()\n",
        "from fastapi import FastAPI, APIRouter as Router\nrouter = Router()\n",
        "from fastapi import (\n    FastAPI,\n    APIRouter as R,\n)\nrouter = R()\n",
        "from fastapi.routing import APIRouter as R\nrouter = R()\n",
        "app.include_router(router)\n",
        "app.include_router(\n    router,\n    prefix='/x',\n)\n",
        "parent.include_router(child)\n",
    ],
)
def test_no_bare_apirouter_finds_a_bare_router_and_include_router(bad):
    assert hits(ROUTES, bad) == {"no-bare-apirouter": 1}


@pytest.mark.parametrize(
    "good",
    [
        "from . import api_routing\nrouter = api_routing.router()\napi_routing.include(app, router)\n",
        "from .api_routing import include, router\nr = router(prefix='/x')\ninclude(app, r)\n",
        "from fastapi import APIRouter\n\n\ndef routes(made: APIRouter) -> APIRouter:\n    return made\n",
        "# never APIRouter() or app.include_router(r) here\nx = 1\n",
        "HELP = 'use api_routing.include, not app.include_router(r)'\n",
        "from fastapi import FastAPI as R\napp = R()\n",
        "from .other import R\nx = R()\n",
    ],
)
def test_no_bare_apirouter_allows_api_routing_s_router_and_include(good):
    assert "no-bare-apirouter" not in hits(ROUTES, good)


def test_no_bare_apirouter_lets_api_routing_build_the_router_and_reads_only_shipped_backend_code():
    text = "from fastapi import APIRouter\nr = APIRouter()\napp.include_router(r)\n"
    assert hits(ROUTES, text) == {"no-bare-apirouter": 2}
    for path in ("backend/api_routing.py", "backend/tests/test_routes.py", "backend/conftest.py", "clients/x.py"):
        assert "no-bare-apirouter" not in hits(path, text), path


def test_the_live_app_has_no_bare_task_and_no_bare_router():
    """The two bans hold on today's tree: app.py's own create_task helper and api_routing.py are not hits."""
    found = landmines.scan([landmines.BY_ID["no-lifespan-task"], landmines.BY_ID["no-bare-apirouter"]])
    assert found == {}


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
