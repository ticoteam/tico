"""The xdist scheduler (backend/tests/worker_schedule.py): a module sharing a fixture is one unit for one worker, the
slowest units go first, and every collected test is handed out exactly once."""

import json
from types import SimpleNamespace

from backend.tests.worker_schedule import SharedFixtureScheduling, shares_fixture

SHARED = 'import pytest\n\n@pytest.fixture(scope="module")\ndef built():\n    return 1\n'
PLAIN = "import pytest\n\n@pytest.fixture\ndef one():\n    return 1\n"


def test_a_module_shares_a_fixture_by_its_own_scope_or_a_helper_it_imports():
    assert shares_fixture(SHARED, set())
    assert shares_fixture(SHARED.replace("module", "session"), set())
    assert not shares_fixture(PLAIN, set())
    assert shares_fixture("from backend.tests.helpers.sample_company import (\n    sample,\n)\n", {"sample_company"})
    assert shares_fixture("from backend.tests.helpers import sample_company\n", {"sample_company"})
    assert not shares_fixture("from backend.tests.helpers.sample_company_rows import x\n", {"sample_company"})


class Node:
    def __init__(self, name):
        self.name, self.sent, self.shutting_down = name, [], False
        self.gateway = SimpleNamespace(id=name)

    def send_runtest_some(self, indexes):
        self.sent.append(list(indexes))

    def shutdown(self):
        self.shutting_down = True


def test_shared_modules_go_whole_to_one_worker_and_the_slowest_work_goes_first(tmp_path):
    tests = tmp_path / "t"
    tests.mkdir()
    (tests / "test_demo.py").write_text(SHARED)
    (tests / "test_slow.py").write_text(PLAIN)
    (tests / "test_fast.py").write_text(PLAIN)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/test-timings.json").write_text(
        json.dumps({"release": {"t/test_demo.py": 40.0, "t/test_slow.py": 30.0, "t/test_fast.py": 1.0}})
    )
    config = SimpleNamespace(rootpath=tmp_path, getvalue=lambda name: ["2*popen"], option=SimpleNamespace())
    scheduler = SharedFixtureScheduling(config)
    collection = [f"t/test_fast.py::test_{i}" for i in range(3)]
    collection += [f"t/test_demo.py::test_{i}" for i in range(3)]
    collection += [f"t/test_slow.py::test_{i}" for i in range(2)]
    nodes = [Node("a"), Node("b")]
    for node in nodes:
        scheduler.add_node(node)
        scheduler.add_node_collection(node, collection)
    scheduler.schedule()

    first = {node.name: [collection[i] for i in node.sent[0]] for node in nodes}
    assert first["a"] == [f"t/test_demo.py::test_{i}" for i in range(3)]  # 40 s, one unit
    assert first["b"] == ["t/test_slow.py::test_0"]  # 15 s each, ahead of 0.33 s each
    done = {node.name: 0 for node in nodes}
    while any(done[node.name] < sum(map(len, node.sent)) for node in nodes):
        for node in nodes:
            pending = [index for batch in node.sent for index in batch][done[node.name] :]
            if pending:
                done[node.name] += 1
                scheduler.mark_test_complete(node, pending[0])
    assert all(node.shutting_down for node in nodes)
    handed = sorted(collection[i] for node in nodes for batch in node.sent for i in batch)
    assert handed == sorted(collection)
    assert scheduler.tests_finished
