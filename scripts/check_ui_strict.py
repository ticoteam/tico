#!/usr/bin/env python3
"""The UI's strict rule: every UI script is TypeScript, and every .ts file type-checks strictly.

    python3 scripts/check_ui_strict.py                fail on the rules below, as the working tree stands
    python3 scripts/check_ui_strict.py --base REF     also fail when a count rose against REF
    python3 scripts/check_ui_strict.py --write        lower each strict count to today's (never raise one; a file at
                                                      zero loses its piece)

Mirrors scripts/check_pyright_ratchet.py. The rules:

1. No JavaScript under ui/ outside ui/vendor/ (third-party code), ui/tests/ (the Node browser scripts, *.cjs) and
   ALLOWED_JS (the one generated bundle, ui/assets/observability-vendors.js) and KEPT_JS (the classic scripts that stay
   JavaScript, each with its reason). Every other UI script is a .ts file.
2. Every .ts file under ui/ is strict: tsc with ui/tsconfig.strict.json reports nothing in it. A file with findings has a
   piece under ui/baselines/strict/<path>.json, {"count": N}, its findings today. A piece only goes down; a file at zero
   has no piece.
3. A piece may be added for one case only: a .ts file that is a rename of a .js file at REF (same path, .js -> .ts).
   scripts/ui_flip.py writes those. Any other new .ts file must be at zero.
4. Counts never rise against REF. REF may hold the pieces, or the single list from before them (ui/strict-legacy.json).
5. Shift-left, in .ts files with no piece: a repeated cast (`x as T`), a repeated non-null assertion (`x!`), or `any`
   (`: any`, `as any`, `<any>`, `, any`). Comments and strings are not read. Each hit is a failure: a strict file is clean,
   so today's hits are listed for a follow-up, not baselined.
6. A strict file carries no // @ts-nocheck or // @ts-ignore. // @ts-expect-error, with its reason, is allowed.
7. ui/tsconfig.strict.json keeps every strict flag on, and its include and exclude lists stay as they are.
8. ui/index.html has a blank line between two <script src> tags.

Runs in a few seconds: one tsc pass over the UI. The pinned TypeScript is fetched with npx, as ui_baseline.py does.
"""

import argparse
import bisect
import collections
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import ui_baseline  # the per-file baseline paths and the script-tag rule, shared with the ratchets

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "ui/tsconfig.strict.json"
LEGACY = "ui/strict-legacy.json"  # the single list before the pieces; read from REF, never written
STRICT = "strict"
TSC = ["npx", "--yes", "-p", "typescript@5.9.3", "tsc", "-p", CONFIG, "--pretty", "false"]
TSC_LINE = re.compile(r"^(?P<file>[^()\n]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")
TSC_GLOBAL = re.compile(r"^error (?P<code>TS\d+): (?P<msg>.*)$")
REQUIRED_FLAGS = (
    "strict",
    "noUncheckedIndexedAccess",
    "noImplicitOverride",
    "exactOptionalPropertyTypes",
    "noFallthroughCasesInSwitch",
    "noUnusedLocals",
)
REQUIRED_INCLUDE = ("./**/*.ts", "./**/*.js")
# Vendored, generated and npm-package code: the only folders the config leaves out.
ALLOWED_EXCLUDE = {"./vendor/**", "./assets/**", "./node_modules/**", "./observability-vendors.ts"}
# The one generated bundle (npm run build:observability): the only JavaScript that may sit under ui/ outside tests/.
ALLOWED_JS = {"ui/assets/observability-vendors.js"}
# Classic scripts that stay JavaScript, each with its reason. Each declares a global or a JSDoc-typed function that a strict
# .ts file reads. As .ts, the script loses its JSDoc types or its initial values type as never or implicit any, and the
# reader stops type-checking. Typing these by hand is the follow-up. scripts/ui_flip.py keeps them as they are.
KEPT_JS = {
    "ui/app/finance.js": "the globals FIN and FIN_ACCESS: as .ts they are implicit any where finance-attribution.ts reads them",
    "ui/app/automations.js": "the global AU: as .ts it is implicit any where the automations files read it",
    "ui/app/bot-page.js": "the global BOT: as .ts it is implicit any where a strict .ts file reads it",
    "ui/app/welcome.js": "the global ONB: as .ts it is implicit any where a strict .ts file reads it",
    "ui/app/settings-webhooks.js": "the global WH_TRIGGERS: as .ts it is implicit any where a strict .ts file reads it",
    "ui/app/hub-v2.js": "v2Get: a strict .ts file calls it with its JSDoc signature, and its arguments become required",
    "ui/app/avatars.js": "avatar: a strict .ts file calls it with its JSDoc signature, and its arguments become required",
    "ui/sw.js": "service worker, served raw at /sw.js",
}
SKIP_TS = ("ui/vendor/", "ui/assets/", "ui/node_modules/")
DIRECTIVE = re.compile(r"@ts-(nocheck|ignore)\b")


