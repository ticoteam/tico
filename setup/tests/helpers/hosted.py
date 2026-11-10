"""Helpers that other tests share, moved out of test_hosted.py so they import no test file."""

import stat
from setup import cli, dns, envfile, hosted
from setup.tests import fakes
from setup.tests.fakes import CaptureIO


DOMAIN = "acme.hosted.example"


ZONE = "hosted.example"


INVITE = "inv_SECRET-0123456789"


HOSTED = ["--non-interactive", "--domain", DOMAIN, "--hosted-zone", ZONE, "--owner-email", "ana@acme.example"]


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
