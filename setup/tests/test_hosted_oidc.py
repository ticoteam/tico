"""`tico setup --hosted invited --auth oidc`: a server Tico hosts that signs people in with any OpenID Connect issuer (a
Tico account issuer, say) and keeps the owner's sign-in link as break-glass. The issuer, client id, button name and owner
email come from flags; the client secret only from TICO_OIDC_CLIENT_SECRET in the environment, and it is never printed,
saved in state.json or put in a command line."""
import hashlib
import json

import pytest

from setup import hosted, settings as st, signin, state, verify
from setup.tests.test_hosted import DOMAIN, HOSTED, INVITE, env_sent, go, link_token, over_ssh
from setup.tests.fakes import FakeShell

ISSUER = "https://accounts.example"
SECRET = "client-SECRET-0123456789abcdef"
OIDC = ["--auth", "oidc", "--issuer", ISSUER, "--client-id", "install-client-1", "--signin-label", "Tico account"]


@pytest.fixture(autouse=True)
def checks_pass(monkeypatch):
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])
    monkeypatch.delenv("TICO_GATEWAY_INVITE", raising=False)
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", SECRET)


def test_hosted_with_an_issuer_signs_in_there_and_keeps_the_owner_link_as_break_glass(monkeypatch):
    monkeypatch.setenv("TICO_GATEWAY_INVITE", INVITE)
    shell = FakeShell()
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "invited", "--preset",
                    "recommended", *OIDC, *HOSTED], deps=over_ssh(shell))
    assert code == 0, out
    env = env_sent(shell)
    token = link_token()
    assert {k: env.get(k) for k in ("TICO_HOSTED", "TICO_AUTH_PROXY", "TICO_OIDC_ISSUER", "TICO_OIDC_CLIENT_ID",
                                    "TICO_OIDC_CLIENT_SECRET", "TICO_OIDC_LABEL", "TICO_OWNER_EMAIL",
                                    "TICO_OWNER_LINK_HASH", "TICO_INSTALL_PRESET")} == {
        "TICO_HOSTED": "invited", "TICO_AUTH_PROXY": "oidc", "TICO_OIDC_ISSUER": ISSUER,
        "TICO_OIDC_CLIENT_ID": "install-client-1", "TICO_OIDC_CLIENT_SECRET": SECRET, "TICO_OIDC_LABEL": "Tico account",
        "TICO_OWNER_EMAIL": "ana@acme.example", "TICO_OWNER_LINK_HASH": hashlib.sha256(token.encode()).hexdigest(),
        "TICO_INSTALL_PRESET": "recommended"}
    # The secret and the link reach no output and no command line; the secret only the .env on stdin.
    cmds = "\n".join(shell.cmds())
    assert SECRET not in out and SECRET not in cmds and token not in out and token not in cmds
    saved, saved_env = state.load(DOMAIN)
    assert SECRET not in json.dumps(saved) and token not in json.dumps(saved) + saved_env
    assert saved["auth"] == "oidc" and saved["issuer"] == ISSUER and saved["signin_label"] == "Tico account"
    # The ready path names the button and the owner; the link is for our team only, by its file.
    assert 'Open https://acme.hosted.example and press "Sign in with Tico account" as ana@acme.example, the owner.' in out
    assert f"the owner's sign-in link is in {hosted.link_file(DOMAIN)} (0600)" in out and "not for the ready email" in out
    assert "send it to" not in out


def test_a_rerun_keeps_the_issuer_and_the_first_link():
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "invited", "--company", "Acme", *HOSTED]
    assert go([*argv, *OIDC], deps=over_ssh(FakeShell()))[0] == 0
    first = link_token()
    again = FakeShell()
    code, out = go(argv, deps=over_ssh(again))         # no --auth: the saved choice holds
    assert code == 0, out
    env = env_sent(again)
    assert env["TICO_AUTH_PROXY"] == "oidc" and env["TICO_OIDC_ISSUER"] == ISSUER and env["TICO_OIDC_CLIENT_SECRET"] == SECRET
    assert link_token() == first


def test_the_dry_run_plan_names_the_issuer_and_the_button_and_masks_the_secret():
    code, out = go(["--dry-run", "--target", "aws", "--hosted", "invited", *OIDC, *HOSTED])
    assert code == 0, out
    assert SECRET not in out and "TICO_OIDC_CLIENT_SECRET=********" in out
    assert f'Sign-in: OpenID Connect OIDC at {ISSUER}, button "Sign in with Tico account"' in out
    assert "redirect URI must be exactly https://acme.hosted.example/auth/callback" in out
    assert "Break-glass: the owner's single-use sign-in link" in out and "not for the ready email" in out
    assert not hosted.link_file(DOMAIN).exists()


