---
name: shape-a-spec-to-an-appetite
description: Set how much a chosen problem is worth before any design, then shape the core of a spec to fit that time, with its rabbit holes and no-gos named.
---
# Shape a spec to an appetite

When to use: at the start of every new spec, before the solution is drafted. Use it again when a spec's scope grows
past its appetite.

Steps:
1. Confirm the problem was chosen by the Head of Product or the owner. If it was not, stop and ask who decided.
2. Write the appetite in team-weeks: a small batch (one to two weeks for a designer and one or two engineers) or a big
   batch (a full six-week cycle). A problem that needs more than a big batch is two problems, or a bet for a later cycle.
3. Fix the time and vary the scope. Sketch the smallest solution that solves the core problem inside the appetite.
   Mark everything else as scope the team can drop.
4. Find the rabbit holes: the parts too unknown, complex or open-ended to bet on. An integration with no documentation,
   a data migration, a permission model with many roles. For each one, write a fix in the spec, cut it, or name a
   spike with its own time limit that settles it.
5. Write the no-gos: the nearby work a reasonable reader might assume is included. Each no-go is one line.
6. Compare the sketch with the appetite. If the sketch needs more time, cut scope. Do not raise the appetite silently.
   A raised appetite is the owner's decision, dated, with the reason.
7. Put the appetite at the top of the spec, with who set it and when.

Checks before you finish:
- The appetite is written before the solution, with a unit (team-weeks).
- Every rabbit hole has a fix, a cut or a time-boxed spike.
- The non-goals name at least one nearby thing someone might expect.
- Any change to the appetite is dated, with who agreed it.

Example (generic, invented): "Appetite: two weeks, one designer and one engineer. Problem: members lose their booking
when a class moves. Core solution: notify the member and keep the booking. Rabbit hole: recurring classes that change
time zone. Fix: out of scope; this spec covers same-day moves only. No-go: changing how instructors set schedules."
