# Quality method

The standing method behind `playbooks/weekly-quality-page.md`. `checklists.md` holds this team's pass standards; this file
holds how the scores work and what the sources say. Figures name their source and the year it was read.

## Definitions

- **Pass rate**: inspections passed divided by inspections. Say whether the inspections are random checks, complaint
  checks, or both. Report them apart.
- **Re-do rate**: re-dos divided by jobs completed in the same window. Say which denominator you used.
- **First-time-fix rate**: the share of jobs closed without a return visit inside a set window. Definitions vary; the team's
  window is written in this file once chosen, and it is never compared with a figure that used another window.
- **Re-do cause**: one of these, in order of how often the team should look at it first:
  - unclear pass standard (system);
  - equipment or supplies missing or wrong (system);
  - instructions or checklist out of date (system);
  - missed checklist item (crew, unless the item is hard to see, which makes it system);
  - wrong method (crew, unless the method is the one the team taught, which makes it system);
  - customer changed the scope (neither);
  - other, with a note.

## Benchmarks (vendor-published; check before quoting)

- Aquant's 2025 Field Service Benchmark Report (vendor-published, read 2026-11-01 through search summaries): median
  first-time-fix 75 percent, top 20 percent about 86 percent, bottom 20 percent about 53 percent, over a 30-day window across
  157 service organizations. Industrial machinery reads lower, at about 71 percent.
- Aquant's 2026 edition (same source, same read): industry average 77 percent across 161 organizations.
- These are a vendor's sample of field-service businesses, not a standard. Use them to ask a question ("are we far below
  the typical range?"), not to set a target. The team's own trend over 8 weeks matters more.

## Reading a small sample

- Under 10 jobs in a crew's window: the score is not yet reliable and the crew is not ranked. Say so on the page.
- Show the range with the rate. A Wilson score interval (95 percent) is a standard way to do it. For example, 6 passes in
  8 jobs is 75 percent, with a range of about 41 to 93 percent. 15 in 16 is 94 percent, with a range of about 72 to 99.
  A range that wide means one more month of data before anyone acts.
- Use a rolling window (8 weeks) for crew scores, and a monthly figure only with the count behind it.

## Common cause and special cause

- One failure is usually noise. Watch the trend, not the single week.
- The same item failing across several crews is a common cause: the checklist, the training or the equipment. The fix
  belongs to the system, and the proposal goes to the checklist.
- A crew that stays out of line for several weeks, with enough jobs to be reliable, is a special cause worth a look. The
  look is a conversation with the Operations Manager, with the facts, never a conclusion on this bot's page.
- Control charts (Shewhart, standard quality-control practice) are the usual tool for telling the two apart. A small team can
  use a simple rule: act on a point outside the crew's own range, or on several points in a row on the same side of the
  average.

## Sampling and evidence

- Inspect a random share of jobs. A score from complaint-driven jobs only makes every crew look worse, and one from the
  easiest jobs only makes them look better.
- A photo proves an item only if it shows the pass standard. An item without a photo is marked "not evidenced".
- A checklist change is proposed, reviewed by a person, and dated. Old results are not re-scored under a new standard.

## Sources

- Aquant, 2025 and 2026 Field Service Benchmark Reports (vendor-published), via search summaries, read 2026-11-01. Confirm
  figures at the source before quoting.
- ISO 9001:2015, clause 10.2 (nonconformity and corrective action), as the reference for recording a cause and a fix. The text
  was not read for this file.
- Wilson score interval and Shewhart control charts: standard statistical and quality-control methods, from general reference,
  not read in this run.
