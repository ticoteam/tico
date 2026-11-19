"""Build, check and publish a new cloud machine base snapshot from one release (docs/cloud-machines.md).

Run from a computer with the boat CLI signed in to the account Tico HQ uses:

    python3 -m runner_support.base_snapshot --release v0.6.81 --name tico-runner-base-0-6-81

`--name` is the stem of the published name: the script adds a random suffix (`tico-runner-base-0-6-81-3f9a1c2e`)
and prints the result. boat's `snapshot <id> <name>` replaces a snapshot that has the name and has no create-only
switch, so two runs given the same name would otherwise both see it free and the second save would change an image
the first one already published. With the suffix no two runs target the same name. The name is also looked up
before the build and again right before the save, and a lookup boat does not answer clearly is refused. The snapshot
HQ uses now stays as it is, and a bad image rolls back by setting HQ_BOAT_BASE_SNAPSHOT back. The steps:

1. A new build sandbox with none of the account's stored secrets (`boat new --no-env`): a saved snapshot hands its
   variables to every machine made from it.
2. The release is cloned there and `runner_support.cloud_machine base` prepares it: the runner installed, not
   enrolled. A random stamp of this run is written to IMAGE_STAMP.
3. The sandbox is checked (`check_script`), saved under the name, and deleted.
4. A second sandbox is made from the saved snapshot, again with no secrets and no enroll code, and checked the same
   way, stamp included: what HQ will boot is what this run built and checked. Then it is deleted too.

Every sandbox the run made is deleted at the end, each one on its own, even when an earlier delete fails or times
out; any left over are named with the command that deletes them. Pointing HQ at the new name
(HQ_BOAT_BASE_SNAPSHOT) stays a person's step; the script prints it at the end.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence

from runner_support.cloud_machine import DEFAULT_USER, MARKER, UNIT

RELEASE_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SUFFIX_HEX = 8  # 4 random bytes, `-` + 8 hex characters on the name
# boat snapshot names: no dots (docs/cloud-machines.md). Lowercase, digits and hyphens keep them safe in a shell.
# The stem leaves room for the suffix inside 63 characters.
NAME_RE = re.compile(rf"^[a-z0-9][a-z0-9-]{{0,{62 - SUFFIX_HEX - 1}}}$")
REPO = "https://github.com/ticoteam/tico.git"
IMAGE_STAMP = MARKER.parent / "base-snapshot.stamp"
BUILD_TIMEOUT_S = 1800  # boat exec: above 600 the output streams live and survives network cuts
CHECK_TIMEOUT_S = 120
NEW_TIMEOUT_S = 900
# Boot runs at once on a sandbox made from the snapshot; with no enroll code it must do nothing. Give it time to
# settle before the check reads the service.
SETTLE_S = 20
# The error codes that mean "no saved snapshot has this name". A bare 404 is not enough: it could be any missing
# resource. Anything else is refused, so a wrong guess stops the run instead of replacing a snapshot.
MISSING_CODES = {"not_found", "snapshot_not_found"}

Run = Callable[..., subprocess.CompletedProcess]


class Refused(Exception):
    """A step that must not go on; the text says why."""


def check_args(release: str, name: str) -> None:
    if not RELEASE_RE.match(release):
        raise Refused(f"--release {release!r} is not a release tag like v0.6.81")
    if not NAME_RE.match(name):
        raise Refused(
            f"--name {name!r}: use lowercase letters, digits and hyphens only (no dots), "
            f"at most {62 - SUFFIX_HEX} (a suffix is added)"
        )


def published_name(stem: str) -> str:
    return f"{stem}-{secrets.token_hex(SUFFIX_HEX // 2)}"


def build_script(release: str, stamp: str, user: str = DEFAULT_USER) -> str:
    """What runs in the build sandbox: the release's checkout, `base` as root, then this run's stamp."""
    return (
        "set -euo pipefail\n"
        f"git clone --quiet --depth 1 --branch {shlex.quote(release)} {REPO} ~/tico\n"
        f"cd ~/tico && sudo python3 -m runner_support.cloud_machine base --user {shlex.quote(user)}\n"
        f"echo {shlex.quote(stamp)} | sudo tee {shlex.quote(str(IMAGE_STAMP))} >/dev/null\n"
    )


# The sha256 of each `export ASCII_TOKEN=` line in the user's ~/.bashrc, one per line: never the value itself.
TOKEN_HASHES = (
    "grep -s '^export ASCII_TOKEN=' ~/.bashrc | while IFS= read -r line; do "
    "printf '%s' \"$line\" | sha256sum | cut -c1-64; done"
)


def check_script(
    release: str,
    stamp: str,
    user: str = DEFAULT_USER,
    *,
    settle_s: int = 0,
    build_token_hashes: Sequence[str] | None = None,
) -> str:
    """The checks on a sandbox that should be this run's base: prints `check passed` and exits 0, or names the failure.

    On the build sandbox (`build_token_hashes` None) no `export ASCII_TOKEN=` line may be left: `base` takes boat's
    box token out. A sandbox made from the snapshot gets a new box token of its own from boat, so there the check is
    that none of its token lines is one the build sandbox had (compared by hash, never by value)."""
    r, u, bare = shlex.quote(release), shlex.quote(user), shlex.quote(release.removeprefix("v"))
    marker, stamp_file = shlex.quote(str(MARKER)), shlex.quote(str(IMAGE_STAMP))
    if build_token_hashes is None:
        token_check = """! grep -qs '^export ASCII_TOKEN=' "$home/.bashrc" || fail "boat's ASCII_TOKEN line is still in ~/.bashrc"\n"""
    else:
        old = " ".join(shlex.quote(h) for h in build_token_hashes if re.fullmatch(r"[0-9a-f]{64}", h))
        hashes = TOKEN_HASHES.replace("~", '"$home"')
        token_check = (
            f"for h in $({hashes}); do\n"
            f"  for old in {old or '-'}; do [ \"$h\" != \"$old\" ] || fail \"the build sandbox's box token is in the image\"; done\n"
            "done\n"
        )
    return f"""set -u
