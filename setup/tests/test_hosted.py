"""`tico setup --hosted own|invited [--preset recommended]`: a server Tico hosts, from our operator's flags only. The
owner's single-use sign-in link is made here and written to a 0600 file; the server gets its hash. Never printed."""
import hashlib
import json
import stat

import pytest

from setup import cli, dns, envfile, hosted, state, verify
from setup.remote import Result
from setup.tests import fakes
from setup.tests.fakes import CaptureIO, FakeShell

DOMAIN = "acme.hosted.example"
ZONE = "hosted.example"
INVITE = "inv_SECRET-0123456789"
HOSTED = ["--non-interactive", "--domain", DOMAIN, "--hosted-zone", ZONE, "--owner-email", "ana@acme.example"]


@pytest.fixture(autouse=True)
def checks_pass(monkeypatch):
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    monkeypatch.delenv("TICO_GATEWAY_INVITE", raising=False)


def go(argv, deps=None):
    io = CaptureIO()
    code = cli.main(argv, deps=deps or fakes.deps(), io=io)
    return code, io.text


def over_ssh(shell):
    r = fakes.FakeResolver({(DOMAIN, dns.A): ["203.0.113.7"]}, {ZONE: fakes.NETLIFY_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.ssh_shell = r, (lambda: [("Google", r)]), (lambda *a: shell)
    return d


def env_sent(shell):
    return envfile.parse(next(i for c, i in shell.calls if i and b"TICO_DOMAIN" in i).decode())


def link_token():
    path = hosted.link_file(DOMAIN)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    url = path.read_text().strip()
    assert url.startswith(f"https://{DOMAIN}/auth/owner-link#")
    return url.split("#", 1)[1]


def test_hosted_own_with_the_preset_writes_the_mark_the_choices_and_only_the_links_hash(monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
    shell = FakeShell()
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", "--preset", "recommended",
                    *HOSTED], deps=over_ssh(shell))
    assert code == 0, out
    env = env_sent(shell)
    token = link_token()
    assert {k: env.get(k) for k in ("TICO_HOSTED", "TICO_AUTH_PROXY", "TICO_OWNER_LINK_HASH", "TICO_INSTALL_PRESET",
                                    "TICO_SAMPLE_COMPANY", "TICO_MODELS_PRESET", "TICO_SUPPORT", "TICO_GATEWAY_INVITE",
                                    "TICO_TEAM_NAME")} == {
        "TICO_HOSTED": "own", "TICO_AUTH_PROXY": "owner-link", "TICO_OWNER_LINK_HASH": hashlib.sha256(token.encode()).hexdigest(),
        "TICO_INSTALL_PRESET": "recommended", "TICO_SAMPLE_COMPANY": "on", "TICO_MODELS_PRESET": "subscriptions-gateway",
        "TICO_SUPPORT": "on", "TICO_GATEWAY_INVITE": INVITE, "TICO_TEAM_NAME": "Sample Co"}
    assert not {"TICO_OIDC_ISSUER", "TICO_OIDC_CLIENT_ID", "TICO_OIDC_CLIENT_SECRET", "TICO_ACCESS_ISSUER"} & set(env)
    # The link and the invite reach no output and no command; the invite only the server's .env, on stdin.
    cmds = "\n".join(shell.cmds())
    assert token not in out and token not in cmds and INVITE not in out and INVITE not in cmds
    # The invite stays on the server: setup's own saved copy of the .env leaves it out.
    saved, saved_env = state.load(DOMAIN)
    assert token not in json.dumps(saved) + saved_env and INVITE not in json.dumps(saved) + saved_env
    assert "TICO_GATEWAY_INVITE" not in saved_env and envfile.parse(saved_env)["TICO_HOSTED"] == "own"
    assert f"The owner's sign-in link is in {hosted.link_file(DOMAIN)} (0600): send it to ana@acme.example" in out
    assert "Open https://" not in out
    # The server's own computer: the Docker runner on the server's network, with a code that is never printed.
    computer = next(c for c in shell.cmds() if "backend.manage enrollment" in c)
    assert "--server-network tico_default" in computer and "--label 'Server computer'" in computer
    assert '--code "$TICO_JOIN_CODE"' in computer and 'rm -f "$code_file"' in computer
    assert "Server computer added" in out
    assert "Settings > Computers > Server computer > Sign in, to Claude and to ChatGPT" in out


def test_hosted_invited_without_the_preset_is_the_mark_and_the_link_alone():
    shell = FakeShell()
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "invited", "--company", "Acme",
                    *HOSTED], deps=over_ssh(shell))
    assert code == 0, out
    env = env_sent(shell)
    assert env["TICO_HOSTED"] == "invited" and env["TICO_AUTH_PROXY"] == "owner-link"
    assert not {"TICO_INSTALL_PRESET", "TICO_SUPPORT", "TICO_GATEWAY_INVITE"} & set(env)
    assert not any("backend.manage enrollment" in c for c in shell.cmds())
    assert "Settings > Computers > Add computer" in out


