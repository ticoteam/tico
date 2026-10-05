"""Docker integration: bot code cannot read the runner's registration, and still works.

Needs Docker and a built runner image; skipped otherwise:
    docker build --target runner -t tico-runner:local .
    TICO_RUNNER_TEST_IMAGE=tico-runner:local pytest runner/tests/test_isolation_docker.py

It starts the image the way docker/runner.compose.yaml does (root, five capabilities) on a volume laid
out the way an older image left it (everything owned by 10002), so the one-time migration runs too.
Inside, runner/tests/isolation/turn_side.py plays the supervisor and drops a turn to the bot user. Then
the volume is used the way an older image (no capabilities, only uid 10002) would after a rolled-back
update: it must still read its registration and state and run a harness, and `bot` must still not.
"""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

IMAGE = os.environ.get("TICO_RUNNER_TEST_IMAGE", "tico-runner:local")
HERE = Path(__file__).parent / "isolation"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300, **kwargs)


@pytest.fixture
def volume():
    if not shutil.which("docker") or docker("image", "inspect", IMAGE).returncode != 0:
        pytest.skip(f"Docker or the runner image {IMAGE} is not available")
    name = "tico-isolation-test-" + uuid.uuid4().hex[:8]
    yield name
    docker("volume", "rm", "-f", name)


CAPS = [a for cap in ("CHOWN", "DAC_OVERRIDE", "KILL", "SETGID", "SETUID") for a in ("--cap-add", cap)]


