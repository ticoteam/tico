"""The model CLIs a runner uses, installed and kept current by the runner itself.

Each harness is described by a manifest (runner/harnesses/*.toml; docs/harnesses.md). The runner
installs only the harnesses its assigned bots run on (primary and fallback), plus any the owner turned
on for this computer in Settings, into a tools directory of its own, never system-wide:

    <tools>/<id>/<uid>/     one complete install (an npm prefix, a venv, or a binary)
    <tools>/<id>/current    symlink to the install in use
    <tools>/bin/<exe>       symlink the runner puts at the END of PATH

Rules that keep it safe to run beside people's own tools and beside running turns:
  * a harness already on PATH (Homebrew, npm -g) is used as it is and never touched here;
  * nothing is replaced while a turn on that harness runs: the new version is staged beside the
    old one (slow, in a worker thread) and only switched in by `step`, on the runner's main loop,
    at a moment no turn uses it;
  * a harness pinned to a version is installed at that version and never moved by the daily check;
  * an install no bot here needs stays and is kept current until the owner turns it off; only then is
    it removed, and never while a turn uses it.
"""

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import isolation
from .outage import log

HERE = Path(__file__).resolve().parent
MANIFEST_DIR = HERE / "harnesses"
HOSTS_DIR = HERE / "hosts"

METHODS = ("npm", "pip", "script", "binary")
POLICIES = ("latest", "pinned")
VERSION_TIMEOUT_S = 60       # seconds for `<cli> --version` right after an install: a fresh Node CLI on a busy computer can take more than 15
AUTH_METHODS = ("device-code", "subscription", "api-key")
CHECK_EVERY_S = 24 * 3600      # how often a managed harness is compared with the newest release
RETRY_AFTER_S = 30 * 60        # a failed install or update waits this long before trying again
INSTALL_TIMEOUT_S = 900
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
EXECUTABLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]*$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]*$")
ACTIONS = ("update", "pin", "unpin")


class ManifestError(ValueError):
    """A manifest that cannot be used; the message names the file and the field."""


@dataclasses.dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    host: str
    executable: str
    providers: tuple
    catch_all: bool
    install: dict
    version_args: tuple
    version_pattern: str
    policy: str
    pin: str
    auth_methods: tuple
    api_key_env: tuple
    login: dict | None
    source: str = ""

    @property
    def method(self):
        return self.install["method"]

    @property
    def bin_relative(self):
        """Where the executable sits inside one install directory."""
        return {"npm": f"node_modules/.bin/{self.executable}", "pip": f"venv/bin/{self.executable}",
                "script": self.install.get("bin", ""), "binary": f"bin/{self.executable}"}[self.method]


def _table(data, name, where):
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: [{name}] must be a table")
    return value


def _strings(value, field, where, pattern=None, minimum=0):
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value) \
            or len(value) < minimum:
        raise ManifestError(f"{where}: {field} must be a list of {'at least one ' if minimum else ''}string")
    for item in value:
        if pattern and not pattern.fullmatch(item):
            raise ManifestError(f"{where}: {field} has an invalid entry {item!r}")
    return tuple(value)


