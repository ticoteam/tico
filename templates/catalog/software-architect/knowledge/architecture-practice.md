# Architecture practice

Standing reference for design reviews, decision records, architecture checks and the debt register. The team's own
significant-decision rule lives in `decision-rules.md`; this file holds general practice and the sources behind it. Where the
two disagree, the team's rule wins, and the disagreement goes in `decisions.md`.

## Decision records

- An architecture decision record captures one decision, its context and its consequences, short enough to read in two
  minutes. Michael Nygard proposed the format in 2011: title, context, decision, status, consequences (Cognitect blog,
  2011). Keep superseded records and mark them; do not delete them.
- Common templates such as MADR add options considered and decision drivers. Use the repository's own format when it has one.
- Write a record when the decision would be expensive to reverse or when people will ask why later. Record the decision that
  was made, with its options, and never present an option as the decision.

## Design reviews

- Restate the problem before you judge the proposal. If the problem cannot be stated in three sentences, that is the first
  finding.
- Sort decisions as two-way doors (cheap to undo) or one-way doors (costly to undo). Give one-way doors the scrutiny. The
  idea comes from Jeff Bezos's 2015 shareholder letter, read in secondary coverage; the letter itself was not read for this
  file.
- Check the data path. When a relation changes from one to many, every existing reader changes meaning: a join fans out rows,
  and a check that means "any" can flip to "all". Walk two example rows through each reader.
- Trace every serializer of a record, not only the reader the design names. A field that is private in one response can be
  public in another, such as a list or a tool result.
- An access check is only as strong as who can be issued the credential it checks. Ask who can obtain each new scope.
- Check the schema and default page size of every service a design calls. A default limit that silently truncates results is
  a design defect.
- Ask what happens when a dependency is slow or down, how the change is turned off, and who is paged.

## Verdicts

A verdict is the first line of a review, and it is advice: the human who owns the decision approves or blocks it. Use one of three:

- Approved: the design can be built as written.
- Approved with changes: each change names its replacement and whether it blocks the build.
- Rework: only when the shape is wrong. Examples: the capability already exists under another name, the logic sits in the wrong
  layer, a data migration is needed and not planned, a job cannot run twice, an endpoint breaks existing clients, or money,
  deletion or access is decided without a human. A count of must-change points alone is not a rework.

Check your own verdicts. Once a month, compare the reviews with what was built. Sort each as built as approved, changed with a
stated reason, raised a point that the team then took, or was too strict. A repeated pattern becomes a rule change in
`decision-rules.md`.

## Structure and fitness

- The C4 model describes a system at four zoom levels: context, container, component and code. It is independent of notation,
  and the code level is usually generated, not drawn by hand (Simon Brown, c4model.com).
- Architecture fitness functions are automated checks of an architectural property, such as a dependency rule, a latency
  budget or an interface contract, run with the tests (Ford, Parsons, Kua and Sadalage, Building Evolutionary Architectures,
  second edition). A rule that no check enforces will drift.
- Parallel change (expand, migrate, contract): add the new form beside the old, move each consumer, and remove the old form
  only when nothing uses it. Stopping after expand leaves two forms in the code (Danilo Sato, Martin Fowler's bliki,
  "ParallelChange", 2014).

## Technical debt

- Ward Cunningham's 1992 debt metaphor describes the cost of working with code that is not yet right. The register in this
  bot's files makes the cost, the risk and the fix size explicit, so items can be compared (see the skill
  `skills/score-a-tech-debt-item/SKILL.md`).
- Record a debt item with its dated evidence. A feeling is allowed when it is marked as a feeling.

## Sources

- Michael Nygard, "Documenting Architecture Decisions", Cognitect blog, 2011 (fetched as a summary, 2026-11-01).
- MADR, the markdown architectural decision records project, github.com/adr/madr (repository page read 2026-11-01; the current
  version was not stated there).
- Simon Brown, "The C4 model for visualising software architecture", c4model.com (read as a snippet, 2026-11-01).
- Ford, Parsons, Kua and Sadalage, Building Evolutionary Architectures, second edition (publisher's page read as a snippet,
  2026-11-01).
- Danilo Sato, "ParallelChange", Martin Fowler's bliki, 2014 (fetched as a summary, 2026-11-01).
- Jeff Bezos, shareholder letter 2015, read only through secondary coverage (blog.kindel.com, 2019).
- Ward Cunningham, debt metaphor, OOPSLA experience report, 1992 (from the author's known account; not read again for this file).
