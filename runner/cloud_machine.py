"""A cloud machine (docs/cloud-machines.md): the base image step and the enroll-at-boot step.

Tico HQ starts each machine, a bot's own or the company's shared one, from one saved boat snapshot (`boat new --from
<HQ_BOAT_BASE_SNAPSHOT>`) and gives it two variables: TICO_ENROLL_TOKEN, the one-time code the server minted for this
machine (backend/cloud_machines.py), and TICO_HUB_URL, the server to enroll with. Nothing here knows which bots it
will run: the server assigns them to the computer the enroll makes. The runner is the native Linux checkout runner
(docs/install.md, "A Linux checkout"), run as the sandbox's own user: the bots on one machine (one, or the shared
machine's members) are one trust group, and that user may use sudo. The Docker runner (`install.sh --runner`) drops
bot code to a user with no capabilities, so a bot there could never use sudo.

`base` runs once, as root, on the sandbox the snapshot is saved from, in a checkout of the release. It builds the
runner's venv and Rust binary as the user, gives that user passwordless sudo (/etc/sudoers.d/tico-runner: only here,
so no other computer gets it), turns on lingering so the user's systemd starts at boot, writes the marker
/etc/tico/cloud-machine.json and enables tico-cloud-machine.service. It refuses a computer that is already enrolled:
every machine made from the snapshot would be the same runner.

`base` also installs the tools every bot's computer needs, so they are in the snapshot and no boot waits for them
(`tools()`): Google Chrome stable (Chromium on any other architecture), the computer screen's Xvfb, x11vnc and matchbox
(the runner starts them on :99 and up when the screen is on), fonts, and Node.js 22 or newer with npm. Anything else a
bot needs it installs itself with sudo, and that stays on the machine's disk across stops (on the shared machine, for every
member bot).

`boot` is that service, at every boot. Without the marker it does nothing, so it can never act on another computer.
Not enrolled and both variables found: it enrolls once to that hub and runs `scripts/tico install` (the systemd user
units, which then start at every boot by themselves). Enrolled: it starts those units. Not enrolled and no code (the
base sandbox itself): nothing. A failed enroll exits 1, and systemd tries again in 30 seconds; a retry with the same
code gets the same runner back (the server derives the runner token from the code), so an answer lost on the way
does no harm.

Where boat puts the variables for a service is not documented (they are documented for `boat exec`, `boat ssh` and
every shell, through managed ~/.bashrc blocks), so `boot` looks, in order, in its own environment (which also holds
`systemctl set-environment` variables), the file TICO_BOOT_ENV_FILE names, /etc/environment, and the user's ~/.bashrc
and ~/.profile. Files are parsed, never sourced. The code never reaches a log or a command line: the enroll reads it
from a 0600 file that is removed right after, every printed line has it masked, and once the runner is enrolled every
line that sets TICO_ENROLL_TOKEN is taken out of those files (on every boot, in case boat writes its block again).
The code is single-use and lasts 15 minutes, so one that survives somewhere still enrolls nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN, HUB, FILE = "TICO_ENROLL_TOKEN", "TICO_HUB_URL", "TICO_BOOT_ENV_FILE"
MARKER = Path("/etc/tico/cloud-machine.json")
SUDOERS = Path("/etc/sudoers.d/tico-runner")
UNIT = "tico-cloud-machine.service"
UNIT_DIR = Path("/etc/systemd/system")
APT = Path("/etc/apt")
# The computer screen's programs (runner-rs/src/screen.rs, docs/install.md): the same packages, so one set of tools.
DISPLAY_PACKAGES = ("xvfb", "x11vnc", "matchbox-window-manager")
FONT_PACKAGES = ("fonts-liberation", "fonts-noto-color-emoji")
NODE_MAJOR = 22
CHROME_KEY_URL = "https://dl.google.com/linux/linux_signing_key.pub"
NODE_KEY_URL = "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key"
JOBS = ("bot", "connectors", "close-calls", "importers")   # scripts/tico install's units (runner/systemd_units.py)
DEFAULT_USER = "user"                                       # boat's sandbox user (docs.boat.dev/machines/tools)
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
ATTEMPTS, BACKOFF_S = 5, 5


class Refused(Exception):
    """A step that must not go on; the text says why, never with a secret in it."""


# -- reading the variables --------------------------------------------------------------------------------------------

def parse(text: str) -> dict[str, str]:
    """`KEY=value` and `export KEY=value` lines, unquoted the way sh would; anything else is skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        try:
            words = shlex.split(match.group(2), comments=True)
        except ValueError:
            continue
        out[match.group(1)] = words[0] if len(words) == 1 else ""
    return out


