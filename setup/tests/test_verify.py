
from setup import verify
from setup.remote import Result
from setup.tests.fakes import FakeShell

GOOGLE = ("https://accounts.google.com/o/oauth2/v2/auth?client_id=cid&response_type=code"
          "&redirect_uri=https%3A%2F%2Ftico.example.com%2Fauth%2Fcallback&scope=openid+email")


def getter(code, location="", **_):
    return lambda url, follow=True: (code, {"Location": location}, "")


def test_signin_redirect_ok_only_with_exact_redirect_uri_and_provider_host():
    c = verify.check_signin("tico.example.com", "google", "cid", get=getter(302, GOOGLE))
    assert c.ok and "auth/callback" in c.detail


def test_health():
    assert verify.check_health("t", get=lambda u: (200, {}, "")).ok
    c = verify.check_health("t", get=lambda u: (502, {}, ""))
    assert not c.ok and "docker compose logs server" in c.hint

    def boom(u):
        raise OSError("refused")
    assert not verify.check_health("t", get=boom).ok


def test_env_permissions():
    assert verify.check_env_perms(FakeShell({"stat": Result(0, "600\n")})).ok
    c = verify.check_env_perms(FakeShell({"stat": Result(0, "644\n")}))
    assert not c.ok and "chmod 600" in c.hint


def _run(monkeypatch, results, wait, provider="google"):
    """results: successive (tls_ok, health_ok) outcomes; the last repeats."""
    seq, slept = iter(results), []
    state = {"cur": (True, True)}

    def tls(domain):
        state["cur"] = next(seq, state["cur"])
        return verify.Check("HTTPS certificate", state["cur"][0], "x")

    monkeypatch.setattr(verify, "check_dns", lambda *a: verify.Check("DNS", True, "ok"))
    monkeypatch.setattr(verify, "check_tls", tls)
    monkeypatch.setattr(verify, "check_health", lambda d: verify.Check("/healthz", state["cur"][1], "x"))
    monkeypatch.setattr(verify, "check_signin", lambda *a: verify.Check("Sign-in redirect", state["cur"][1], "x"))
    said = []
    out = verify.run_all(domain="t.example.com", provider=provider, client_id="c", front_door="caddy", records=[], resolvers=[],
                         shell=None, wait_https=wait, sleep=slept.append, say=said.append)
    return out, slept, said


def test_runner_hostname_must_answer_from_the_server_not_a_login():
    c = verify.check_runner_host("r.example.com", get=getter(200))
    assert c.ok
    c = verify.check_runner_host("r.example.com", get=getter(302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/r.example.com"))
    assert not c.ok and "team.cloudflareaccess.com" in c.detail and "Access application" in c.hint
    c = verify.check_runner_host("r.example.com", get=getter(404))
    assert not c.ok and "path ^/(?:api/v2|download)" in c.hint
    seen = []
    verify.check_runner_host("r.example.com", get=lambda url, follow=True: seen.append((url, follow)) or (200, {}, ""))
    assert seen == [("https://r.example.com/api/v2/agents/setup-script", False)]


def test_run_all_checks_the_runner_hostname_only_when_there_is_one(monkeypatch):
    monkeypatch.setattr(verify, "check_runner_host", lambda h: verify.Check("Runner hostname", True, h))
    monkeypatch.setattr(verify, "check_dns", lambda *a: verify.Check("DNS", True, "ok"))
    monkeypatch.setattr(verify, "_https_checks", lambda *a: [])
    kw = dict(domain="t.example.com", provider="cloudflare", client_id="", front_door="cloudflared", records=[], resolvers=[], shell=None)
    assert [c.name for c in verify.run_all(**kw)] == ["DNS"]
    assert [c.detail for c in verify.run_all(**kw, runner_host="r.example.com") if c.name == "Runner hostname"] == ["r.example.com"]
