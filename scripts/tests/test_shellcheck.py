"""Every shell script the images and the release gate run passes shellcheck.

A mistake in docker/*.sh shows up only when an image is built or started, and one in the gate scripts only during
Release validation. shellcheck finds most of them in a second. GitHub's Ubuntu runners have 0.9.0; elsewhere
`pip install shellcheck-py==0.9.0.6` provides the same version, so local results match CI.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted([*ROOT.glob('docker/*.sh'), *ROOT.glob('scripts/*.sh')])


@pytest.mark.skipif(not shutil.which('shellcheck'), reason='shellcheck not installed')
@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_shell_script_passes_shellcheck(script):
    result = subprocess.run(['shellcheck', '-S', 'warning', '-f', 'gcc', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