def env_files(home: Path, environ: dict[str, str], etc_environment: Path = Path("/etc/environment")) -> list[Path]:
    named = environ.get(FILE, "").strip()
    return ([Path(named)] if named else []) + [etc_environment, home / ".bashrc", home / ".profile"]


def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def inputs(environ: dict[str, str], files: list[Path]) -> tuple[str, str, str]:
    """(code, hub URL, where they were found). Each is taken from the first place that has it."""
    found = {TOKEN: "", HUB: ""}
    where: list[str] = []
    places = [("the service environment", environ)] + [(str(p), parse(read(p))) for p in files]
    for name, values in places:
        for key in found:
            value = (values.get(key) or "").strip()
            if value and not found[key]:
                found[key] = value
                if name not in where:
                    where.append(name)
    return found[TOKEN], found[HUB], ", ".join(where)


def mask(text: str, secret: str) -> str:
    return text.replace(secret, "[enroll code]") if secret else text


def scrub(path: Path) -> bool:
    """Takes every line that sets TICO_ENROLL_TOKEN out of the file, keeping its other lines, mode and owner.
    True when it changed something."""
    text = read(path)
    if TOKEN not in text:
        return False
    kept = [line for line in text.splitlines(keepends=True)
            if not ((m := LINE_RE.match(line)) and m.group(1) == TOKEN)]
    if len(kept) == len(text.splitlines(keepends=True)):
        return False
    info = path.stat()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("".join(kept))
        os.chmod(tmp, info.st_mode & 0o7777)
        if os.geteuid() == 0:
            os.chown(tmp, info.st_uid, info.st_gid)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


# -- the machine ------------------------------------------------------------------------------------------------------

