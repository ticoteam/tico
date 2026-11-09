"""Helpers that other tests share, moved out of test_wizard.py so they import no test file."""

import pytest
from setup import cli, verify
from setup.tests import fakes
from setup.tests.fakes import CaptureIO


SECRET = "GOCSPX-supersecret"


BASE = [
    "--non-interactive",
    "--domain",
    "tico.example.com",
    "--auth",
    "google",
    "--client-id",
    "cid.apps.googleusercontent.com",
    "--company",
    "Acme",
    "--owner-email",
    "me@example.com",
]


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("TICO_OIDC_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(verify, "run_all", lambda **kw: [verify.Check("stub", True, "ok")])


def go(argv, deps=None, io=None):
    io = io or CaptureIO()
    code = cli.main(argv, deps=deps or fakes.deps(), io=io)
    return code, io.text
