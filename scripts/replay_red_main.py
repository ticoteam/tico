#!/usr/bin/env python3
"""Replay historical red-main diffs through the current light PR selector, without collecting or running tests."""

import argparse
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import subprocess
import time

if __package__:
    from . import pr_checks as checks
else:
    import pr_checks as checks


class Replay:
    def __init__(self, root=checks.ROOT, graph=None):
        self.root = root
        self.graph = graph or checks.DependencyMap(root)
        self.core = checks.browser_scripts([], root)
        self.timings = checks.committed_timings(root)
        self.counts = Counter()
        self.definitions = defaultdict(set)
        # Static collection is sufficient for budget estimates; importing tests would start application fixtures.
        for path in self.graph.files:
            if not checks.is_test(path) or (tree := self.graph.parse(path)) is None:
                continue
            module_slow = any(
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
                and "pytest.mark.slow" in ast.unparse(n.value)
                for n in tree.body
            )
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    self.definitions[node.name].add(path)
                    if not module_slow and not any("pytest.mark.slow" in ast.unparse(d) for d in node.decorator_list):
                        self.counts[path] += 1

    def resolve(self, path):
        """Preserve live paths; follow script suffix conversions and unambiguous file moves."""
        if (self.root / path).is_file():
            return path
        for old, new in ((".cjs", ".cts"), (".js", ".ts")):
            if path.endswith(old) and (self.root / (path[: -len(old)] + new)).is_file():
                return path[: -len(old)] + new
        matches = self.graph.by_base.get(Path(path).name, set())
        return next(iter(matches)) if len(matches) == 1 else None

    def plan(self, changed, added=None):
        # Cases have no A/M status, so retain selection's conservative default; merge costs supply git's additions.
        picked = checks.selection(changed, root=self.root, graph=self.graph, core=self.core, added=added)
        picked = checks.narrow(picked, changed, "pull_request", root=self.root, graph=self.graph, added=added)
        record = picked.get("narrow")
        if record:
            counts = {p: self.counts[p] for p in picked["python"] if self.counts[p]}
            seconds = checks.weights(counts, self.timings)
            kept, left = checks.cap_tests(counts, record["tier"], seconds, record["budget_seconds"])
            if not left:
                kept = [p for p in kept if p not in record["smoke_unless_capped"]]
            picked["python"] = kept
            record["left_over_budget"] = left
        return picked

    def target(self, description):
        if description.startswith(("basedpyright:", "ruff:", "shellcheck:", "actionlint:")):
            command = {
                "basedpyright": "scripts/check_types.sh",
                "ruff": "scripts/lint.sh",
                "shellcheck": "scripts/shell_lint.sh",
                "actionlint": "actionlint",
            }[description.split(":", 1)[0]]
            workflow = (self.root / checks.PR_WORKFLOW).read_text()
            return ("static", command) if command in workflow else ("missing", description)
        match = re.search(r"[\w/-]+\.(?:py|cjs|cts)\b", description)
        if match:
            path = self.resolve(match[0])
            if path:
                return ("file" if checks.is_check(path) else "unsupported", path)
            return "missing", match[0]
        name = description.split()[0].split("[")[0]
        paths = self.definitions.get(name, ())
        return ("file", next(iter(paths))) if len(paths) == 1 else ("missing", name)

    def case(self, case, number):
        changed, moved, gone = [], {}, []
        for path in case["changed_files"]:
            current = self.resolve(path)
            if current and current != path:
                moved[path] = current
            if current is None:
                gone.append(path)
            # A missing input can be a deletion. Keep it for the selector's gone-file rules, and disclose it.
            changed.append(current or path)
        targets = [self.target(t) for t in case["failing_tests"]]
        stale = not changed or any(kind == "missing" for kind, _ in targets)
        picked = self.plan(sorted(set(changed))) if changed else None
        results = []
        for description, (kind, target) in zip(case["failing_tests"], targets, strict=True):
            caught = False
            reason = "no changed files" if not changed else "target no longer resolves to a runnable check"
            if picked:
                if kind == "unsupported":
                    reason = "outside the PR test jobs"
                elif kind == "missing":
                    pass
                elif kind == "static":
                    caught, reason = True, "static job: " + target
                elif target.startswith("ui/tests/"):
                    caught = Path(target).stem in picked["browser"]
                    reason = "browser selected" if caught else "browser not linked"
                else:
                    caught = target in picked["python"] or (
                        picked["contracts"] and target in checks.contract_files(picked["sweeps"])
                    )
                    reason = (
                        "Python/contracts selected"
                        if caught
                        else (
                            "time budget"
                            if target in picked.get("narrow", {}).get("left_over_budget", ())
                            else "depth cut"
                            if target in picked.get("narrow", {}).get("left_transitive", ())
                            else "not linked"
                        )
                    )
            results.append(dict(test=description, target=target, caught=caught, reason=reason))
        return dict(
            case=number,
            fix_pr=case["fix_pr"],
            cause=case["cause"],
            stale=stale,
            caught=not stale and all(r["caught"] for r in results),
            results=results,
            moved=moved,
            gone=gone,
            python=len(picked["python"]) if picked else 0,
            browser=len(picked["browser"]) if picked else 0,
        )

    def run(self, cases, only=None):
        return [self.case(c, n) for n, c in enumerate(cases, 1) if only is None or n == only]


