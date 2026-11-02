import glob, os
out = set()
for f in glob.glob(os.environ.get("TICO_SCANR_DIR", "/tmp/tico-scanr") + "/*.txt"):
    out |= set(open(f).read().splitlines())
print("SCANBEGIN")
for l in sorted(out): print("SCAN\t" + l)
print("SCANEND", len(out))
