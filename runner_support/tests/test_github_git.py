"""A bot's git push with a GitHub App token, end to end, the way a bot's turn gets one: the hub mints an installation
token (backend/github_app.py, POST /app/installations/{id}/access_tokens), the real Rust runner hands it to the turn as
its credential helper (`tico-runner git-credential` and the credential socket, runner-rs/src/gitcred.rs), and plain git
in the turn clones the repository, commits, pushes a branch and deletes it (credential_use.py `GIT`). This is what
GitHub's token format change broke on Nov 14 2026 (stateless `ghs_<app id>_<JWT>` tokens, about 520 characters).

Two scenarios, both slow (the release binary and a runner process):
- offline, every release: a fake GitHub mints stateless tokens and a local HTTPS git server takes a push only with
  `x-access-token:<the minted token>`. No network.
- live, a release check: the real GitHub and a real App, which needs, from the environment (release validation passes
  them from repository secrets of the same names; .github/workflows/ci.yml):
      TICO_GIT_CHECK_APP_ID           the App's numeric id
      TICO_GIT_CHECK_PRIVATE_KEY      the App's private key (PEM text)
      TICO_GIT_CHECK_REPOSITORY       owner/name of a scratch repository the App is installed on (Contents: write)
      TICO_GIT_CHECK_INSTALLATION_ID  optional: the installation's id (otherwise found from the owner's name)
  Without them it skips, naming what to set. It leaves nothing behind: its branch `tico-git-check-<random>` is
  deleted by the turn, or by the test through the API when the turn could not."""

import base64
import datetime
import hashlib
import http.server
import json
import os
import secrets
import ssl
import subprocess
import threading

import httpx
import pytest

from backend import github_app as G
from backend.repositories import note_chosen, sync
from backend.tests.helpers.github_app import FakeGitHub, PEM, stateless_token
from backend.tests.live import live  # noqa: F401
from runner_support.tests.helpers.conformance import computer, rust_binary  # noqa: F401
from runner_support.tests.helpers.credential_turns import nowhere, turn

pytestmark = pytest.mark.slow

LIVE = ("TICO_GIT_CHECK_APP_ID", "TICO_GIT_CHECK_PRIVATE_KEY", "TICO_GIT_CHECK_REPOSITORY")
OFFLINE_REPOSITORY = "Acme/tico-git-check"


@pytest.fixture
def harness():
    return "claude"


def connect_app(api, app_id, pem, repository, installation=None):
    """The company's GitHub App as the manifest flow leaves it (G.GitHubApp.save), on `repository`'s owner, and the
    ops bot pointed at `repository` as a person does in its settings; then the repositories the App reaches, as Tico
    syncs them after a connect."""
    service = api.app.state.github_app
    owner = repository.split("/")[0]
    conversion = {
        "id": int(app_id),
        "slug": "tico-git-check",
        "client_id": "tico-git-check",
        "pem": pem,
        "owner": {"login": owner, "type": "Organization"},
    }
    with service.store.transaction() as c:
        service.save(c, "human:ana", owner, False, conversion)
        if installation:
            c.execute("UPDATE github_app SET installation_id=? WHERE id='app'", (int(installation),))
        c.execute("UPDATE bot_config SET repo=? WHERE bot='ops'", (repository,))
        assert c.execute("SELECT changes()").fetchone()[0] == 1, "the ops bot has no configuration row"
        note_chosen(c, repository)
    assert service.installation(), f"the App is not installed on {owner}"
    assert sync(service) == "synced", "Tico could not list the repositories the App reaches"
    return service


def minted(service):
    """Every installation token the hub holds (backend/github_app.py mint's cache): the values that must not show."""
    with service.lock:
        return [entry["token"] for entry in service.cache.values()]


def git_check(api, computer, url, branch):
    """The turn's `GIT` use, without the turn's environment (it holds the token: never in an assertion message)."""
    _, uses, conversation = turn(api, computer, f"GIT {url} {branch}")
    found = [{k: v for k, v in u.items() if k != "environ"} for u in uses if u["use"] == "git"]
    assert len(found) == 1, f"{len(found)} git uses"
    return found[0], conversation


def assert_pushed(use, tokens):
    credential = use["credential"]
    assert credential["username"] == "x-access-token", credential
    assert credential["sha256"] in {hashlib.sha256(t.encode()).hexdigest() for t in tokens}, (
        "git's credential helper gave a value the hub never minted",
        credential,
    )
    steps = use["steps"]
    failed = next((s for s in steps if s["code"] != 0), None)
    assert failed is None, f"git {' '.join(failed['argv'][-3:])} failed: {failed['stderr']}" if failed else ""
    assert len(steps) == 8, steps
    pushed, gone = steps[5]["stdout"], steps[7]["stdout"]
    assert use["branch"] in pushed, "the pushed branch was not on the remote"
    assert gone.strip() == "", "the branch is still on the remote after its delete"


# ---------------------------------------------------------------- offline: a fake GitHub and a local git server