class Finding:
    def __init__(self, file, line, col, code, msg):
        self.file, self.line, self.col, self.code, self.msg = file, line, col, code, msg

    def text(self):
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.msg}"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def git_show(ref, path):
    done = git("show", f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def exists_at(ref, path):
    return git("cat-file", "-e", f"{ref}:{path}").returncode == 0


def ts_in_scope(rel):
    return rel.startswith("ui/") and rel.endswith(".ts") and not rel.endswith(".d.ts") and not rel.startswith(SKIP_TS)


def ts_on_disk():
    return sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "ui").rglob("*.ts")
        if ts_in_scope(path.relative_to(ROOT).as_posix())
    )


def js_on_disk():
    """Every JavaScript or Node script under ui/ (node_modules aside), as a path from the repo root."""
    found = []
    for base, dirs, files in os.walk(ROOT / "ui"):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        for name in files:
            if name.endswith((".js", ".mjs", ".cjs")):
                found.append((Path(base) / name).relative_to(ROOT).as_posix())
    return sorted(found)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def run_tsc():
    """(findings in source files, configuration errors). A TS5xxx code is an option error, reported on the config
    file or with no file at all: a config error, whichever file tsc names."""
    done = subprocess.run(TSC, cwd=ROOT, capture_output=True, text=True, check=False)
    findings, config_errors = [], []
    for raw in done.stdout.splitlines():
        m = TSC_LINE.match(raw)
        if m:
            file = m["file"].replace("\\", "/")
            if file == CONFIG or m["code"].startswith("TS5"):
                config_errors.append(raw)
            else:
                findings.append(Finding(file, int(m["line"]), int(m["col"]), m["code"], m["msg"]))
            continue
        if TSC_GLOBAL.match(raw):
            config_errors.append(raw)
    if done.returncode not in (0, 1, 2) or (done.returncode != 0 and not findings and not config_errors):
        sys.exit(f"tsc exited {done.returncode} without diagnostics:\n{done.stdout}{done.stderr}")
    return findings, config_errors


def load_pieces():
    """{file: count} of the strict pieces in the working tree."""
    return ui_baseline.load_layout(STRICT)


def pieces_at(ref):
    """{file: count} of the strict pieces at a git ref, or None when the ref has none."""
    folder = f"{ui_baseline.LAYOUT}/{STRICT}"
    listed = git("ls-tree", "-r", "-z", "--name-only", ref, "--", folder)
    if listed.returncode != 0:
        return None
    names = [name for name in listed.stdout.split("\0") if name.endswith(".json")]
    if not names:
        return None
    blobs = ui_baseline.blobs_at(ref, names)
    return {
        "ui/" + name[len(folder) + 1 : -len(".json")]: int(json.loads(blob)["count"])
        for name, blob in zip(names, blobs, strict=True)
    }


def base_counts(ref):
    """{file: count} of the strict baseline at REF: its pieces, else the single list from before them. None when REF
    has neither (the ref is older than the ratchet)."""
    pieces = pieces_at(ref)
    if pieces is not None:
        return pieces
    old = git_show(ref, LEGACY)
    if old is not None:
        return {path: int(count) for path, count in json.loads(old).get("files", {}).items()}
    return None


def is_rename_of_js(ref, rel):
    """Whether rel is a .ts file that is the .js file of the same path at REF, renamed (scripts/ui_flip.py)."""
    return rel.endswith(".ts") and exists_at(ref, rel[:-3] + ".js") and not exists_at(ref, rel)


def check_config():
    try:
        config = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{CONFIG}: cannot be read as JSON ({error})"]
    problems = []
    options = config.get("compilerOptions", {})
    for flag in REQUIRED_FLAGS:
        if options.get(flag) is not True:
            problems.append(f"{CONFIG}: {flag} must be true (the strict rule)")
    include = config.get("include", [])
    for pattern in REQUIRED_INCLUDE:
        if pattern not in include:
            problems.append(f'{CONFIG}: include must list "{pattern}" (every file is checked, so the globals resolve)')
    for pattern in config.get("exclude", []):
        if pattern not in ALLOWED_EXCLUDE:
            problems.append(f"{CONFIG}: exclude {pattern} is not allowed (excluded code is not checked)")
    return problems


