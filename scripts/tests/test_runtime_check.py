"""scripts/runtime_check.py: the image build refuses a Python the locks were not compiled for and an old SQLite."""
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts import runtime_check

ROOT = Path(__file__).resolve().parents[2]


def test_the_test_interpreter_passes():
    # The tests run on the locked Python, so this is what the image build sees on a good base image.
    result = subprocess.run([sys.executable, 'scripts/runtime_check.py'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'ok' in result.stdout


class OldSQLite:
    """A connection that reports an old version and has no FTS5, as a distro SQLite built without it would."""

    def __init__(self, version, fts5=False):
        self.db, self.version, self.fts5 = sqlite3.connect(':memory:'), version, fts5

    def execute(self, sql):
        if sql == 'SELECT sqlite_version()':
            return self.db.execute('SELECT ?', (self.version,))
        if 'fts5' in sql and not self.fts5:
            raise sqlite3.OperationalError('no such module: fts5')
        return self.db.execute(sql)

    def close(self):
        self.db.close()


def test_a_wrong_python_an_old_sqlite_and_one_without_fts5_are_refused():
    major, minor = runtime_check.locked_python()
    found = runtime_check.problems(version_info=(major, minor + 1, 0))
    assert len(found) == 1 and f'Python {major}.{minor + 1}' in found[0] and f'{major}.{minor}' in found[0]
    assert runtime_check.problems(connect=lambda _: OldSQLite('3.34.1', fts5=True)) == ['SQLite 3.34.1, older than 3.35.0']
    found = runtime_check.problems(connect=lambda _: OldSQLite('3.46.0'))
    assert len(found) == 1 and 'lacks FTS5' in found[0]


def test_the_image_build_runs_it_in_the_source_stage():
    # Every image is FROM the same PYTHON_IMAGE, so checking it once in the source stage covers server and runner.
    dockerfile = (ROOT / 'Dockerfile').read_text()
    source = dockerfile.split(' AS source\n', 1)[1].split('\nFROM ', 1)[0]
    assert '\nRUN python scripts/runtime_check.py\n' in source
