"""The `local` target: what scripts/install.sh runs on the server itself."""

from setup import dns, state
from setup.tests import fakes
from setup.tests.fakes import FakeShell
from setup.tests.helpers.wizard import BASE, SECRET, go  # noqa: F401  (SECRET fixture env is autouse in that module)

import pytest


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    from setup import verify

    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])


def local_deps(shell, ip="203.0.113.7"):
    r = fakes.FakeResolver({("tico.example.com", dns.A): [ip]}, {"example.com": fakes.NETLIFY_NS})
    d = fakes.deps()
    d.resolver, d.public_resolvers, d.local_shell = r, (lambda: [("Google", r)]), (lambda: shell)
    d.public_ip = lambda: ip
    return d


def test_local_run_pins_the_release_and_never_uses_ssh(monkeypatch):
    shell = FakeShell()
    d = local_deps(shell)
    d.ssh_shell = lambda *a: pytest.fail("the local target must not open an SSH connection")
    code, out = go(["--target", "local", "--tico-version", "v0.2.0", "--yes", *BASE], deps=d)
    assert code == 0, out
    env_input = next(i for c, i in shell.calls if i and b"TICO_DOMAIN" in i).decode()
    assert "TICO_TAG=v0.2.0" in env_input and "COMPOSE_PROFILES=caddy,updater" in env_input
    assert not any(SECRET in c for c in shell.cmds()) and SECRET not in out
    assert state.load("tico.example.com")[0]["tag"] == "v0.2.0"
