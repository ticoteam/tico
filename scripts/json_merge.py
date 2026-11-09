#!/usr/bin/env python3
"""Merge two edits of docs/openapi/v2.json. git runs this as the merge driver for that file (.gitattributes).

git calls it when both sides changed the file, with git's %O %A %B:
    python3 scripts/json_merge.py BASE OURS THEIRS
On a clean merge it writes the result to OURS and exits 0. When the sides change the same value differently it
leaves OURS as it is, names the path on stderr and exits 1, which git reports as a conflict like any other.
CONTRIBUTING.md registers the driver in each clone; without it git merges the file as text.

Objects merge key by key, so edits to different keys combine. A list or a scalar merges only when one side left
it alone. Key order follows backend/openapi_v2.py: spec() sorts the paths, the schemas and each operation's
responses, and those stay sorted. Every other object keeps the order of the side it came from, and a key only
the other side adds goes just after its predecessor there. Two sides adding keys after the same key conflict.

The result is rendered as backend/openapi_v2.render() renders it. That it equals what the generator writes from
the merged source is checked by `python3 -m backend.openapi_v2 --check`, which CI runs on every pull request.
"""

import json
import sys

MISSING = object()


class Conflict(Exception):
    pass


def sorted_here(keys):
    """Whether the generator writes this object's keys sorted. `keys` are the object keys from the root to it."""
    return (
        keys == ("paths",)
        or keys == ("components", "schemas")
        or (len(keys) == 4 and keys[0] == "paths" and keys[3] == "responses")
    )


def merge_value(keys, base, ours, theirs):
    # Objects always merge key by key, even when they are equal: dict equality ignores key order, and the order is
    # part of the output.
    if isinstance(ours, dict) and isinstance(theirs, dict) and (base is MISSING or isinstance(base, dict)):
        return merge_object(keys, {} if base is MISSING else base, ours, theirs)
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    raise Conflict("%s: both sides change it differently" % (".".join(keys) or "the root"))


def merge_object(keys, base, ours, theirs):
    merged = {}
    for key in dict.fromkeys([*base, *ours, *theirs]):
        value = merge_value(keys + (key,), base.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING))
        if value is not MISSING:
            merged[key] = value
    if sorted_here(keys):
        order = sorted(merged)
    else:
        order = key_order(keys, base, ours, theirs, merged)
    return {key: merged[key] for key in order}


def key_order(keys, base, ours, theirs, merged):
    """Ours' order, with each key only theirs adds placed after its predecessor in theirs."""
    ours_keys = list(ours)
    order = [key for key in ours_keys if key in merged]
    added_by_ours_after = {
        ours_keys[i - 1] if i else None for i, key in enumerate(ours_keys) if key not in base and key in merged
    }
    anchor = None
    for key in theirs:
        if key not in merged:
            continue
        if key not in ours and key not in base:
            if anchor in added_by_ours_after:
                raise Conflict("%s: both sides add keys after the same key" % ".".join(keys + (key,)))
            order.insert(0 if anchor is None else order.index(anchor) + 1, key)
        anchor = key
    return order


def load(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return {} if not text.strip() else json.loads(text)


def main(argv):
    if len(argv) != 3:
        print("usage: json_merge.py BASE OURS THEIRS", file=sys.stderr)
        return 2
    base_path, ours_path, theirs_path = argv
    try:
        merged = merge_value((), load(base_path), load(ours_path), load(theirs_path))
    except Conflict as conflict:
        print(
            "json_merge: conflict at %s; resolve the source, then python3 -m backend.openapi_v2" % conflict,
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print("json_merge: cannot merge: %s" % error, file=sys.stderr)
        return 1
    with open(ours_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(merged, indent=2, sort_keys=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