def check_js():
    """Rule 1: no JavaScript under ui/ except vendor code, the Node test scripts and the generated bundle."""
    return [
        f"{rel}: JavaScript under ui/; write it as TypeScript (a strict .ts file) instead"
        for rel in js_on_disk()
        if not rel.startswith(("ui/vendor/", "ui/tests/")) and rel not in ALLOWED_JS and rel not in KEPT_JS
    ]


def check_strict(by_file, pieces, on_disk):
    problems = []
    for rel in sorted(by_file):
        found = by_file[rel]
        allowed = pieces.get(rel)
        if allowed is None:
            problems.append(f"NOT STRICT {rel}: {len(found)} finding(s); a .ts file without a piece must pass")
        elif len(found) > allowed:
            problems.append(f"GREW {rel}: {len(found)} finding(s), its piece allows {allowed}; fix them instead")
        else:
            continue
        problems += [f"    {f.text()}" for f in sorted(found, key=lambda f: (f.line, f.col, f.code))]
    for rel, allowed in sorted(pieces.items()):
        now = len(by_file.get(rel, []))
        if rel not in on_disk:
            problems.append(f"STALE {rel}: no such .ts file; remove its piece (--write does)")
        elif now == 0:
            problems.append(f"CLEAN {rel}: passes strict now; its piece goes (--write removes it)")
        elif now < allowed:
            problems.append(f"LOWER {rel}: {now} finding(s), its piece says {allowed}; run --write")
    return problems


def check_growth(pieces, base, ref):
    """Rules 3 and 4 against REF: a piece is new only for a rename of a .js file, and no count rises."""
    problems = []
    for rel, count in sorted(pieces.items()):
        was = base.get(rel)
        if was is None:
            if not is_rename_of_js(ref, rel):
                problems.append(
                    f"NEW BASELINE {rel}: {count} finding(s) in a .ts file that is not a rename of a .js file at {ref}; "
                    "a new file must pass, so fix the findings"
                )
        elif count > was:
            problems.append(f"GREW {rel}: {was} -> {count} since {ref}; fix the findings instead")
    return problems


def check_directives(pieces):
    problems = []
    for rel in ts_on_disk():
        if rel in pieces:
            continue
        for number, line in enumerate(read(rel).splitlines(), 1):
            if DIRECTIVE.search(line):
                problems.append(
                    f"{rel}:{number}: a strict file may not use // @ts-nocheck or // @ts-ignore; "
                    "fix the error, or use // @ts-expect-error with the reason"
                )
    return problems


def check_script_tags():
    text = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    return [
        f"ui/index.html:{number}: a <script src> directly after another; leave a blank line between them "
        "(python3 scripts/ui_baseline.py migrate adds it)"
        for number in ui_baseline.adjacent_script_lines(text)
    ]


# ---- Rule 5: shift-left. Reads TypeScript tokens; comments and strings are never looked into. ----

Tok = collections.namedtuple("Tok", "kind v offset nl")
TOKEN = re.compile(
    r"""(?P<ws>\s+)|(?P<comment>//[^\n]*|/\*.*?\*/)|(?P<str>'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*")"""
    r"""|(?P<tpl>`(?:\\.|[^`\\])*`)|(?P<word>[A-Za-z_$][\w$]*)|(?P<num>\d[\w.]*)"""
    r"""|(?P<op>\?\.(?!\d)|=>|===|!==|==|!=|&&|\|\||\?\?|\.\.\.|[^\s\w$])""",
    re.S,
)
KEYWORDS = frozenset(
    "return typeof await yield case in of void delete else do instanceof new throw extends implements const let var "
    "function class import export from if while for switch catch default as is keyof readonly satisfies".split()
)
ANY_AFTER = frozenset({":", "as", "<", ",", "|", "&"})


def tokens(text):
    out, pos, nl = [], 0, False
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if m is None:  # the op alternative takes any single character, so this does not happen; never loop on it
            pos += 1
            continue
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            nl = nl or "\n" in m.group()
        else:
            out.append(Tok(kind, m.group(), m.start(), nl))
            nl = False
        pos = m.end()
    return out


def _open_for(toks, j):
    """The index of the bracket that opens the one closing at toks[j], or -1."""
    depth = 0
    for k in range(j, -1, -1):
        if toks[k].kind != "op":
            continue
        if toks[k].v in (")", "]", "}"):
            depth += 1
        elif toks[k].v in ("(", "[", "{"):
            depth -= 1
            if depth == 0:
                return k
    return -1


TYPE_ARG_OPS = frozenset({".", ",", "[", "]", "(", ")", "{", "}", ":", "|", "&", "=>", "<", ">"})


