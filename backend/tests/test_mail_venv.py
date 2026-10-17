"""scripts/mail.sh rebuilds its venv when a new Python (a new runner image) would leave the packages behind."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# python3 for the script: answers the version question and builds a venv whose python logs what it ran.
FAKE_PYTHON3 = """#!/bin/sh
case "$1" in
  -c) echo "$FAKE_MINOR" ;;
  -m) [ "$2" = venv ] || exit 1
      mkdir -p "$3/bin" "$3/lib/python$FAKE_MINOR/site-packages"
      printf '#!/bin/sh\\necho "$*" >> "%s/log"\\n' "$3" > "$3/bin/python"
      chmod +x "$3/bin/python" ;;
  *) exit 1 ;;
esac
"""


def run(tmp_path, minor):
    hub = tmp_path / "hub"
    if not hub.exists():
        (hub / "scripts").mkdir(parents=True)
        (hub / "connectors/mail").mkdir(parents=True)
        shutil.copy(ROOT / "scripts/mail.sh", hub / "scripts/mail.sh")
        (hub / "connectors/mail/requirements.txt").write_text("google-auth==2\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").write_text(FAKE_PYTHON3)
        (bin_dir / "python3").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "TICO_MAIL_VENV": str(tmp_path / "venv"),
           "FAKE_MINOR": minor}
    return subprocess.run(["bash", str(hub / "scripts/mail.sh"), "doctor"], env=env, capture_output=True, text=True,
                          check=True)


def test_mail_venv_is_rebuilt_for_a_new_python_and_kept_otherwise(tmp_path):
    venv = tmp_path / "venv"
    first = run(tmp_path, "3.12")
    assert "building the venv" in first.stderr
    assert (venv / "lib/python3.12").is_dir()

    again = run(tmp_path, "3.12")
    assert again.stderr == ""
    assert (venv / "log").read_text().count("-m connectors.mail doctor") == 2

    upgraded = run(tmp_path, "3.15")
    assert "built by another Python, rebuilding it for python3.15" in upgraded.stderr
    assert (venv / "lib/python3.15").is_dir() and not (venv / "lib/python3.12").exists()
    assert (venv / "log").read_text().splitlines()[-1] == "-m connectors.mail doctor"
