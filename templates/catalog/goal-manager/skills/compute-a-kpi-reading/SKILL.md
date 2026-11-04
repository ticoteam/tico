---
name: compute-a-kpi-reading
description: "Computes one KPI reading for the period that just ended, checks it against known values, and posts it with its evidence and definition version. Use it on the KPI pass or for a new KPI's first reading."
---

# Compute a KPI reading

## When to use

- The KPI pass finds a KPI whose period has ended.
- A new KPI's folder is built and its first reading is due.

## Steps

1. Read the KPI: `hub kpi show <id>` for its definition, unit, direction, cadence and `definition_version`.
2. Compare versions. Read `kpis/<slug>/definition.md`. If Tico's version is newer, stop, report it, and follow `playbooks/new-kpi.md`.
3. Run the known-values check in `kpis/<slug>/known-values.md`. A mismatch is a failure: the computation, the source or the known value is wrong. Stop and report it. Never edit a known value to make the number pass.
4. Compute the value for the period that just ended, from the sources in `sources.md`. Read-only queries only: `hub sql` for Tico's own data, or a tool the owner declared in `tools:`.
5. Set the quality. `measured` for a complete period from a source you trust. `partial` when the period is not complete. `estimate` when a source was down and you used a stand-in. Say which in the note.
6. Post it:
   `hub kpi log <id> <value> "<how it was computed>" --period-start <date> --period-end <date> --evidence "<link to the query or file>" --quality measured --definition-version <n>`
7. For a wrong earlier reading, post a correction with `--supersedes <reading id>` and a note on what changed. Never post a second reading for the same period without it.
8. If the data is missing, post nothing. Report the KPI, what is missing, and how long it has been missing.

## Checks before you finish

- The period boundaries are exact, with their time zone.
- The definition version matches the KPI's current version.
- The evidence is a link to what shows the inputs.
- No zero stands in for missing data.
- No `auto:` KPI was touched.

## Example

A KPI measures the share of new accounts that finish setup in a week. The week ran Monday to Sunday, and 18 of 40 new accounts finished.

    hub kpi log <kpi-id> 45 "18 of 40 new accounts finished setup, 2026-10-19 to 2026-10-25" --period-start 2026-10-19 --period-end 2026-10-25 --evidence "<link to the query>" --quality measured --definition-version 2

The unit is percent. The numbers and the version are invented for this example.
