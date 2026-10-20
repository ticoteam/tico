One file per recurring kind of work. A playbook is the checklist you would hand a new hire.

- `kpi-pass.md`: the daily routine. Every KPI due on its cadence, one pass, a time budget each, then the status pass.
- `new-kpi.md`: what to build the first time a KPI needs computing, and how to change one that is wrong.
- `status-pass.md`: `hub goal refresh` after the readings, and how to report what it did.
- `check-ins.md`: when a KPI slips, ask the goal's owner and record the answer.
- `goals-make-sense.md`: flag vague, duplicate and unmeasured goals; suggest a measure or clearer words.
- `weekly-goals-review.md`: the short review for the owner.
- `recoveries.md`: where the CEO Bot is on at level 2, recoveries for red or stalled goals and merged asks.

## Proposals
Every change to a goal or a KPI that is not yours to make is a proposal the owner confirms. Write the payload
to a file, then `hub proposal create --kind <kind> [--goal ID] [--kpi ID] --payload-file f.json --reason "..."`.
`hub proposal list` shows what is pending; look before you propose the same thing again.

| kind | needs | payload |
|---|---|---|
| `flag` | `--goal` | `{"issue": "vague" \| "duplicate" \| "unmeasured", "note": "one sentence"}` |
| `goal_wording` | `--goal` | `{"title": "...", "body": "..."}` (either one) |
| `goal_kpi` | `--goal` | `{"kpi_id": "..."}` to link an existing KPI, or `{"kpi": {"name", "definition", "unit", "direction", "cadence", "source_note"}, "owner": "...", "target": {...}}` for a new one |
| `kpi_definition` | `--kpi` | any of `{"name", "definition", "unit", "direction", "cadence", "source_note"}` |
| `goal_why` | `--goal` | `{"why": "one line tying the goal to the vision"}` (only where the CEO Bot is on) |
| `recovery` | `--goal` | `{"evidence": "...", "options": [{"label": "...", "actions": [...]}], "pick": 0}` (the CEO Bot at level 2; `playbooks/recoveries.md`) |
| `kpi_target` | `--kpi` and `--goal` | `{"kind": "improve", "baseline": 40, "target": 65, "deadline": "2026-12-31"}` or `{"kind": "maintain", "min": 40, "max": 60}` |

`direction` is up, down or range; `cadence` is daily, weekly or monthly. An improvement target needs a target and a
deadline; a range needs a min, a max or both. The server refuses anything else with the reason.
