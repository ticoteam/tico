"""Build, check and publish a new cloud machine base snapshot from one release (docs/cloud-machines.md).

Run from a computer with the boat CLI signed in to the account Tico HQ uses:

    python3 -m runner_support.base_snapshot --release v0.6.81 --name tico-runner-base-0-6-81

It never replaces a saved snapshot: a name that already exists is refused, so the snapshot HQ uses now stays as it
is and a bad image rolls back by setting HQ_BOAT_BASE_SNAPSHOT back. The steps:

1. A new build sandbox with none of the account's stored secrets (`boat new --no-env`): a saved snapshot hands its
   variables to every machine made from it.
2. The release is cloned there and `runner_support.cloud_machine base` prepares it: the runner installed, not
   enrolled.
3. The sandbox is checked (CHECK below), saved under the name, and deleted.
4. A second sandbox is made from the saved snapshot, again with no secrets and no enroll code, and checked the same
   way, so what HQ will boot is what was checked. Then it is deleted too.

Pointing HQ at the new name (HQ_BOAT_BASE_SNAPSHOT) stays a person's step; the script prints it at the end.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence

from runner_support.cloud_machine import DEFAULT_USER, MARKER, UNIT

RELEASE_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
# boat snapshot names: no dots (docs/cloud-machines.md). Lowercase, digits and hyphens keep them safe in a shell.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPO = "https://github.com/ticoteam/tico.git"
BUILD_TIMEOUT_S = 1800  # boat exec: above 600 the output streams live and survives network cuts
CHECK_TIMEOUT_S = 120
# Boot runs at once on a sandbox made from the snapshot; with no enroll code it must do nothing. Give it time to
# settle before the check reads the service.
SETTLE_S = 20
MISSING_CODES = {"not_found", "snapshot_not_found"}

Run = Callable[..., subprocess.CompletedProcess]


class Refused(Exception):
    """A step that must not go on; the text says why."""


def check_args(release: str, name: str) -> None:
    if not RELEASE_RE.match(release):
        raise Refused(f"--release {release!r} is not a release tag like v0.6.81")
    if not NAME_RE.match(name):
        raise Refused(f"--name {name!r}: use lowercase letters, digits and hyphens only (no dots), at most 63")


def build_script(release: str, user: str = DEFAULT_USER) -> str:
    """What runs in the build sandbox: the release's checkout, then `base` as root."""
    return (
        "set -euo pipefail\n"
        f"git clone --quiet --depth 1 --branch {shlex.quote(release)} {REPO} ~/tico\n"
        f"cd ~/tico && sudo python3 -m runner_support.cloud_machine base --user {shlex.quote(user)}\n"
    )


def check_script(release: str, user: str = DEFAULT_USER, settle_s: int = 0) -> str:
    """The checks on a sandbox that should be a fresh base: prints `check passed` and exits 0, or names the failure."""
    r, u, bare = shlex.quote(release), shlex.quote(user), shlex.quote(release.removeprefix("v"))
    marker = shlex.quote(str(MARKER))
    return f"""set -u
fail() {{ echo "check failed: $1"; exit 1; }}
sleep {int(settle_s)}
test -f {marker} || fail "no marker {MARKER}: base did not finish"
release=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' {marker}) \\
  || fail "the marker is not readable"
[ "$release" = {bare} ] || fail "the marker says $release, not {release}"
root=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["root"])' {marker})
tag=$(git -C "$root" describe --tags --exact-match 2>/dev/null) || fail "the checkout is not on a release tag"
[ "$tag" = {r} ] || fail "the checkout is on $tag, not {release}"
test -x "$root/runtime/runner-venv/bin/python" || fail "the runner's Python is missing"
test ! -e "$(getent passwd {u} | cut -d: -f6)/.config/tico/runner.json" || fail "this runner is enrolled"
systemctl is-enabled --quiet {UNIT} || fail "{UNIT} is not enabled"
loginctl show-user {u} -p Linger | grep -qx Linger=yes || fail "lingering is off for {user}"
test ! -e /.dockerenv || fail "/.dockerenv exists: the runner would not update itself"
! grep -qs '^export ASCII_TOKEN=' ~/.bashrc || fail "boat's ASCII_TOKEN line is still in ~/.bashrc"
! sudo grep -qs TICO_ENROLL_TOKEN /etc/environment ~/.bashrc ~/.profile || fail "an enroll code is on the disk"
echo "check passed"
"""