def self_signed(directory):
    """A certificate and key for 127.0.0.1, in `directory`: (certificate path, key path)."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "git-server.pem", directory / "git-server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


class GitServer:
    """Git's smart HTTP over HTTPS (`git http-backend`) for the repositories under `root`, taking a request only with
    Basic `x-access-token:<token>` for a token in `tokens` (GitHub's rule for an App token); 401 otherwise."""

    def __init__(self, root, tokens, cert, key):
        self.root, self.tokens, self.refused, self.accepted = root, tokens, 0, 0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.serve(self)

            do_POST = do_GET

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def serve(self, request):
        given = request.headers.get("Authorization", "")
        allowed = {"Basic " + base64.b64encode(f"x-access-token:{t}".encode()).decode() for t in list(self.tokens)}
        if given not in allowed:
            self.refused += 1
            request.send_response(401)
            request.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
            request.send_header("Content-Length", "0")
            request.end_headers()
            return
        self.accepted += 1
        path, _, query = request.path.partition("?")
        body = request.rfile.read(int(request.headers.get("Content-Length") or 0))
        env = {
            "GIT_PROJECT_ROOT": str(self.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "REQUEST_METHOD": request.command,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_TYPE": request.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": "x-access-token",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().splitlines():
            name, _, value = line.partition(": ")
            if name.lower() == "status":
                status = int(value.split()[0])
            elif name:
                headers.append((name, value))
        request.send_response(status)
        for name, value in headers:
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(payload)))
        request.end_headers()
        request.wfile.write(payload)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """A fake GitHub minting stateless tokens, and the scratch repository on a local git server that knows them."""
    fake = FakeGitHub()
    fake.installations = [{"id": 77, "account": {"login": "Acme"}}]
    fake.repositories = [{"full_name": OFFLINE_REPOSITORY, "default_branch": "main"}]
    issued = []

    def mint():
        issued.append(stateless_token())
        return issued[-1]

    fake.new_token = mint
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake))
    root = tmp_path / "git-server"
    bare = root / (OFFLINE_REPOSITORY + ".git")
    seed = tmp_path / "seed"
    ident = ["-c", "user.name=Acme", "-c", "user.email=acme@acme.invalid"]
    for argv in (
        ["init", "-q", "--bare", "-b", "main", str(bare)],
        ["-C", str(bare), "config", "http.receivepack", "true"],
        ["init", "-q", "-b", "main", str(seed)],
        ["-C", str(seed), *ident, "commit", "-q", "--allow-empty", "-m", "Start"],
        ["-C", str(seed), "push", "-q", str(bare), "main"],
    ):
        subprocess.run(["git", *argv], check=True, capture_output=True)
    cert, key = self_signed(tmp_path)
    server = GitServer(root, issued, cert, key)
    yield server, issued, cert
    server.stop()


@pytest.fixture
def setup(request):
    """The computer's settings: the offline scenario's git host, for its harness (credential_use.py `GIT`)."""
    if "offline" not in request.fixturenames:
        return {}
    server, _, cert = request.getfixturevalue("offline")
    return {"env": {"FAKE_GIT_HOST": server.host, "FAKE_GIT_CAINFO": str(cert)}}


def test_a_stateless_app_token_pushes_through_the_runners_credential_helper(api, offline, computer):
    server, issued, _ = offline
    service = connect_app(api, 4242, PEM, OFFLINE_REPOSITORY, installation=77)
    computer.start()
    branch = "tico-git-check-" + secrets.token_hex(4)
    use, conversation = git_check(api, computer, f"https://{server.host}/{OFFLINE_REPOSITORY}.git", branch)
    assert issued and all(len(t) > 400 and t.count(".") == 2 for t in issued), "the fake did not mint stateless tokens"
    assert use["credential"]["length"] == len(issued[-1]), use["credential"]
    assert_pushed(use, issued)
    assert server.refused >= 1 and server.accepted >= 4, (server.refused, server.accepted)  # asked, then answered
    nowhere(api, computer, conversation, *minted(service))


# ---------------------------------------------------------------- live: the real GitHub, a release check


MISSING = [name for name in LIVE if not os.environ.get(name)]


def live_config():
    repository = os.environ["TICO_GIT_CHECK_REPOSITORY"].strip()
    assert repository.count("/") == 1, "TICO_GIT_CHECK_REPOSITORY is owner/name"
    pem = os.environ["TICO_GIT_CHECK_PRIVATE_KEY"].replace("\\n", "\n").strip() + "\n"
    return (
        os.environ["TICO_GIT_CHECK_APP_ID"].strip(),
        pem,
        repository,
        os.environ.get("TICO_GIT_CHECK_INSTALLATION_ID"),
    )


def delete_branch(service, repository, branch):
    """The branch gone through the API, whatever the turn managed (a failed run leaves nothing behind)."""
    try:
        token, _ = service.mint([repository], {"contents": "write", "metadata": "read"})
        httpx.delete(
            f"{G.API}/repos/{repository}/git/refs/heads/{branch}",
            headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
            timeout=20,
        )
    except Exception:  # pragma: no cover - cleanup after a failure already reported
        pass


@pytest.mark.skipif(
    bool(MISSING),
    reason="The live git check needs a GitHub App installed on a scratch repository (Contents: write): set "
    + ", ".join(MISSING)
    + " (TICO_GIT_CHECK_INSTALLATION_ID optional); release validation passes repository secrets of those names",
)
def test_a_real_app_token_pushes_to_github_through_the_runners_credential_helper(api, computer):
    app_id, pem, repository, installation = live_config()
    service = connect_app(api, app_id, pem, repository, installation)
    computer.start()
    branch = "tico-git-check-" + secrets.token_hex(4)
    try:
        use, conversation = git_check(api, computer, f"https://github.com/{repository}.git", branch)
        tokens = minted(service)
        assert tokens and all(t.startswith("ghs_") for t in tokens), "the hub minted no installation token"
        assert_pushed(use, tokens)
        nowhere(api, computer, conversation, *tokens)
    finally:
        delete_branch(service, repository, branch)
