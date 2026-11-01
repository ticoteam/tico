"""scripts/install.sh --recommended: the quick start with the recommended choices made, run for real against a local
release with docker, curl and the host checks stubbed (the `box` of test_install_runner.py)."""
import os
import pty
import re
import stat
import subprocess
import sys
import time

import pytest

from setup.tests.test_install_runner import box, install  # noqa: F401  (box is a fixture)

TOKEN = "owner-token-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CODE = "enroll_code-1234567890"
INVITE = "inv_SECRET-0123456789"
DOCKER = """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in
  "compose version --short") echo 2.29.7 ;;
  "version --format"*) echo 27.0.0 ;;
  "volume inspect tico-runner"|"container inspect"*) exit 1 ;;
  "compose exec -T server cat /data/local-owner.token") echo "$STUB_TOKEN" ;;
  "network inspect tico_default") [ -z "${STUB_NO_NETWORK:-}" ] || exit 1 ;;
esac
exit 0
"""
# The health check answers; the enrollment call records how it was asked (its argv and its --config on stdin) and
# answers like the server: a code, or the 422 it gives a write without an Idempotency-Key (always, with STUB_REFUSE);
# every other call (the release download, file://) is the real curl.
CURL = """#!/bin/sh
case "$*" in
  *healthz*) echo ok ;;
  *enrollments*) echo "curl $*" >> "$STUB_LOG"; cat > "$STUB_CURL_CONFIG"
    case "$*" in
      *"Idempotency-Key: "?*) [ -n "${STUB_REFUSE:-}" ] || [ -n "${STUB_NO_CODE:-}" ] || printf '{"code": "%s", "expires_at": "2026-10-18T23:00:00Z", "operator": null}\\n' "$STUB_CODE" ;;
      *) STUB_REFUSE=1 ;;
    esac
    [ -z "${STUB_REFUSE:-}" ] || printf '{"error":{"code":"idempotency_key","detail":"Provide an Idempotency-Key of 1–200 characters"}}\\n' ;;
  *) exec /usr/bin/curl "$@" ;;
esac
"""


@pytest.fixture
def server(box):  # noqa: F811
    for name, text in (("docker", DOCKER), ("curl", CURL)):
        (box["stubs"] / name).write_text(text)
        (box["stubs"] / name).chmod(0o755)
    box["config"] = box["tmp"] / "curl-config"
    box["env"] = {"STUB_TOKEN": TOKEN, "STUB_CODE": CODE, "STUB_CURL_CONFIG": str(box["config"])}
    box["computer"] = box["dir"].parent / (box["dir"].name + "-runner")
    return box


def run(box, *args, **env):
    return install(box, *args, **box["env"], **env)


def env_of(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))


def test_recommended_writes_the_choices_and_the_invite_and_never_shows_the_invite(server):
    r = run(server, "--recommended", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=INVITE)
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(server["dir"] / ".env")
    assert {k: env.get(k) for k in ("TICO_INSTALL_PRESET", "TICO_SAMPLE_COMPANY", "TICO_MODELS_PRESET", "TICO_SUPPORT",
                                    "TICO_GATEWAY_INVITE", "TICO_TEAM_NAME", "TICO_OWNER_EMAIL")} == {
        "TICO_INSTALL_PRESET": "recommended", "TICO_SAMPLE_COMPANY": "on", "TICO_MODELS_PRESET": "subscriptions-gateway",
        "TICO_SUPPORT": "on", "TICO_GATEWAY_INVITE": INVITE, "TICO_TEAM_NAME": '"Sample Co"', "TICO_OWNER_EMAIL": "ana@example.com"}
    # Quick-start sign-in: no domain, no sign-in proxy, no OAuth client, and no hosted mark on a self-hosted install.
    assert not {"TICO_DOMAIN", "TICO_AUTH_PROXY", "TICO_OIDC_CLIENT_ID", "TICO_HOSTED"} & set(env)
    assert stat.S_IMODE((server["dir"] / ".env").stat().st_mode) == 0o600
    # The invite is in .env only: never in what the install prints, nor on any command line it ran.
    assert INVITE not in r.stdout + r.stderr and INVITE not in server["log"].read_text()
    # Dev 210 enrolls on the first start, so the install says it will connect, not that it has.
    assert "Tico Gateway: it connects on the first start, from the invite this install was given." in r.stdout
    # The link is the only sign-in, and the one step left is the two subscription sign-ins.
    assert f"/api/v2/local-signin?token={TOKEN}" in r.stdout
    assert "One step is left: in the app, Settings > Computers > Server computer > Sign in, to Claude and to ChatGPT." in r.stdout
    assert "Finish setup" not in r.stdout and "Bots need an AI provider" not in r.stdout


