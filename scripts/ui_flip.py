#!/usr/bin/env python3
"""Flip the UI's JavaScript to TypeScript: every ui/ .js file that compiles to the same output as a .ts file.

    python3 scripts/ui_flip.py          flip what is left: move, references, baseline pieces, strict counts
    python3 scripts/ui_flip.py --check  change nothing; exit 1 when a .js file under ui/ could still be flipped

Rerunnable. A .ts file is never touched, so on a newer main this flips what main added and leaves the rest. For each
candidate:

1. The proof: esbuild compiles the file's text with the JS loader and with the TS loader (both --target=esnext
   --charset=utf8), and the two outputs must be byte-identical. The one text change a flip makes is an ESM path that
   names a flipped file (`./building.js` -> `./building.ts`); both sides carry that same path change, so the proof checks
   the parse, not the path. A file that fails it, or that TypeScript cannot parse, stays .js and is listed with why.
2. The move: git mv to .ts, with that path change written into it.
3. References: `ui/<path>.js` and `/tico/ui/<path>.js` in every tracked text file, a quoted `'<path>.js'` that names a
   flipped file (the tests use uiFile('app/x.js')), and a relative src or href in ui/ HTML.
4. Baselines: a flipped file's tsc piece goes (its findings are the strict ratchet's now); its biome piece is renamed
   (biome/app/x.js.json -> biome/app/x.ts.json, same counts); its strict piece is written with today's count.
5. Configs: ui/tsconfig*.json allow .ts import paths, and exclude the observability entry as a .ts file.
"""

import argparse
import collections
import posixpath
import re
import subprocess
import sys
from pathlib import Path

import check_ui_strict as strict
import ui_baseline

ROOT = Path(__file__).resolve().parent.parent
ESBUILD = ROOT / "node_modules" / ".bin" / "esbuild"
# Not candidates: vendored code, the Node test scripts (.cjs), and the generated observability bundle.
NOT_FLIPPED = ("ui/vendor/", "ui/tests/", "ui/assets/", "ui/node_modules/")
# Records, not references: the changelog and the historical cleanup reports name files as they were.
NOT_REWRITTEN = ("CHANGELOG.md", "scripts/cleanup/", "ui/vendor/")
# Files that stay JavaScript, with the reason: the gate's list, so the two cannot drift apart. The proof decides the rest.
KEEP = strict.KEPT_JS
ESM_PATH = re.compile(r"""(?P<q>['"])(?P<spec>\.{1,2}/[^'"\n]*?)\.js(?P=q)""")
HTML_PATH = re.compile(r"""(?P<attr>\b(?:src|href)=")(?P<spec>[^"/:][^"]*?)\.js(?=")""")


def git(*args, check=True):
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    if check and done.returncode != 0:
        sys.exit(f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done


def rel_of(path):
    return Path(path).relative_to(ROOT).as_posix()


def read_text(rel):
    return (ROOT / rel).read_bytes().decode("utf-8")


def candidates():
    listed = git("ls-files", "-z", "--", "ui").stdout.decode().split("\0")
    return sorted(rel for rel in listed if rel.endswith(".js") and not rel.startswith(NOT_FLIPPED))


def respec(text, rel, flipped):
    """The ESM paths in one file that name a flipped file: `./x.js` becomes `./x.ts`, relative to the file's folder."""
    folder = posixpath.dirname(rel)

    def swap(m):
        target = posixpath.normpath(posixpath.join(folder, m["spec"] + ".js"))
        return f"{m['q']}{m['spec']}.ts{m['q']}" if target in flipped else m.group(0)

    return ESM_PATH.sub(swap, text)


def compile_with(loader, text):
    return subprocess.run(
        [str(ESBUILD), f"--loader={loader}", "--target=esnext", "--charset=utf8"],
        cwd=ROOT,
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )


def proof(rel, flipped):
    """None when the file flips; otherwise why it stays JavaScript."""
    text = respec(read_text(rel), rel, flipped)
    as_js = compile_with("js", text)
    if as_js.returncode != 0:
        return "esbuild cannot compile it as JavaScript"
    as_ts = compile_with("ts", text)
    if as_ts.returncode != 0:
        first = as_ts.stderr.decode(errors="replace").strip().splitlines()
        return "TypeScript cannot parse it: " + (first[0] if first else "unknown error")
    if as_js.stdout != as_ts.stdout:
        return "esbuild output differs between the JS and TS loaders"
    return None


def decide(cands):
    """(flipped, kept): a file that fails the proof stays JavaScript, and the others are proved again without it."""
    kept = dict(KEEP)
    while True:
        flipped = set(cands) - set(kept)
        failed = {}
        for rel in sorted(flipped):
            reason = proof(rel, flipped)
            if reason:
                failed[rel] = reason
        if not failed:
            return flipped, kept
        kept.update(failed)


def kept_importing(kept, flipped):
    """Kept JavaScript that imports a flipped file: its import path would name a file that no longer exists."""
    bad = []
    for rel in sorted(kept):
        for m in ESM_PATH.finditer(read_text(rel)):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), m["spec"] + ".js"))
            if target in flipped:
                bad.append(f"{rel} imports {m['spec']}.js, which flips; keep that file too, or flip this one")
    return bad