def parse(data, source="", hosts_dir=HOSTS_DIR):
    """A validated Manifest from a parsed TOML document."""
    where = source or "manifest"
    if not isinstance(data, dict):
        raise ManifestError(f"{where}: not a table")
    known = {"id", "name", "host", "executable", "providers", "catch_all", "install", "version",
             "update", "auth", "login"}
    if set(data) - known:
        raise ManifestError(f"{where}: unknown field(s) {', '.join(sorted(set(data) - known))}")
    ident = data.get("id")
    if not isinstance(ident, str) or not NAME.fullmatch(ident):
        raise ManifestError(f"{where}: id must be lowercase letters, digits and hyphens")
    where = f"{where} ({ident})"
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError(f"{where}: name is required")
    host = data.get("host")
    if not isinstance(host, str) or not NAME.fullmatch(host) or not (Path(hosts_dir) / f"{host}.py").is_file():
        raise ManifestError(f"{where}: host {host!r} is not an adapter in runner/hosts/")
    executable = data.get("executable")
    if not isinstance(executable, str) or not EXECUTABLE.fullmatch(executable):
        raise ManifestError(f"{where}: executable must be a bare file name")
    providers = _strings(data.get("providers"), "providers", where, PROVIDER_ID, minimum=1)
    if len(set(providers)) != len(providers):
        raise ManifestError(f"{where}: providers lists one twice")

    install = dict(_table(data, "install", where))
    method = install.get("method")
    if method not in METHODS:
        raise ManifestError(f"{where}: install.method must be one of {', '.join(METHODS)}")
    if method in ("npm", "pip"):
        package = install.get("package")
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise ManifestError(f"{where}: install.package must be a package name")
    elif method == "script":
        url = install.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ManifestError(f"{where}: install.url must be an https URL")
        if not isinstance(install.get("prefix_env"), str) or not ENV_NAME.fullmatch(install["prefix_env"]):
            raise ManifestError(f"{where}: install.prefix_env names the variable that tells the script "
                                "where to install")
        binary = install.get("bin")
        if not isinstance(binary, str) or not binary or binary.startswith("/") or ".." in Path(binary).parts:
            raise ManifestError(f"{where}: install.bin is the executable's path inside the install directory")
        if install.get("shell", "sh") not in ("sh", "bash"):
            raise ManifestError(f"{where}: install.shell is sh or bash")
        _strings(install.get("requires", []), "install.requires", where, EXECUTABLE)
    else:
        url = install.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ManifestError(f"{where}: install.url must be an https URL "
                                "(with {os} and {arch} for the platform)")
        sums = install.get("sha256")
        if not isinstance(sums, dict) or not sums or not all(
                isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in sums.values()):
            raise ManifestError(f"{where}: a binary download needs install.sha256 per platform "
                                "(for example linux-arm64), each a sha256 hex digest")

    if install.get("latest_url") is not None:
        latest = install["latest_url"]
        if not isinstance(latest, str) or not latest.startswith("https://"):
            raise ManifestError(f"{where}: install.latest_url must be an https URL")
        try:
            if re.compile(install.get("latest_pattern", "(x)")).groups != 1 or "latest_pattern" not in install:
                raise ManifestError(f"{where}: install.latest_pattern needs exactly one capture group")
        except re.error as exc:
            raise ManifestError(f"{where}: install.latest_pattern is not a regular expression ({exc})")

    version = _table(data, "version", where)
    args = _strings(version.get("args", ["--version"]), "version.args", where)
    pattern = version.get("pattern", r"(\d+\.\d+\.\d+)")
    try:
        if re.compile(pattern).groups != 1:
            raise ManifestError(f"{where}: version.pattern needs exactly one capture group")
    except re.error as exc:
        raise ManifestError(f"{where}: version.pattern is not a regular expression ({exc})")

    update = _table(data, "update", where)
    policy, pin = update.get("policy", "latest"), str(update.get("pin") or "")
    if policy not in POLICIES:
        raise ManifestError(f"{where}: update.policy must be latest or pinned")
    if policy == "pinned" and not VERSION.fullmatch(pin):
        raise ManifestError(f"{where}: a pinned harness needs update.pin, a version")

    auth = _table(data, "auth", where)
    methods = _strings(auth.get("methods", []), "auth.methods", where)
    if set(methods) - set(AUTH_METHODS):
        raise ManifestError(f"{where}: auth.methods are {', '.join(AUTH_METHODS)}")
    keys = _strings(auth.get("api_key_env", []), "auth.api_key_env", where, ENV_NAME)
    if "api-key" in methods and not keys:
        raise ManifestError(f"{where}: api-key sign-in needs auth.api_key_env")

    login = None
    if "login" in data:
        raw = _table(data, "login", where)
        login = {"command": list(_strings(raw.get("command"), "login.command", where, minimum=1)),
                 "terminal": bool(raw.get("terminal", False)), "paste_code": bool(raw.get("paste_code", False))}
        if "device-code" not in methods and "subscription" not in methods:
            raise ManifestError(f"{where}: login is for device-code or subscription sign-in")
    elif "device-code" in methods:
        raise ManifestError(f"{where}: device-code sign-in needs a [login] command for the browser relay")
    return Manifest(ident, name.strip(), host, executable, providers, bool(data.get("catch_all", False)),
                    install, args, pattern, policy, pin, methods, keys, login, source)