class Boat:
    """The boat CLI calls this script makes; `run` is subprocess.run, or a fake in tests."""

    def __init__(self, run: Run = subprocess.run, say: Callable[[str], None] = print):
        self.run, self.say = run, say

    def _call(self, *args: str, timeout: float | None = None, capture: bool = True) -> subprocess.CompletedProcess:
        return self.run(["boat", *args], capture_output=capture, text=True, timeout=timeout)

    def snapshot_exists(self, name: str) -> bool:
        """Whether a saved snapshot has this name. An answer that says neither is refused: never risk a replace."""
        p = self._call("snapshot", "shares", name, "--json", timeout=60)
        if p.returncode == 0:
            return True
        error = last_json(p.stdout) or last_json(p.stderr) or {}
        if error.get("code") in MISSING_CODES or error.get("status") == 404:
            return False
        raise Refused(f"could not tell whether snapshot {name} exists: {first_line(p.stderr or p.stdout)}")

    def new(self, label: str, source: str | None = None) -> str:
        args = ["new", "--no-env", "--name", label, "--json"]
        if source:
            args += ["--from", source]
        p = self._call(*args, timeout=900)
        sandbox = ready_id(p.stdout)
        if p.returncode or not sandbox:
            raise Refused(f"boat new {label} failed: {first_line(p.stderr or p.stdout)}")
        self.say(f"sandbox {sandbox} ({label}) is ready")
        return sandbox

    def exec(self, sandbox: str, script: str, timeout: int) -> subprocess.CompletedProcess:
        # Output streams to this terminal: the build takes minutes and its log is the record.
        return self._call(
            "exec",
            sandbox,
            "--timeout",
            str(timeout),
            "--",
            "bash",
            "-lc",
            script,
            timeout=timeout + 120,
            capture=False,
        )

    def check(self, sandbox: str, release: str, settle_s: int = 0) -> None:
        p = self._call(
            "exec", sandbox, "--timeout", str(CHECK_TIMEOUT_S), "--", "bash", "-lc",
            check_script(release, settle_s=settle_s), timeout=CHECK_TIMEOUT_S + settle_s + 120,
        )  # fmt: skip
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode or "check passed" not in out:
            failed = [line for line in out.splitlines() if line.startswith("check failed")]
            raise Refused(f"sandbox {sandbox}: {failed[0] if failed else first_line(out) or 'the check did not run'}")
        self.say(f"sandbox {sandbox}: check passed")

    def save(self, sandbox: str, name: str) -> None:
        if self._call("snapshot", sandbox, name, "--json", timeout=1800).returncode:
            raise Refused(f"boat snapshot {sandbox} {name} failed")
        self.say(f"saved snapshot {name}")

    def delete(self, sandbox: str) -> None:
        if self._call("delete", sandbox, "--yes", timeout=300).returncode:
            self.say(f"warning: could not delete sandbox {sandbox}; delete it by hand (boat delete {sandbox})")


def last_json(text: str | None) -> dict | None:
    for line in reversed((text or "").splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def ready_id(text: str | None) -> str | None:
    """The sandbox id from `boat new --json` (JSONL; the `ready` event carries it)."""
    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event") == "ready" and event.get("id"):
            return str(event["id"])
    return None


def first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def refresh(boat: Boat, *, release: str, name: str, keep: bool = False) -> None:
    """Build, check, save under a new name, check what was saved. Every sandbox is deleted unless `keep`."""
    check_args(release, name)
    if boat.snapshot_exists(name):
        raise Refused(f"snapshot {name} already exists: pick a new name, a saved snapshot is never replaced")
    made: list[str] = []
    try:
        build = boat.new(f"{name}-build")
        made.append(build)
        boat.say(f"building the base of {release} (several minutes)")
        if boat.exec(build, build_script(release), BUILD_TIMEOUT_S).returncode:
            raise Refused(f"the base step failed on sandbox {build} (output above)")
        boat.check(build, release)
        # A name taken while this ran would be replaced by the save: look again right before it.
        if boat.snapshot_exists(name):
            raise Refused(f"snapshot {name} appeared while building: nothing saved")
        boat.save(build, name)
        trial = boat.new(f"{name}-check", source=name)
        made.append(trial)
        boat.check(trial, release, settle_s=SETTLE_S)
    finally:
        for sandbox in made:
            if keep:
                boat.say(f"kept sandbox {sandbox} (--keep): delete it with boat delete {sandbox}")
            else:
                boat.delete(sandbox)
    boat.say(
        f"done: snapshot {name} has release {release}, installed and not enrolled.\n"
        f"Next, a person sets HQ_BOAT_BASE_SNAPSHOT={name} on Tico HQ. Note its current value first: setting it back "
        "rolls back. Machines that exist keep their disk; new and replaced ones start from the new snapshot."
    )


def main(argv: Sequence[str] | None = None, boat: Boat | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m runner_support.base_snapshot", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--release", required=True, help="the release tag to build, like v0.6.81")
    parser.add_argument("--name", required=True, help="a new snapshot name, like tico-runner-base-0-6-81 (no dots)")
    parser.add_argument("--keep", action="store_true", help="keep the sandboxes to look at, instead of deleting them")
    args = parser.parse_args(argv)
    try:
        refresh(boat or Boat(), release=args.release, name=args.name, keep=args.keep)
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
