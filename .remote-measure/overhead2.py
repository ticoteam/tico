"""Wall and CPU time of one shard-sized test list (4 workers): plain runs around two coverage configs."""
import json, os, resource, subprocess, sys, time
from pathlib import Path

tests = json.loads(Path(".remote-measure/overhead_input.json").read_text())["tests"]
base = [sys.executable, "-m", "pytest", "-q", "-n", "4", "--api-database-snapshots", "--maxschedchunk=2", "-p", "no:cacheprovider"]
Path("ci-results").mkdir(exist_ok=True)
Path("ci-results/omit.ini").write_text("[run]\nomit =\n    */tests/*\n    */migrations/*\n")


def run(label, extra, data=None):
    env = dict(os.environ)
    if data:
        env.update({"COVERAGE_CORE": "sysmon", "COVERAGE_FILE": data})
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    proc = subprocess.run(base + extra + tests, env=env, capture_output=True, text=True)
    wall = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    text = proc.stdout + proc.stderr
    summary = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line][-1:]
    size = os.path.getsize(data) if data and os.path.exists(data) else 0
    print(f"{label}: rc={proc.returncode} wall={wall:.1f}s cpu={cpu:.1f}s coverage_warnings={text.count('CoverageWarning')} data_bytes={size}", flush=True)
    for line in summary:
        print("   ", line[:160], flush=True)
    if proc.returncode:
        print(text[-1500:], flush=True)
    return proc.returncode, wall, cpu


print("python", sys.version.split()[0], "tests", len(tests), flush=True)
rows = {}
rows["plain-1"] = run("plain-1", [])
rows["all"] = run("cov-all (--cov=, omit tests and migrations)", ["--cov=", "--cov-config=ci-results/omit.ini", "--cov-report="], "ci-results/measure-all.data")
rows["plain-2"] = run("plain-2", [])
rows["src"] = run("cov-src (--cov=backend --cov=clients, same omit)", ["--cov=backend", "--cov=clients", "--cov-config=ci-results/omit.ini", "--cov-report="], "ci-results/measure-src.data")
rows["plain-3"] = run("plain-3", [])
plain = [rows[k][1] for k in ("plain-1", "plain-2", "plain-3")]
plain_cpu = [rows[k][2] for k in ("plain-1", "plain-2", "plain-3")]
base_wall = sum(plain) / len(plain)
base_cpu = sum(plain_cpu) / len(plain_cpu)
for key in ("all", "src"):
    rc, wall, cpu = rows[key]
    print(f"overhead {key}: rc={rc} {100 * (wall / base_wall - 1):+.1f}% wall vs mean plain {base_wall:.1f}s, {100 * (cpu / base_cpu - 1):+.1f}% cpu", flush=True)
print("plain wall spread:", [round(x, 1) for x in plain], flush=True)
