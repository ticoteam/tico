#!/usr/bin/env python3
"""Report verification changes and stale or named reverts as counted facts, without blocking."""

import argparse
import ast
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

# Direct invocation has scripts/gates, rather than the repository, on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import check_migration_order, diff_facts, freeze_guard

ROOT = Path(__file__).resolve().parents[2]
SKIP = re.compile(
    r"\bpytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.skip\s*\("
    r"|@(?:unittest\.)?skip(?:If|Unless)?\b|\bunittest\.skip(?:If|Unless)?\s*\("
    r"|#\[ignore\b|\b(?:test|it)\.(?:skip|only)\b"
)
SUPPRESSION = re.compile(
    r"#\s*(?:noqa\b|type:\s*ignore\b|pyright:\s*ignore\b)|#!?\[allow\s*\("
    r"|eslint-disable\b|@ts-(?:ignore|expect-error)\b|biome-ignore\b"
)
BASELINES = {
    "ruff-baseline-counts.json",
    "ruff-baseline.toml",
    ".basedpyright/baseline.json",
    "ui/types-baseline.json",
    "ui/lint-baseline.json",
    "ui/strict-legacy.json",
}


def test_definitions(path, source):
    """Test identities, so changing a signature or moving a test within a file is not a deletion."""
    found = []
    if path.endswith(".py") and "tests" in Path(path).parts:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return [
                (m[1], source.count("\n", 0, m.start()) + 1)
                for m in re.finditer(r"(?m)^\s*(?:async )?def (test_\w+)", source)
            ]

        def visit(node, scope=()):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = (*scope, child.name)
                    if not isinstance(child, ast.ClassDef) and child.name.startswith("test_"):
                        found.append((".".join(name), child.lineno))
                    visit(child, name)
                else:
                    visit(child, scope)

        visit(tree)
    else:
        pattern = None
        if path.endswith(".rs"):
            pattern = (
                r"#\[\s*(?:\w+::)*\w*test(?:_case)?\b(?:\([^\]]*\))?\s*\]"
                r"\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)"
            )
        elif re.search(r"\.test\.[cm]?[jt]sx?$", path):
            pattern = r"\b(?:test|it)(?:\.(?:skip|only))?\s*\(\s*(['\"`][^\n]*?['\"`])"
        if pattern:
            found = [(m[1], source.count("\n", 0, m.start()) + 1) for m in re.finditer(pattern, source)]
    return found


