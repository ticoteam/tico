"""Throwaway: records every JSON document's watched keys as written to SQLite during the test suite."""
import atexit, json, os, re, sqlite3, sys
OUT = os.environ.get("TICO_SCAN_DIR", "/tmp/tico-scan")
os.makedirs(OUT, exist_ok=True)
WATCH = set(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_scan_watch.txt")).read().split())
_seen = set()
_file = None
def _emit(line):
    global _file
    if line in _seen: return
    _seen.add(line)
    if _file is None:
        _file = open(os.path.join(OUT, f"{os.getpid()}.txt"), "a", buffering=1)
    _file.write(line + "\n")
def _walk(node, path, table, depth=0):
    if depth > 8: return
    if isinstance(node, dict):
        for k, v in node.items():
            if k in WATCH:
                _emit(f"{table}\t{path}.{k}")
            key = k if re.fullmatch(r"[a-z_]{1,30}", str(k)) else "*"
            _walk(v, f"{path}.{key}", table, depth + 1)
    elif isinstance(node, list):
        for v in node[:20]:
            _walk(v, path + "[]", table, depth + 1)
_ins = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.I)
_upd = re.compile(r"UPDATE\s+(\w+)\s+SET\s+", re.I)
def _columns(sql, n):
    m = _ins.search(sql)
    if m:
        cols = [c.strip() for c in m.group(2).split(",")]
        marks = [v.strip() for v in m.group(3).split(",")]
        out, i = [None] * n, 0
        for c, v in zip(cols, marks):
            if v == "?" and i < n:
                out[i] = f"{m.group(1)}.{c}"; i += 1
            elif "?" in v:
                i += v.count("?")
        return out
    m = _upd.search(sql)
    if m:
        out, i = [None] * n, 0
        for c, extra in re.findall(r"(\w+)\s*=\s*(\?|[^,?]*\?)", sql[m.end():].split(" WHERE ")[0]):
            if i < n:
                out[i] = f"{m.group(1)}.{c}"; i += 1
        return out
    return [None] * n
_date = re.compile(r"\d{4}-\d{2}-\d{2}")
_clock = re.compile(r"\d{2}:\d{2}")
def _scan(sql, params):
    try:
        if not isinstance(params, (list, tuple)) or not re.search(r"\b(INSERT|UPDATE)\b", sql, re.I): return
        names = None
        for i, p in enumerate(params):
            if isinstance(p, str) and (_date.fullmatch(p) or _clock.fullmatch(p)):
                if names is None: names = _columns(sql, len(params))
                if names[i]: _emit(("DATE" if _date.fullmatch(p) else "CLOCK") + "\t" + names[i])
            if isinstance(p, str) and len(p) > 1 and p[0] in "{[" and len(p) < 2_000_000:
                try: doc = json.loads(p)
                except ValueError: continue
                if names is None: names = _columns(sql, len(params))
                _walk(doc, "$", names[i] or ("?" + sql[:50].replace("\n", " ").replace("\t", " ")))
    except Exception:
        pass
class _Mixin:
    def execute(self, sql, *a):
        if a: _scan(sql, a[0])
        return super().execute(sql, *a)
    def executemany(self, sql, seq):
        seq = list(seq)
        for p in seq[:50]: _scan(sql, p)
        return super().executemany(sql, seq)
_real = sqlite3.connect
_made = {}
def connect(*a, **k):
    base = k.get("factory") or sqlite3.Connection
    if not issubclass(base, _Mixin):
        base = _made.setdefault(base, type("Scan" + base.__name__, (_Mixin, base), {}))
    k["factory"] = base
    return _real(*a, **k)
sqlite3.connect = connect
