# Verification metrics

These append-only files will hold one JSON object per line (JSONL). No files or
records are generated yet. Times are UTC ISO 8601; commit fields are full SHAs;
`pr` is a pull request number or null.

| File | Fields | Records |
| --- | --- | --- |
| `red_main.jsonl` | `timestamp`, `commit`, `pr`, `check`, `test`, `run_url`, `fix_commit` | A failing check on main; `test` and `fix_commit` may be null until known. |
| `escapes.jsonl` | `timestamp`, `commit`, `pr`, `test`, `selected_tests`, `selection_reason`, `run_url` | A failure missed by test selection; `selected_tests` is an array of test paths. |
| `green_but_wrong.jsonl` | `timestamp`, `commit`, `pr`, `description`, `evidence_url`, `fix_commit` | Incorrect behavior despite green checks; `fix_commit` may be null until known. |

Record repository paths and public evidence, without customer text or credentials.