def _type_args_open(toks, j):
    """The index of the `<` that opens the type-argument list closing at toks[j] (a `>`), or -1. The `<` must follow a
    name, and the list may hold only type-like tokens: `a < b && c > (d)` is an expression, not a list."""
    depth = 0
    for k in range(j, max(-1, j - 60), -1):
        t = toks[k]
        if t.kind == "op":
            if t.v == ">":
                depth += 1
            elif t.v == "<":
                depth -= 1
                if depth == 0:
                    named = k > 0 and toks[k - 1].kind == "word" and toks[k - 1].v not in KEYWORDS
                    return k if named else -1
            elif t.v not in TYPE_ARG_OPS:
                return -1
        elif t.kind not in ("word", "str", "num"):
            return -1
    return -1


def _is_name(t):
    return t.kind == "word" and t.v not in KEYWORDS


def _chain_start(t):
    """Whether a token can be the left end of a member chain: a name, a literal, or the end of a call or an index."""
    return _is_name(t) or t.kind in ("num", "str", "tpl") or (t.kind == "op" and t.v in (")", "]"))


def _operand(toks, i):
    """The text of the expression that ends just before toks[i]: a name, a member chain, a call or an index. A call's
    type arguments are part of it, so `a.f<T>(x)` and `b.f<T>(x)` are two different expressions. The chain goes back
    only through a dot, so the `if (ok)` before `dialog.q(x)!` is not part of it."""
    start, j = i, i - 1
    while j >= 0:
        t = toks[j]
        if t.kind == "op" and t.v in (")", "]"):
            k = _open_for(toks, j)
            if k < 0:
                break
            start, j = k, k - 1
            if toks[k].v == "(" and j >= 0 and toks[j].kind == "op" and toks[j].v == ">":
                m = _type_args_open(toks, j)
                if m >= 0:
                    start, j = m, m - 1
            if j >= 0 and _is_name(toks[j]) and toks[k].v in ("(", "["):
                start, j = j, j - 1  # the name a call or an index is on
            elif j >= 0 and toks[j].kind == "op" and toks[j].v in (")", "]"):
                continue  # a call on the result of a call, or an index on one: f(a)(b)
        elif _is_name(t) or t.kind in ("num", "str", "tpl"):
            start, j = j, j - 1
        else:
            break
        if j >= 1 and toks[j].kind == "op" and toks[j].v in (".", "?.") and _chain_start(toks[j - 1]):
            start, j = j, j - 1
        else:
            break
    return " ".join(t.v for t in toks[start:i])


def _type_tokens(toks, i):
    """The tokens of the type that follows an `as` (toks[i] is the first of them)."""
    out, depth = [], 0
    for k in range(i, len(toks)):
        t = toks[k]
        if k > i and t.nl and depth == 0 and t.v not in ("|", "&", "."):
            break
        if t.kind == "op":
            if t.v in ("(", "[", "{", "<"):
                depth += 1
            elif t.v in (")", "]", "}", ">"):
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and t.v in (";", ",", "=", "=>", "&&", "||", "??", "?", ":"):
                break
        elif depth == 0 and t.kind == "word" and t.v == "as":
            break
        out.append(t)
    return out


def _opens_list(toks, k):
    """Whether the `{` at toks[k] opens an import or export list."""
    before = toks[k - 1].v if k > 0 else ""
    if before in ("import", "export"):
        return True
    if before in (",", "type"):
        for m in range(k - 1, max(-1, k - 400), -1):
            if toks[m].v in ("import", "export"):
                return True
            if toks[m].kind == "op" and toks[m].v in (";", "{", "}"):
                return False
    return False


def _alias(toks, i):
    """Whether the `as` at toks[i] is an import or export alias (`* as ns`, `{ a as b }`), not a cast."""
    if i > 1 and toks[i - 1].v == "*" and toks[i - 2].v in ("import", "export"):
        return True
    depth = 0
    for k in range(i - 1, max(-1, i - 400), -1):
        t = toks[k]
        if t.kind != "op":
            continue
        if t.v == "}":
            depth += 1
        elif t.v == "{":
            if depth == 0:
                return _opens_list(toks, k)
            depth -= 1
        elif t.v == ";" and depth == 0:
            return False
    return False


def _nonnull(toks, i):
    """Whether the `!` at toks[i] is a non-null assertion: after a name, a call, an index or a parenthesis, and not a
    definite assignment (`let x!: T`) or an assignment (`x! = y`)."""
    prev = toks[i - 1] if i > 0 else None
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    if prev is None or (nxt is not None and nxt.kind == "op" and nxt.v in ("=", ":")):
        return False
    if prev.kind == "op" and prev.v in (")", "]"):
        return True
    return prev.kind in ("word", "num") and prev.v not in KEYWORDS


