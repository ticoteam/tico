# Review an asset

Triggered by a task with copy, a page, a deck, an email or a proposed name attached: "is this on brand?", "can we call it X?". Budget 20 minutes.

Inputs: the attached item, its audience and where it will appear, the deadline, the brand card (`hub brand show`) and `knowledge/rulings.md`.

Outcome: a verdict and marked changes on the task. The work itself is not edited.

Done when: the verdict is on the task in one line with the path to the full review, every change is marked rule or suggestion, and any open question is asked once.

---

## 1. Read the request

    hub task show <id>

Find the item, its audience, where it will appear and the deadline. If the audience is missing, ask once with `hub task ask <id>`.

## 2. Review copy

Follow `skills/review-copy-against-the-brand-book/SKILL.md`. Read it once as the audience, then line by line against the brand card (`hub brand show`). Mark each change as **rule** (quote the rule) or **suggestion** (your judgement, clearly labelled). Check product and plan names, prices and claims against `hub doc search`. Do not rewrite whole paragraphs; show the smallest change that fixes each line.

## 3. Review a name

Follow `skills/run-a-name-clash-check/SKILL.md`. Check the name against the naming rules first (descriptive or coined, length, how it reads aloud, how it sits next to existing product names). Then list the close hits from the registers, app stores and the web, each with its link and date. End with the line: "This is a starting point, not clearance; legal counsel decides."

## 4. Hand it back

Write `reports/reviews/<date>-<item>.md`: the verdict first, then the marked changes, then open questions. On the task, put the verdict in one line and the path. If the review exposed a missing rule, propose it for the owner. Then:

    hub task update <id> --status done --note "<verdict>; reports/reviews/<date>-<item>.md"
