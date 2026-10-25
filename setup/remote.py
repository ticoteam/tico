"""Doing things on the server: a command runner (SSH or SSM), the bootstrap script, and the install steps."""
from __future__ import annotations

import base64
import gzip
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import contract


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Shell(Protocol):
    def run(self, cmd: str, input: bytes | None = None) -> Result: ...


class SSHShell:
    """Uses the user's own ssh, key and agent; nothing is stored."""

    def __init__(self, host: str, port: int = 22, identity: str = "", timeout: int = 900):
        self.host, self.port, self.identity, self.timeout = host, port, identity, timeout

    def argv(self, cmd: str) -> list[str]:
        a = ["ssh", "-p", str(self.port), "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30"]
        if self.identity:
            a += ["-i", self.identity]
        return a + [self.host, cmd]

    def run(self, cmd: str, input: bytes | None = None) -> Result:
        p = subprocess.run(self.argv(cmd), input=input, capture_output=True, timeout=self.timeout)
        return Result(p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace"))


class LocalShell:
    """The machine the wizard runs on (scripts/install.sh): the same steps as over SSH, without the SSH."""

    def run(self, cmd: str, input: bytes | None = None) -> Result:
        p = subprocess.run(["sh", "-c", cmd], input=input, capture_output=True, timeout=900)
        return Result(p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace"))


class SSMShell:
    """Shell over AWS Systems Manager: no SSH key, no port 22. Commands must not carry secrets (SSM logs them)."""

    def __init__(self, ssm, instance_id: str, sleep: Callable[[float], None] = time.sleep):
        self.ssm, self.iid, self.sleep = ssm, instance_id, sleep

    def run(self, cmd: str, input: bytes | None = None, *, ready_timeout: int = 0) -> Result:
        if input is not None:
            raise ValueError("SSMShell does not pass stdin")
        deadline = time.monotonic() + ready_timeout
        while True:
            if ready_timeout and time.monotonic() >= deadline:
                return Result(124, "", "timed out waiting for Systems Manager")
            try:
                online = not ready_timeout or any(
                    row.get("PingStatus") == "Online" for row in self.ssm.describe_instance_information(
                        Filters=[{"Key": "InstanceIds", "Values": [self.iid]}])["InstanceInformationList"])
                if online:
                    sent = self.ssm.send_command(InstanceIds=[self.iid], DocumentName="AWS-RunShellScript",
                                                 Parameters={"commands": [cmd]}, TimeoutSeconds=900)
                    break
            except Exception as exc:
                # Online can precede Run Command registration. Retry only this pre-acceptance refusal,
                # never a failed/uncertain invocation, an explicit access denial, or a transport failure.
                if not ready_timeout or getattr(exc, "response", {}).get("Error", {}).get("Code") != "InvalidInstanceId":
                    raise
            self.sleep(min(5, max(0, deadline - time.monotonic())))
        cid = sent["Command"]["CommandId"]
        for _ in range(450):
            self.sleep(2)
            try:
                inv = self.ssm.get_command_invocation(CommandId=cid, InstanceId=self.iid)
            except self.ssm.exceptions.InvocationDoesNotExist:
                continue
            if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
                code = inv.get("ResponseCode", 0 if inv["Status"] == "Success" else 1)
                return Result(code, inv.get("StandardOutputContent", ""), inv.get("StandardErrorContent", ""))
        return Result(124, "", "timed out waiting for the command")


class StepFailed(RuntimeError):
    pass


def fetch_and_run(url: str, args: str = "") -> str:
    """Downloads an installer to a temp file and runs it with sh, on one line. Never `curl | sh`: the scripts run
    under sh or bash `set -eu` without pipefail, where a failed download feeds sh nothing and "succeeds".
    Each failure exits on its own, since under `set -e` a failure on the left of `&&` does not stop the script."""
    u = shlex.quote(url)
    run = f'sh "$f" {args}'.rstrip()
    return (f'f=$(mktemp) || exit 1; if ! curl -fsSL {u} -o "$f"; then rm -f "$f"; '
            f'echo "tico-setup: could not download {url}" >&2; exit 1; fi; '
            f'{run} || {{ rc=$?; rm -f "$f"; exit "$rc"; }}; rm -f "$f"')


def _sh(cmd: str, root: bool) -> str:
    return f"sudo -n sh -c {shlex.quote(cmd)}" if root else cmd


@dataclass
class Step:
    description: str
    run: Callable[[], None]


DOCKER_OK = "docker compose version >/dev/null 2>&1"


class Host:
    """One SSH host: run as root (sudo when needed), install Docker once."""

    def __init__(self, r: Shell, say: Callable[[str], None] = print, scrub: Callable[[str], str] = lambda s: s):
        self.r, self.say, self.scrub, self.root = r, say, scrub, False

    def ex(self, cmd: str, what: str, input: bytes | None = None) -> Result:
        res = self.r.run(_sh(cmd, not self.root), input)
        if res.returncode:
            raise StepFailed(f"{what} failed (exit {res.returncode}): {self.scrub((res.stderr or res.stdout).strip()[-600:])}")
        return res

    def connect(self) -> None:
        res = self.r.run("id -u")
        if res.returncode and isinstance(self.r, LocalShell):
            raise StepFailed("Could not run commands on this machine: " + self.scrub(res.stderr.strip()[-400:]))
        if res.returncode:
            raise StepFailed("Could not connect over SSH: " + self.scrub(res.stderr.strip()[-400:])
                             + "\nCheck `ssh <host>` works from this terminal with your key or agent.")
        self.root = res.stdout.strip() == "0"
        if not self.root and self.r.run("sudo -n true").returncode:
            raise StepFailed("This user cannot run sudo without a password. Use root, or a user with passwordless sudo.")

    def docker(self) -> None:
        if self.r.run(_sh(DOCKER_OK, not self.root)).returncode == 0:
            self.say("    Docker and the Compose plugin are already installed.")
            return
        self.say("    Installing Docker (get.docker.com) ...")
        self.ex(fetch_and_run("https://get.docker.com") + " && systemctl enable --now docker", "Installing Docker")
        self.ex(DOCKER_OK, "Docker Compose plugin check (needs Compose v2)")

    def write_private(self, path: str, data: bytes, what: str) -> None:
        self.ex(f"umask 077; cat > {path}.new && mv {path}.new {path} && chmod 600 {path}", what, data)


def ssh_steps(r: Shell, *, env_text: str, compose: bytes | None, compose_url: str,
              say: Callable[[str], None] = print, scrub: Callable[[str], str] = lambda s: s,
              company_file_data: str = "") -> list[Step]:
    """Idempotent: each step checks before it changes, so a re-run only does what is missing."""
    d = contract.REMOTE_DIR
    h = Host(r, say, scrub)

    def files() -> None:
        h.ex(f"mkdir -p {d} && chmod 755 {d}", "Creating " + d)
        h.ex(" && ".join(company_file_dir_lines()), "Creating the private starter plan folder")
        if company_file_data:
            f = f"{d}/{contract.COMPANY_FILE_DIR}/{contract.COMPANY_FILE_NAME}"
            sha = hashlib.sha256(company_file_data.encode("ascii")).hexdigest()
            # The payload travels only on SSH stdin; verify and set the container user's ownership before rename.
            h.ex(f"umask 077; cat > {f}.new && "
                 f'test "$(sha256sum {f}.new | cut -d\" \" -f1)" = {sha} && '
                 f"chown 10001:10001 {f}.new && chmod 600 {f}.new && mv {f}.new {f} "
                 f"|| {{ rm -f {f}.new; exit 1; }}", "Writing the starter plan", company_file_data.encode("ascii"))
        if compose is not None:
            h.ex(f"cat > {d}/compose.yaml.new && mv {d}/compose.yaml.new {d}/compose.yaml", "Writing compose.yaml", compose)
        else:
            h.ex(f"curl -fsSL {shlex.quote(compose_url)} -o {d}/compose.yaml.new && mv {d}/compose.yaml.new {d}/compose.yaml",
                 "Downloading compose.yaml")
        want = hashlib.sha256(env_text.encode()).hexdigest()
        cur = r.run(_sh(f"sha256sum {d}/.env 2>/dev/null", not h.root)).stdout.split()[:1]
        if cur == [want]:
            say("    .env is already up to date.")
            h.ex(f"chmod 600 {d}/.env", "Setting .env permissions")
            return
        h.write_private(f"{d}/.env", env_text.encode(), "Writing .env")

    def up() -> None:
        if company_file_data:
            h.ex("\n".join(["set -eu", f"cd {d}", "docker compose pull --quiet",
                             *plan_read_lines(running=False), "docker compose up -d", *plan_read_lines()]),
                 "Starting Tico with the selected starter plan")
        else:
            h.ex(f"cd {d} && docker compose pull --quiet && docker compose up -d", "docker compose up -d")

    return [
        Step("Check this machine's access (root or sudo)" if isinstance(r, LocalShell) else "Connect over SSH and check sudo", h.connect),
        Step("Install Docker + Compose if missing", h.docker),
        Step(f"Write {d}/compose.yaml and {d}/.env (mode 0600)", files),
        Step("docker compose pull && docker compose up -d", up),
    ]


def _b64gz(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data, 9, mtime=0)).decode()


def docker_install_lines(os_family: str, need_aws_cli: bool) -> list[str]:
    if os_family == "al2023":
        return ["dnf install -y docker awscli >/dev/null", "systemctl enable --now docker",
                "mkdir -p /usr/local/lib/docker/cli-plugins",
                'curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)"'
                " -o /usr/local/lib/docker/cli-plugins/docker-compose", "chmod +x /usr/local/lib/docker/cli-plugins/docker-compose"]
    lines = [f"if ! docker compose version >/dev/null 2>&1; then {fetch_and_run('https://get.docker.com')}; fi",
             "systemctl enable --now docker"]
    return lines + (["command -v aws >/dev/null || snap install aws-cli --classic"] if need_aws_cli else [])


# Executed by the existing bootstrap only after ordinary app startup succeeds.
# Keeping the finite source here also lets tests exercise the real installer
# boundary with an in-memory release archive and no Docker or network authority.
VISUAL_BOOTSTRAP = r'''
import hashlib,io,json,os,pathlib,re,subprocess,tarfile,tempfile,urllib.parse,urllib.request

def pairs(items):
    value={}
    for key,item in items:
        if key in value:raise ValueError()
        value[key]=item
    return value

def fetch(url,limit):
    # GitHub serves release bytes through its fixed asset host. Only that
    # credential-free HTTPS redirect is allowed, never a caller destination.
    class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
        max_redirections=3
        def redirect_request(self,request,fp,code,message,headers,url):
            target=urllib.parse.urlsplit(url)
            if target.scheme!='https' or target.hostname!='release-assets.githubusercontent.com' or target.username or target.password or target.port not in (None,443):raise ValueError()
            return super().redirect_request(request,fp,code,message,headers,url)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),ReleaseRedirect())
    with opener.open(url,timeout=5) as response:
        raw=response.read(limit+1)
        if len(raw)>limit:raise ValueError()
        return raw

def install(directory,version,runtime,fetch=fetch):
    root=pathlib.Path(directory)
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?',version):raise ValueError()
    if not isinstance(runtime,dict) or set(runtime)!={'revision','runtime_files'} or not re.fullmatch(r'[0-9a-f]{40}',runtime['revision']):raise ValueError()
    if root.resolve()!=root or root.stat().st_uid!=os.geteuid() or root.stat().st_mode&0o022:raise ValueError()
    name='tico-bundle-'+version+'.tar.gz'
    base='https://github.com/ticoteam/tico/releases/download/'+version+'/'
    sums=fetch(base+'SHA256SUMS',1048576).decode('ascii')
    matches=[line.split()[0] for line in sums.splitlines() if len(line.split())==2 and line.split()[1].lstrip('*')==name]
    if len(matches)!=1 or not re.fullmatch(r'[0-9a-f]{64}',matches[0]):raise ValueError()
    blob=fetch(base+name,8388608)
    if hashlib.sha256(blob).hexdigest()!=matches[0]:raise ValueError()
    files={}
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:gz') as archive:
        total=0
        for member in archive:
            if not member.isfile() or member.name in files or member.name.startswith('/') or '..' in member.name.split('/'):raise ValueError()
            total+=member.size
            if member.size>2097152 or total>8388608:raise ValueError()
            files[member.name]=archive.extractfile(member).read()
    manifest=json.loads(files['installer-manifest.json'],object_pairs_hook=pairs)
    if set(manifest)!={'protocol','revision','files','runtime_files'} or manifest['protocol']!='tico-hosted-assets-v1':raise ValueError()
    if manifest['revision']!=runtime['revision'] or manifest['runtime_files']!=runtime['runtime_files']:raise ValueError()
    required={'compose.yaml','setup/__init__.py','setup/visual_config.py','setup/visual_contract.py','setup/envfile.py','setup/contract.py','setup/cli.py','setup/settings.py'}
    if not isinstance(manifest['files'],dict) or not required<=set(manifest['files']) or len(manifest['files'])>100:raise ValueError()
    for path,digest in manifest['files'].items():
        if not (path=='compose.yaml' or re.fullmatch(r'setup/[a-z_][a-z0-9_]*\.py',path)):raise ValueError()
        if hashlib.sha256(files[path]).hexdigest()!=digest:raise ValueError()
    if files.get('VERSION',b'').decode().strip()!=version[1:]:raise ValueError()
    # The app's existing Compose selection and environment remain authoritative.
    # A differing embedded Compose is not silently replaced by this optional step.
    if (root/'compose.yaml').is_symlink() or (root/'compose.yaml').read_bytes()!=files['compose.yaml']:raise ValueError()
    if (root/'setup').is_symlink():raise ValueError()
    selected={path:files[path] for path in manifest['files'] if path.startswith('setup/')}
    selected['installer-manifest.json']=files['installer-manifest.json']
    # Validate every destination before writing any file. Interrupted exact
    # installs can resume; existing unrelated/different source is never replaced.
    for path,data in selected.items():
        target=root/path
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_bytes()!=data)):raise ValueError()
    (root/'setup').mkdir(mode=0o755,exist_ok=True)
    for path,data in selected.items():
        target=root/path
        descriptor,temporary=tempfile.mkstemp(prefix='.visual-bundle-',dir=target.parent)
        try:
            with os.fdopen(descriptor,'wb') as output:
                os.fchmod(output.fileno(),0o644)
                output.write(data);output.flush();os.fsync(output.fileno())
            os.replace(temporary,target)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
    return True

def selected_runtime(directory):
    def run(args,limit=65536):
        result=subprocess.run(args,cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,
                              env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/root'})
        if result.returncode or len(result.stdout)>limit:raise ValueError()
        return result.stdout
    container=run(['docker','compose','ps','-q','server'],256).decode().strip()
    if not re.fullmatch(r'[0-9a-f]{64}',container):raise ValueError()
    labels=json.loads(run(['docker','inspect','--format','{{json .Config.Labels}}',container]),object_pairs_hook=pairs)
    runtime=json.loads(run(['docker','exec',container,'python','-B','-m','backend.visual_config','capabilities']),object_pairs_hook=pairs)
    if labels.get('org.opencontainers.image.revision')!=runtime.get('revision'):raise ValueError()
    return labels.get('org.opencontainers.image.version',''),runtime
'''


def bootstrap_script(*, os_family: str, domain: str, front_door: str, compose: bytes | None, compose_url: str,
                     env_b64: str = "", env_param: tuple[str, str] | None = None, wait_for_ip: str = "") -> str:
    """One script that installs Docker and starts Tico. Used as EC2 user-data (env from a SecureString parameter)
    and as the pasted install command (env embedded)."""
    d = contract.REMOTE_DIR
    q = shlex.quote
    L = ["#!/bin/bash", "set -eu", f"exec > >(tee -a {BOOT_LOG}) 2>&1", "echo tico-setup: start $(date -u)"]
    L += docker_install_lines(os_family, need_aws_cli=bool(env_param))
    L += [f"mkdir -p {d}", *company_file_dir_lines()]
    if compose is not None:
        L += [f"echo {_b64gz(compose)} | base64 -d | gunzip > {d}/compose.yaml"]
    else:
        L += [f"curl -fsSL {q(compose_url)} -o {d}/compose.yaml"]
    if env_param:
        name, region = env_param
        L += [f"cat > {d}/pull-env.sh <<'EOS'", "#!/bin/sh", "set -eu", "umask 077",
              f"aws ssm get-parameter --region {q(region)} --name {q(name)} --with-decryption "
              f"--query Parameter.Value --output text > {d}/.env.new && mv {d}/.env.new {d}/.env",
              *plan_copy_lines(), "EOS",
              f"chmod 700 {d}/pull-env.sh", f"{d}/pull-env.sh"]
    else:
        L += ["umask 077", f"echo {env_b64} | base64 -d > {d}/.env"]
    L += [f"chmod 600 {d}/.env"]
    if front_door == "caddy" and wait_for_ip:
        L += ["# Caddy asks for its certificate at first start; wait until the name points here or that fails.",
              f"for i in $(seq 1 80); do getent hosts {q(domain)} | grep -q {q(wait_for_ip)} && break; sleep 15; done"]
    L += [f"cd {d} && docker compose pull --quiet && docker compose up -d", f"touch {d}/.bootstrapped",
          "echo tico-setup: done",
          "# Optional operator source follows the actual running release; failure does not gate the app.",
          "if command -v python3 >/dev/null 2>&1; then", "python3 -B - <<'TICO_VISUAL_BUNDLE' || true",
          VISUAL_BOOTSTRAP,
          "import signal", "def expired(_signum,_frame): raise TimeoutError()",
          "signal.signal(signal.SIGALRM,expired)", "signal.alarm(40)",
          "try:", f"    version,runtime=selected_runtime({d!r})", f"    install({d!r},version,runtime)",
          "except Exception:", "    print('tico-setup: optional visual operator pending')",
          "finally:", "    signal.alarm(0)", "TICO_VISUAL_BUNDLE", "fi"]
    return "\n".join(L) + "\n"


BOOT_LOG = "/var/log/tico-setup.log"


def finish_boot_guard_lines() -> list[str]:
    """A re-run touches a server whose boot either finished (.bootstrapped) or stopped: its log's last tico-setup line is
    its FAILED one. A first boot still going is left alone. Not a lock: two re-runs started at once both pass it."""
    return [f"if [ ! -f {contract.REMOTE_DIR}/.bootstrapped ] && ! grep 'tico-setup:' {BOOT_LOG} 2>/dev/null | tail -n 1 "
            "| grep -q 'tico-setup: FAILED'; then",
            '  echo "tico-setup: the first boot is still running; not taking it over. Re-run setup once it ends." >&2',
            '  exit 5', 'fi']


def rerun_script(*, compose: bytes | None, compose_url: str, plan: bool) -> str:
    """A re-run on an existing AWS server, by SSM: refresh the env and restart, or finish a first boot that stopped. With
    the plan bucket it first installs this release's compose.yaml, the folder it mounts and the copy step (a server set up
    before them has none), pulls this release's image, and checks that the running app reads the plan: setup deletes
    the bucket's copy only after that. Any failure stops here with its line, the bucket's copy kept for the next run."""
    d, q = contract.REMOTE_DIR, shlex.quote
    L = ["set -eu", "umask 077", *finish_boot_guard_lines()]
    if plan:
        L += company_file_dir_lines()
        L += [f"echo {_b64gz(compose)} | base64 -d | gunzip > {d}/compose.yaml.new" if compose is not None else
              f"curl -fsSL {q(compose_url)} -o {d}/compose.yaml.new", f"mv {d}/compose.yaml.new {d}/compose.yaml"]
    L += [f"{d}/pull-env.sh"]
    if plan:
        L += plan_copy_lines()
    L += [f"cd {d}", *(["docker compose pull --quiet", *plan_read_lines(running=False)] if plan else []), "docker compose up -d"]
    if plan:
        L += plan_read_lines()
    L += [f"if [ ! -f {d}/.bootstrapped ]; then",
          f"  touch {d}/.bootstrapped && echo \"tico-setup: done, finished by a re-run of setup $(date -u)\" >> {BOOT_LOG}", "fi"]
    return "\n".join(L) + "\n"


# Run by the server's image with the plan's sha256: exits 0 only when the app's own reader returns exactly that plan, so
# an image from before TICO_COMPANY_FILE, an older plan left in place or a plan in the env never passes.
PLAN_READ = ("import hashlib, sys; from backend.config import _company_file_data as f; "
             "data, _, _ = f(); "
             "sys.exit(0 if hashlib.sha256(data.encode('ascii')).hexdigest() == sys.argv[1] else 1)")


def plan_read_lines(running: bool = True) -> list[str]:
    """In the server's folder: the app reads the plan setup selected. Before `docker compose up -d` (running=False) a
    one-off container of the pulled image checks it, so an image that cannot read it is never started; after, the running
    server checks it."""
    d, q = contract.REMOTE_DIR, shlex.quote
    run = ("docker compose exec -T server python" if running else
           "docker compose run --rm --no-deps -T --entrypoint python server")
    return [f"plan_sha=$(sed -n 's/^TICO_COMPANY_FILE_SHA256=//p' {d}/.env)",
            "plan_try=1",
            f'until {run} -c {q(PLAN_READ)} "$plan_sha" >/dev/null 2>&1; do',
            '  [ "$plan_try" -ge ' + ("12" if running else "1") + ' ] && { echo "tico-setup: FAILED: the '
            + ("running app" if running else "image") + ' does not read the starter plan; the plan is kept for a retry" >&2; exit 6; }',
            "  plan_try=$((plan_try + 1)); sleep 10",
            "done"]


def company_file_dir_lines() -> list[str]:
    """The folder compose.yaml mounts read-only into the server (contract.COMPANY_FILE_MOUNT), there even without a plan,
    readable only by the server's user (10001, Dockerfile)."""
    f = f"{contract.REMOTE_DIR}/{contract.COMPANY_FILE_DIR}"
    return [f"mkdir -p {f}", f"chown 10001:10001 {f}", f"chmod 700 {f}"]


def plan_copy_lines() -> list[str]:
    """pull-env.sh, after the .env: with TICO_COMPANY_FILE_S3 set (--plan-bucket), copy the starter plan once with the
    server's role and check its sha256. A mismatch stops the boot, so a wrong team never installs. A copy already in
    place with the right sha256 is kept: setup deletes the bucket's copy once the server is up."""
    d = contract.REMOTE_DIR
    f = f"{d}/{contract.COMPANY_FILE_DIR}/{contract.COMPANY_FILE_NAME}"
    return [f"plan_src=$(sed -n 's/^TICO_COMPANY_FILE_S3=//p' {d}/.env)",
            'if [ -n "$plan_src" ]; then',
            f"  plan_sha=$(sed -n 's/^TICO_COMPANY_FILE_SHA256=//p' {d}/.env)",
            f"  plan_region=$(sed -n 's/^TICO_COMPANY_FILE_S3_REGION=//p' {d}/.env)",
            f'  if [ "$(sha256sum {f} 2>/dev/null | cut -d" " -f1)" != "$plan_sha" ]; then',
            f'    rm -f {f}.new',
            # A role just given the read (a reused server) can take a few seconds to be allowed it.
            '    plan_try=1',
            f'    until aws s3 cp --only-show-errors --region "$plan_region" "$plan_src" {f}.new; do',
            '      [ "$plan_try" -ge 6 ] && { echo "tico-setup: FAILED: cannot copy the starter plan from $plan_src" >&2; exit 4; }',
            '      plan_try=$((plan_try + 1)); sleep 10',
            '    done',
            f'    plan_got=$(sha256sum {f}.new | cut -d" " -f1)',
            '    if [ "$plan_got" != "$plan_sha" ]; then',
            f'      rm -f {f}.new',
            '      echo "tico-setup: FAILED: the starter plan from $plan_src has sha256 $plan_got, not $plan_sha; '
            'not starting Tico" >&2',
            '      exit 3',
            '    fi',
            # Each step fails on its own: under `set -e` a failure on the left of `&&` does not stop the script.
            *[f'    {step} || {{ rm -f {f}.new; echo "tico-setup: FAILED: cannot {what} the starter plan; not starting Tico" >&2; exit 7; }}'
              for step, what in ((f"chown 10001:10001 {f}.new", "give the server's user"),
                                 (f"chmod 0400 {f}.new", "make read-only"), (f"mv -f {f}.new {f}", "put in place"))],
            '  fi',
            'fi']


def env_b64(env_text: str) -> str:
    return base64.b64encode(env_text.encode()).decode()


def paste_command(script: str) -> str:
    """The single line the user pastes on the server. The script is inside it, so it holds secrets."""
    return f"echo {_b64gz(script.encode())} | base64 -d | gunzip | sudo bash"
