"""Throwaway: a row dict that records a read of a key absent from it whose renamed partner is in it."""
import atexit, builtins, importlib, json, os, pkgutil, sys, types
from backend.migrations.renames_data import COLUMNS
PART = {}
for _t, _o, _n, _w in COLUMNS:
    PART.setdefault(_o, set()).add(_n)
    PART.setdefault(_n, set()).add(_o)
OUT = os.environ.get("TICO_SCANR_DIR", "/tmp/tico-scanr")
os.makedirs(OUT, exist_ok=True)
seen = {}
def note(kind, k, d):
    try:
        if not isinstance(k, str) or k not in PART or builtins.dict.__contains__(d, k): return
        other = [p for p in PART[k] if builtins.dict.__contains__(d, p)]
        if not other: return
        f = sys._getframe(2); fn = f.f_code.co_filename
        if "/tests/" in fn or "_scan_rows" in fn: return
        rel = fn.split("/backend/")[-1] if "/backend/" in fn else fn.split("/")[-1]
        line = f"{kind}\t{rel}:{f.f_lineno}\t{k}\t{other[0]}"
        if line not in seen:
            seen[line] = 1
            with open(os.path.join(OUT, f"{os.getpid()}.txt"), "a") as fh: fh.write(line + "\n")
    except Exception: pass
class Meta(type):
    def __instancecheck__(cls, x): return isinstance(x, builtins.dict)
    def __subclasscheck__(cls, x): return issubclass(x, builtins.dict)
class D(builtins.dict, metaclass=Meta):
    def get(self, k, default=None):
        note("get", k, self); return builtins.dict.get(self, k, default)
    def __contains__(self, k):
        note("in", k, self); return builtins.dict.__contains__(self, k)
    def __missing__(self, k):
        note("idx", k, self); raise KeyError(k)
def patch():
    import backend
    for m in pkgutil.walk_packages(backend.__path__, "backend."):
        if ".tests" in m.name or ".migrations." in m.name: continue
        try: importlib.import_module(m.name)
        except Exception: pass
    for name, mod in list(sys.modules.items()):
        if name.startswith(("backend.", "clients.", "connectors.")) and ".tests" not in name and isinstance(mod, types.ModuleType):
            try: src = open(mod.__file__).read()
            except Exception: continue
            if "fastapi" in src or "pydantic" in src or "dataclass" in src: continue
            if "dict" not in vars(mod): mod.dict = D