def test_recommended_adds_a_docker_computer_on_the_server_network_without_the_owner_token_on_a_command_line(server):
    r = run(server, "--recommended", "--owner-email", "ana@example.com")
    assert r.returncode == 0, r.stdout + r.stderr
    computer = server["computer"]
    assert env_of(computer / ".env") == {"TICO_URL": "http://server:8765", "TICO_CODE": CODE, "TICO_RUNNER_LABEL": '"Server computer"',
                                         "TICO_TAG": "v0.2.0", "TICO_UPDATER_TAG": "v0.2.0", "TICO_SCREEN": "1"}
    assert "name: tico_default" in (computer / "runner.override.yaml").read_text()
    log = server["log"].read_text()
    assert "docker compose -f runner.compose.yaml -f runner.override.yaml up -d" in log   # the Docker runner, isolated
    assert "/api/v2/enrollments" in log and TOKEN not in log                              # the token went on stdin
    assert server["config"].read_text() == f'header = "Authorization: Bearer {TOKEN}"\n'
    # The server refuses an enrollment without an Idempotency-Key (the stub answers 422 then, as the server does).
    key = re.search(r"Idempotency-Key: (\S+)", log).group(1)
    assert 1 <= len(key) <= 200
    assert not (server["dir"] / "runner.compose.yaml").exists()                          # the server's directory is its own


