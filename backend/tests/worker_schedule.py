"""How xdist hands tests to workers (conftest.py pytest_xdist_make_scheduler): like `--dist load`, with two changes.

A module whose tests share a module, class, package or session fixture (a built demo, a migrated old schema, a copied
repository) is one unit of work, so one worker builds that fixture once. Under plain `load` its tests spread over the
workers and each one built it again: test_demo_company's two demo builds ran on up to a dozen workers of a 16-worker run.

The slowest work goes first, by the seconds scripts/test-timings.json records per file, so the long units start while
every worker still has short tests to take and none of them runs alone at the end. A file with no record counts as
average. The order changes which tests share a worker, never which tests run.

The controller collects nothing itself, so a module is recognised by its source: a fixture with a wider scope in the
file, or an import from a helper module that defines one (backend/tests/helpers/sample_company.py and others)."""

import json
import re
from pathlib import Path

from xdist.scheduler import LoadScopeScheduling

WIDE_FIXTURE = re.compile(r"""@pytest\.fixture\([^)]*scope\s*=\s*["'](?:module|class|package|session)["']""")
# A helper's session fixture is built once per worker whichever modules use it, so only its narrower ones count.
MODULE_FIXTURE = re.compile(r"""@pytest\.fixture\([^)]*scope\s*=\s*["'](?:module|class|package)["']""")
HELPERS = "backend/tests/helpers"
AVERAGE = 0.5  # seconds per test of a file with no record (the recorded median is about 0.4)


def wide_fixture_helpers(root):
    """Helper module names (sample_company) with a module fixture: a test module importing one shares it."""
    names = set()
    for path in (root / HELPERS).glob("*.py"):
        try:
            if MODULE_FIXTURE.search(path.read_text()):
                names.add(path.stem)
        except (OSError, UnicodeError):
            continue
    return names


def shares_fixture(text, helpers):
    """Whether a test module's source defines a fixture wider than a test, or imports one from a helper module."""
    if WIDE_FIXTURE.search(text):
        return True
    return any(re.search(rf"helpers\.{name}\b|helpers import (?:[^\n]*[ ,(])?{name}\b", text) for name in helpers)


def recorded_seconds(root):
    """{test file: seconds} from the release gate's record, the whole suite's (scripts/release_checks.py TIMINGS)."""
    try:
        timings = json.loads((root / "scripts/test-timings.json").read_text())
    except (OSError, ValueError):
        return {}
    for kind in ("release", "default"):
        if isinstance(timings.get(kind), dict) and timings[kind]:
            return {name: float(seconds) for name, seconds in timings[kind].items()}
    return {}


class SharedFixtureScheduling(LoadScopeScheduling):
    """LoadScopeScheduling whose units are single tests, except a module that shares a fixture, which is one unit."""

    def __init__(self, config, log=None):
        super().__init__(config, log)
        self.root = Path(config.rootpath)
        self.helpers = wide_fixture_helpers(self.root)
        self.seconds = recorded_seconds(self.root)
        self.shared = {}

    def _module_shares(self, module):
        if module not in self.shared:
            try:
                self.shared[module] = shares_fixture((self.root / module).read_text(), self.helpers)
            except (OSError, UnicodeError):
                self.shared[module] = False
        return self.shared[module]

    def _split_scope(self, nodeid):
        module = nodeid.split("::", 1)[0]
        return module if self._module_shares(module) else nodeid

    def mark_test_complete(self, node, item_index, duration=0):
        """As LoadScopeScheduling, but a finished unit leaves the node's list: with a unit per test the list held every
        test the node ever ran, and each completion counted through all of them."""
        nodeid = self.registered_collections[node][item_index]
        scope = self._split_scope(nodeid)
        work = self.assigned_work[node]
        work[scope][nodeid] = True
        if all(work[scope].values()):
            del work[scope]
        self._reschedule(node)

    def schedule(self):
        """LoadScopeScheduling.schedule with the units ordered slowest first (see the module docstring)."""
        assert self.collection_is_completed
        if self.collection is not None:
            for node in self.nodes:
                self._reschedule(node)
            return
        if not self._check_nodes_have_same_collection():
            self.log("**Different tests collected, aborting run**")
            return
        self.collection = list(next(iter(self.registered_collections.values())))
        if not self.collection:
            return
        units, per_file = {}, {}
        for nodeid in self.collection:
            units.setdefault(self._split_scope(nodeid), {})[nodeid] = False
            module = nodeid.split("::", 1)[0]
            per_file[module] = per_file.get(module, 0) + 1

        def weight(scope):
            module = scope.split("::", 1)[0]
            recorded = self.seconds.get(module)
            if recorded is None:
                return AVERAGE * len(units[scope])
            return recorded * len(units[scope]) / per_file[module]

        for scope in sorted(units, key=weight, reverse=True):  # stable: a file's tests stay together, in order
            self.workqueue[scope] = units[scope]
        for _ in range(len(self.nodes) - len(self.workqueue)):
            unused, _assigned = self.assigned_work.popitem()
            unused.shutdown()
        for node in self.nodes:
            self._assign_work_unit(node)
        for node in self.nodes:
            self._reschedule(node)
        if not self.workqueue:
            for node in self.nodes:
                node.shutdown()