fail() {{ echo "check failed: $1"; exit 1; }}
sleep {int(settle_s)}
home=$(getent passwd {u} | cut -d: -f6)
[ -n "$home" ] && [ -d "$home" ] || fail "there is no user {user} with a home directory"
test -f {marker} || fail "no marker {MARKER}: base did not finish"
release=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' {marker}) \\
  || fail "the marker is not readable"
[ "$release" = {bare} ] || fail "the marker says $release, not {release}"
[ "$(cat {stamp_file} 2>/dev/null)" = {shlex.quote(stamp)} ] || fail "the image is not the one this run built"
root=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["root"])' {marker})
tag=$(git -C "$root" describe --tags --exact-match 2>/dev/null) || fail "the checkout is not on a release tag"
[ "$tag" = {r} ] || fail "the checkout is on $tag, not {release}"
test -x "$root/runtime/runner-venv/bin/python" || fail "the runner's Python is missing"
test ! -e "$home/.config/tico/runner.json" || fail "this runner is enrolled"
systemctl is-enabled --quiet {UNIT} || fail "{UNIT} is not enabled"
loginctl show-user {u} -p Linger | grep -qx Linger=yes || fail "lingering is off for {user}"
test ! -e /.dockerenv || fail "/.dockerenv exists: the runner would not update itself"
{token_check}! sudo grep -qs TICO_ENROLL_TOKEN /etc/environment "$home/.bashrc" "$home/.profile" || fail "an enroll code is on the disk"
echo "check passed"
"""


class Boat:
    """The boat CLI calls this script makes; `run` is subprocess.run, or a fake in tests. `made` holds every sandbox
    id boat named, so cleanup reaches each one, even one whose create then failed."""

    def __init__(self, run: Run = subprocess.run, say: Callable[[str], None] = print):
        self.run, self.say = run, say
        self.made: list[str] = []

    def _call(self, *args: str, timeout: float, capture: bool = True) -> subprocess.CompletedProcess:
        try:
            return self.run(["boat", *args], capture_output=capture, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused(f"boat {' '.join(args[:2])} did not answer within {int(timeout)} s") from None
        except OSError as exc:
            raise Refused(f"could not run boat: {exc}") from None

    def snapshot_exists(self, name: str) -> bool:
        """Whether a saved snapshot has this name. An answer that says neither is refused: never risk a replace."""
        p = self._call("snapshot", "shares", name, "--json", timeout=60)
        if p.returncode == 0:
            return True
        error = last_json(p.stdout) or last_json(p.stderr) or {}
        if error.get("code") in MISSING_CODES:
            return False
        raise Refused(f"could not tell whether snapshot {name} exists: {first_line(p.stderr or p.stdout)}")

    def new(self, label: str, source: str | None = None) -> str:
        args = ["new", "--no-env", "--name", label, "--json"]
        if source:
            args += ["--from", source]
        try:
            p = self._call(*args, timeout=NEW_TIMEOUT_S)
        except Refused as exc:
            raise Refused(f"{exc}: it may still make one, so look for a sandbox named {label} (boat list)") from None
        sandbox = sandbox_id(p.stdout)
        if sandbox:
            self.made.append(sandbox)
        if p.returncode or not sandbox or not ready_id(p.stdout):
            hint = "" if sandbox else f"; look for a sandbox named {label} (boat list)"
            raise Refused(f"boat new {label} failed: {first_line(p.stderr or p.stdout)}{hint}")
        self.say(f"sandbox {sandbox} ({label}) is ready")
        return sandbox

    def sh(self, sandbox: str, script: str, timeout: int) -> subprocess.CompletedProcess:
        return self._call(
            "exec", sandbox, "--timeout", str(timeout), "--", "bash", "-lc", script, timeout=timeout + 120
        )

    def build(self, sandbox: str, release: str, stamp: str) -> None:
        # Output streams to this terminal: the build takes minutes and its log is the record.
        p = self._call(
            "exec", sandbox, "--timeout", str(BUILD_TIMEOUT_S), "--", "bash", "-lc", build_script(release, stamp),
            timeout=BUILD_TIMEOUT_S + 120, capture=False,
        )  # fmt: skip
        if p.returncode:
            raise Refused(f"the base step failed on sandbox {sandbox} (output above)")

    def token_hashes(self, sandbox: str) -> list[str]:
        p = self.sh(sandbox, TOKEN_HASHES, CHECK_TIMEOUT_S)
        if p.returncode:
            raise Refused(f"sandbox {sandbox}: could not read its box token lines")
        return [line for line in (p.stdout or "").split() if re.fullmatch(r"[0-9a-f]{64}", line)]

    def check(self, sandbox: str, script: str, settle_s: int = 0) -> None:
        p = self.sh(sandbox, script, CHECK_TIMEOUT_S + settle_s)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode or "check passed" not in out:
            failed = [line for line in out.splitlines() if line.startswith("check failed")]
            raise Refused(f"sandbox {sandbox}: {failed[0] if failed else first_line(out) or 'the check did not run'}")
        self.say(f"sandbox {sandbox}: check passed")

    def save(self, sandbox: str, name: str) -> None:
        if self._call("snapshot", sandbox, name, "--json", timeout=1800).returncode:
            raise Refused(f"boat snapshot {sandbox} {name} failed")
        self.say(f"saved snapshot {name}")

    def delete(self, sandbox: str) -> bool:
        try:
            return self._call("delete", sandbox, "--yes", timeout=300).returncode == 0
        except Refused:
            return False

    def clean_up(self, keep: bool) -> None:
        """Delete every sandbox this run made, each on its own; never raises, so the run's own error stays the one
        reported. Any left over are named with the command that deletes them."""
        if keep:
            for sandbox in self.made:
                self.say(f"kept sandbox {sandbox} (--keep): delete it with boat delete {sandbox} --yes")
            return
        left = [sandbox for sandbox in self.made if not self.delete(sandbox)]
        for sandbox in left:
            self.say(f"warning: sandbox {sandbox} was not deleted and still costs money: boat delete {sandbox} --yes")


def json_lines(text: str | None) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def last_json(text: str | None) -> dict | None:
    found = json_lines(text)
    return found[-1] if found else None


def ready_id(text: str | None) -> str | None:
    """The sandbox id from `boat new --json` (JSONL; the `ready` event carries it)."""
    return next((str(e["id"]) for e in json_lines(text) if e.get("event") == "ready" and e.get("id")), None)


def sandbox_id(text: str | None) -> str | None:
    """Any sandbox id `boat new --json` printed, ready or not, so a create that failed late is still cleaned up."""
    return ready_id(text) or next((str(e["id"]) for e in json_lines(text) if e.get("id")), None)


def first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def refresh(boat: Boat, *, release: str, stem: str, keep: bool = False) -> str:
    """Build, check, save under a new name, check what was saved; returns the name. Every sandbox is deleted unless
    `keep`."""
    check_args(release, stem)
    name = published_name(stem)
    stamp = secrets.token_hex(16)
    if boat.snapshot_exists(name):
        raise Refused(f"snapshot {name} already exists: run again for a new suffix; a saved snapshot is never replaced")
    try:
        build = boat.new(f"{name}-build")
        hashes = boat.token_hashes(build)
        boat.say(f"building the base of {release} (several minutes)")
        boat.build(build, release, stamp)
        boat.check(build, check_script(release, stamp))
        # The suffix makes a clash unlikely; look again right before the save, which would replace a snapshot.
        if boat.snapshot_exists(name):
            raise Refused(f"snapshot {name} appeared while building: nothing saved")
        boat.save(build, name)
        try:
            trial = boat.new(f"{name}-check", source=name)
            boat.check(trial, check_script(release, stamp, settle_s=SETTLE_S, build_token_hashes=hashes), SETTLE_S)
        except Refused as exc:
            raise Refused(
                f"snapshot {name} was saved but failed its check, so do not point HQ at it "
                f"(remove it with boat snapshot rm {name}): {exc}"
            ) from None
    finally:
        boat.clean_up(keep)
    boat.say(
        f"done: snapshot {name} has release {release}, installed and not enrolled.\n"
        f"Next, a person sets HQ_BOAT_BASE_SNAPSHOT={name} on Tico HQ. Note its current value first: setting it back "
        "rolls back. Machines that exist keep their disk; new and replaced ones start from the new snapshot."
    )
    return name


def main(argv: Sequence[str] | None = None, boat: Boat | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m runner_support.base_snapshot", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--release", required=True, help="the release tag to build, like v0.6.81")
    parser.add_argument(
        "--name",
        required=True,
        help="the snapshot name's stem, like tico-runner-base-0-6-81 (no dots); a suffix is added",
    )
    parser.add_argument("--keep", action="store_true", help="keep the sandboxes to look at, instead of deleting them")
    args = parser.parse_args(argv)
    try:
        refresh(boat or Boat(), release=args.release, stem=args.name, keep=args.keep)
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