class Machine:
    """One machine's paths and the commands it runs. Everything that touches the system goes through `run`, so the
    tests drive it with a fake."""

    def __init__(self, *, user: str, home: Path, uid: int, gid: int, root: Path, run=subprocess.run, say=print,
                 sleep=time.sleep, etc: Path = Path("/etc")):
        self.user, self.home, self.uid, self.gid, self.root = user, home, uid, gid, root
        self.run, self.say, self.sleep, self.etc = run, say, sleep, etc

    @classmethod
    def for_user(cls, user: str, root: Path, **kw) -> "Machine":
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            raise Refused(f"there is no user {user} on this machine") from None
        return cls(user=user, home=Path(entry.pw_dir), uid=entry.pw_uid, gid=entry.pw_gid, root=root, **kw)

    @property
    def config(self) -> Path:
        return self.home / ".config" / "tico" / "runner.json"

    @property
    def projects(self) -> Path:
        return self.home / "workspace"

    @property
    def python(self) -> Path:
        return self.root / "runtime" / "runner-venv" / "bin" / "python"

    @property
    def unit_dir(self) -> Path:
        return self.home / ".config" / "systemd" / "user"

    def user_env(self) -> list[str]:
        """A clean environment for the user's commands: nothing of this service's, so the code never rides along."""
        runtime = f"/run/user/{self.uid}"
        return ["env", "-i", f"HOME={self.home}", f"USER={self.user}", f"LOGNAME={self.user}",
                f"PATH={self.home}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus",
                "LANG=C.UTF-8", f"TICO_ROOT={self.root}", f"TICO_RUNNER_PYTHON={self.python}"]

    def as_user(self, *args: str, timeout: int = 900, secret: str = "") -> subprocess.CompletedProcess:
        command = ["runuser", "-u", self.user, "--", *self.user_env(), *args]
        done = self.run(command, cwd=str(self.root), capture_output=True, text=True, timeout=timeout)
        for line in ((done.stdout or "") + (done.stderr or "")).splitlines():
            if line.strip():
                self.say("  " + mask(line, secret))
        return done

    def system(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self.run(list(args), capture_output=True, text=True, timeout=timeout)


def marker_path(etc: Path) -> Path:
    return etc / "tico" / "cloud-machine.json"


def load(etc: Path = Path("/etc"), **kw) -> Machine | None:
    """The machine `base` prepared, or None on any other computer."""
    try:
        data = json.loads(marker_path(etc).read_text())
    except (OSError, ValueError):
        return None
    return Machine.for_user(str(data.get("user") or DEFAULT_USER), Path(data.get("root") or ROOT), etc=etc, **kw)


# -- boot -------------------------------------------------------------------------------------------------------------

def enroll(m: Machine, code: str, hub: str) -> bool:
    m.projects.mkdir(mode=0o700, parents=True, exist_ok=True)
    (m.projects / "secrets").mkdir(mode=0o700, exist_ok=True)
    fd, code_file = tempfile.mkstemp(dir=m.home, prefix=".tico-enroll.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"code": code}, handle)
        if os.geteuid() == 0:
            os.chown(code_file, m.uid, m.gid)
            for path in (m.projects, m.projects / "secrets"):
                os.chown(path, m.uid, m.gid)
        for attempt in range(1, ATTEMPTS + 1):
            if m.config.exists():
                return True
            binary = m.as_user(str(m.python), "-m", "runner.engine", "path", timeout=60)
            if binary.returncode or not (binary.stdout or "").strip():
                m.say("no Rust runner in this checkout: rebuild the base image (python3 -m runner.cloud_machine base)")
                return False
            done = m.as_user((binary.stdout or "").strip().splitlines()[-1], "--config", str(m.config), "enroll",
                             "--url", hub, "--code-file", code_file, "--label", "cloud machine",
                             "--projects", str(m.projects), timeout=120, secret=code)
            if not done.returncode and m.config.exists():
                return True
            m.say(f"enroll attempt {attempt} of {ATTEMPTS} failed")
            if attempt < ATTEMPTS:
                m.sleep(attempt * BACKOFF_S)
        return m.config.exists()
    finally:
        Path(code_file).unlink(missing_ok=True)


def installed(m: Machine) -> bool:
    return all((m.unit_dir / f"tico-{job}.service").is_file() for job in JOBS)


def boot(m: Machine | None, environ: dict[str, str]) -> int:
    """One boot's work; 0 when there is nothing left to do, 1 to have systemd try again."""
    if m is None:
        print(f"no {MARKER}: not a Tico cloud machine, nothing to do")
        return 0
    files = env_files(m.home, environ, m.etc / "environment")
    if not m.config.exists():
        code, hub, where = inputs(environ, files)
        if not code or not hub:
            m.say("not enrolled, and no enroll code here yet: nothing to do")
            return 0
        if not CODE_RE.match(code) or not URL_RE.match(hub):
            m.say(f"the enroll code or hub URL from {where} is not well formed: nothing to do")
            return 0
        m.say(f"enrolling with {hub} (the code from {where})")
        if not enroll(m, code, hub):
            return 1
        m.say("enrolled")
    for path in files:
        try:
            if scrub(path):
                m.say(f"took the enroll code out of {path}")
        except OSError as exc:
            m.say(f"could not take the enroll code out of {path}: {exc.strerror or type(exc).__name__}")
    if environ.get(TOKEN):
        m.system("systemctl", "unset-environment", TOKEN)
    if not installed(m):
        done = m.as_user(str(m.root / "scripts" / "tico"), "install")
        if done.returncode:
            m.say("scripts/tico install failed; trying again")
            return 1
        return 0
    done = m.as_user("systemctl", "--user", "start", *(f"tico-{job}.service" for job in JOBS), timeout=120)
    return 1 if done.returncode else 0


# -- base -------------------------------------------------------------------------------------------------------------

def sudoers_text(user: str) -> str:
    return (f"# Tico cloud machine (runner/cloud_machine.py base): its bots are one trust group, so its runner may "
            f"use sudo.\n"
            f"{user} ALL=(ALL) NOPASSWD:ALL\n")


def unit_text(m: Machine) -> str:
    return "\n".join([
        "[Unit]",
        "Description=Tico cloud machine: enroll the runner once, then start it",
        # The user's systemd (lingering) runs the runner's units; this one only needs it up before it talks to it.
        f"Wants=network-online.target user@{m.uid}.service",
        f"After=network-online.target user@{m.uid}.service",
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={m.root}",
        f"ExecStart={m.python} -m runner.cloud_machine boot",
        "Restart=on-failure",
        "RestartSec=30",
        "Environment=PYTHONUNBUFFERED=1",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def write_root_file(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def browser_package(arch: str) -> str:
    """Google has no Linux arm64 Chrome, so every other architecture gets the distro's Chromium."""
    return "google-chrome-stable" if arch == "x86_64" else "chromium"


def tool_files(arch: str, apt: Path = APT) -> dict[Path, str]:
    """The apt sources for Node.js and (only where Google builds it) Chrome. Written only once their signing keys are on
    disk: apt refuses a repository whose Signed-By key is missing, and the whole `apt-get update` with it."""
    sources, keyrings = apt / "sources.list.d", apt / "keyrings"
    files = {sources / "nodesource.sources": (
        "Types: deb\n"
        f"URIs: https://deb.nodesource.com/node_{NODE_MAJOR}.x\n"
        "Suites: nodistro\n"
        "Components: main\n"
        f"Signed-By: {keyrings / 'nodesource.asc'}\n")}
    if arch == "x86_64":
        files[sources / "google-chrome.sources"] = (
            "Types: deb\n"
            "URIs: https://dl.google.com/linux/chrome/deb/\n"
            "Suites: stable\n"
            "Components: main\n"
            "Architectures: amd64\n"
            f"Signed-By: {keyrings / 'google-chrome.asc'}\n")
    return files


def tool_commands(arch: str, apt: Path = APT) -> list[tuple[str, list[str] | dict[Path, str]]]:
    """Root's steps, in order, as (what, argv), or (what, {path: text}) for the apt sources to write. The order
    matters on a fresh image: the distro's lists and curl first, then the signing keys, and only then the sources that
    name them, then the lists again with those repositories. Each step is safe to run again: installs of present
    packages do nothing, and the keys and sources are rewritten whole."""
    keyrings = apt / "keyrings"
    get = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
    install = [*get, "install", "-y", "-q", "--no-install-recommends"]
    keys = [("NodeSource's signing key", ["curl", "-fsSL", NODE_KEY_URL, "-o", str(keyrings / "nodesource.asc")])]
    if arch == "x86_64":
        keys.insert(0, ("Google's signing key",
                        ["curl", "-fsSL", CHROME_KEY_URL, "-o", str(keyrings / "google-chrome.asc")]))
    packages = [browser_package(arch), *DISPLAY_PACKAGES, *FONT_PACKAGES, "nodejs"]
    return [
        ("the package lists", [*get, "update", "-qq"]),
        ("curl and ca-certificates", [*install, "ca-certificates", "curl"]),
        ("the apt keyring directory", ["install", "-d", "-m", "0755", str(keyrings)]),
        *keys,
        ("the apt sources of NodeSource" + (" and Google" if arch == "x86_64" else ""), tool_files(arch, apt)),
        ("the package lists with the new repositories", [*get, "update", "-qq"]),
        (" ".join(packages), [*install, *packages]),
        # boat's image ships Node 24 ahead of apt's 22 on PATH, so the check is a floor, not an exact match
        (f"Node.js {NODE_MAJOR} or newer", ["node", "-e", f"process.exit(Number(process.versions.node.split('.')[0]) "
                                                          f">= {NODE_MAJOR} ? 0 : 1)"]),
        ("npm", ["npm", "--version"]),
    ]


def tools(m: Machine, *, arch: str, apt: Path = APT) -> None:
    """The tools every bot's computer needs, installed as root by `base` (see the module docstring). Node stays because
    the runner installs each harness its bots use (Claude Code, Codex, Pi, Gemini, Grok) with npm, even over a copy the
    image left on PATH (runner-rs/src/harness_tools.rs, runner/harnesses/*.toml). Nothing else is here: a bot that needs
    ffmpeg or a toolchain installs it itself. A failed step refuses the base."""
    m.say(f"installing the tools bots need: {browser_package(arch)}, Xvfb, Node.js {NODE_MAJOR} or newer")
    for what, argv in tool_commands(arch, apt):
        m.say(f"installing {what}")
        if isinstance(argv, dict):
            for path, text in argv.items():
                write_root_file(path, text, 0o644)
            continue
        done = m.system(*argv, timeout=1800)
        if done.returncode:
            for line in ((done.stdout or "") + (done.stderr or "")).splitlines()[-20:]:
                if line.strip():
                    m.say("  " + line)
            raise Refused(f"installing {what} failed (above)")


def checkout_release(root: Path, run=subprocess.run) -> str:
    from runner import release_update
    return release_update.checkout_release(root, run=run)


ASCII_TOKEN_LINE = re.compile(r"^export ASCII_TOKEN=")


def drop_ascii_token(bashrc: Path) -> int:
    """Take boat's box token out of the user's ~/.bashrc (box-scoped and 90 days, so a snapshot must not carry it).
    The file keeps its owner and mode. Returns the number of lines removed; the value is never printed."""
    try:
        before = bashrc.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return 0
    kept = [line for line in before if not ASCII_TOKEN_LINE.match(line)]
    removed = len(before) - len(kept)
    if removed:
        st = bashrc.stat()
        tmp = bashrc.with_name(bashrc.name + ".tmp")
        tmp.write_text("".join(kept))
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, bashrc)
    return removed


def base(m: Machine, *, release: str, sudoers: Path = SUDOERS, unit_dir: Path = UNIT_DIR, apt: Path = APT,
         arch: str | None = None) -> None:
    if m.config.exists():
        raise Refused(f"{m.config} exists: this computer is enrolled, and every machine made from its snapshot would "
                      "be that same runner. Build the base on a new sandbox.")
    if not release:
        raise Refused(f"{m.root} is not on a release tag: check out the release first (git checkout vX.Y.Z)")
    m.say(f"preparing the runner of release {release} for {m.user}")
    # The same three steps as scripts/setup-runner.sh, as the user, so the checkout and venv stay theirs.
    steps = [] if m.python.exists() else [("the runner's Python", ["python3", "-m", "venv", str(m.python.parent.parent)])]
    steps += [
        ("its packages", [str(m.python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                          "-r", str(m.root / "backend" / "requirements.txt")]),
        ("the Rust runner of this release", [str(m.python), "-m", "runner.engine", "prepare"]),
    ]
    for what, command in steps:
        m.say(f"installing {what}")
        if m.as_user(*command).returncode:
            raise Refused(f"installing {what} failed (above)")
    tools(m, arch=arch or platform.machine(), apt=apt)
    fd, tmp = tempfile.mkstemp(prefix="tico-sudoers.")
    with os.fdopen(fd, "w") as handle:
        handle.write(sudoers_text(m.user))
    try:
        if m.system("visudo", "-cf", tmp).returncode:
            raise Refused("visudo refused the sudoers line")
    finally:
        Path(tmp).unlink(missing_ok=True)
    write_root_file(sudoers, sudoers_text(m.user), 0o440)
    m.say(f"{m.user} may use sudo without a password ({sudoers})")
    if m.system("loginctl", "enable-linger", m.user).returncode:
        raise Refused(f"loginctl enable-linger {m.user} failed: the runner would not start at boot")
    write_root_file(marker_path(m.etc), json.dumps({"user": m.user, "root": str(m.root), "release": release}, indent=2)
                    + "\n", 0o644)
    write_root_file(unit_dir / UNIT, unit_text(m), 0o644)
    for command in (["systemctl", "daemon-reload"], ["systemctl", "enable", UNIT]):
        if m.system(*command).returncode:
            raise Refused(" ".join(command) + " failed")
    if drop_ascii_token(m.home / ".bashrc"):
        m.say(f"removed boat's ASCII_TOKEN line from {m.home / '.bashrc'}")
    m.say(f"ready: {UNIT} enrolls the runner at the first boot that has {TOKEN} and {HUB}. Save the snapshot now.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m runner.cloud_machine", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("base", help="prepare the sandbox a base snapshot is saved from (as root, once)")
    b.add_argument("--user", default=DEFAULT_USER, help=f"the user the runner runs as (default {DEFAULT_USER})")
    sub.add_parser("boot", help="the boot service: enroll once, then start the runner")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print(f"{args.cmd} runs as root (sudo python3 -m runner.cloud_machine {args.cmd})", file=sys.stderr)
        return 2
    try:
        if args.cmd == "base":
            m = Machine.for_user(args.user, ROOT)
            base(m, release=checkout_release(ROOT))
            return 0
        return boot(load(), dict(os.environ))
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
