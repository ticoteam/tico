#!/usr/bin/env bash
# Temporary (threads/perf): sample the benchmark server's GIL holder with py-spy and print the top frames.
set -u
pip install -q py-spy >/dev/null 2>&1 || python3 -m pip install -q py-spy >/dev/null 2>&1
"$(command -v py-spy)" record --subprocesses --gil --nonblocking --rate 100 --format raw -o /tmp/stacks.txt -- \
  python3 scripts/benchmark_threads.py "$@" > /tmp/bench.json 2>/tmp/bench.err
tail -5 /tmp/bench.err
python3 - <<'PY'
import collections, json
self_, incl, total = collections.Counter(), collections.Counter(), 0
for line in open("/tmp/stacks.txt"):
    stack, _, n = line.rstrip().rpartition(" ")
    n = int(n); total += n
    frames = stack.split(";")
    if any(f.startswith(("stream ", "writer ", "slow_consumer ", "scenario ", "main (")) and "benchmark_threads" in f
           for f in frames):
        continue
    def name(f):
        f = f.split(" (")[0] + " " + f.split("/")[-1].rstrip(")") if "(" in f else f
        return f
    names = [name(f) for f in frames]
    self_[names[-1]] += n
    for x in set(names):
        incl[x] += n
print("samples", total)
print("== inclusive")
for k, v in incl.most_common(70): print(f"{100*v/total:5.1f}% {k}")
print("== self")
for k, v in self_.most_common(40): print(f"{100*v/total:5.1f}% {k}")
PY
python3 -c "
import json;d=json.load(open('/tmp/bench.json'))
print(json.dumps({k:d[k] for k in ('room_writes','viewers','reconnect_burst','slow_consumer')},indent=1)[:3000])"
