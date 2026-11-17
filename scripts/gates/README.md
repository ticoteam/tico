# Verification gates

Add a Python file directly under `scripts/gates/`. Every `*.py` there runs as
`python3 scripts/gates/<name>.py --base <sha>` from the repository root, in filename
order. Helpers belong elsewhere. Exit 0 to pass; any other status fails. Print
useful failure details: the runner shows output only on failure and prints a timed
PASS/FAIL line per gate. Keep checks fast, deterministic and read-only.

`from scripts import diff_facts` works without installing a package.
`diff_facts.diff_facts(base)` compares committed base and HEAD content and returns
`base`, `head`, `tier` and `files`; each file has its kind, added/deleted line
numbers and hunks. A hunk's `strings_only` includes comments; `copy_only` also
excludes SQL. Unsupported or unparseable code stays conservative.
`diff_facts.deleted_recent(base, days=14)` returns deleted lines recently committed
on that exact base. Neither helper reads uncommitted content.

The local check, PR static job and merge-commit checks discover gates automatically.
With no gate scripts and no rules, `bash scripts/gates.sh` prints `gates: none`.

## Structural rules

Add one ast-grep rule in `scripts/rules/<name>.yml` or `.yaml`, with fixtures in
`scripts/rules/fixtures/<name>/`: at least one `pass.*` that must not match and one
`fail.*` that must match. Every fixture is checked separately via stdin, using the
rule's language; path filters apply only to the repository scan. Rules scan the
repository, including hidden paths, respecting ignore files and excluding `.git`
and the fixture tree. All rule severities are forced to error.

The runner pins ast-grep-cli 0.51.1 through `uvx`. CI fetches it and fails if it
cannot run. Locally it uses the offline cache and warns loudly if rules are
skipped. Populate the cache with
`uvx --from ast-grep-cli==0.51.1 ast-grep --version`.