def load_all(directory=MANIFEST_DIR, hosts_dir=HOSTS_DIR):
    """Every manifest in `directory`, by id. Two manifests may not claim a provider, a host or an
    executable, and at most one is the catch-all."""
    found = {}
    for path in sorted(Path(directory).glob("*.toml")):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"{path.name}: {exc}")
        manifest = parse(data, path.name, hosts_dir)
        if path.stem != manifest.id:
            raise ManifestError(f"{path.name}: the file is named for the harness id ({manifest.id})")
        found[manifest.id] = manifest
    claimed = {}
    for manifest in found.values():
        for kind, values in (("provider", manifest.providers), ("host", (manifest.host,)),
                             ("executable", (manifest.executable,))):
            for value in values:
                if (kind, value) in claimed:
                    raise ManifestError(f"{manifest.id}: {kind} {value} is already claimed by {claimed[kind, value]}")
                claimed[kind, value] = manifest.id
    if sum(1 for manifest in found.values() if manifest.catch_all) > 1:
        raise ManifestError("more than one catch-all harness")
    return found


def version_key(text):
    """Numbers of a version, for comparing releases; anything else sorts as older."""
    return tuple(int(part) for part in re.findall(r"\d+", str(text or "").split("-")[0].split("+")[0]))


def newer(latest, installed):
    return bool(latest and installed and version_key(latest) > version_key(installed))


def _platform():
    system = {"darwin": "darwin", "linux": "linux"}.get(sys.platform, sys.platform)
    machine = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    import platform
    return system, machine.get(platform.machine().lower(), platform.machine().lower())


