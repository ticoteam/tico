"""Run the end-to-end kinds and print one verdict per kind: `python -m e2e.run` (see e2e/README.md).

Each kind is one pytest run over its test files, with the slow tests included (the default pytest run leaves them
out). The exit code is 0 only when every kind passes; a skipped kind is not a pass."""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from e2e import renames

ROOT = Path(__file__).resolve().parents[1]
KINDS = {
    "checks": ["e2e/tests/test_renames.py", "e2e/tests/test_preserve.py"],
    "contract": ["e2e/tests/test_contract.py"],
    "upgrade": ["e2e/tests/test_upgrade.py"],
    "rollback": ["e2e/tests/test_rollback.py"],
    "roles": ["e2e/tests/test_roles.py"],
}


def run_kind(kind, files, env, verbose):
    command = [sys.executable, "-m", "pytest", "-q", "-rs", "-o", "addopts=", "-m", "", "-p", "no:cacheprovider", *files]
    started = time.time()
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    seconds = time.time() - started
    output = result.stdout + result.stderr
    verdict = "PASS" if result.returncode == 0 else "FAIL"
    summary = next((line for line in reversed(output.splitlines()) if " in " in line and ("passed" in line or "failed" in line
                    or "skipped" in line or "error" in line)), "").strip("= ")
    if result.returncode == 0 and " skipped" in summary:
        verdict = "SKIP"
    print(f"{kind:<10} {verdict:<5} {summary} ({seconds:.1f}s)", flush=True)
    if verdict != "PASS" or verbose:
        print(output.rstrip(), flush=True)
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m e2e.run", description=__doc__.split("\n")[0])
    parser.add_argument("--kind", action="append", choices=sorted(KINDS), help="a kind to run (repeatable; default all)")
    parser.add_argument("--rename-map", metavar="PATH", help="a YAML rename map the goldens and before-states are read through")
    parser.add_argument("--record", action="store_true", help="re-record the contract goldens from this code (review the diff)")
    parser.add_argument("--old-server-ref", metavar="REF",
                        help="the rollback's old server is the checkout at this git ref (a commit in this checkout); "
                             "without it the rollback kind checks this checkout's server on a copy only")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each kind's full pytest output")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.rename_map:
        path = Path(args.rename_map).resolve()
        renames.load(path)            # refuse a malformed map before any kind runs
        env[renames.ENV] = str(path)
    if args.record:
        env["TICO_E2E_RECORD"] = "1"
    if args.old_server_ref:
        env["TICO_E2E_OLD_SERVER_REF"] = args.old_server_ref
    kinds = args.kind or list(KINDS)
    verdicts = {kind: run_kind(kind, KINDS[kind], env, args.verbose) for kind in kinds}
    failed = [kind for kind, verdict in verdicts.items() if verdict != "PASS"]
    print("e2e: all %d kinds passed" % len(kinds) if not failed else "e2e: not passing: " + ", ".join(failed), flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
