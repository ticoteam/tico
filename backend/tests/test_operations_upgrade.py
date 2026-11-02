"""Upgrade proof for Operations: an install the base release made (304ff2712, the records backend) boots under this
branch's code and keeps every row; a data dir a crash left half-upgraded boots too; each demo starts on an empty folder
and its records load; and the first boot on a big synthetic book is timed.

The base release runs from `git archive` of its commit, in a child process with only that tree on the path, so no module
of this branch is loaded there. Every row is synthetic (the Acme demo and generated rows). Outbound calls are refused
while this branch's server runs (demo.network_blocked). The QuickBooks fake is not needed: no books connection is made."""
import contextlib
import csv
import datetime
import dataclasses
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import demo, demo_seed, manage
from backend.app import create_app
from backend.records import store as RS
from backend.scheduler import Scheduler
from backend.store import Store

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[2]
BASE = os.environ.get("TICO_UPGRADE_BASE", "304ff2712")       # main before this branch
NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)
DATA = REPO / "backend" / "demo_data"
RECORDS = ("locations", "items", "projects", "fin_contacts", "bots")
NEW_TABLES = ("equipment", "due_dates", "due_date_occurrences")
NEW_COLUMNS = {"locations": ("customer_id",), "items": ("description", "taxable", "quantity_on_hand", "quantity_synced_at"),
               "fin_contacts": ("payment_terms_days", "owner_id", "renewal_notice_days"), "fin_external_refs": ("detail",)}
# What a start rewrites without the upgrade: the roster's change counter (bumped by its own triggers), and the search
# index's segments (the same rows, merged again).
EVERY_START = ("roster_clock",)

# Runs under the base release's code (its cwd and PYTHONPATH), as an owner would seed it: Finance set up, then contacts,
# locations, items and projects through the Finance actions, and tasks through the API. `big` adds that many locations
# and items straight to the records store, for the timing.
OLD_SEED = r'''
import sys
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from backend import demo
from backend.app import create_app
from backend.finance import actions as finance
from backend.records import store as records

NOW = datetime(2026, 9, 25, 18, 30, tzinfo=timezone.utc)
data, big = Path(sys.argv[1]), int(sys.argv[2])
settings = demo.build(data, now=NOW)
app = create_app(settings)
with TestClient(app, base_url="http://127.0.0.1:8765") as api:
    api.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
    with app.state.store.transaction() as c:
        assert finance.ensure_setup(c, settings.owner_email)

    def act(kind, payload, key):
        done = api.post("/api/v2/finance/actions", json={"kind": kind, "payload": payload},
                        headers={"Idempotency-Key": key}).json()
        assert done["status"] == "committed", done
        return done["result"]
    customer = act("contact_create", {"name": "Acme Customer", "kind": "customer"}, "c1")["contact_id"]
    act("contact_create", {"name": "Acme Vendor", "kind": "vendor"}, "c2")
    main = act("location_create", {"name": "Acme Main Shop", "kind": "store"}, "l1")["location_id"]
    act("location_create", {"name": "Acme Back Room", "kind": "other", "parent_id": main}, "l2")
    act("item_create", {"name": "Service call", "kind": "service", "price": 9500}, "i1")
    act("item_create", {"name": "Filter", "kind": "inventory", "price": 1200, "unit": "each"}, "i2")
    act("project_create", {"name": "Spring project", "customer_id": customer}, "p1")
    act("project_create", {"name": "Shop refit"}, "p2")
    for n in (1, 2):
        assert api.post("/api/v2/tasks", json={"title": f"Old install task {n}", "body": "x", "owner": "bot:coo"},
                        headers={"Idempotency-Key": f"t{n}"}).status_code == 200
    with app.state.store.transaction() as c:
        for n in range(big):
            records.insert(c, "locations", {"name": f"Site {n}", "kind": "customer_site", "address": "{}",
                                            "created_at": "2026-01-01T00:00:00Z", "created_by_id": "human:ana"})
            records.insert(c, "items", {"name": f"Part {n}", "kind": "inventory", "created_at": "2026-01-01T00:00:00Z",
                                        "created_by_id": "human:ana"})
'''



