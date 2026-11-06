"""Where the mail connector keeps things: the Mac layout by default, named places on a Linux runner."""

import os
import subprocess
from pathlib import Path

from connectors.mail import locations

HUB = Path("/Users/x/tico-work/tico")
ROOT = Path(__file__).resolve().parents[3]


def test_a_linux_runner_names_its_places():
    env = {"TICO_PROJECTS_DIR": "/home/runner/workspace"}
    assert locations(env, HUB) == (Path("/home/runner/workspace"), Path("/home/runner/workspace/runtime/mail"))
    env["TICO_MAIL_RUNTIME_DIR"] = "/var/lib/mail"
    assert locations(env, HUB)[1] == Path("/var/lib/mail")


def venv_of(env):
    """The venv scripts/mail.sh would use, read by running it with a python that only says so."""
    fake = ROOT / "scripts/mail.sh"
    text = fake.read_text().split('if [ ! -x')[0] + 'echo "$VENV"\n'
    done = subprocess.run(
        ["bash", "-c", text.replace('"$(dirname "$0")/.."', '"' + str(ROOT) + '"')],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **env},
    )
    return done.stdout.strip()


def test_the_venv_follows_the_projects_folder_a_runner_names_and_a_named_venv_wins():
    """The image's checkout is read-only and has no sibling folders, so the default must not be /opt/runtime."""
    assert venv_of({"TICO_PROJECTS_DIR": "/home/runner/workspace"}) == "/home/runner/workspace/runtime/mail/venv"
    assert venv_of({"TICO_PROJECTS_DIR": "/w", "TICO_MAIL_VENV": "/tools/mail-venv"}) == "/tools/mail-venv"
    assert venv_of({}) == str(ROOT.parent / "runtime/mail/venv")
