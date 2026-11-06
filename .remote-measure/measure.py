"""Historical diff coverage, one shard: run the shard's default tests with per-test coverage contexts, then report, for each
historical PR, which of its target statement lines ran under a test that PR's own selection includes.

Run as: python3 .remote-measure/measure.py SHARD   (prints one RESULT line of JSON)."""
import json, os, subprocess, sys, time
from pathlib import Path

from coverage import Coverage

shard = int(sys.argv[1])
inp = json.loads(Path(".remote-measure/measure_input.json").read_text())
tests = inp["shards"][shard]
Path("ci-results").mkdir(exist_ok=True)
Path("ci-results/measure.ini").write_text("[run]\nomit =\n    */tests/*\n    */migrations/*\n")
data = f"ci-results/measure-{shard}.data"
env = dict(os.environ, COVERAGE_CORE="sysmon", COVERAGE_FILE=data)
workers = os.environ.get("WORKERS", "16")
cmd = [
    sys.executable, "-m", "pytest", "-q", "-rfE", "-n", workers, "--api-database-snapshots", "--maxschedchunk=2",
    "-p", "no:cacheprovider", "--cov=backend", "--cov=clients", "--cov-config=ci-results/measure.ini",
    "--cov-report=", "--cov-context=test", *tests,
]
start = time.perf_counter()
proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
wall = time.perf_counter() - start
print(f"shard {shard}: rc={proc.returncode} wall={wall:.0f}s tests={len(tests)} workers={workers}", flush=True)
for line in proc.stdout.splitlines():
    if line.startswith(("FAILED", "ERROR")) or " passed" in line or " failed" in line:
        print("   ", line[:200], flush=True)
if proc.returncode:
    print(proc.stderr[-2000:], flush=True)

store = Coverage(data_file=data, config_file=False)
store.load()
cd = store.get_data()
contexts = cd.measured_contexts()
print(f"shard {shard}: measured contexts {len(contexts)}", flush=True)

selections = inp["selections"]
shard_tests = set(tests)
statement_cache = {}


def statements(name):
    if name not in statement_cache:
        path = os.path.abspath(name)
        statement_cache[name] = set(Coverage(config_file=False).analysis2(path)[1]) if os.path.isfile(path) else set()
    return statement_cache[name]


results = {}
for pr, files in inp["targets"].items():
    own = set(selections[pr]) & shard_tests
    out = {}
    for name, lines in files.items():
        want = sorted(line for line in lines if line in statements(name))
        if not want:
            continue
        absolute = os.path.realpath(os.path.abspath(name))
        by_line = cd.contexts_by_lineno(absolute) if os.path.isfile(name) else {}
        covered = [
            line
            for line in want
            if {c.split("|")[0].split("::")[0] for c in by_line.get(line, []) if c} & own
        ]
        out[name] = {"statements": want, "covered": covered}
    results[pr] = out

report = {"shard": shard, "rc": proc.returncode, "contexts": len(contexts), "results": results}
Path(f"ci-results/measure-shard-{shard}.json").write_text(json.dumps(report))
print("RESULT " + json.dumps(report), flush=True)
