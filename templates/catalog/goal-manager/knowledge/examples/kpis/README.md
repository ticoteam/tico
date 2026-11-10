# The shape of a KPI folder

Copy `activation-rate/` to `kpis/<slug>/` in your repository, where `<slug>` is the KPI's `slug` from
`tico kpi list`. The files:

- `definition.md`: the KPI's definition as Tico holds it, its version, and the exact counting rules.
- `sources.md`: where each input comes from, who owns that system, and how late it lags.
- `compute.sql` (or `compute.py`, or a query file for the source): the one thing that produces the number.
- `known-values.md`: inputs with the exact outputs the computation must give. Run it before trusting a number.
- `changelog.md`: one dated line per change to the computation or the definition version.