def shift_left_hits(text):
    """(line, message) for each shift-left hit in a .ts file: a repeated cast or non-null assertion, and any `any`."""
    toks = tokens(text)
    newlines = [m.start() for m in re.finditer("\n", text)]
    hits = []
    casts = collections.defaultdict(list)
    nonnulls = collections.defaultdict(list)
    for i, t in enumerate(toks):
        line = bisect.bisect_left(newlines, t.offset) + 1
        if t.kind == "word" and t.v == "as" and not _alias(toks, i):
            if i + 1 >= len(toks) or toks[i + 1].v == "const":
                continue
            target = _type_tokens(toks, i + 1)
            if not target or [x.v for x in target] == ["any"]:  # `as any` is counted at its `any`
                continue
            casts[f"{_operand(toks, i)} as {' '.join(x.v for x in target)}"].append(line)
        elif t.kind == "op" and t.v == "!" and _nonnull(toks, i):
            operand = _operand(toks, i)
            if operand:
                nonnulls[f"{operand}!"].append(line)
        elif t.kind == "word" and t.v == "any" and i > 0 and toks[i - 1].v in ANY_AFTER:
            # `o.any` is a member and `{a: 1, any: 2}` a key; the type `any` is never followed by `.` or `:`.
            if not (i + 1 < len(toks) and toks[i + 1].v in (".", ":")):
                hits.append((line, "uses `any`: type it (unknown, a named type or a generic) instead"))
    for label, table in (("cast", casts), ("non-null assertion", nonnulls)):
        for key, lines in table.items():
            if len(lines) > 1:
                for line in lines:
                    hits.append((line, f"repeated {label} `{key}` ({len(lines)} times in this file): name it once"))
    return sorted(hits)


def check_shift_left(pieces):
    """The problems: every hit in a .ts file with no strict piece (a strict file, which may not repeat a cast or a
    non-null assertion, nor use any). Files on the ratchet keep their findings and are not read here."""
    problems = []
    for rel in ts_on_disk():
        if rel in pieces:
            continue
        hits = shift_left_hits(read(rel))
        if hits:
            problems.append(f"SHIFT-LEFT {rel}: {len(hits)} hit(s)")
            problems += [f"    {rel}:{line}: {message}" for line, message in hits]
    return problems


def write(by_file, pieces, on_disk):
    grown = [rel for rel in by_file if rel not in pieces or len(by_file[rel]) > pieces[rel]]
    if grown:
        for rel in sorted(grown):
            print(f"refusing to raise or add {rel}: {len(by_file[rel])} finding(s); fix them instead")
        return 1
    kept = {rel: len(by_file[rel]) for rel in pieces if by_file.get(rel) and rel in on_disk}
    ui_baseline.write_layout(STRICT, kept)
    print(f"wrote {ui_baseline.LAYOUT}/{STRICT}: {len(kept)} file(s), {sum(kept.values())} finding(s)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", metavar="REF", help="git ref whose counts must not be exceeded (e.g. origin/main)")
    parser.add_argument("--write", action="store_true", help="lower the strict pieces to today's counts (never raise)")
    args = parser.parse_args(argv)
    if args.base and git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}").returncode != 0:
        sys.exit(f"--base {args.base}: no such commit in this checkout (fetch it first)")

    findings, config_errors = run_tsc()
    if config_errors:
        print("\n".join(config_errors))
        sys.exit(f"tsc reported a configuration error in {CONFIG}")
    by_file = collections.defaultdict(list)
    for finding in findings:
        if ts_in_scope(finding.file):
            by_file[finding.file].append(finding)
    on_disk = set(ts_on_disk())
    pieces = load_pieces()

    if args.write:
        return write(by_file, pieces, on_disk)

    problems = check_config()
    problems += check_js()
    problems += check_strict(by_file, pieces, on_disk)
    problems += check_directives(pieces)
    problems += check_script_tags()
    if args.base:
        base = base_counts(args.base)
        if base is None:
            print(f"ui strict: {args.base} has no strict baseline yet; growth checks skipped")
        else:
            problems += check_growth(pieces, base, args.base)
    problems += check_shift_left(pieces)
    for problem in problems:
        print(problem)
    total = sum(len(v) for v in by_file.values())
    summary = (
        f"ui strict: {len(on_disk)} .ts file(s) checked, {total} finding(s) in {len(by_file)}; "
        f"{len(pieces)} piece(s) holding {sum(pieces.values())} finding(s)"
    )
    if problems:
        print(f"{summary}; {len(problems)} problem(s)")
        return 1
    print(f"{summary}; ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
