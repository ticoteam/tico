"""Records, per test file, the repository files it opens (imports included), lists and executes."""
import json, os, sys
from collections import defaultdict
from pathlib import Path

ROOT = os.path.realpath(os.getcwd())
OUT = Path(ROOT) / ".audit-out"
current = ["<session>"]
seen = defaultdict(lambda: defaultdict(set))


def rel(path):
    try:
        path = os.fsdecode(path)
    except TypeError:
        return None
    if not path:
        return None
    full = os.path.realpath(os.path.join(os.getcwd(), path))
    if not full.startswith(ROOT + os.sep):
        return None
    out = os.path.relpath(full, ROOT)
    if "/__pycache__/" in "/" + out:
        head, name = out.rsplit("__pycache__/", 1)
        out = head + name.split(".", 1)[0] + ".py"
    return out


def hook(event, args):
    try:
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            r = rel(args[0])
            if r:
                seen[current[0]]["open"].add(r)
        elif event in ("os.listdir", "os.scandir"):
            r = rel(args[0] if args and args[0] is not None else ".")
            if r:
                seen[current[0]]["list"].add(r)
        elif event == "glob.glob":
            seen[current[0]]["glob"].add(str(args[0]) + "|" + str(args[1] if len(args) > 1 else ""))
        elif event == "subprocess.Popen":
            argv = args[1] if len(args) > 1 else []
            argv = [argv] if isinstance(argv, (str, bytes)) else list(argv or [])
            for i, a in enumerate(argv):
                a = os.fsdecode(a) if isinstance(a, (str, bytes, os.PathLike)) else ""
                r = rel(a) if a and ("/" in a or os.path.exists(os.path.join(ROOT, a))) else None
                if r:
                    seen[current[0]]["exec"].add(r)
                if i and os.fsdecode(argv[i - 1]) == "-m":
                    seen[current[0]]["module"].add(a)
    except Exception:
        pass


sys.addaudithook(hook)

import pytest  # noqa: E402


@pytest.hookimpl(hookwrapper=True)
def pytest_pycollect_makemodule(module_path, parent):
    previous = current[0]
    current[0] = rel(module_path) or previous
    yield
    current[0] = previous


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    current[0] = rel(item.path) or "<unknown>"
    yield


def pytest_unconfigure(config):
    OUT.mkdir(exist_ok=True)
    (OUT / f"{os.getpid()}.json").write_text(json.dumps({t: {k: sorted(v) for k, v in kinds.items()} for t, kinds in seen.items()}))
