"""Preflight (clients/preflight.py) over a repo built in a temp folder: no git remote, no model, no mail."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clients import preflight as PF


def lines(fn, *args):
    r = PF.Report("t")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(r, *args)
    return r, out.getvalue().splitlines(), result


class Manifest(unittest.TestCase):
    def test_a_manifest_that_names_another_employee_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "employee.yaml").write_text("name: someone-else\n")
            r, out, m = lines(PF.check_manifest, "seo", d)
        self.assertEqual(m, {"name": "someone-else"})
        self.assertEqual(r.counts["FAIL"], 1)
        self.assertTrue(any("expected 'seo'" in l for l in out))


class RetiredMailCli(unittest.TestCase):
    """`mail.sh` is gone: a bot told to run it fails every mail step."""

    PATTERN = next(pattern for pattern, why in PF.STALE if "mail.sh" in why)

    def test_no_template_or_policy_tells_a_bot_to_run_it(self):
        root = Path(__file__).resolve().parents[2]
        hits = [
            f"{path.relative_to(root)}:{n}"
            for base in ("templates", "policies")
            for path in sorted((root / base).rglob("*"))
            if path.is_file() and path.suffix in (".md", ".yaml", ".yml", ".json", ".txt")
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
            if self.PATTERN.search(line)
        ]
        self.assertEqual(hits, [])

    def test_a_bot_repo_that_still_names_it_is_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "AGENTS.md").write_text("Read the inbox: `$HUB_DIR/scripts/mail.sh inbox --untriaged`.\n")
            r, out, _ = lines(PF.check_stale, d)
        self.assertEqual(r.counts["WARN"], 1)
        self.assertIn("tico email", out[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