FAKE_RELAY = r'''#!PYTHON
import http.client, http.server, json, os, sys, threading, urllib.parse
if sys.argv[1:] == ["--build-id"]:
    print("BUILD_ID")
    sys.exit(0)
origin = urllib.parse.urlsplit(os.environ["TICO_SCREEN_PYTHON_URL"])
public_host, public_port = os.environ["TICO_SCREEN_PUBLIC_BIND"].rsplit(":", 1)


class Control(http.server.BaseHTTPRequestHandler):
    def send_json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/control/v1/hello":
            self.send_json({"boot": "fake-boot", "challenge": "fake"})
        else:
            self.send_error(404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/control/v1/start":
            self.send_json({"boot": "fake-boot"})
        elif self.path == "/control/v1/command":
            self.send_json({"boot": "fake-boot", "reports": [], "sessions": [], "exit_usage": None})
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


class Public(http.server.BaseHTTPRequestHandler):
    # The public port passes every request on to the Python server, as the Rust relay does.
    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        upstream = http.client.HTTPConnection(origin.hostname, origin.port, timeout=60)
        upstream.request(self.command, self.path, body=body or None,
                         headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")})
        reply = upstream.getresponse()
        data = reply.read()
        self.send_response(reply.status)
        for name, value in reply.getheaders():
            if name.lower() not in ("connection", "transfer-encoding", "content-length"):
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = forward

    def log_message(self, *args):
        pass


control = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Control)
threading.Thread(target=control.serve_forever, daemon=True).start()
public = http.server.ThreadingHTTPServer((public_host, int(public_port)), Public)
print("screen relay listening on 127.0.0.1:%d" % control.server_address[1], flush=True)
public.serve_forever()
'''


def fake_screen_relay(folder):
    """The Rust screen relay at its process boundary: `--build-id` answers with this checkout's id, and its control API
    answers the server's hello, start and heartbeats with nothing on the relay. The demo's pages do not use the relay, and
    building or downloading the real one is not part of this test (backend/screen_binary.py)."""
    from backend.screen_binary import source_id
    path = folder / "tico-screen-relay"
    path.write_text(FAKE_RELAY.replace("#!PYTHON", "#!" + sys.executable).replace("BUILD_ID", source_id()))
    path.chmod(0o755)
    return path


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """The base release's tree, from git: `git archive` of BASE, extracted into a folder of its own."""
    if subprocess.run(["git", "cat-file", "-e", BASE + "^{commit}"], cwd=REPO, capture_output=True).returncode:
        pytest.skip(f"{BASE} is not in this clone; the test needs full history (fetch-depth 0)")
    archive = subprocess.run(["git", "archive", "--format=tar", BASE], cwd=REPO, capture_output=True, check=True).stdout
    folder = tmp_path_factory.mktemp("base")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(folder, filter="data")
    return folder


def run_base(base, data, big=0):
    """Seed `data` with the base release's code, in a child process that imports nothing from this checkout."""
    done = subprocess.run([sys.executable, "-c", OLD_SEED, str(data), str(big)], cwd=base, capture_output=True,
                          text=True, timeout=1800, env={**os.environ, "PYTHONPATH": str(base)})
    assert done.returncode == 0, done.stderr[-4000:]


@pytest.fixture(scope="module")
def old_install(base, tmp_path_factory):
    """The data dir the base release wrote: the Acme demo, Finance set up, and records and tasks added the way a person
    adds them. Its install has no business type."""
    data = tmp_path_factory.mktemp("old") / "data"
    run_base(base, data)
    return data


@pytest.fixture(scope="module")
def fresh_acme(tmp_path_factory):
    """This branch's own first build of the Acme demo: the schema a fresh install ends with."""
    return demo.build(tmp_path_factory.mktemp("fresh") / "acme", now=NOW).db_path


def copy_of(source, tmp_path, name):
    target = tmp_path / name
    shutil.copytree(source, target)
    return target


def upgraded_settings(data):
    """The settings a real install boots with, on the files the demo made: not demo mode."""
    return dataclasses.replace(demo.prepare(data), demo=False, demo_public=False)


@contextlib.contextmanager
def started(data):
    """This branch's server on `data`, started the way an install starts: its startup runs the upgrade. Signed in as the
    install's owner; nothing may leave the machine while it runs."""
    settings = upgraded_settings(data)
    with demo.network_blocked() as attempts:
        with TestClient(create_app(settings)) as api:
            api.headers["Authorization"] = "Bearer " + settings.local_owner_token_file.read_text().strip()
            yield api
    assert not attempts, attempts


def tables(path):
    """Every table's columns and rows, read from the file with plain sqlite."""
    with closing(sqlite3.connect(path)) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: ([r[1] for r in c.execute(f'PRAGMA table_info("{t}")')], c.execute(f'SELECT * FROM "{t}"').fetchall())
                for t in names}


