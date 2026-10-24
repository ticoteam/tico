"""Every repo file a test was seen opening or executing must select that test."""
import json, subprocess, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, ".")
from scripts import pr_checks as c

seen = defaultdict(lambda: defaultdict(set))
for f in Path(".audit-out").glob("*.json"):
    for t, kinds in json.loads(f.read_text()).items():
        for k, v in kinds.items():
            seen[t][k] |= set(v)
g = c.DependencyMap(c.ROOT)
tracked = set(g.files)
tests = [t for t in seen if c.is_test(t)]
print("AUDIT tests observed", len(tests))
cache, misses, checked = {}, defaultdict(set), 0
for t in tests:
    files = (seen[t]["open"] | seen[t]["exec"]) & tracked
    for r in files:
        if c.unread(r) or c.full_suite(r):
            continue
        if r not in cache:
            cache[r] = g.affected([r], [r])
        checked += 1
        if t not in cache[r]:
            misses[t].add(r)
print("AUDIT pairs checked", checked, "files", len(cache))
print("AUDIT misses", sum(map(len, misses.values())))
for t in sorted(misses):
    print("MISS", t, "<-", " ".join(sorted(misses[t])[:12]))
lists = defaultdict(set)
for t in tests:
    for d in seen[t]["list"]:
        lists[d].add(t)
print("AUDIT listed dirs", len(lists))
for d, ts in sorted(lists.items()):
    sample = [r for r in tracked if r.startswith(d + "/") and "/" not in r[len(d) + 1:]][:1]
    if sample:
        hit = g.affected(sample, sample)
        bad = sorted(ts - hit)
        if bad:
            print("LISTMISS", d, "<-", " ".join(bad[:8]))
