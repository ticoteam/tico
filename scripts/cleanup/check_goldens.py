#!/usr/bin/env python3
"""Whether the contract goldens changed only by the rename map: the goldens at REF, through the map, against the ones here.

    python3 scripts/cleanup/check_goldens.py REF

Prints each difference; exits 1 when there is one. Run after `python3 -m e2e.run --kind contract --record`.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from e2e import renames  # noqa: E402

GOLDENS = "e2e/goldens"


def differences(old, new, path=""):
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            where = f"{path}.{key}"
            if key not in new:
                yield f"only at REF: {where}"
            elif key not in old:
                yield f"only here: {where}"
            else:
                yield from differences(old[key], new[key], where)
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            yield f"{path}: {len(old)} items at REF, {len(new)} here"
        for i, (a, b) in enumerate(zip(old, new, strict=False)):
            yield from differences(a, b, f"{path}[{i}]")
    elif old != new:
        yield f"{path}: {old!r} at REF, {new!r} here"


def main(ref):
    rename_map = renames.load(ROOT / "scripts/cleanup/renames.yaml")
    found = 0
    for path in sorted((ROOT / GOLDENS).glob("*.json")):
        shown = subprocess.run(["git", "show", f"{ref}:{GOLDENS}/{path.name}"], cwd=ROOT, capture_output=True, check=False, text=True)
        if shown.returncode:
            print(f"{path.stem}: new golden")
            continue
        old = renames.apply_fields(json.loads(shown.stdout)["body"], renames.golden_fields(rename_map, path.stem))
        new = renames.drop_added(json.loads(path.read_text())["body"], rename_map["golden_added"].get(path.stem))
        for line in differences(old, new):
            print(f"{path.stem} {line}")
            found += 1
    print(f"{found} differences")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