def lost_rows(before, after):
    """Rows of the old release the upgrade no longer has, compared on the columns both releases have: a column added
    later is not a change to a row."""
    lost = {}
    for table, (columns, rows) in before.items():
        if table in EVERY_START or table.startswith("external_sections_fts") or table not in after:
            continue
        now_columns, now_rows = after[table]
        keep = [now_columns.index(name) for name in columns]
        missing = Counter(rows) - Counter(tuple(row[i] for i in keep) for row in now_rows)
        if missing:
            lost[table] = sum(missing.values())
    return lost


def schema(path):
    with closing(sqlite3.connect(path)) as c:
        return {t: {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}
                for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def assert_schema_complete(data, fresh):
    """Every table and column a fresh install of this branch has, the upgraded install has too."""
    have, want = schema(data / "hub.sqlite"), schema(fresh)
    assert not set(want) - set(have), sorted(set(want) - set(have))
    assert {t: sorted(want[t] - have[t]) for t in want if want[t] - have[t]} == {}
    assert not set(NEW_TABLES) - set(have)
    for table, columns in NEW_COLUMNS.items():
        assert not set(columns) - have[table], (table, columns)


def count(path, table):
    with closing(sqlite3.connect(path)) as c:
        return c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def test_an_old_install_boots_twice_under_this_branch_and_keeps_its_rows(old_install, fresh_acme, tmp_path):
    data = copy_of(old_install, tmp_path, "upgrade")
    before = tables(data / "hub.sqlite")
    with started(data) as api:
        # The new tables and columns exist, and every old row is still there.
        assert_schema_complete(data, fresh_acme)
        assert lost_rows(before, tables(data / "hub.sqlite")) == {}
        for table in RECORDS:
            if table in before:
                assert count(data / "hub.sqlite", table) == len(before[table][1]), table
        for path in ("config", "locations", "items", "projects", "equipment", "due-dates", "customers"):
            assert api.get("/api/v2/" + path).status_code == 200, path
        names = [row["name"] for row in api.get("/api/v2/locations").json()["locations"]]
        assert "Acme Main Shop" in names and "Acme Back Room" in names
        assert api.get("/api/v2/config").json().get("business_type") in (None, "")
        # The scheduler's pass runs clean on the upgraded install.
        assert Scheduler(api.app.state.store, api.app.state.execution).tick()["failures"] == []
    first = tables(data / "hub.sqlite")
    # The second start changes nothing: no table gains or loses a row, and no row's content moves. (No request is made:
    # a read of a page writes its own usage counter.)
    with started(data):
        pass
    second = tables(data / "hub.sqlite")
    assert set(first) == set(second)
    for table in set(first) - set(EVERY_START) - {t for t in first if t.startswith("external_sections_fts")}:
        assert Counter(first[table][1]) == Counter(second[table][1]), table
    assert lost_rows(first, second) == {}


def test_a_sidebar_with_no_business_type_gets_the_upgrade_default(old_install, tmp_path):
    data = copy_of(old_install, tmp_path, "sidebar")
    with started(data) as api:
        sidebar = api.get("/api/v2/company/sidebar").json()
    assert sidebar["business_type"] is None
    # Locations, products and projects hold rows, so their tabs show; equipment holds none, so its tab is hidden.
    assert sidebar["hidden_sections"] == ["operations.equipment"]


def test_a_half_upgraded_data_dir_boots_and_finishes_the_upgrade(old_install, fresh_acme, tmp_path):
    """A crash part way: some of this release's columns and the equipment table were written, and the rest was not."""
    data = copy_of(old_install, tmp_path, "half")
    equipment = next(s for s in RS.TABLES.split(";") if "CREATE TABLE IF NOT EXISTS equipment" in s)
    with closing(sqlite3.connect(data / "hub.sqlite")) as c:
        c.execute("ALTER TABLE locations ADD COLUMN customer_id TEXT REFERENCES fin_contacts(id)")
        c.execute("ALTER TABLE items ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        c.execute("ALTER TABLE fin_contacts ADD COLUMN payment_terms_days INTEGER")
        c.execute(equipment)
        c.commit()
    before = tables(data / "hub.sqlite")
    with started(data) as api:
        after = tables(data / "hub.sqlite")        # before any read, which writes its own usage counter
        assert_schema_complete(data, fresh_acme)
        assert lost_rows(before, after) == {}
        assert api.get("/api/v2/equipment").status_code == 200
        assert api.get("/api/v2/locations").status_code == 200
    assert_schema_complete(data, fresh_acme)


def test_a_fresh_install_boots_on_an_empty_folder(tmp_path):
    """The installer's files, then the first start: `initialize` imports the registry the installer wrote (the same steps
    as backend.manage initialize), and the server answers."""
    data = tmp_path / "fresh"
    data.mkdir()
    settings = upgraded_settings(data)
    demo_seed.write_registry(settings)
    with demo.network_blocked() as attempts:
        store = Store(settings)
        store.initialize()
        store.seed(entries=manage.resolved_registry(data, settings.registry_dir))
        store.enqueue_existing()
    assert not attempts, attempts
    with started(data) as api:
        for path in ("config", "company/sidebar", "locations", "items", "projects", "equipment", "due-dates"):
            assert api.get("/api/v2/" + path).status_code == 200, path
    assert (data / "hub.sqlite").exists()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def csv_rows(company, name):
    with open(DATA / company / (name + ".csv"), encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def warranties(company):
    """The equipment whose warranty still runs: an end already past adds no due date (finance/equipment.py)."""
    today = datetime.date.today().isoformat()
    with open(DATA / company / "equipment.csv", encoding="utf-8", newline="") as f:
        return sum(1 for row in csv.DictReader(f) if row.get("warranty_ends_on", "") >= today)


@pytest.mark.parametrize("company", ["", "heating-air", "kitchen", "works"])
def test_each_demo_starts_as_the_docs_say_and_its_records_load(tmp_path, company):
    """`python3 -m backend.demo [--company X]` on an empty folder: the process starts, answers as the owner, and the
    records the company's CSV files hold are listed by the API. The log says whether onboarding opens."""
    data, log_path, port = tmp_path / "demo", tmp_path / "demo.log", free_port()
    args = [sys.executable, "-m", "backend.demo", "--data-dir", str(data), "--port", str(port)]
    if company:
        args += ["--company", company]
    with log_path.open("w") as log:
        proc = subprocess.Popen(args, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONPATH": str(REPO),
                                     "TICO_SCREEN_RELAY_BIN": str(fake_screen_relay(tmp_path))})
    try:
        deadline = time.monotonic() + 300
        while True:
            assert proc.poll() is None, log_path.read_text()[-3000:]
            assert time.monotonic() < deadline, log_path.read_text()[-3000:]
            with contextlib.suppress(OSError):
                candidate = (data / "local-owner.token").read_text().strip()
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/v2/config",
                                                              headers={"Authorization": "Bearer " + candidate}), timeout=5)
                token = candidate
                break
            time.sleep(0.5)

        def get(path):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/v2/{path}",
                                             headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=30) as reply:
                return json.loads(reply.read())
        onboarding = get("onboarding")
        print(f"ONBOARDING company={company or 'acme'} needed={onboarding['needed']} completed={onboarding['completed']} "
              f"bots={len(onboarding['bots'])}", flush=True)
        if company:
            expected = {"locations": "locations", "items": "items", "equipment": "equipment", "due-dates": "due_dates"}
            for route, name in expected.items():
                listed = get(route)
                # Equipment whose warranty still runs carries its own due date as well.
                extra = warranties(company) if name == "due_dates" else 0
                assert len(listed[route.replace("-", "_")]) == csv_rows(company, name) + extra, route
            assert get("company/sidebar")["business_type"] == {"heating-air": "field_service", "kitchen": "restaurant",
                                                               "works": "manufacturer"}[company]
        else:
            assert get("tasks")["tasks"], "the Acme demo has tasks"
    finally:
        proc.terminate()
        proc.wait(timeout=120)
    assert "is ready" in log_path.read_text()


def test_the_first_boot_on_a_big_book_is_timed(base, tmp_path):
    """20,000 locations and 20,000 items on the base release's tables; the first start on this branch is timed."""
    data = tmp_path / "big"
    run_base(base, data, big=20000)
    before = {table: count(data / "hub.sqlite", table) for table in ("locations", "items")}
    assert before["locations"] >= 20000 and before["items"] >= 20000
    started_at = time.monotonic()
    with started(data) as api:
        assert api.get("/api/v2/locations").status_code == 200
    seconds = time.monotonic() - started_at
    flag = "  FLAG: over 30s" if seconds > 30 else ""
    print(f"\nUPGRADE_TIMING first boot, {before['locations']} locations + {before['items']} items: {seconds:.1f}s{flag}",
          flush=True)
    assert {table: count(data / "hub.sqlite", table) for table in before} == before
