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
import time
import tomllib
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # Docker: opt-in

IMAGE = os.environ.get("TICO_RUNNER_TEST_IMAGE", "").strip()
HERE = Path(__file__).parent / "isolation"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300, **kwargs)


@pytest.fixture
def volume():
    if not IMAGE:
        pytest.skip("Set TICO_RUNNER_TEST_IMAGE to a freshly built runner image; the release check selects its candidate")
    if not shutil.which("docker"):
        pytest.skip("Docker is not installed; runner isolation requires Docker and an explicitly selected image")
    if docker("image", "inspect", IMAGE).returncode != 0:
        pytest.fail(f"The explicitly selected runner image {IMAGE} is unavailable")
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
    assert result["token_bodies"]["turn_start"] == [{"bot": "alpha"}], result
    assert result["token_bodies"]["git_helper"] and all(
        body == {"bot": "alpha", "purpose": "git"}
        for body in result["token_bodies"]["git_helper"]
    ), result

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


def test_the_bot_user_clones_and_fetches_the_mirror_the_supervisor_owns(volume):
    # Git drops `-c safe.directory` for the upload-pack it starts for a file:// clone, so before the trust file the
    # supervisor's mirror was "dubious ownership" to the bot user on every Linux computer (TK-ZBPQC6JB).
    ran = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *CAPS,
                 "-v", f"{volume}:/home/runner", "-v", f"{HERE / 'mirror_side.py'}:/mirror_side.py:ro",
                 IMAGE, "python", "/mirror_side.py")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    result = json.loads(ran.stdout.strip().splitlines()[-1])
    assert result["flag_only"][0] != 0 and "dubious ownership" in result["flag_only"][1], (
        "This git now honours -c safe.directory for a local upload-pack (or no longer checks ownership): update this "
        "test, not the fix", result)
    assert result["clone"] == [0, ""] and result["fetch"] == [0, ""], result
    assert result["commits"] == "2" and result["trust_file"] == "0o644", result


# ------------------------------------------------------------------ the real runner, every harness, a task's session
#
# These start the image's own runner (its entrypoint and binary, the way docker/runner.compose.yaml does) against
# isolation/fake_hub.py in a container beside it, with isolation/fake_cli.py standing in for every harness's
# executable. So they test what an image ships, and pointing TICO_RUNNER_TEST_IMAGE at an older release shows
# whether that release had the defect.

HARNESSES = Path(__file__).resolve().parents[1] / "harnesses"
ROOT = Path(__file__).resolve().parents[2]
WAIT_SECONDS = 300


def harnesses():
    """Every harness and variant the manifests name, with what its stand-in CLI needs to answer."""
    found = []
    for path in sorted(HARNESSES.glob("*.toml")):
        manifest = tomllib.loads(path.read_text())
        base_caps = manifest.get("capabilities") or {}
        rows = [(manifest["runtime"], manifest, base_caps)]
        for variant, fields in (manifest.get("variants") or {}).items():
            rows.append((variant, {**manifest, **{k: fields[k] for k in ("host", "executable") if k in fields}},
                         {**base_caps, **(fields.get("capabilities") or {})}))
        for harness, fields, caps in rows:
            found.append({
                "harness": harness, "runtime": manifest["runtime"], "host": fields["host"],
                "executable": fields["executable"], "bot": "acme-" + "".join(c if c.isalnum() else "-" for c in harness),
                "version_args": (manifest.get("version") or {}).get("args") or ["--version"],
                "status": caps.get("sign_in_status") or {},
                "key_env": ((manifest.get("auth") or {}).get("api_key_env") or [""])[0],
            })
    return found