def download(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": "tico-runner"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return reply.read()


@dataclasses.dataclass
class Job:
    kind: str                     # install | update | check
    harness: str
    version: str = ""             # what to install; "" means the newest
    origin: str = "auto"          # auto | owner
    request: str = ""             # the server's request id, for an owner's action
    future: object = None
    staged: dict | None = None


class Harnesses:
    """One runner's tools directory, its manifests, and the work of keeping them current."""

    def __init__(self, tools_dir, state_path, manifests=None, *, run=subprocess.run, fetch=download,
                 clock=time.time, on_switch=None, check_every=CHECK_EVERY_S, python=None):
        self.tools = Path(tools_dir)
        self.state_path = Path(state_path)
        self.manifests = manifests if manifests is not None else load_all()
        self.run, self.fetch, self.clock = run, fetch, clock
        self.on_switch = on_switch or (lambda manifest: None)
        self.check_every = check_every
        self.python = python or sys.executable
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="harness-install")
        self.wanted = set()
        self.needed = set()           # what this computer's bots run on: always installed
        self.choices = {}             # the owner's on/off per harness for this computer; {} from an older server
        self.requests = []            # owner updates waiting for their turn: [Job]
        self.job = None               # the one job the worker is running
        self.staged = {}              # harness id -> Job whose new version waits for an idle moment
        self.versions = {}            # (path, mtime) -> detected version
        self.results = []             # finished owner requests: (request id, state, message)
        self.state = self._load()

    # ------------------------------------------------------------------ state
    def _load(self):
        try:
            value = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            value = {}
        value = value if isinstance(value, dict) else {}
        return {"overrides": value.get("overrides") if isinstance(value.get("overrides"), dict) else {},
                "checked": value.get("checked") if isinstance(value.get("checked"), dict) else {},
                "latest": value.get("latest") if isinstance(value.get("latest"), dict) else {},
                "failed": value.get("failed") if isinstance(value.get("failed"), dict) else {}}

    def _save(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True))
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log(f"Tico runner: could not save harness state ({type(exc).__name__})")

    def policy(self, manifest):
        """(policy, pin): the owner's choice on this computer, else the manifest's."""
        override = self.state["overrides"].get(manifest.id)
        if isinstance(override, dict) and override.get("policy") in POLICIES:
            return override["policy"], str(override.get("pin") or "")
        return manifest.policy, manifest.pin

    # ------------------------------------------------------------------ finding what is installed
    @property
    def bin_dir(self):
        return self.tools / "bin"

    def expose_path(self):
        """Put the tools directory's bin last on PATH: anything the person already has wins."""
        entries = os.environ.get("PATH", os.defpath).split(os.pathsep)
        if str(self.bin_dir) not in entries:
            os.environ["PATH"] = os.pathsep.join([*entries, str(self.bin_dir)])

    def locate(self, manifest):
        """(path, source) of the executable a turn would run: "path" for one the person installed,
        "tools" for one this runner installed, or (None, "")."""
        entries = [entry for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
                   if entry and Path(entry) != self.bin_dir]
        found = shutil.which(manifest.executable, path=os.pathsep.join(entries))
        if found:
            return found, "path"
        ours = self.bin_dir / manifest.executable
        if ours.exists() and os.access(ours, os.X_OK):
            return str(ours), "tools"
        return None, ""

    def detect(self, manifest, path, timeout=15):
        """The installed version, or "" when the executable does not answer."""
        try:
            key = (path, os.stat(path).st_mtime_ns, os.stat(os.path.realpath(path)).st_mtime_ns)
        except OSError:
            return ""
        if key in self.versions:
            return self.versions[key]
        version = ""
        try:
            done = self.run([path, *manifest.version_args], capture_output=True, text=True, timeout=timeout,
                            stdin=subprocess.DEVNULL)
            found = re.search(manifest.version_pattern, (done.stdout or "") + "\n" + (done.stderr or ""))
            version = found.group(1) if found and done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
        if version:                    # a failed probe is asked again next time
            self.versions[key] = version
        return version

    # ------------------------------------------------------------------ what is wanted
    def harness_for_runtime(self, runtime):
        return next((m for m in self.manifests.values() if m.host == runtime), None)

    def want(self, runtimes=()):
        """Install the harnesses these runtimes (this computer's bots, primary and fallback) run on, plus
        the ones the owner turned on here. The company's other providers are not installed."""
        found = set()
        for runtime in runtimes or ():
            manifest = self.harness_for_runtime(runtime)
            if manifest:
                found.add(manifest.id)
        with self.lock:
            self.needed = found
            return self._rewant()

    def choose(self, choices):
        """The owner's on/off per harness for this computer (`{id: bool}`), as the server keeps it."""
        with self.lock:
            self.choices = {str(k): bool(v) for k, v in (choices or {}).items() if str(k) in self.manifests}
            return self._rewant()

    def _rewant(self):
        self.wanted = self.needed | {ident for ident, on in self.choices.items() if on}
        return set(self.wanted)

    def turned_off(self, ident):
        """Off on this computer: the owner said so and none of its bots runs on it."""
        return self.choices.get(ident) is False and ident not in self.needed

    def install_plan(self, wanted=None):
        """What would be installed right now: wanted harnesses that are nowhere on this computer."""
        wanted = self.wanted if wanted is None else wanted
        return sorted(ident for ident in wanted
                      if ident in self.manifests and not self.locate(self.manifests[ident])[0])

    # ------------------------------------------------------------------ owner actions
    def request(self, action, harness, request_id=""):
        """Apply an owner's action. pin and unpin are settled at once; update waits for `step`.
        Returns (state, message) with state done | failed | queued."""
        manifest = self.manifests.get(harness)
        if action not in ACTIONS or manifest is None:
            return "failed", "This computer does not know that harness"
        path, source = self.locate(manifest)
        with self.lock:
            if action == "unpin":
                self.state["overrides"][manifest.id] = {"policy": "latest", "pin": ""}
                self._save()
                return "done", f"{manifest.name} follows the newest release again"
            if not path:
                return "failed", f"{manifest.name} is not installed on this computer"
            if source != "tools":
                return "failed", (f"{manifest.name} was installed outside Tico ({path}); "
                                  "update it where it was installed")
            if action == "pin":
                version = self.detect(manifest, path)
                if not version:
                    return "failed", f"Could not read the installed version of {manifest.name}"
                self.state["overrides"][manifest.id] = {"policy": "pinned", "pin": version}
                self._save()
                return "done", f"{manifest.name} is pinned to {version}"
            if any(job.harness == manifest.id for job in self.requests) or (
                    self.job and self.job.harness == manifest.id and self.job.request):
                return "queued", "An update is already waiting"
            self.requests.append(Job("update", manifest.id, "", "owner", request_id))
            return "queued", f"{manifest.name} updates when no turn is using it"

    # ------------------------------------------------------------------ the loop
    def step(self, busy=()):
        """One pass of the runner's main loop. Never blocks: slow work runs in the worker, and a
        staged version is switched in only when no running turn uses that harness (`busy` is the
        set of host names in use). Returns owner requests that finished: [(id, state, message)]."""
        with self.lock:
            self._harvest()
            for ident, job in list(self.staged.items()):
                manifest = self.manifests[ident]
                if manifest.host in busy:
                    continue
                del self.staged[ident]
                if self.turned_off(ident):
                    shutil.rmtree(job.staged["dir"], ignore_errors=True)
                    if job.request:
                        self.results.append((job.request, "failed", f"{manifest.name} is turned off on this computer"))
                    continue
                self._commit(manifest, job)
            for ident, manifest in self.manifests.items():
                # Turned off: removed at a moment no turn uses it and nothing is being installed for it.
                if (self.turned_off(ident) and manifest.host not in busy and ident not in self.staged
                        and not (self.job and self.job.harness == ident)):
                    self._remove(manifest)
            if self.job is None:
                job = self._next()
                if job:
                    self.job = job
                    job.future = self.pool.submit(self._work, job)
            done, self.results = self.results, []
            return done

    def _harvest(self):
        job = self.job
        if not job or not job.future.done():
            return
        self.job = None
        manifest = self.manifests[job.harness]
        try:
            result = job.future.result()
        except Exception as exc:
            self._failed(manifest, job, str(exc) or type(exc).__name__)
            return
        if job.kind == "check":
            self.state["checked"][manifest.id] = self.clock()
            self.state["latest"][manifest.id] = result
            self._save()
            return
        job.staged = result
        self.staged[manifest.id] = job

    def _failed(self, manifest, job, message):
        message = " ".join(message.split())[-300:]
        if job.kind == "check":
            self.state["checked"][manifest.id] = self.clock()      # try again tomorrow, not next tick
        else:
            self.state["failed"][manifest.id] = {"at": self.clock(), "error": message}
        self._save()
        log(f"Tico runner: {manifest.name} {job.kind} failed: {message}")
        if job.request:
            self.results.append((job.request, "failed", message))

    def backing_off(self, manifest):
        failure = self.state["failed"].get(manifest.id)
        return bool(failure) and self.clock() - float(failure.get("at") or 0) < RETRY_AFTER_S

    def _next(self):
        if self.requests:
            return self.requests.pop(0)
        for ident in sorted(self.manifests):
            manifest = self.manifests[ident]
            if self.backing_off(manifest) or ident in self.staged:
                continue
            path, source = self.locate(manifest)
            policy, pin = self.policy(manifest)
            if not path:
                if ident in self.wanted:
                    return Job("install", ident, pin if policy == "pinned" else "")
                continue
            if source != "tools" or self.turned_off(ident):
                continue                     # the person's own install, or one on its way out
            installed = self.detect(manifest, path)
            if policy == "pinned" and installed != pin:
                return Job("update", ident, pin)
            if policy == "latest" and newer(self.state["latest"].get(ident, ""), installed):
                return Job("update", ident, "")
            # A pinned harness is checked too, so `update_available` can still say so.
            if self.clock() - float(self.state["checked"].get(ident) or 0) >= self.check_every:
                return Job("check", ident)
        return None

    # ------------------------------------------------------------------ worker thread
    def _work(self, job):
        manifest = self.manifests[job.harness]
        if job.kind == "check":
            return self.newest(manifest)
        return self.stage(manifest, job.version)

    def _env(self):
        # Installs run as the supervisor, which owns the tools directory and keeps its own caches out of
        # the bot user's HOME (runner/isolation.py).
        home = {"HOME": str(self.tools / ".home")} if isolation.enabled() else {}
        if home:
            Path(home["HOME"]).mkdir(parents=True, exist_ok=True)
        return {**os.environ, **home, "npm_config_update_notifier": "false", "npm_config_fund": "false",
                "npm_config_audit": "false", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}

    def _exec(self, argv, env_extra=None):
        done = self.run(argv, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S,
                        stdin=subprocess.DEVNULL, env={**self._env(), **(env_extra or {})})
        if done.returncode:
            tail = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-3:]
            raise RuntimeError(f"{Path(argv[0]).name} exited {done.returncode}: {' | '.join(tail)}")
        return done.stdout or ""

    def newest(self, manifest):
        """The newest release of the harness, or "" when there is no way to ask."""
        install = manifest.install
        if manifest.method == "npm":
            self._need("npm")
            text = self._exec(["npm", "view", install["package"], "version"])
        elif manifest.method == "pip":
            text = self._exec([self.python, "-m", "pip", "index", "versions", install["package"]])
            found = re.search(r"\(([0-9][^)]*)\)", text)
            text = found.group(1) if found else ""
        elif install.get("latest_url"):
            body = self.fetch(install["latest_url"]).decode("utf-8", "replace")
            found = re.search(install["latest_pattern"], body)
            return found.group(1) if found else ""
        else:
            return ""
        found = re.search(r"\d+(?:\.\d+)+\S*", text)
        return found.group(0) if found else ""

    @staticmethod
    def _need(program):
        if not shutil.which(program):
            raise RuntimeError(f"{program} is not installed on this computer, and this harness needs it")

    def stage(self, manifest, version):
        """Install one complete copy beside whatever is there and prove it answers `--version`."""
        root = self.tools / manifest.id
        root.mkdir(parents=True, exist_ok=True)
        dest = root / uuid.uuid4().hex[:10]
        dest.mkdir()
        try:
            self._install(manifest, version, dest)
            path = dest / manifest.bin_relative
            if not path.exists():
                raise RuntimeError(f"the install did not produce {manifest.bin_relative}")
            found = self.detect(manifest, str(path), timeout=VERSION_TIMEOUT_S)
            if not found:
                raise RuntimeError(f"{manifest.executable} does not answer {' '.join(manifest.version_args)}")
            return {"dir": dest, "version": found}
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise

    def _install(self, manifest, version, dest):
        install = manifest.install
        method = manifest.method
        if method == "npm":
            self._need("npm")
            self._exec(["npm", "install", "--prefix", str(dest), "--no-audit", "--no-fund", "--loglevel=error",
                        f"{install['package']}@{version or 'latest'}"])
        elif method == "pip":
            self._exec([self.python, "-m", "venv", str(dest / "venv")])
            spec = f"{install['package']}=={version}" if version else install["package"]
            self._exec([str(dest / "venv" / "bin" / "pip"), "install", "--no-cache-dir", spec])
        elif method == "script":
            shell = install.get("shell", "sh")
            for program in (shell, *install.get("requires", [])):
                self._need(program)
            if version and "{version}" not in install["url"]:
                raise RuntimeError("this harness's installer only installs the newest release, so it cannot be "
                                   f"moved to {version}")
            body = self.fetch(install["url"].format(version=version or "latest"))
            expected = install.get("sha256")
            if expected and hashlib.sha256(body).hexdigest() != expected:
                raise RuntimeError("the install script does not match its pinned checksum")
            script = dest / "install.sh"
            script.write_bytes(body)
            self._exec([shell, str(script)], env_extra={"NO_COLOR": "1", install["prefix_env"]: str(dest)})
            script.unlink()
        else:
            system, machine = _platform()
            platform_key = f"{system}-{machine}"
            digest = install["sha256"].get(platform_key)
            if not digest:
                raise RuntimeError(f"no download is published for {platform_key}")
            body = self.fetch(install["url"].format(os=system, arch=machine, version=version or "latest"))
            if hashlib.sha256(body).hexdigest() != digest:
                raise RuntimeError("the download does not match its checksum")
            target = dest / "bin"
            target.mkdir()
            if install["url"].endswith((".tar.gz", ".tgz")):
                archive = dest / "download.tgz"
                archive.write_bytes(body)
                with tarfile.open(archive) as bundle:
                    member = next((m for m in bundle.getmembers() if Path(m.name).name == manifest.executable), None)
                    if member is None or not member.isfile():
                        raise RuntimeError(f"the archive holds no {manifest.executable}")
                    (target / manifest.executable).write_bytes(bundle.extractfile(member).read())
                archive.unlink()
            else:
                (target / manifest.executable).write_bytes(body)
            (target / manifest.executable).chmod(0o755)

    # ------------------------------------------------------------------ switching in
    def _commit(self, manifest, job):
        """Make the staged install the one in use. Called only with no running turn on this host."""
        root = self.tools / manifest.id
        dest = job.staged["dir"]
        try:
            self.on_switch(manifest)                  # a warm process would keep the old files open
            link = root / ".current.new"
            link.unlink(missing_ok=True)
            link.symlink_to(dest.name)
            os.replace(link, root / "current")
            self.bin_dir.mkdir(parents=True, exist_ok=True)
            entry = self.bin_dir / ".exe.new"
            entry.unlink(missing_ok=True)
            entry.symlink_to(os.path.relpath(root / "current" / manifest.bin_relative, self.bin_dir))
            os.replace(entry, self.bin_dir / manifest.executable)
            for old in root.iterdir():
                if old.is_dir() and not old.is_symlink() and old != dest:
                    shutil.rmtree(old, ignore_errors=True)
        except OSError as exc:
            self._failed(manifest, job, f"could not switch to the new version ({type(exc).__name__})")
            shutil.rmtree(dest, ignore_errors=True)
            return
        self.versions.clear()
        self.state["failed"].pop(manifest.id, None)
        if not job.version:
            self.state["latest"][manifest.id] = job.staged["version"]
        policy, _ = self.policy(manifest)
        if policy == "pinned" and job.kind == "update" and not job.version:
            # An owner's Update on a pinned harness moves the pin with it.
            self.state["overrides"][manifest.id] = {"policy": "pinned", "pin": job.staged["version"]}
        self._save()
        log(f"Tico runner: {manifest.name} {job.kind}d to {job.staged['version']}"
            if job.kind == "update" else f"Tico runner: installed {manifest.name} {job.staged['version']}")
        if job.request:
            self.results.append((job.request, "done", f"{manifest.name} is at {job.staged['version']}"))

    def _remove(self, manifest):
        """Uninstall what this runner installed for `manifest`. A person's own install is theirs."""
        root, entry = self.tools / manifest.id, self.bin_dir / manifest.executable
        if not root.exists() and not entry.is_symlink():
            return
        self.on_switch(manifest)                      # a warm process would keep the files open
        if entry.is_symlink():
            entry.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        self.versions.clear()
        for key in ("checked", "latest", "failed"):
            self.state[key].pop(manifest.id, None)
        self._save()
        log(f"Tico runner: removed {manifest.name}; it is turned off on this computer")

    # ------------------------------------------------------------------ what the heartbeat says
    def report(self, runtimes=None, wanted=None):
        """{harness id: {installed, version, pinned, authenticated, update_available, ...}}."""
        runtimes = runtimes or {}
        wanted = self.wanted if wanted is None else wanted
        out = {}
        with self.lock:
            working = {self.job.harness: self.job} if self.job else {}
            queued = {job.harness for job in self.requests}
            for ident, manifest in sorted(self.manifests.items()):
                path, source = self.locate(manifest)
                installed = self.detect(manifest, path) if path else ""
                policy, pin = self.policy(manifest)
                latest = self.state["latest"].get(ident, "")
                row = runtimes.get(manifest.host) or {}
                job = working.get(ident)
                failure = self.state["failed"].get(ident) or {}
                if job and job.kind != "check":
                    state, detail = ("installing" if job.kind == "install" else "updating"), ""
                elif ident in self.staged:
                    state, detail = "updating", "Waiting for a running turn to finish"
                elif ident in queued:
                    state, detail = "queued", "Waiting for its turn"
                elif failure and self.backing_off(manifest):
                    state, detail = "failed", str(failure.get("error") or "")[:300]
                else:
                    state, detail = "idle", ""
                out[ident] = {
                    "name": manifest.name, "runtime": manifest.host, "installed": bool(path),
                    "version": installed[:100], "managed": source == "tools", "source": source,
                    "pinned": policy == "pinned", "pin": pin if policy == "pinned" else "",
                    "authenticated": (row.get("authenticated") if path else "missing") or "unknown",
                    "update_available": bool(source == "tools" and newer(latest, installed)),
                    "latest": latest[:100], "wanted": ident in wanted, "state": state, "detail": detail,
                    **{key: row[key] for key in ("goals", "commands") if key in row},
                }
        return out

    def stop(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


_MANIFESTS = None


def executable_for(runtime):
    """The program a runtime runs (`cursor` runs `cursor-agent`); the runtime's own name when no
    manifest says otherwise. Read from the shipped manifests once."""
    global _MANIFESTS
    if _MANIFESTS is None:
        _MANIFESTS = load_all()
    manifest = next((m for m in _MANIFESTS.values() if m.host == runtime), None)
    return manifest.executable if manifest else runtime


def tools_dir(config, config_path=None, state_dir=None):
    """Where this runner's tools live: TICO_TOOLS_DIR, the registration's `tools_dir`, else beside
    the registration file (in the container that is /home/runner/tools, inside the volume)."""
    chosen = os.environ.get("TICO_TOOLS_DIR") or (config or {}).get("tools_dir")
    if chosen:
        return Path(chosen).expanduser()
    return (Path(config_path).parent if config_path else Path(state_dir or ".")) / "tools"


class Relay:
    """Owner actions from the server (backend/harness_actions.py), the same shape as the browser
    sign-in relay: the runner asks what is wanted and reports what happened. Nothing listens here."""

    POLL_S = 5

    def __init__(self, tools, client):
        self.tools, self.client = tools, client
        self.polled = 0.0
        self.known = {}

    def _report(self, action_id, state, message=""):
        try:
            self.client.post(f"runner-harness-actions/{action_id}/report", {"state": state, "message": message[:300]})
            return True
        except Exception:
            return False

    def tick(self, busy=(), force=False, ask=None):
        """Take new requests, advance the installer one step, report what finished. `ask`: whether to
        ask the server for requests (the runner's event stream decides); None, every POLL_S."""
        if ask is None:
            ask = force or time.monotonic() - self.polled >= self.POLL_S
        if ask:
            self.polled = time.monotonic()
            reply = self.client.get("runner-harness-actions")
            # The owner's on/off per harness here. A server from before the setting sends none: the
            # computer then installs what its bots run on and keeps what it already has.
            if isinstance((reply or {}).get("choices"), dict):
                self.tools.choose(reply["choices"])
            for row in (reply or {}).get("actions", []):
                if not isinstance(row, dict) or not row.get("id") or row["id"] in self.known:
                    continue
                state, message = self.tools.request(str(row.get("action")), str(row.get("harness")), row["id"])
                self.known[row["id"]] = state
                self._report(row["id"], "running" if state == "queued" else state, message)
        for action_id, state, message in self.tools.step(busy):
            self.known[action_id] = state
            if not self._report(action_id, state, message):
                self.tools.results.append((action_id, state, message))     # said again next tick