def test_a_turn_cannot_read_the_registration_but_can_run_a_harness_and_push(volume):
    # A volume from before this change: one user, 10002, owns everything.
    seeded = docker("run", "--rm", "-u", "10002", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c", """
        set -e; cd /home/runner
        echo '{"token": "REGISTRATION-SECRET"}' > runner.json; chmod 600 runner.json
        mkdir -p state-abc tools/bin .codex workspace/emp-alpha; echo secret > state-abc/runner.sqlite; chmod 700 state-abc
        echo '{"login": "chatgpt"}' > .codex/auth.json; chmod 600 .codex/auth.json
        printf '#!/bin/sh\\necho "codex logged in: $(cat "$HOME/.codex/auth.json")"\\n' > tools/bin/codex; chmod 755 tools/bin/codex
    """)
    assert seeded.returncode == 0, seeded.stderr
    ran = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                 "-v", f"{volume}:/home/runner",
                 "-v", f"{HERE / 'turn_side.py'}:/turn_side.py:ro", "-v", f"{HERE / 'turn.sh'}:/turn.sh:ro",
                 IMAGE, "python", "/turn_side.py")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    result = json.loads(ran.stdout.strip().splitlines()[-1])
    turn = dict(line.split("=", 1) for line in result["turn"])
    assert turn["uid"] == "10003" and turn["caps"] == "CapEff:0000000000000000", result
    assert turn["read_registration"] == "no" and turn["delete_registration"] == "no", result
    assert turn["read_state"] == "no" and turn["registration_in_env"] == "no", result
    assert result["registration"] == "ticorun 600", result
    assert turn["harness"] == 'codex logged in: {"login": "chatgpt"}', result       # the model login moved with the bot
    assert turn["push"] == "ok" and result["pushed"] == "from the turn", result        # the helper got the bot's token
    assert result["auth_seen"][-1].startswith("Basic ") and len(result["auth_seen"]) == 2, result   # 401, then the token

    # The update is rolled back: the previous image runs as 10002 with no capabilities on the migrated volume.
    old = docker("run", "--rm", "-u", "10002", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c", """
        set -e; cd /home/runner
        grep -q REGISTRATION-SECRET runner.json
        echo more >> state-abc/runner.sqlite                   # its state is still its own to write
        python3 -c 'import json; c = json.load(open("runner.json")); json.dump(c, open("runner.json", "w"))'   # as the entrypoint rewrites it
        [ "$(tools/bin/codex login status)" = 'codex logged in: {"login": "chatgpt"}' ]   # the login is still readable
        cd workspace/emp-alpha && git init -q -b main . && git -c user.name=t -c user.email=t@x commit -q --allow-empty -m old
    """)
    assert old.returncode == 0, old.stdout + old.stderr
    # ...and the bot user still cannot open any of it.
    for path in ("runner.json", "state-abc/runner.sqlite"):
        blocked = docker("run", "--rm", "-u", "10003", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c",
                         f"! cat /home/runner/{path} >/dev/null 2>&1")
        assert blocked.returncode == 0, path


def test_the_runner_starts_again_after_a_turn_took_the_secrets_folder(volume):
    # An older image gave the secrets folder to the bot user before a turn; legacy secrets are now the
    # supervisor's (runner-entrypoint.sh), so the next start must still prepare the volume, not fail on
    # chmod, and take the folder back.
    seeded = docker("run", "--rm", "-u", "0", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c", """
        set -e; cd /home/runner; : > .tico-two-user-layout; chown 10002:10002 . .tico-two-user-layout
        mkdir -p workspace/secrets; echo A=1 > workspace/secrets/_shared.env
        chown 10002:10002 workspace; chown -R 10003:10002 workspace/secrets; chmod 660 workspace/secrets/_shared.env
    """)
    assert seeded.returncode == 0, seeded.stderr
    started = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                     "-v", f"{volume}:/home/runner", IMAGE, "sh", "-c",
                     "stat -c '%u %a' /home/runner/workspace/secrets; cat /home/runner/workspace/secrets/_shared.env")
    assert started.returncode == 0, started.stdout + started.stderr
    assert started.stdout.split() == ["10002", "700", "A=1"], started.stdout


def test_the_codex_home_is_the_bot_users_and_group_writable_so_both_users_can_use_a_login(volume):
    # An older volume: ~/.codex made by the supervisor, mode 755, holding a login only its owner could write.
    seeded = docker("run", "--rm", "-u", "10002", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c",
                    "mkdir -m 755 /home/runner/.codex && echo '{}' > /home/runner/.codex/config.toml")
    assert seeded.returncode == 0, seeded.stderr
    started = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                     "-v", f"{volume}:/home/runner", IMAGE, "sh", "-c", """
        stat -c '%u:%g %a' /home/runner/.codex /home/runner/.codex/config.toml
        # what `codex login` does as the bot user, then what the supervisor sees of it
        setpriv --reuid=10003 --regid=10002 --clear-groups --inh-caps=-all --ambient-caps=-all sh -c \\
          'umask 002; echo key > /home/runner/.codex/auth.json && chmod g+rw /home/runner/.codex/auth.json'
        cat /home/runner/.codex/auth.json
        stat -c '%g' /home/runner/.codex/auth.json
        # the mail tool's venv goes under the workspace, which the bot user can write
        setpriv --reuid=10003 --regid=10002 --clear-groups --inh-caps=-all --ambient-caps=-all \\
          sh -c 'TICO_PROJECTS_DIR=/home/runner/workspace; mkdir -p "$TICO_PROJECTS_DIR/runtime/mail/venv" && echo mail-ok'
    """)
    assert started.returncode == 0, started.stdout + started.stderr
    assert started.stdout.split()[-7:] == ["10003:10002", "2770", "10003:10002", "664", "key", "10002", "mail-ok"], started.stdout


def test_the_bot_user_can_build_the_mail_venv_where_the_connectors_job_made_the_folder_first(volume):
    # The connectors job (ticorun, umask 022) made workspace/runtime/mail with mail.db before any turn ran.
    seeded = docker("run", "--rm", "-u", "0", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c", """
        set -e; cd /home/runner; : > .tico-two-user-layout; chown 10002:10002 . .tico-two-user-layout; chmod 1770 .
        mkdir -p workspace/secrets workspace/runtime/mail; echo db > workspace/runtime/mail/mail.db
        chown -R 10002:10002 workspace; chmod 755 workspace/runtime workspace/runtime/mail
    """)
    assert seeded.returncode == 0, seeded.stderr
    started = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                     "-v", f"{volume}:/home/runner", IMAGE, "sh", "-c", """
        stat -c '%u:%g %a' /home/runner/workspace/runtime /home/runner/workspace/runtime/mail
        setpriv --reuid=10003 --regid=10002 --clear-groups --inh-caps=-all --ambient-caps=-all \\
          sh -c 'umask 002; mkdir -p /home/runner/workspace/runtime/mail/venv && echo mail-ok'
        # the supervisor still writes beside it, through the group
        setpriv --reuid=10002 --regid=10002 --clear-groups sh -c 'echo x > /home/runner/workspace/runtime/mail/audit.jsonl && echo super-ok'
    """)
    assert started.returncode == 0, started.stdout + started.stderr
    assert started.stdout.split() == ["10003:10002", "2770", "10003:10002", "2770", "mail-ok", "super-ok"], started.stdout


def test_both_users_can_write_the_mail_database_the_connectors_job_created_and_nothing_else_widens(volume):
    # The job (ticorun, umask 022) made mail.db, its -wal/-shm and audit.jsonl as 0644 before any turn ran:
    # a bot's `mail.sh search` then failed with "attempt to write a readonly database".
    seeded = docker("run", "--rm", "-u", "0", "-v", f"{volume}:/home/runner", "--entrypoint", "sh", IMAGE, "-c", """
        set -e; cd /home/runner; : > .tico-two-user-layout; chown 10002:10002 . .tico-two-user-layout; chmod 1770 .
        mkdir -p workspace/secrets workspace/runtime/mail
        for f in mail.db mail.db-wal mail.db-shm mail.db-journal audit.jsonl; do echo x > workspace/runtime/mail/$f; done
        echo x > workspace/runtime/other.txt
        chown -R 10002:10002 workspace; chmod 755 workspace/runtime workspace/runtime/mail
        chmod 644 workspace/runtime/mail/* workspace/runtime/other.txt
    """)
    assert seeded.returncode == 0, seeded.stderr
    started = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                     "-v", f"{volume}:/home/runner", IMAGE, "sh", "-c", """
        cd /home/runner/workspace/runtime
        stat -c '%a' mail/mail.db mail/mail.db-wal mail/mail.db-shm mail/mail.db-journal mail/audit.jsonl other.txt
        setpriv --reuid=10003 --regid=10002 --clear-groups --inh-caps=-all --ambient-caps=-all \\
          sh -c 'for f in mail.db mail.db-wal mail.db-shm mail.db-journal audit.jsonl; do echo bot >> mail/$f; done && echo bot-ok'
        setpriv --reuid=10002 --regid=10002 --clear-groups \\
          sh -c 'for f in mail.db mail.db-wal mail.db-shm mail.db-journal audit.jsonl; do echo sup >> mail/$f; done && echo super-ok'
    """)
    assert started.returncode == 0, started.stdout + started.stderr
    assert started.stdout.split() == ["664"] * 5 + ["644", "bot-ok", "super-ok"], started.stdout
