"""scripts/finance_lint.py passes on the repo and catches a fin_* write, a business-table read and an inner import."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "finance_lint.py"


def _lint():
    spec = importlib.util.spec_from_file_location("finance_lint", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_repo_passes():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_it_catches_writes_reads_and_inner_imports(tmp_path):
    lint = _lint()
    (tmp_path / "backend" / "finance").mkdir(parents=True)
    bad = tmp_path / "backend" / "billing.py"
    bad.write_text('c.execute("UPDATE fin_lines SET amount=0")\n'
                   'c.execute("SELECT * FROM fin_budgets")\n'
                   'c.execute("SELECT status FROM fin_actions WHERE id=?")\n'
                   "from .finance.store import ledger\n"
                   "from .finance import actions, reads\n")
    inside = tmp_path / "backend" / "finance" / "ok.py"
    inside.write_text('c.execute("DELETE FROM fin_holds")\n')
    found = lint.problems(tmp_path, [bad, inside])
    assert [line.split(": ", 1)[1].split(";")[0] for line in found] == [
        "writes fin_lines", "reads fin_budgets", "imports finance.store"]


def test_it_catches_a_finance_table_named_in_quotes_outside_finance(tmp_path):
    """A table named in a list (a dev copy's, say) is how a delete loop reaches Finance's rows without a write in sight."""
    lint = _lint()
    (tmp_path / "backend" / "finance").mkdir(parents=True)
    listing = tmp_path / "backend" / "devcopy.py"
    listing.write_text('EMPTY = ("fin_connections", "fin_holds")\n')
    inside = tmp_path / "backend" / "finance" / "actions.py"
    inside.write_text('DEV_COPY_EMPTIED = ("fin_connections",)\n')
    found = lint.problems(tmp_path, [listing, inside])
    assert [line.split(": ", 1)[1].split(";")[0] for line in found] == [
        "names fin_connections", "names fin_holds"]