class Stack:
    """A fake hub and GitHub on a private network, and the runner's volume; the runner container comes and goes."""

    def __init__(self, tmp):
        self.name = "tico-turns-" + uuid.uuid4().hex[:8]
        self.hub, self.runner, self.volume = self.name + "-hub", self.name + "-runner", self.name
        self.tmp = tmp
        self.turns = 0

    def must(self, *args, **kwargs):
        done = docker(*args, **kwargs)
        assert done.returncode == 0, (args[:3], done.stdout[-2000:], done.stderr[-2000:])
        return done.stdout

    def start_hub(self, image, bots):
        self.must("network", "create", self.name)
        self.must("run", "-d", "--name", self.hub, "--network", self.name, "--network-alias", "hub", "-u", "0",
                  "--entrypoint", "python", "-v", f"{HERE / 'fake_hub.py'}:/fake_hub.py:ro", image, "/fake_hub.py")
        origins = "".join(f"""
            git init -q -b main /tmp/{bot} && cd /tmp/{bot} && echo '# Acme bot' > AGENT.md && git add AGENT.md
            git -c user.name='Acme' -c user.email=bots@acme.example commit -qm start
            git clone -q --bare /tmp/{bot} /srv/git/acme/bot-{bot}.git && cd / && rm -rf /tmp/{bot}""" for bot in bots)
        self.must("exec", self.hub, "sh", "-c", "set -e; mkdir -p /srv/git/acme /work/queue /work/claimed /work/done" + origins)

    def put(self, path, value):
        self.must("exec", "-i", self.hub, "sh", "-c", f"cat > /work/{path}.tmp && mv /work/{path}.tmp /work/{path}",
                  input=json.dumps(value))

    def seed(self, image, script):
        """Run `script` as root in `image` with the volume mounted and the fake GitHub reachable."""
        return self.must("run", "--rm", "-u", "0", "--network", self.name, "-v", f"{self.volume}:/home/runner",
                         "--entrypoint", "sh", image, "-c", "set -e; cd /home/runner\n" + script)

    def start_runner(self, image, table):
        spec = {h["executable"]: {"host": h["host"], "version_args": h["version_args"], "version": "9.9.9 (fake)",
                                  "status": h["status"]} for h in table}
        (self.tmp / "spec.json").write_text(json.dumps(spec))
        mounts = [a for h in table for a in ("-v", f"{HERE / 'fake_cli.py'}:/usr/local/bin/{h['executable']}:ro")]
        docker("rm", "-f", self.runner)
        self.must("run", "-d", "--name", self.runner, "--network", self.name, "--user", "0", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges:true", *CAPS, "-v", f"{self.volume}:/home/runner",
                  "-v", f"{self.tmp / 'spec.json'}:/fake/spec.json:ro", *mounts, image)

    def queue(self, table):
        """One task turn per harness, each in its own task's lane; returns the attempt ids."""
        self.turns += 1
        ids = []
        for h in table:
            task = "t-" + h["bot"]
            aid = f"a{self.turns}-{h['bot']}"
            self.put(f"queue/{aid}.json", {
                "id": aid, "job_id": "j" + aid, "bot": h["bot"], "token": f"acme-turn-{aid}", "lease_seconds": 600,
                "conversation": {"id": "c-" + h["bot"]}, "message": {"text": "Acme: probe this checkout", "refs": {}},
                "lane": f"task:{task}", "task": {"id": task, "title": "Acme probe"}, "credential_vault": True,
                "config": {"runtime": h["runtime"], "harness": h["harness"], "model": ""}})
            ids.append(aid)
        return ids

    def completions(self, ids):
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            listed = docker("exec", self.hub, "ls", "/work/done").stdout.split()
            if all(f"{aid}.json" in listed for aid in ids) or time.monotonic() > deadline:
                break
            if docker("inspect", "-f", "{{.State.Running}}", self.runner).stdout.strip() != "true":
                break  # the runner stopped: nothing more will finish
            time.sleep(2)
        return {aid: json.loads(self.must("exec", self.hub, "cat", f"/work/done/{aid}.json") or "null") or {}
                for aid in ids if f"{aid}.json" in listed}

    def logs(self):
        done = docker("logs", "--tail", "120", self.runner)
        return (done.stdout + done.stderr)[-6000:]

    def owners(self, image):
        """uid:gid and mode of every path in the volume, as root sees them."""
        out = self.must("run", "--rm", "-u", "0", "-v", f"{self.volume}:/home/runner", "--entrypoint", "find", image,
                        "/home/runner", "-printf", "%U:%G %m %p\\n")
        return {path: (owner, mode) for owner, mode, path in (line.split(" ", 2) for line in out.splitlines())}

    def close(self):
        for container in (self.runner, self.hub):
            docker("rm", "-f", container)
        docker("network", "rm", self.name)
        docker("volume", "rm", "-f", self.volume)


