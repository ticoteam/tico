# A new KPI, or a wrong one

A KPI exists in Tico (a human made it, or the owner confirmed your `goal_kpi` proposal). You build what computes
it. You never create the KPI record yourself.

1. `hub kpi show <id>`: name, definition, unit, direction, cadence, source note, owner, version.
2. Copy `knowledge/examples/kpis/activation-rate/` to `kpis/<slug>/`, using the KPI's `slug`.
3. Write `definition.md` from Tico's definition in your own precise terms: numerator, denominator, period,
   exclusions. If the definition cannot be computed as written, do not bend it: propose a clearer one
   (`hub proposal create --kind kpi_definition --kpi <id> --payload-file f.json --reason "..."`) and stop.
   Check the measure too: a rate names its denominator, the period has an exact boundary and time zone, and each input
   comes from the system of record for it, with its lag written in `sources.md`.
4. Write `sources.md`, then the computation. Read-only queries only: `hub sql` for Tico's own data, or a tool the
   owner declared in `tools:`. No declared tool for the source: file one task for the owner naming the source
   you need, and leave the KPI reported as missing.
5. Write `known-values.md`: at least two periods where you can say the exact answer from a source you trust or
   from the owner. Run it. Only then post the first reading.
6. A definition changes only when the owner confirms your proposal. Then update `definition.md`, the computation
   and the known values together, add a line to `changelog.md`, and post the next reading with the new
   `--definition-version`. Earlier readings keep the version they used.
7. Never edit a known value so that a number passes. A mismatch means the computation or the source is wrong, or
   the known value is: ask the owner which.

**Done when:** `definition.md`, `sources.md`, the computation and `known-values.md` are in `kpis/<slug>/`, the known values pass,
and the first reading is posted or reported as missing.