def apply_moves(flipped):
    for rel in sorted(flipped):
        text = respec(read_text(rel), rel, flipped)
        new = rel[:-3] + ".ts"
        git("mv", rel, new)
        (ROOT / new).write_bytes(text.encode("utf-8"))


def html_paths(text, rel, flipped):
    folder = posixpath.dirname(rel)

    def swap(m):
        target = posixpath.normpath(posixpath.join(folder, m["spec"] + ".js"))
        return m["attr"] + m["spec"] + ".ts" if target in flipped else m.group(0)

    return HTML_PATH.sub(swap, text)


def rewrite_refs(flipped):
    """The tracked text files that named a flipped file, rewritten to name it as .ts. Returns those files."""
    if not flipped:  # an empty alternation would match every ui/ path, so nothing is rewritten
        return []
    names = sorted((rel[len("ui/") :] for rel in flipped), key=len, reverse=True)
    alt = "|".join(re.escape(name) for name in names)
    served = re.compile(r"(?<![\w./-])((?:/tico/)?ui/)(" + alt + r")(?![\w-])(?!\.\w)")
    quoted = re.compile(r"(?P<q>['\"`])(?P<n>" + alt + r")(?P=q)")
    changed = []
    for rel in git("ls-files", "-z").stdout.decode().split("\0"):
        if not rel or rel.startswith(NOT_REWRITTEN):
            continue
        path = ROOT / rel
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = served.sub(lambda m: m.group(1) + m.group(2)[:-3] + ".ts", text)
        new = quoted.sub(lambda m: m.group("q") + m.group("n")[:-3] + ".ts" + m.group("q"), new)
        if rel.startswith("ui/") and rel.endswith(".html"):
            new = html_paths(new, rel, flipped)
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed.append(rel)
    return changed


def move_pieces(flipped):
    for rel in sorted(flipped):
        new = rel[:-3] + ".ts"
        tsc = ui_baseline.baseline_path("tsc", rel)
        if tsc.exists():
            git("rm", "-q", "--", rel_of(tsc))
        old_biome = ui_baseline.baseline_path("biome", rel)
        if old_biome.exists():
            new_biome = ui_baseline.baseline_path("biome", new)
            new_biome.parent.mkdir(parents=True, exist_ok=True)
            git("mv", rel_of(old_biome), rel_of(new_biome))


def write_strict(flipped):
    """Each flipped file's strict piece: its findings today, none at zero. Returns the flipped files that have findings."""
    findings, config_errors = strict.run_tsc()
    if config_errors:
        print("\n".join(config_errors))
        sys.exit(f"tsc reported a configuration error in {strict.CONFIG}")
    counts = collections.Counter(f.file for f in findings if strict.ts_in_scope(f.file))
    pieces = ui_baseline.load_layout(strict.STRICT)
    with_findings = []
    for rel in flipped:
        new = rel[:-3] + ".ts"
        if counts.get(new):
            pieces[new] = counts[new]
            with_findings.append(new)
        else:
            pieces.pop(new, None)
    ui_baseline.write_layout(strict.STRICT, pieces)
    return with_findings


def edit_configs():
    """Both tsconfigs: .ts import paths are allowed (the `./x.ts` the flip wrote), and the observability entry is .ts."""
    for name in ("ui/tsconfig.json", "ui/tsconfig.strict.json"):
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        if '"allowImportingTsExtensions"' not in text:
            text = text.replace('"noEmit": true,', '"noEmit": true,\n    "allowImportingTsExtensions": true,', 1)
        text = text.replace('"./observability-vendors.js"', '"./observability-vendors.ts"')
        path.write_text(text, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if a file could be flipped")
    args = parser.parse_args(argv)
    if not ESBUILD.is_file():
        sys.exit("node_modules/.bin/esbuild is missing: run `npm ci --ignore-scripts` first")

    flipped, kept = decide(candidates())
    for rel, reason in sorted(kept.items()):
        print(f"kept JavaScript {rel}: {reason}")
    if args.check:
        if flipped:
            print(f"{len(flipped)} .js file(s) under ui/ could be flipped: run python3 scripts/ui_flip.py")
            return 1
        print(f"ui flip: every UI script is TypeScript ({len(kept)} kept)")
        return 0

    problems = kept_importing(kept, flipped)
    if problems:
        sys.exit("\n".join(problems))
    apply_moves(flipped)
    changed = rewrite_refs(flipped)
    move_pieces(flipped)
    edit_configs()
    with_findings = write_strict(flipped)
    print(
        f"ui flip: {len(flipped)} file(s) to TypeScript, {len(kept)} kept; {len(changed)} tracked file(s) name them as .ts; "
        f"{len(with_findings)} strict piece(s) written"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
