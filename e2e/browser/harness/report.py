#!/usr/bin/env python3
"""The browser harness's matrix, from what each harness's turn left behind: report.py <work folder>.

The shell writes one row per harness (rows/<name>.json: its harness id, model, whether its turn ran and why not), the
turn's probe result (results/<name>.json) and the dump the stand-in CLI wrote (dumps/<name>.json). This prints the table,
one line per check, and returns the failure count: every failed check, every row whose turn never reached the probe, and
a DevTools row whose config carried no chrome-devtools server. A row flagged `gap` (a path the harness cannot exercise
here) is listed and does not count. The secret scan counts in the shell step that runs it."""
import glob
import json
import os
import sys

COLUMNS = ["model", "turn", "env", "json/version", "DevTools MCP", "hub MCP", "hub CLI", "real CLI", "secrets"]


def load(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def status(checks, marker):
    """PASS when every check whose name `marker` picks passed, FAIL when one did not, n/a when none is picked."""
    picked = [item for item in checks if marker(item.get("name", ""))]
    if not picked:
        return "n/a"
    return "FAIL" if any(not item.get("ok") for item in picked) else "PASS"


def row_cells(row, result, dump, secrets):
    cells = {column: "-" for column in COLUMNS}
    cells["model"] = row.get("model", "")
    cells["real CLI"] = row.get("real_cli", "n/a")
    cells["secrets"] = secrets.get("verdict", "not scanned")
    checks = result.get("checks", [])
    cells["turn"] = "PASS" if checks and dump is not None else "FAIL"
    cells["env"] = status(checks, lambda n: n.startswith("TICO_BROWSER_URL"))
    cells["json/version"] = status(checks, lambda n: "json/version" in n)
    cells["DevTools MCP"] = status(checks, lambda n: "chrome-devtools" in n or n.startswith(("list_pages", "navigate_page", "take_snapshot")))
    cells["hub MCP"] = status(checks, lambda n: "hub MCP" in n or ("hub_browser" in n and "MCP" in n)
                              or "hub_credential_request" in n or "MCP fill" in n)
    cells["hub CLI"] = status(checks, lambda n: "(CLI)" in n)
    if result.get("data", {}).get("devtools", "").startswith("not configured"):
        # Documented: these harnesses take no DevTools MCP (integrations/chrome.md); Playwright over TICO_BROWSER_URL and
        # the hub CLI are their browser path.
        cells["DevTools MCP"] = "n/a (none written)"
    return cells


def main(work):
    rows = sorted(glob.glob(os.path.join(work, "rows", "*.json")))
    if not rows:
        print("harness: no rows were written; nothing ran")
        return 1
    secrets = load(os.path.join(work, "secrets.json"), {})
    table, details, failures = [], [], 0
    for path in rows:
        row = load(path, {})
        name = row.get("name", os.path.basename(path)[:-5])
        if row.get("gap"):
            cells = {column: "-" for column in COLUMNS}
            cells.update({"model": row.get("model", ""), "turn": "gap", "real CLI": row.get("real_cli", "n/a")})
            table.append((name, cells))
            details.append("%s: GAP %s" % (name, row.get("why", "")))
            continue
        result = load(os.path.join(work, "results", name + ".json"), {})
        dump = load(os.path.join(work, "dumps", name + ".json"))
        cells = row_cells(row, result, dump, secrets)
        checks = result.get("checks", [])
        if not checks:
            failures += 1
            details.append("%s: FAIL the turn never reached a probe: %s" % (name, row.get("why") or "no result file"))
        if row.get("expect_devtools") and cells["DevTools MCP"] in ("n/a (none written)", "n/a"):
            failures += 1
            cells["DevTools MCP"] = "FAIL"
            details.append("%s: FAIL its config carries no chrome-devtools server" % name)
        for item in checks:
            if not item.get("ok"):
                failures += 1
            details.append("%s: %s %s%s" % (name, "PASS" if item.get("ok") else "FAIL", item.get("name", ""),
                                            "" if item.get("ok") else " (" + item.get("detail", "") + ")"))
        table.append((name, cells))
    width = max(len(name) for name, _ in table) + 2
    print("\n" + "harness".ljust(width) + " ".join(column.ljust(14) for column in COLUMNS))
    for name, cells in table:
        print(name.ljust(width) + " ".join(str(cells.get(column, "-")).ljust(14) for column in COLUMNS))
    print("")
    for line in details:
        print("  " + line)
    print("\nharness: %d failed check(s) across %d harness row(s)" % (failures, len(table)))
    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