@pytest.mark.parametrize("trouble, env", [("no code", {"STUB_NO_CODE": "1"}), ("no Docker network", {"STUB_NO_NETWORK": "1"})])
def test_a_server_computer_that_cannot_start_leaves_the_install_running_and_says_how_to_add_it(server, trouble, env):
    r = run(server, "--recommended", "--owner-email", "ana@example.com", **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Add it later: Settings > Computers > Add computer" in r.stderr, trouble
    assert env_of(server["dir"] / ".env")["TICO_INSTALL_PRESET"] == "recommended"
    assert "Tico is running on this computer only." in r.stdout


def test_a_refused_enrollment_puts_the_servers_short_reason_in_the_warning_and_never_the_token(server):
    r = run(server, "--recommended", "--owner-email", "ana@example.com", STUB_REFUSE="1")
    assert r.returncode == 0, r.stdout + r.stderr
    warning = [line for line in r.stderr.splitlines() if "did not give a computer code" in line]
    assert len(warning) == 1 and "(Provide an Idempotency-Key of 1–200 characters)" in warning[0]
    assert TOKEN not in r.stderr and CODE not in r.stderr and "Add it later" in warning[0]


def test_recommended_without_an_invite_still_installs_and_says_in_one_line_that_the_gateway_is_not_set_up(server):
    r = run(server, "--recommended", "--owner-email", "ana@example.com")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TICO_GATEWAY_INVITE" not in (server["dir"] / ".env").read_text()
    assert "Tico Gateway: not set up (this install was given no invite)." in r.stdout


def test_an_invite_that_is_not_one_stops_before_anything_is_installed_and_is_not_shown(server):
    bad = "inv secret;$(id)"
    r = run(server, "--recommended", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=bad)
    assert r.returncode == 2 and "TICO_GATEWAY_INVITE is not an invite code" in r.stderr
    assert bad not in r.stdout + r.stderr and not (server["dir"] / ".env").exists()


@pytest.mark.parametrize("bad", [
    "inv_ok-12345678\nTICO_SIGN_IN=open",          # a valid first line, then a line of its own for .env
    "TICO_SIGN_IN=open\ninv_ok-12345678",          # the valid line last
    "inv_ok-12345678\r",                           # a carriage return on the end
    "inv_ok-12345678\n",                           # a trailing newline alone
    "x" * 201,
    "inv_ok-",                                     # one short of 8
])
def test_an_invite_is_checked_whole_so_another_line_never_reaches_the_env_file(server, bad):
    r = run(server, "--recommended", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=bad)
    assert r.returncode == 2 and "TICO_GATEWAY_INVITE is not an invite code" in r.stderr, r.stdout + r.stderr
    assert not (server["dir"] / ".env").exists()
    shown = r.stdout + r.stderr
    assert all(part not in shown for part in bad.split() if part)


def test_support_off_is_written_for_the_server_and_the_quick_start_and_byo_choices_are_unchanged(server):
    r = run(server, "--recommended", "--support", "off", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=INVITE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert env_of(server["dir"] / ".env")["TICO_SUPPORT"] == "off" and "Tico Support is off" in r.stdout
    for d in (server["dir"], server["computer"]):
        for p in d.iterdir():
            p.unlink() if p.is_file() else None
    server["log"].write_text("")
    # The quick start with its own choices: no preset, no invite (even when the environment has one), no server computer.
    r = run(server, "--local", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=INVITE)
    assert r.returncode == 0, r.stdout + r.stderr
    env = env_of(server["dir"] / ".env")
    assert not {"TICO_SUPPORT", "TICO_INSTALL_PRESET", "TICO_SAMPLE_COMPANY", "TICO_MODELS_PRESET", "TICO_GATEWAY_INVITE", "TICO_HOSTED"} & set(env)
    assert env["TICO_TEAM_NAME"] == '"My Team"' and "Finish setup" in r.stdout
    assert not (server["computer"] / ".env").exists() and "enrollments" not in server["log"].read_text()
    (server["dir"] / ".env").unlink()
    r = run(server, "--local", "--support", "off", "--owner-email", "ana@example.com")
    assert r.returncode == 0 and env_of(server["dir"] / ".env")["TICO_SUPPORT"] == "off"


def test_support_given_again_on_an_installed_tico_is_saved_and_nothing_else_changes(server):
    r = run(server, "--recommended", "--owner-email", "ana@example.com", TICO_GATEWAY_INVITE=INVITE)
    assert r.returncode == 0, r.stdout + r.stderr
    env_file = server["dir"] / ".env"
    before = env_of(env_file)
    assert before["TICO_SUPPORT"] == "on"
    for switch in ("off", "on", "off"):
        r = run(server, "--local", "--support", switch)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "repairing and restarting; your .env is kept" in r.stdout and f"Tico Support is now {switch}" in r.stdout
        assert env_of(env_file) == {**before, "TICO_SUPPORT": switch}
        assert env_file.read_text().count("TICO_SUPPORT=") == 1 and stat.S_IMODE(env_file.stat().st_mode) == 0o600
    # Not given, the saved switch stays, even under --recommended, whose own default is on.
    for args in (("--local",), ("--recommended",)):
        r = run(server, *args)
        assert r.returncode == 0 and env_of(env_file)["TICO_SUPPORT"] == "off" and "Tico Support is now" not in r.stdout
    assert INVITE not in r.stdout + r.stderr


@pytest.mark.parametrize("args, wanted", [(("--recommended", "--support", "maybe"), "--support is on or off"),
                                          (("--support", "off"), "--support goes with --local or --recommended"),
                                          (("--recommended", "--tunnel"), "--local cannot be combined")])
def test_preset_flags_refuse_what_does_not_go_together(server, args, wanted):
    r = run(server, *args)
    assert r.returncode == 2 and wanted in r.stderr, r.stderr


def on_a_terminal(box, answer: str, *args, stop_at: bytes = b"") -> tuple[int, str]:
    """install.sh with a controlling terminal, typing `answer` at the first question; stopped once `stop_at` shows.
    The directory comes from TICO_INSTALL_DIR, so with no `args` the install has no arguments at all."""
    variables = {"PATH": f"{box['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
                 "HOME": str(box["tmp"]), "STUB_LOG": str(box["log"]), "TICO_INSTALL_RELEASES_URL": "file://" + str(box["rel"]),
                 "TICO_INSTALL_MEMINFO": str(box["tmp"] / "meminfo"), "TICO_INSTALL_DIR": str(box["dir"]), **box["env"]}
    script = box["rel"] / "download" / "v0.2.0" / "install.sh"
    pid, fd = pty.fork()
    if pid == 0:
        os.execve("/bin/sh", ["sh", str(script), *args], variables)
    out, sent, deadline = b"", False, time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if stop_at and stop_at in out:
            os.kill(pid, 9)
            break
        if not sent and b"Choose [1]:" in out:
            os.write(fd, answer.encode() + b"\n")
            sent = True
        if sent and b"(you are the owner):" in out and not out.endswith(b"\n"):
            os.write(fd, b"ana@example.com\n")
    else:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


@pytest.mark.parametrize("answer, preset", [("", "recommended"), ("1", "recommended"), ("2", None)])
def test_on_a_terminal_with_nothing_chosen_the_install_asks_and_the_recommended_setup_is_the_default(server, answer, preset):
    code, out = on_a_terminal(server, answer)
    assert code == 0, out
    assert "1) Recommended:" in out and "3) Team install:" in out
    assert env_of(server["dir"] / ".env").get("TICO_INSTALL_PRESET") == preset


def test_with_nothing_chosen_and_no_terminal_nothing_is_asked_and_the_wizard_runs_on_flags_as_before(server):
    # A fresh install, no mode and no --yes: only the missing terminal keeps the question away. Its own session has none.
    # This one runs the wizard, which needs Python 3.10+: pin python3 to the test's interpreter, not the host's /usr/bin one.
    (server["stubs"] / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (server["stubs"] / "python3").chmod(0o755)
    variables = {"PATH": f"{server['stubs']}:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
                 "HOME": str(server["tmp"]), "STUB_LOG": str(server["log"]),
                 "TICO_INSTALL_RELEASES_URL": "file://" + str(server["rel"]),
                 "TICO_INSTALL_MEMINFO": str(server["tmp"] / "meminfo"), "TICO_INSTALL_DIR": str(server["dir"]),
                 **server["env"]}
    script = server["rel"] / "download" / "v0.2.0" / "install.sh"
    r = subprocess.run(["sh", str(script)], env=variables, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120, start_new_session=True)
    assert "How should Tico be set up?" not in r.stdout and "Choose [1]" not in r.stdout
    assert "Setting up your team" in r.stdout
    assert "No terminal to ask questions on; the wizard runs with flags only" in r.stderr
    env = server["dir"] / ".env"
    assert not env.exists() or "TICO_INSTALL_PRESET" not in env.read_text()
    assert "enrollments" not in server["log"].read_text()


def test_the_question_is_not_asked_with_a_mode_with_yes_or_on_an_existing_install(server):
    r = run(server, "--local", "--owner-email", "ana@example.com")
    assert r.returncode == 0 and "How should Tico be set up?" not in r.stdout
    code, out = on_a_terminal(server, "1")                      # an existing .env: the upgrade path, as before
    assert code == 0 and "How should Tico be set up?" not in out and "TICO_INSTALL_PRESET" not in (server["dir"] / ".env").read_text()
    (server["dir"] / ".env").unlink()
    code, out = on_a_terminal(server, "1", "--yes", stop_at=b"Setting up your team")   # --yes: the team install, as before
    assert "Setting up your team" in out and "How should Tico be set up?" not in out


@pytest.mark.parametrize("given, shown", [
    (["--dir", "<dir>"], "Setting up your team"),
    (["--version", "v0.2.0"], "Setting up your team"),
    (["--owner-email", "ana@example.com"], "--owner-email, --owner-name and --team-name go with --local"),
])
def test_on_a_terminal_any_argument_at_all_keeps_the_question_away(server, given, shown):
    # Options that choose no setup still make it a scripted install: it does what it did before, with no question.
    args = [str(server["dir"]) if a == "<dir>" else a for a in given]
    code, out = on_a_terminal(server, "1", *args, stop_at=b"Setting up your team")
    assert shown in out and "How should Tico be set up?" not in out and "Choose [1]" not in out