def allowances(text, path):
    """Compare counts per rule and entry lists, ignoring diagnostic ranges in pyright pieces."""
    if not text.strip():
        return Counter()
    try:
        data = tomllib.loads(text) if path.endswith(".toml") else json.loads(text)
    except ValueError:
        return Counter(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    counts = Counter()

    def visit(value, key=()):
        if isinstance(value, dict):
            for name, child in value.items():
                visit(child, (*key, name))
        elif isinstance(value, list):
            for entry in value:
                identity = (
                    entry.get("code")
                    if isinstance(entry, dict) and "code" in entry
                    else json.dumps(entry, sort_keys=True)
                )
                counts[(*key, identity)] += 1
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            counts[key] = value

    visit(data)
    return counts


def verifier_edit(path, before, after):
    name = Path(path).name
    if path.startswith(("scripts/gates/", "scripts/rules/", ".github/workflows/", "scripts/ci/")):
        return True
    if (
        re.fullmatch(r"scripts/(?:check[^/]*|lint[^/]*|[^/]*guard[^/]*)\.(?:sh|py)", path)
        or re.fullmatch(r"pyrightconfig[^/]*\.json", name)
        or path in {"scripts/diff_facts.py", "scripts/pr_checks.py", "scripts/gates.sh"}
        or name
        in {
            "pytest.ini",
            ".pytest.ini",
            "ruff.toml",
            ".ruff.toml",
            "pyrightconfig.json",
            "conftest.py",
            "biome.json",
            "biome.jsonc",
        }
    ):
        return True
    if name == "pyproject.toml":

        def sections(text):
            tool = tomllib.loads(text).get("tool", {})
            return {name: tool.get(name) for name in ("pytest", "ruff", "pyright", "basedpyright")}

        return sections(before) != sections(after)
    return False


def code_line(text):
    return freeze_guard.meaningful(text) and not text.lstrip().startswith(("#", "//", "/*", "*", "--"))


def normalized(change):
    """Use the release guard's inverse calculation without comments or spacing-only changes."""
    files = {}
    for path, pair in change["files"].items():
        files[path] = tuple(Counter() for _ in pair)
        for target, source in zip(files[path], pair, strict=True):
            for text, count in source.items():
                if code_line(text):
                    target[re.sub(r"\s+", "", text)] += count
    hunks = {
        path: [
            tuple([re.sub(r"\s+", "", text) for text in side if code_line(text)] for side in pair) for pair in groups
        ]
        for path, groups in change["hunks"].items()
    }
    return {**change, "files": files, "meaningful": files, "hunks": hunks}


def whole_hunk_reverted(change, log):
    """A small inverse can undo a complete hunk of a larger recent commit."""
    header, body = [], []

    def reverted():
        patch = "\n".join(header + body)
        earlier = normalized(freeze_guard.parse_log(f"\0hunk\0\0hunk\0\0{patch}")[0])
        gone, total, back, removed = freeze_guard.undone(freeze_guard.preimage_change(change, earlier), earlier)
        size = sum(sum(side.values()) for pair in change["files"].values() for side in pair)
        return (
            total + removed > 0
            and (gone, back) == (total, removed)
            and total + removed >= freeze_guard.REVERT_SHARE * size
        )

    for line in log.splitlines():
        if line.startswith(("diff --git ", "@@ ")):
            if body and reverted():
                return True
            body = []
            if line.startswith("diff --git "):
                header = [line]
            else:
                body = [line]
        elif line.startswith(("--- ", "+++ ")):
            header.append(line)
        elif line[:1] in {"+", "-"}:
            body.append(line)
    return bool(body) and reverted()


def stale_reverts(facts, repo, sources, now=None):
    base, head = facts["base"], facts["head"]
    own = set(diff_facts.git(repo, "rev-list", f"{base}..{head}").split())
    messages = diff_facts.git(repo, "log", "--format=%(trailers:key=Revert-Ok,unfold=true)%x00", f"{base}..{head}")
    recent = {}
    for row in diff_facts.deleted_recent(base, head=head, repo=repo, now=now):
        if row["commit"] not in own and code_line(row["text"]):
            recent[row["path"], row["line"]] = row
    if not recent:
        return []
    candidates = {}
    for item in facts["files"]:
        if (
            freeze_guard.TEST_PATH.search(item["path"])
            or item["path"].startswith("e2e/")
            or check_migration_order.migrations([item["path"]])
        ):
            continue
        before, after = (text.splitlines() for text in sources[item["path"]])
        for hunk in item["hunks"]:
            if hunk["copy_only"]:
                continue
            deleted = [number for number in hunk["deleted"] if code_line(before[number - 1])]
            rows = [recent.get((item["path"], number)) for number in deleted]
            # A refactor deleting a larger block is not evidence of an older tree. The whole deleted hunk must
            # belong to one recent change, and its replacement must restore that change's previous content.
            if not rows or any(row is None for row in rows) or len({row["commit"] for row in rows}) != 1:
                continue
            added = Counter(re.sub(r"\s+", "", after[n - 1]) for n in hunk["added"] if code_line(after[n - 1]))
            candidates.setdefault(rows[0]["commit"], []).append((rows, added))
    if not candidates:
        return []
    patch = diff_facts.git(repo, "diff", "--no-ext-diff", "--no-renames", "--no-color", "-U0", base, head)
    change = normalized(freeze_guard.parse_log(f"\0{head}\0\0change\0\0{patch}")[0])
    found = []
    for sha, hunks in sorted(candidates.items()):
        log = diff_facts.git(
            repo,
            "log",
            "-1",
            "--diff-merges=first-parent",
            "-p",
            "-U0",
            "--no-renames",
            "--no-ext-diff",
            "--no-color",
            "--format=%x00%H%x00%P%x00%s%x00%b%x00",
            sha,
        )
        earlier = freeze_guard.parse_log(log)[0]
        clean = normalized(earlier)
        rows = [
            row
            for group, added in hunks
            if not (added - clean["files"].get(group[0]["path"], (Counter(), Counter()))[1])
            for row in group
        ]
        if not rows:
            continue
        share = freeze_guard.reverts(change, clean)
        coherent = []
        whole = False
        for path in sorted({row["path"] for row in rows}):
            if path not in clean["files"]:
                continue
            pair = change["files"].get(path, (Counter(), Counter()))
            local = {"files": {path: pair}, "meaningful": {path: pair}, "hunks": {path: change["hunks"].get(path, [])}}
            previous = {"files": {path: clean["files"][path]}, "meaningful": {path: clean["files"][path]}}
            gone, _, back, removed = freeze_guard.undone(local, previous)
            size = sum(sum(side.values()) for side in pair)
            # A stale overwrite dominates the file's change. Removing a small obsolete part during a rewrite
            # must not become a revert merely because the same text occurs elsewhere in the change.
            if size and gone + back >= freeze_guard.REVERT_SHARE * size:
                local_whole = whole_hunk_reverted(local, log)
                restores = back == removed or (
                    back >= freeze_guard.MIN_LINES and back >= freeze_guard.REVERT_SHARE * removed
                )
                if restores or local_whole:
                    coherent.extend(row for row in rows if row["path"] == path)
                    whole |= local_whole
        rows = coherent
        if not rows:
            continue
        if share is None and not whole:
            continue
        # Blame must identify the actual undone additions, not merely nearby common lines.
        gone, total, _, _ = freeze_guard.undone(change, clean, "meaningful")
        blamed = sum(
            re.sub(r"\s+", "", row["text"]) in clean["files"].get(row["path"], (Counter(), Counter()))[0]
            for row in rows
        )
        if not gone or not blamed or (not whole and gone < total and blamed < freeze_guard.MIN_LINES):
            continue
        permitted = freeze_guard.revert_allowed(messages, earlier, str(repo), base=base)
        row = rows[0]
        found.append(
            {
                "kind": "named_revert" if permitted else "stale_revert",
                "path": row["path"],
                "line": row["line"],
                "reason": (
                    f"undoes a whole hunk of recent commit {sha[:12]}"
                    if whole
                    else f"undoes {share:.0%} of recent commit {sha[:12]}"
                )
                + (" (Revert-Ok)" if permitted else "; a stale tree overwrites newer work"),
                "commit": sha,
            }
        )
    return found


def check(base, head="HEAD", repo=ROOT, now=None):
    ancestor = diff_facts.git(repo, "merge-base", base, head).strip()
    facts = diff_facts.diff_facts(ancestor, head, repo)
    found = []
    sources = {}

    def add(kind, path, line, reason):
        found.append({"kind": kind, "path": path, "line": line, "reason": reason})

    for item in facts["files"]:
        path = item["path"]
        before = "" if item["status"] == "added" else diff_facts.git(repo, "show", f"{facts['base']}:{path}")
        after = "" if item["status"] == "deleted" else diff_facts.git(repo, "show", f"{facts['head']}:{path}")
        sources[path] = before, after
        line = next(iter(item["added"] or item["deleted"]), 1)
        old_tests = test_definitions(path, before)
        remaining = Counter(name for name, _ in test_definitions(path, after))
        if item["status"] == "deleted" and (
            old_tests or (path.endswith(".py") and "tests" in Path(path).parts and Path(path).name.startswith("test_"))
        ):
            add("deleted_test", path, 1, "removed test file")
        else:
            for name, number in old_tests:
                if remaining[name]:
                    remaining[name] -= 1
                else:
                    add("deleted_test", path, number, f"removed test {name}")
        raised = False
        if not item["binary"]:
            lines = after.splitlines()
            for number in item["added"]:
                text = lines[number - 1]
                for pattern, kind, reason in (
                    (SKIP, "new_skip", "added skip, xfail or focused test"),
                    (SUPPRESSION, "new_suppression", "added verification suppression"),
                ):
                    if pattern.search(text):
                        add(kind, path, number, reason)
            if path.startswith(("baselines/", "ui/baselines/")) or path in BASELINES:
                if allowances(after, path) - allowances(before, path):
                    add("raised_baseline", path, line, "allowed verification counts or entries grew")
                    raised = True
        # A baseline only counts when it allows more; shrinking or reshaping one is the ratchet doing its job.
        baseline = path.startswith(("baselines/", "ui/baselines/")) or path in BASELINES
        if not raised and not baseline and verifier_edit(path, before, after):
            add("verifier_edit", path, line, "changed verification code or configuration")
    found.extend(stale_reverts(facts, repo, sources, now))
    return {"base": facts["base"], "head": facts["head"], "facts": found, "count": len(found)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)
    try:
        result = check(args.base)
        for fact in result["facts"]:
            print(f"{fact['kind']}: {fact['path']}:{fact['line']}: {fact['reason']}")
        print(f"verifier count: {result['count']}")
        if path := os.environ.get("VERIFIER_COUNT_JSON"):
            Path(path).write_text(json.dumps(result, indent=2) + "\n")
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            counts = Counter(f["kind"] for f in result["facts"])
            with Path(path).open("a") as summary:
                summary.write(f"\nverifier count: {result['count']}\n\n| Verification fact | Count |\n| --- | ---: |\n")
                for kind, count in sorted(counts.items()):
                    summary.write(f"| {kind} | {count} |\n")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"verifier count: cannot check: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
