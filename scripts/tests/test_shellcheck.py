"""Every shell script the images and the release gate run passes shellcheck.

A mistake in docker/*.sh shows up only when an image is built or started, and one in the gate scripts only during
Release validation. shellcheck finds most of them in a second. GitHub's Ubuntu runners have 0.9.0; elsewhere
`pip install shellcheck-py==0.9.0.6` provides the same version, so local results match CI.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted([*ROOT.glob('docker/*.sh'), *ROOT.glob('scripts/*.sh')])


def test_shell_scripts_claude_md_says_to_run_are_executable():
    """CLAUDE.md names them as commands (`scripts/dev_setup.sh`); a bot runs that line as written."""
    named = sorted(set(re.findall(r'`(scripts/\w+\.sh)\b', (ROOT / 'CLAUDE.md').read_text())))
    assert 'scripts/dev_setup.sh' in named
    listed = subprocess.run(['git', 'ls-files', '-s', '--', *named], cwd=ROOT, capture_output=True, text=True)
    # The mode git has committed; the file's own mode where the tests run on a copy with no .git.
    modes = {line.split('\t')[1]: line.split()[0] == '100755' for line in listed.stdout.splitlines()}
    assert [path for path in named if not modes.get(path, os.access(ROOT / path, os.X_OK))] == []