def image_ready(image):
    if not shutil.which("docker"):
        pytest.skip("Docker is not installed; runner isolation requires Docker and an explicitly selected image")
    if docker("image", "inspect", image).returncode != 0 and docker("pull", "-q", image).returncode != 0:
        pytest.fail(f"The runner image {image} is unavailable")


@pytest.fixture
def stack(tmp_path):
    if not IMAGE:
        pytest.skip("Set TICO_RUNNER_TEST_IMAGE to a freshly built runner image; the release check selects its candidate")
    image_ready(IMAGE)
    made = Stack(tmp_path)
    yield made
    made.close()


def registration(bots):
    """What an install on the two-user layout leaves: the registration, and each bot's checkout cloned from its
    GitHub repository by the bot user."""
    config = json.dumps({"url": "http://hub:8765", "token": "acme-runner-token", "runner_id": "r1",
                         "projects_dir": "/home/runner/workspace", "self_update": False})
    clones = "".join(f"""
        git clone -q http://hub:8766/acme/bot-{bot}.git workspace/bot-{bot}
        git -C workspace/bot-{bot} config user.name 'Acme {bot}'; git -C workspace/bot-{bot} config user.email {bot}@acme.example
        chown -R 10003:10002 workspace/bot-{bot}; chmod -R g+rwX workspace/bot-{bot}""" for bot in bots)
    return f"""
        : > .tico-two-user-layout; chown 10002:10002 .tico-two-user-layout
        printf '%s' '{config}' > runner.json; chmod 600 runner.json
        mkdir -p workspace/secrets; echo ACME_LEGACY=1 > workspace/secrets/_shared.env
        chown 10002:10002 . workspace; chown -R 10002:10002 workspace/secrets{clones}
    """


def probe_problems(table, done, logs):
    """Each turn must have completed as the bot user, with no capabilities, in its task's own worktree, unable to read
    the runner's registration, state or legacy secrets, and able to commit and pull there."""
    problems = []
    for h in table:
        body = next((b for a, b in done.items() if a.endswith("-" + h["bot"])), None)
        if not body:
            problems.append(f"{h['harness']}: no completion")
            continue
        text = body.get("text") or ""
        if body.get("outcome") != "completed" or "probe {" not in text:
            problems.append(f"{h['harness']}: {body.get('outcome')}: {text[:300]}")
            continue
        seen = json.loads(text[text.index("probe {") + 6:].strip())
        want = {"uid": 10003, "caps": "0000000000000000", "registration": False, "state": False, "secrets": False,
                "commit": "ok", "pull": "ok", "where": f"sessions > bot-{h['bot']} > t-{h['bot']}"}
        wrong = {k: seen.get(k) for k, v in want.items() if seen.get(k) != v}
        if wrong:
            problems.append(f"{h['harness']}: {wrong}")
    return problems + ([f"runner log:\n{logs}"] if problems else [])


