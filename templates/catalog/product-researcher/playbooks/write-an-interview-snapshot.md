# Write an interview snapshot

Triggered by a task naming an interview, call or feedback batch, and used for each new source in
`playbooks/weekly-research-digest.md`. Budget 15 minutes. The outcome is one page in
`knowledge/snapshots/` a product manager can read in two minutes and reuse in a brief.

---

## 1. Read the source

    tico meeting read <id>

Or the notes attached to the task. Read the whole thing before writing. If it is a private meeting, stop: bots
read team meetings only. Note who spoke, when, and how the conversation came about.

## 2. Write the snapshot

`knowledge/snapshots/YYYY-MM-DD-<source>.md`, in this order:
- **Who**: role, kind of team, how long they have used the product. No name unless `knowledge/privacy.md`
  allows it.
- **Their job**: what they were trying to get done, in one sentence.
- **The story**: one specific recent time they tried, in order: what triggered it, what they did, where it
  got hard, what they did instead. Only what they described happening, not what they say they would do.
- **Needs and pain points**: each as "wants to X so that Y", tagged with the quote that shows it.
- **Quotes**: three at most, exact, anonymised, each with the timestamp or line.
- **Surprises**: what contradicted `knowledge/opportunities.md`.
- **Follow-up questions**: what a human could ask next time. You do not ask them.

## 3. Keep the evidence honest

A wish for a feature is recorded as the need behind it, with the request kept as a quote. Opinions about what
other people want are not evidence; leave them out or mark them "opinion". Do not fill a gap with a plausible story.

## 4. Link it

Add the snapshot to the opportunities it supports in `knowledge/opportunities.md`, with the date, and update the
source counts. Competitor names and features go to `tico market report` with the quote and source.

## 5. Finish

Commit, then `tico task done <id> -m "..." --version N`: the snapshot path, the one thing that surprised you,
and what you could not read. You contact no one.

## When the source is thin

A short call with no story gets three lines and a note: "no story told". It counts as a source only for what it says.
