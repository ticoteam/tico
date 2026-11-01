# Review an asset

Triggered by a task with copy, a page, a deck, an email or a proposed name attached: "is this on
brand?", "can we call it X?". Budget 20 minutes. The outcome is a verdict and marked changes on the
task. The work itself is not edited.

---

## 1. Read the request

    hub task show <id>

Find the item, its audience, where it will appear and the deadline. If the audience is missing, ask
once with `hub task ask <id>`.

## 2. Review copy

Read it once as the audience, then line by line against the brand card (`hub brand show`). Mark each change as
**rule** (quote the rule) or **suggestion** (your judgement, clearly labelled). Check product and plan
names, prices and claims against `hub doc search`. Do not rewrite whole paragraphs; show the smallest
change that fixes each line.

## 3. Review a name

Check it against the naming rules (descriptive or coined, length, how it reads aloud, how it sits next
to existing product names). Search the public web and app stores for the same or confusingly similar
names in the same market and list what you found with links. Say plainly: "This is a starting point,
not clearance; legal counsel decides."

## 4. Hand it back

`reports/reviews/<date>-<item>.md`: verdict first, then the marked changes, then open questions. On the
task, the verdict in one line and the path. If the review exposed a missing rule, propose it.
`hub task update <id> --status done --note`.