def test_every_harness_takes_task_turns_as_the_bot_user_and_the_session_stays_the_bots(stack):
    # v0.2.8 crash-looped after its first turn, v0.2.12 re-locked a token it no longer owned, v0.3.43 and v0.3.45 left
    # worktrees with the wrong owner, and Muse Code, Gemini and Antigravity turns ran as the supervisor until 0.3.48.
    # So: a task turn for every harness through the real runner (which adds the worktree as the bot user), a restart,
    # a second turn in the same worktrees, and then who owns the sessions and the runner's private files.
    table = harnesses()
    bots = [h["bot"] for h in table]
    stack.start_hub(IMAGE, bots)
    stack.put("assignments.json", [{"bot": h["bot"], "config": {"runtime": h["runtime"], "harness": h["harness"]}}
                                   for h in table])
    stack.put("credentials.json", [{"id": "k-" + env, "env": env, "value": "acme-fake-key", "kind": "env"}
                                   for env in sorted({h["key_env"] for h in table if h["key_env"]})])
    stack.seed(IMAGE, registration(bots))

    stack.start_runner(IMAGE, table)
    problems = probe_problems(table, stack.completions(stack.queue(table)), stack.logs())
    assert not problems, "\n".join(problems)

    stack.must("restart", "-t", "20", stack.runner)
    problems = probe_problems(table, stack.completions(stack.queue(table)), stack.logs())
    assert not problems, "the first round passed, then after a restart:\n" + "\n".join(problems)

    stack.must("stop", "-t", "20", stack.runner)
    owners = stack.owners(IMAGE)
    workspace = "/home/runner/workspace"
    wrong = []
    for h in table:
        session = f"{workspace}/sessions/bot-{h['bot']}/t-{h['bot']}"
        gitdir = f"{workspace}/bot-{h['bot']}/.git/worktrees/t-{h['bot']}"
        if session not in owners or gitdir not in owners:
            wrong.append(f"{h['harness']}: no session worktree")
        wrong += [f"{path} {owner}" for path, (owner, _) in owners.items()
                  if (path.startswith(session) or path.startswith(gitdir)) and not owner.startswith("10003:")]
    assert not wrong, "the bot user must own its sessions:\n" + "\n".join(wrong[:40])
    assert owners["/home/runner/runner.json"] == ("10002:10002", "600"), owners["/home/runner/runner.json"]
    assert owners["/home/runner/state-r1"][0] == "10002:10002", owners["/home/runner/state-r1"]
    assert owners[f"{workspace}/secrets"] == ("10002:10002", "700"), owners[f"{workspace}/secrets"]


def previous_image():
    """The release before the candidate: TICO_RUNNER_PREVIOUS_IMAGE, or the newest release tag's runner image."""
    chosen = os.environ.get("TICO_RUNNER_PREVIOUS_IMAGE", "").strip()
    if chosen:
        return chosen
    tags = subprocess.run(["git", "tag", "-l", "v[0-9]*", "--sort=-v:refname"], cwd=ROOT, capture_output=True,
                          text=True).stdout.split()
    tags = [t for t in tags if "-" not in t]
    if not tags:
        pytest.fail("No release tag here to start from: set TICO_RUNNER_PREVIOUS_IMAGE")
    return f"ghcr.io/ticoteam/tico-runner:{tags[0]}"