def report(rows):
    print(f"{'cause':<16} {'caught':>6} {'missed':>6} {'stale':>6}")
    for cause in sorted({r["cause"] for r in rows}) + ["TOTAL"]:
        group = [r for r in rows if cause == "TOTAL" or r["cause"] == cause]
        stale = sum(r["stale"] for r in group)
        caught = sum(r["caught"] for r in group)
        print(f"{cause:<16} {caught:>6} {len(group) - stale - caught:>6} {stale:>6}")
    for row in rows:
        print(
            f"case {row['case']:2} fix #{row['fix_pr']}: "
            + ("STALE" if row["stale"] else "caught" if row["caught"] else "MISSED")
        )
        for result in row["results"]:
            print(f"  {'yes' if result['caught'] else ' no'} {result['test']}: {result['reason']} ({result['target']})")
        if row["moved"] or row["gone"]:
            print(f"  mapped {row['moved']}; unresolved inputs retained as deletions: {len(row['gone'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=int, help="one-based case number in red_main_cases.json")
    parser.add_argument("--json", type=Path, help="also write detailed results as JSON")
    parser.add_argument("--cost", action="store_true", help="also measure the last 20 merges on local main")
    args = parser.parse_args()
    start = time.monotonic()
    replay = Replay()
    cases = json.loads((replay.root / "scripts/red_main_cases.json").read_text())
    if args.case is not None and not 1 <= args.case <= len(cases):
        parser.error(f"--case must be between 1 and {len(cases)}")
    rows = replay.run(cases, args.case)
    report(rows)
    costs = []
    if args.cost:
        merges = subprocess.check_output(
            ["git", "log", "main", "--merges", "-20", "--format=%H"], cwd=replay.root, text=True
        ).split()
        for sha in merges:
            changed = subprocess.check_output(
                ["git", "diff", "--name-only", sha + "^1", sha], cwd=replay.root, text=True
            ).splitlines()
            changed = sorted({replay.resolve(p) or p for p in changed})
            added = subprocess.check_output(
                ["git", "diff", "--name-only", "--diff-filter=A", sha + "^1", sha], cwd=replay.root, text=True
            ).splitlines()
            plan = replay.plan(changed, added=[replay.resolve(p) or p for p in added])
            cost = dict(sha=sha, python=len(plan["python"]), browser=len(plan["browser"]))
            costs.append(cost)
            print(f"cost {sha[:10]} Python {cost['python']:4} browser {cost['browser']:3}")
    if args.json:
        args.json.write_text(json.dumps(dict(cases=rows, costs=costs), indent=2) + "\n")
    print(f"Elapsed {time.monotonic() - start:.1f}s; graph built once; no tests collected or run.")
    print(
        "Budget uses committed timings and AST test counts. Cases lack add/delete status: selection's default treats edits as possible additions; costs use git status. CI timing overrides are unavailable."
    )


if __name__ == "__main__":
    main()