def test_the_owner_link_command_works_on_a_hosted_oidc_install():
    argv = ["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--hosted", "invited", "--company", "Acme", *OIDC, *HOSTED]
    assert go(argv, deps=over_ssh(FakeShell()))[0] == 0
    shell = FakeShell()
    code, out = go(["owner-link", "--domain", DOMAIN], deps=over_ssh(shell))
    assert code == 0, out
    assert any("python -m backend.owner_link replace" in c for c in shell.cmds())
    _, saved_env = state.load(DOMAIN)
    assert "TICO_AUTH_PROXY=oidc" in saved_env


def test_a_self_hosted_install_takes_any_issuer_with_no_owner_link():
    shell = FakeShell()
    code, out = go(["--target", "ssh", "--ssh", "root@203.0.113.7", "--yes", "--non-interactive", "--domain",
                    DOMAIN, "--company", "Acme", "--owner-email", "me@example.com", *OIDC], deps=over_ssh(shell))
    assert code == 0, out
    env = env_sent(shell)
    assert env["TICO_AUTH_PROXY"] == "oidc" and env["TICO_OIDC_ISSUER"] == ISSUER and env["TICO_OIDC_LABEL"] == "Tico account"
    assert not {"TICO_HOSTED", "TICO_OWNER_LINK_HASH"} & set(env)
    assert SECRET not in out and f"Open https://{DOMAIN} and sign in as me@example.com." in out


@pytest.mark.parametrize("argv, wanted", [
    (["--auth", "oidc", "--issuer", "http://accounts.example", "--client-id", "c"], "--issuer: 'http://accounts.example' is not valid"),
    (["--auth", "oidc", "--issuer", "https://accounts.example/?x=1", "--client-id", "c"], "is not valid"),
    (["--auth", "google", "--signin-label", "Tico account"], "--signin-label is for --auth oidc"),
    ([*OIDC[:-1], "Tico <b>"], "--signin-label: 'Tico <b>' is not valid"),
])
def test_an_issuer_that_is_not_https_or_flags_for_another_provider_stop_with_one_line(argv, wanted):
    code, out = go(["--dry-run", "--target", "aws", "--hosted", "invited", *argv, *HOSTED]
                   if "google" not in argv else ["--dry-run", "--target", "aws", *argv, *HOSTED])
    assert code == 2 and wanted in out, out


def test_without_an_issuer_a_non_interactive_run_stops():
    code, out = go(["--target", "aws", "--yes", "--hosted", "invited", "--auth", "oidc", "--client-id", "c", *HOSTED])
    assert code == 2 and "Missing --issuer" in out


def test_without_the_secret_in_the_environment_a_non_interactive_run_stops(monkeypatch):
    monkeypatch.delenv("TICO_OIDC_CLIENT_SECRET")
    code, out = go(["--target", "aws", "--yes", "--hosted", "invited", *OIDC, *HOSTED])
    assert code == 2 and "Missing --client-secret (or set TICO_OIDC_CLIENT_SECRET)" in out


def test_the_check_after_install_wants_the_redirect_at_the_issuer():
    def get(url, follow=False):
        return 302, {"Location": "https://accounts.example/authorize?client_id=install-client-1&redirect_uri="
                                 "https%3A%2F%2Facme.hosted.example%2Fauth%2Fcallback"}, b""
    ok = verify.check_signin(DOMAIN, "oidc", "install-client-1", get=get, issuer=ISSUER)
    assert ok.ok, ok
    wrong = verify.check_signin(DOMAIN, "oidc", "install-client-1", get=get, issuer="https://other.example")
    assert not wrong.ok and "Expected a redirect to other.example" in wrong.hint
    assert signin.issuer_host("oidc", "") == "" and signin.issuer_host("google") == "accounts.google.com"


def test_hosted_gather_keeps_owner_link_alone_without_auth():
    class Args:
        hosted, preset, auth, hosted_zone = "own", "", None, "hosted.example"
    s = st.Settings(domain=DOMAIN)
    hosted.gather(Args, s, dry=False)
    assert s.auth == st.OWNER_LINK


def test_separate_bot_computer_does_not_enroll_a_runner_on_the_server(monkeypatch):
    monkeypatch.setenv('TICO_GATEWAY_INVITE', INVITE)
    def unexpected(*args):
        raise AssertionError('server-only install must not enroll a computer')
    monkeypatch.setattr(hosted, 'add_server_computer', unexpected)
    code, out = go(['--target','ssh','--ssh','root@203.0.113.7','--yes','--hosted','invited',
                    '--preset','recommended','--server-computer','none',*OIDC,*HOSTED], deps=over_ssh(FakeShell()))
    assert code == 0, out