def test_handing_an_older_releases_session_to_the_bot_changes_no_owner_outside_it(stack):
    # Before 0.3.48 the handoff walked a session's paths and chowned each one, so a hard link in the session (or a
    # checkout whose objects a local clone shares with the supervisor's mirror) gave the bot user files outside it.
    # The previous release takes a task turn, the session gets files with mixed owners and links out, and the
    # candidate takes the next turn there: nothing outside the session and its checkout may change owner.
    previous = previous_image()
    image_ready(previous)
    table = harnesses()[:1]
    h = table[0]
    bot, other = h["bot"], "acme-other"
    stack.start_hub(previous, [bot, other])
    stack.put("assignments.json", [{"bot": bot, "config": {"runtime": h["runtime"], "harness": h["harness"]}}])
    if h["key_env"]:
        stack.put("credentials.json", [{"id": "k", "env": h["key_env"], "value": "acme-fake-key", "kind": "env"}])
    stack.seed(previous, registration([bot, other]))
    stack.start_runner(previous, table)
    seeded = stack.completions(stack.queue(table))
    assert seeded and all(b.get("outcome") == "completed" for b in seeded.values()), (previous, seeded, stack.logs())
    stack.must("stop", "-t", "20", stack.runner)

    session = f"workspace/sessions/bot-{bot}/t-{bot}"
    stack.seed(previous, f"""
        S={session}; C=workspace/bot-{bot}
        # Mixed owners inside the session: the handoff is meant to give these to the bot user.
        echo root > $S/acme-root-file; echo sup > $S/acme-supervisor-file; chown 10002:10002 $S/acme-supervisor-file
        mkdir -p $S/acme-dir; echo x > $S/acme-dir/nested
        # Supervisor-private files outside it, which the session reaches by a hard link and a symlink.
        mkdir -p state-r1/acme-private; echo private > state-r1/acme-private/note
        chown -R 10002:10002 state-r1/acme-private; chmod 700 state-r1/acme-private; chmod 600 state-r1/acme-private/note
        ln state-r1/acme-private/note $S/acme-hardlink; ln -s /home/runner/runner.json $S/acme-symlink
        # The supervisor's mirror of the bot's repository, made by a local clone, so it shares the checkout's objects.
        mkdir -p state-r1/mirrors; git clone -q --mirror --local $C state-r1/mirrors/acme__bot-{bot}.git
        chown -R 10002:10002 state-r1/mirrors
        # Another bot's checkout with a file root owns.
        echo root > workspace/bot-{other}/acme-root-file
    """)
    before = stack.owners(previous)

    stack.start_runner(IMAGE, table)
    problems = probe_problems(table, stack.completions(stack.queue(table)), stack.logs())
    stack.must("stop", "-t", "20", stack.runner)
    after = stack.owners(IMAGE)
    inside = (f"/home/runner/{session}", f"/home/runner/workspace/bot-{bot}/")
    changed = [f"{path}: {before[path][0]} -> {after[path][0]}" for path in sorted(before)
               if path in after and before[path][0] != after[path][0] and not path.startswith(inside)]
    assert not changed, f"the handoff from {previous} to {IMAGE} changed owners outside the session:\n" + "\n".join(changed[:40])
    assert not problems, "\n".join(problems)
    for name in ("acme-root-file", "acme-supervisor-file", "acme-dir/nested"):
        assert after[f"/home/runner/{session}/{name}"][0].startswith("10003:"), (name, after[f"/home/runner/{session}/{name}"])


def test_gateway_turn_users_block_siblings_and_clean_before_reuse(volume):
    """The source-pinned Rust probe exercises the real pool and relay in the candidate image.

    Build its executable with `cargo test --manifest-path runner-rs/Cargo.toml --no-run`, then set
    TICO_RUNNER_TEST_BINARY to the emitted executable. No host home, credential or database is mounted.
    The probe verifies its fingerprint against the candidate's runner and all 64 passwd entries.
    """
    binary = os.environ.get("TICO_RUNNER_TEST_BINARY", "").strip()
    if not binary:
        pytest.skip("Set TICO_RUNNER_TEST_BINARY for the required Gateway two-user candidate acceptance")
    binary = Path(binary).resolve(strict=True)
    assert binary.is_file() and os.access(binary, os.X_OK), binary
    configured = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", *CAPS,
                        "--security-opt", "no-new-privileges:true", "--network", "none",
                        "-v", f"{volume}:/home/runner", IMAGE, "python", "-c",
                        "import os; print(os.geteuid(),os.environ['TICO_RUNNER_TURN_UIDS'])")
    assert configured.returncode == 0, configured.stdout + configured.stderr
    assert configured.stdout.strip().splitlines()[-1] == "10002 10100-10163", configured.stdout
    result = docker("run", "--rm", "--user", "0", "--cap-drop", "ALL", *CAPS,
                    "--security-opt", "no-new-privileges:true", "--network", "none",
                    "-e", "TICO_TEST_DISPOSABLE=1", "-v", f"{volume}:/home/runner",
                    "-v", f"{binary}:/turn-user-probe:ro", "--entrypoint", "/turn-user-probe", IMAGE,
                    "--ignored", "--exact", "gateway_relay::tests::docker_turn_user_blocks_siblings_and_cleans_before_reuse",
                    "--nocapture", "--test-threads=1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout and "0 failed" in result.stdout, result.stdout
