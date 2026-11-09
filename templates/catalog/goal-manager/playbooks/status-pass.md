# Status pass

After the readings, `hub goal refresh`. The server works out every goal's colour from its KPIs, pace and
deadline (or from the owner's check-in and task progress when a goal has no KPI). You do not decide a colour.

Inputs: the readings posted in this run's KPI pass, and the output of `hub goal refresh`.

- A colour a human set is never changed. Where the arithmetic disagrees, the answer lists it under `suggested`.
  Report each one: the goal, the colour the human set, the colour and reason the arithmetic gives.
- Report each goal whose colour changed: the goal, from, to, and the reason line the server wrote.
- Goals without data stay uncoloured or gray. That is the honest answer; say how many.
- Never call `hub goal status`. If a goal's colour looks wrong, the fix is a better reading, a check-in from its
  owner, or a proposal; not a colour.

**Done when:** `hub goal refresh` has run, each colour change and each disagreement with a colour a human set is in the report, and goals without data are counted.
