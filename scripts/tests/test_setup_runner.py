"""scripts/setup-runner.sh picks the newest supported Python on the runner Mac.

The script itself refuses to run off macOS, so these run its Python pick alone, against a PATH of stand-in
interpreters, the way a Mac with several Homebrew or python.org Pythons looks to it.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PICK = re.search(r'^PYTHON_BIN="\$\(command -v .*\)"$', (ROOT / 'scripts/setup-runner.sh').read_text(), re.M).group(0)


def pick(tmp_path, *dirs):
    path = []
    for name, versions in dirs:
        folder = tmp_path / name
        folder.mkdir()
        for version in versions:
            interpreter = folder / f'python{version}'
            interpreter.write_text('#!/bin/sh\n')
            interpreter.chmod(0o755)
        path.append(str(folder))
    result = subprocess.run(['/bin/bash', '-c', f'{PICK}\nprintf %s "$PYTHON_BIN"'], env={'PATH': ':'.join(path)},
                            capture_output=True, text=True, check=True)
    return Path(result.stdout).relative_to(tmp_path).as_posix() if result.stdout else ''


@pytest.mark.parametrize('dirs, picked', [
    ([('brew', ['3.12', '3.13', '3.15'])], 'brew/python3.15'),
    ([('brew', ['3.12', '3.14'])], 'brew/python3.14'),
    ([('brew', ['3.12'])], 'brew/python3.12'),
    # Older Python earlier on PATH: still the newest wins, wherever it is.
    ([('brew', ['3.12']), ('python.org bin', ['3.15'])], 'python.org bin/python3.15'),
    # Same version twice: the first on PATH, as the shell would run it.
    ([('first', ['3.13']), ('second', ['3.13'])], 'first/python3.13'),
    ([('brew', ['3.11', '3'])], ''),
    ([('empty', [])], ''),
])
def test_setup_runner_picks_the_newest_supported_python(tmp_path, dirs, picked):
    assert pick(tmp_path, *dirs) == picked


def test_setup_runner_stops_with_a_message_when_no_supported_python():
    script = (ROOT / 'scripts/setup-runner.sh').read_text()
    assert re.search(re.escape(PICK) + r'\nif \[ -z "\$PYTHON_BIN" \]; then\n  echo "Python 3\.12 or newer', script)
