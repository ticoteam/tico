#!/usr/bin/env bash
# Temporary (threads/perf): sample the benchmark server's GIL holder with py-spy and print the top frames.
set -u
BENCH_SAMPLE=/tmp/stacks.txt python3 scripts/benchmark_threads.py "$@" > /tmp/bench.json 2>/tmp/bench.err
head -c 600 /tmp/stacks.txt; echo
tail -5 /tmp/bench.err
python3 - <<'PY'
import collections, json
self_, incl, total = collections.Counter(), collections.Counter(), 0
for line in open("/tmp/stacks.txt"):
    stack, _, n = line.rstrip().rpartition(" ")
    n = int(n)
    frames = stack.split(";")
    if False:
        continue
    total += n
    def name(f):
        return f.replace("/home/runner/work/tico/tico/", "")
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