def test_a_server_computer_that_cannot_start_leaves_the_server_running_and_says_how_to_add_it():
    shell = FakeShell({"backend.manage enrollment": Result(1, "", "no such service: server")})
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", "--preset", "recommended",
                    *HOSTED], deps=over_ssh(shell))
    assert code == 0, out
    assert "The server computer did not start" in out and "Add it later: Settings > Computers > Add computer" in out
    assert "The owner's sign-in link is in" in out


def test_a_rerun_keeps_the_first_link():
    shell = FakeShell()
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", *HOSTED, "--company", "Acme"]
    assert go(argv, deps=over_ssh(shell))[0] == 0
    first = link_token()
    assert go(argv, deps=over_ssh(FakeShell()))[0] == 0
    assert link_token() == first


def test_the_dry_run_masks_the_invite_and_makes_no_link(monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
    code, out = go(["--dry-run", "--target", "aws", "--hosted", "own", "--preset", "recommended", *HOSTED])
    assert code == 0, out
    assert "TICO_GATEWAY_INVITE=********" in out and INVITE not in out
    assert "TICO_HOSTED=own" in out and "TICO_AUTH_PROXY=owner-link" in out and "TICO_SUPPORT=on" in out
    assert "single-use sign-in link" in out and "it connects on the first start" in out
    assert not hosted.link_file(DOMAIN).exists()


def test_no_support_turns_the_presets_support_off():
    code, out = go(["--dry-run", "--target", "aws", "--hosted", "own", "--preset", "recommended", "--no-support", *HOSTED])
    assert code == 0 and "TICO_SUPPORT=off" in out and "Tico Support off" in out


def test_self_hosted_installs_leave_the_mark_and_the_preset_unset(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", "GOCSPX-supersecret")
    monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
    code, out = go(["--dry-run", "--target", "aws", "--non-interactive", "--domain", "tico.example.com", "--auth", "google",
                    "--client-id", "cid.apps.googleusercontent.com", "--company", "Acme", "--owner-email", "me@example.com"])
    assert code == 0, out
    for key in ("TICO_HOSTED", "TICO_OWNER_LINK_HASH", "TICO_INSTALL_PRESET", "TICO_GATEWAY_INVITE", "TICO_SUPPORT"):
        assert key not in out
    assert "TICO_AUTH_PROXY=oidc" in out


@pytest.mark.parametrize("argv, wanted", [
    (["--hosted", "team"], "--hosted is own or invited"),
    (["--hosted", "own", "--preset", "everything"], "--preset is recommended"),
    (["--auth", "owner-link"], "--auth owner-link is for a server Tico hosts"),
    (["--hosted", "own", "--auth", "google"], "--hosted signs the owner in with the owner sign-in link"),
    (["--preset", "recommended"], "--preset recommended is for a server Tico hosts"),
])
def test_hosted_flags_that_do_not_go_together_stop_with_one_line(argv, wanted):
    code, out = go(["--dry-run", "--target", "aws", *argv, *HOSTED])
    assert code == 2 and out.strip().splitlines()[-1].startswith(wanted), out


@pytest.mark.parametrize("argv, wanted", [
    (["--non-interactive", "--domain", DOMAIN, "--owner-email", "ana@acme.example"], "--hosted needs --hosted-zone"),
    (["--non-interactive", "--domain", "tico.example.com", "--hosted-zone", ZONE, "--owner-email", "ana@acme.example"],
     f"--domain must be a name under {ZONE}"),
    (["--non-interactive", "--domain", ZONE, "--hosted-zone", ZONE, "--owner-email", "ana@acme.example"],
     f"--domain must be a name under {ZONE}"),
])
def test_the_owner_link_is_only_for_a_domain_under_our_zone(argv, wanted):
    code, out = go(["--target", "aws", "--yes", "--hosted", "own", *argv])
    assert code == 2 and wanted in out
    assert not hosted.link_file(argv[2]).exists()


def test_an_invite_that_is_not_one_stops_and_is_not_shown(monkeypatch):
    bad = "inv_ok-12345678\nTICO_AUTH_PROXY="
    monkeypatch.setenv("TICO_GATEWAY_INVITE", bad)
    code, out = go(["--dry-run", "--target", "aws", "--hosted", "own", "--preset", "recommended", *HOSTED])
    assert code == 2 and "TICO_GATEWAY_INVITE is not an invite code" in out
    assert "inv_ok-12345678" not in out


def test_the_owner_link_command_puts_a_new_link_in_place_and_can_sign_the_owner_out():
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", *HOSTED, "--company", "Acme"]
    assert go(argv, deps=over_ssh(FakeShell()))[0] == 0
    first = link_token()
    shell = FakeShell()
    code, out = go(["owner-link", "--domain", DOMAIN, "--sign-out"], deps=over_ssh(shell))
    assert code == 0, out
    token = link_token()
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert token != first and token not in out and all(token not in c for c in shell.cmds())
    [cmd] = [c for c in shell.cmds() if "backend.owner_link" in c]
    assert f"python -m backend.owner_link replace /data/hub.sqlite {digest} --sign-out" in cmd
    assert "signed out everywhere" in out
    _, saved_env = state.load(DOMAIN)
    assert envfile.parse(saved_env)["TICO_OWNER_LINK_HASH"] == digest


def test_when_the_server_refuses_the_new_link_the_old_one_stays():
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", *HOSTED, "--company", "Acme"]
    assert go(argv, deps=over_ssh(FakeShell()))[0] == 0
    first = link_token()
    shell = FakeShell({"backend.owner_link": Result(2, "", "This server does not use the owner sign-in link")})
    code, out = go(["owner-link", "--domain", DOMAIN], deps=over_ssh(shell))
    assert code == 1 and "the old one still works" in out and link_token() == first


def test_the_owner_link_command_is_refused_on_an_install_without_it():
    state.save("tico.example.com", {"target": "ssh", "domain": "tico.example.com", "auth": "google"}, "")
    code, out = go(["owner-link", "--domain", "tico.example.com"])
    assert code == 2 and "does not use the owner sign-in link" in out


def test_the_invite_is_not_kept_on_this_computer_after_a_new_link_either(monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "own", "--preset", "recommended", *HOSTED]
    first = FakeShell()
    assert go(argv, deps=over_ssh(first))[0] == 0
    assert env_sent(first)["TICO_GATEWAY_INVITE"] == INVITE
    code, out = go(["owner-link", "--domain", DOMAIN], deps=over_ssh(FakeShell()))
    assert code == 0, out
    _, saved_env = state.load(DOMAIN)
    assert INVITE not in saved_env and "TICO_GATEWAY_INVITE" not in saved_env
    assert envfile.parse(saved_env)["TICO_OWNER_LINK_HASH"] == hashlib.sha256(link_token().encode()).hexdigest()
    # A later run takes the invite from the environment only; without it, none is sent.
    monkeypatch.delenv("TICO_GATEWAY_INVITE")
    again = FakeShell()
    assert go(argv, deps=over_ssh(again))[0] == 0
    assert "TICO_GATEWAY_INVITE" not in env_sent(again)
