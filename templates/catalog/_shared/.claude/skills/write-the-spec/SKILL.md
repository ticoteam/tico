---
name: write-the-spec
description: Write the spec for a feature, a change or a bug as one visual page a person can OK from the page alone: a title, one line saying what the spec is for, clickable contents, then real high-resolution (2x) before-and-after screenshots, charts and one-line bullets. The fewest words that get the idea across, clarity above everything; one page by default, never cut clarity to fit a word count. Grounded in real customer requests, real data and real scenarios, with every use case the change touches walked through and either covered by a checkable criterion or named as out. Built with Tico's build-the-page templates and published as the task's Spec. Use when a task is in the Spec stage, when its instructions name write-a-spec or a spec page, or when a chat says spec, write up, PRD, requirements, brief for the builder, acceptance criteria, define done, or what exactly are we building; after brainstorm-a-feature and decide-what-not-to-build have picked what to build; and whenever a builder, reviewer or QA bot says the spec is unclear. Not for choosing what to build (use decide-what-not-to-build first).
metadata:
  function: product
  roles: [feature-owner, product-manager, product-lead]
  actions: [read, draft]
  review: none
---
# Write the spec

## What great looks like

A great spec is the shortest page from which a stranger would build the right thing. The person who OKs it reads the title and the line under it, scans the pictures, and knows exactly what they are agreeing to. The bot that builds from it never has to guess.

Every spec follows the eight page rules in `.claude/skills/build-the-page/SKILL.md`. `.claude/skills/build-the-page/examples/change-spec.html` is a real spec in that shape: read it first.

- **Clarity above all.** One reading, one meaning. Where a sentence could be read two ways, it is rewritten or replaced by a picture or an example.
- **Pictures before words.** The real screen today and the real screen after, sharp at high resolution, with numbered arrows, carry the idea; a chart carries the numbers. Text only says what a picture cannot, in one-line bullets.
- **The fewest words.** Every sentence names a number, a screen, a customer or a rule. A sentence that would fit another spec is cut. Length follows clarity: one page by default, split when that helps, and as long as the use cases need, never cut below clear to meet a word count.
- **Real, not imagined.** It rests on requests customers actually made, numbers pulled from live data on a stated date, and scenarios that actually happened. Nothing is invented; a gap is marked "missing".
- **The use cases are thought through.** Each real scenario is walked from start to finish, and each state the change touches (empty, the limit, no permission, sent twice) ends in a criterion or in a plain "out".

## First time at a company

Read `knowledge/product.md` and, where the repository has it, `knowledge/spec-format.md`. If the **Specs** section exists, skip this part. If not, ask your owner these in one direct chat message, numbered, each with its default (`tico chat ask --to <person> "<the questions>"`). Start on the defaults at once; save the answers, dated, under **Specs** when they arrive.

1. **Who OKs specs, and how do they read them?** Default: any person on the team, on a phone, in about a minute. So the line under the title says what they OK.
2. **Does every spec need a section beyond the template's?** Default: no. Anything you name is saved in `knowledge/spec-format.md` and added to every spec as its own section.
3. **How do I name customers in a spec?** Default: by account id and kind ("account 4417, solo electrician"). Never a name, an email address or a phone number.
4. **Where does the app run for the "after" screenshot?** Default: the demo or dev server described in `knowledge/standards.md`. Where there is none, the spec shows today's screen with arrows and says in the caption what will change.
5. **What do you want asked before a spec is written, not assumed?** Default: only what changes who the feature is for, what a customer pays, or what the company is legally bound to. Everything else I write as a labelled assumption that your OK confirms.
6. **What is the default measure of success?** Default: the area's activation event in `knowledge/area.md`. A target nobody has set is written "missing: to be set", never invented.

## Steps

### 1. Start from what is decided

    tico task show <id>

Read the chat the feature started from and every comment. Then read what earlier work already settled, and reuse it instead of asking again:

- The ideas page and its chosen candidate (`ideas/<slug>.html`, from `brainstorm-a-feature`).
- The verdict comment and the scope table (In, Later, Out) with the appetite, from `decide-what-not-to-build`. If there is none, run that skill's steps 5 to 7 now. A spec without a cut scope is not started.
- `knowledge/problems.md` and any spec already in `specs/` for the same problem. Extend an existing spec; never start a second file.

Pick the template: a bug (`bug-spec`), a new feature or a change on a screen (`screen-spec`), or a change with no screen such as an API, a command or a job (`change-spec`).

**A bug:** reproduce it before writing anything (a screenshot, the call and its output, or the query and its rows) and find the root cause in the code with `path:line`. If part of the cause is unknown, say which part and how it will be found. Never guess one.

### 2. Collect the real material

Three piles. Each item carries where it came from and its date.

| Pile | Where | What you need |
|---|---|---|
| **Requests** | The task's requesters (`tico task requesters`; run it with `--help` for its options); `knowledge/customers/findings.md` and its evidence log; `knowledge/requests.md`; tickets and calls (`tico meeting search "<term>"`, `tico meeting read <id>`) | How many distinct accounts, from which kinds of source, and one or two quotes copied exactly, each with its evidence id |
| **Data** | `tico kpi show <id>`; the team's named queries (`tico db list`, `tico tool query-search <database> <words>`, `tico db <database> --query <id>`) | How many customers meet the problem and how often: the number, what it is out of, the query's name, the date |
| **Scenarios** | The same tickets and calls, and the records themselves | Three to five cases that really happened: which account (by id and kind), what they were doing, what went wrong, what it cost them |

Where a pile is empty, write `missing: <what>, <how to get it>` and carry that line into the spec. Do not fill the gap with a plausible number or a typical-sounding customer. If a quote or a number cannot be traced to its record, it does not go in.

### 3. Draft first, ask second

Write the whole spec before asking anyone anything. Where you had to choose, write the choice as an **Assumed:** bullet under Scope, with why in the same line. The OK on the spec confirms every assumption the page shows, so a labelled assumption is a question answered without a meeting.

Ask before publishing only when the answer changes what is built and falls under what the owner wants asked (question 5 above). Then ask once, on the task: numbered, each with your recommended answer worded so "yes" accepts it (`tico task ask <id> --to <person> "<questions>" --version N`). An open question holds the task until it is answered, so ask only these. Everything else goes in **Open questions** with who owes the answer and by when.

### 4. Walk the use cases

This is where specs fail: the main path is clear and everything around it is left to the builder.

1. **List who acts**: each kind of customer and teammate the change touches, including the one who receives the result (the customer's customer, an admin, a bot).
2. **Walk each real scenario** from step 2 through the new behaviour, from what starts it to the end result. Note each point where the scenario does something the main path did not expect.
3. **Fill the grid** in `references/use-case-grid.md`: for each actor, the states the change touches (first time, empty, one, many, the limit, no permission, offline or slow, sent twice, two people at once, a time boundary, undo, data that exists already).
4. **Give every touched cell an ending**: a numbered criterion, or an **Out** line with its reason. No cell is left to be guessed.
5. **Stop where it stops mattering.** A state the change does not touch is not listed at all.

Each scenario from step 2 must end in at least one criterion. If a real case is not covered, either the scope is wrong or the case goes to **Out** by name.

### 5. Write the title, its line and the pictures

- **Title (H1):** the feature in the customer's words. No "Spec", no date.
- **The line under it:** `<p class="desc">This is a spec to let <who> <do what they cannot now>.</p>` One plain sentence, at most 40 words. It is the goal and the summary. A bug spec: "This is a spec to fix <what goes wrong> for <who>." Anything the OK must cover knowingly (a cost, billing, another customer's data, messages to customers) goes in this line.
- **Contents:** the template's `<nav class="toc">`, one link per H2.
- **Pictures, before any detail.** One before-and-after pair per screen, in the order a person meets them, at phone (390 px) and desktop width. A red numbered arrow on each thing that changes, one line per number in the caption, the same number in the criterion that checks it. A bug spec first gives the report (quoted, one line, who and where) and the root cause with `path:line`, as two bullets.

Pictures are real screenshots, never drawings or mock-ups, at device pixel ratio 2 (a 390 px phone screen is captured 780 px wide). A blurry or 1x screenshot is retaken. "After" is the app running with the smallest local change that shows the result; `.claude/skills/build-the-page/SKILL.md` says how. A change with no screen shows the real call and its real output, before and after.

### 6. Write the sections

Each is an H2, in the template's order. `references/spec-sections.md` says what belongs in each, with a good and a bad line.

1. **Why now.** The main number as a chart, then one-line bullets: the evidence with its count, source and date; the appetite; the measure, its baseline, target, read date and the guard that must not get worse (`skills/write-how-we-will-know/SKILL.md` where your repository has it).
2. **Done when.** Numbered criteria, one behaviour each, as Given / When / Then, each tagged *before release* or *after launch*. Follow `skills/write-acceptance-criteria/SKILL.md` where your repository has it. A bot must be able to check each with a test, a call or a screenshot.
3. **Scope.** **In** and **Out**, copied from the scope table, and **Assumed:** bullets. Every Out is something a reader would have assumed was included, with its reason. Nothing is "nice to have": it is in, or it is out.
4. **Real cases.** Add `<section id="scenarios"><h2>Real cases</h2>` and its contents link. One bullet per scenario from step 2: the account by id and kind, what happened, and the criteria that now cover it.
5. **Plan.** The steps in order, with the files or screens each touches and the test that proves it. Read the code first.
6. **Release needs.** What ships it; data changes, settings, flags.
7. **Open questions.** Each with who owes the answer and a date.

Delete an optional section with nothing to say (Release needs, Open questions), and its contents link.

### 7. Cut the words

Go through the page once with `references/fewest-words.md`. Most first drafts are twice too long. The short version:

- Would the sentence fit another spec? Cut it. Can two lines be one? Merge them.
- A comparison becomes a table, a screen a picture, a number a chart, a rule with exceptions two examples.
- One term for one thing, the customer's term, the same on every line.
- No background, no history, no summary, no adjectives ("simple", "fast", "easy") without a number.

Then put back anything whose absence makes a reader guess. No guessing is the goal, in the fewest words.

### 8. Let length follow clarity

One page is the default. Many actors, many states, or money and data that cannot be wrong can need more.

- **Never pad and never squeeze.** Do not cut a touched use case to save space, or add text to look thorough.
- **Split when that helps.** A spec that keeps growing is usually two features. Split when each part can be built and OK'd on its own: a parent task holds the goal and the scope table, each part is its own task with its own spec (`tico task file --parent <id> --owner <slug> "<title>"`).
- **Length is a warning.** `page.py check` warns over 1,200 words (1,000 for a change spec), not counting pictures and code. Run step 7 again; if the page still needs the words, keep them on the page and say why in a task comment with the count. Every other finding is fixed.

### 9. Check it against reality

Before publishing, read the things a spec can quietly contradict:

- **Room.** `tico pipeline show product`: what is already in Build, and whether this depends on any of it (`tico task relate <id> depends_on <task>`).
- **Direction.** `tico strategy show`: it serves a segment the strategy names, and breaks nothing under "What we won't do".
- **Appetite.** The plan fits the appetite that was set. If not, cut scope with `decide-what-not-to-build`; do not raise the appetite.
- **Other areas.** Each screen or record the plan touches that another Feature Owner owns: name that bot in Open questions so Related input reaches it.
- **Money, privacy, law.** If the change bills a customer, shows one customer's data to another, sends messages to customers or changes what the terms promise, say so in the line under the title.

### 10. Read it as a stranger

Read the page top to bottom as someone who has never seen the task or the product, and list every place you would have to guess: an undefined word, a "should", a state with no ending, a number with no source. Fix each one.

For a big-appetite spec, get a second read from a different model: `tico task child <id> --check-model --title "Read this spec cold" --body "List everything a builder would have to guess, and every sentence that can be read two ways. Do not suggest features."` Fix what it finds; do not argue with it in the task.

### 11. Build, check, publish, look

    python3 .claude/skills/build-the-page/page.py build specs/<slug>-draft.html --out specs/<slug>.html
    python3 .claude/skills/build-the-page/page.py check specs/<slug>.html --kind <bug-spec|screen-spec|change-spec>
    tico page publish specs/<slug>.html --task <id> --as Spec --title "<Feature> · Spec"
    python3 .claude/skills/build-the-page/page.py render <file id> --kind <kind>

Fix everything `check` names. Open both pictures `render` writes and look: the title, its line and the contents at the top, every screenshot drawn, each arrow on its target, nothing cut off at phone width. Fix and publish again (a new version) until it is right.

If `tico task show <id>` still lists the Spec as a missing requirement, attach the page to it: `tico task attach <id> page:<file id> --req "<requirement>" --version N`.

Publishing with `--as Spec` submits the spec. The stage's checker reads it for three things only: the goal, the scope with what is in and what is out, and what done looks like as criteria someone can check. A person, never a bot, gives the OK. Do not message anyone to ask for it.

### 12. Keep it true

- **Related input.** Other bots comment once. Take what makes the spec clearer or catches a missed case; publish a new version. What you do not take gets a line in the ledger (`knowledge/not-building.md`) with the reason.
- **Sent back.** Fix exactly what was named, publish a new version, say in one comment what changed.
- **During the build.** The spec is the reference. If the build shows the plan was wrong, change the spec first, then the code. A change to the goal, the scope or a criterion after the OK is a new version with a "What changed" section first; it is never made silently.

## Gates

- The goal, the scope (in and out) and the criteria are all present and all checkable.
- Every number has its source, its date and what it is out of. Every gap is written "missing", with how to fill it.
- Every quote is copied exactly from a record you opened in this run, with its evidence id. No customer's name or contact details anywhere.
- Every picture is a real screenshot at high resolution (retina, device pixel ratio 2). None is drawn or mocked up, and none shows one person's real data.
- Every real scenario maps to a criterion or to an Out line by name. Every touched state has an ending.
- Nothing is "nice to have", "ideally", "should" or "phase 2" without a ledger row.
- No question that would change what is built is still open when the page is published as Spec.
- `page.py check` reports nothing (or only the length warning, under step 8's rule), and the published page was looked at, rendered, at both widths.
- The appetite on the page is the one that was set. It was not raised to fit the plan.

## Autonomy

Do on your own: the whole spec, from reading to publishing, including choosing between reasonable options (written as assumptions), splitting a feature into parts, and new versions after input.

Needs a person:

- **The OK on the spec.** The stage asks for it; you never do.
- **A raised appetite**, or anything in step 9's last line (billing, another customer's data, messages to customers, the terms): say it in the line under the title so the OK covers it knowingly.
- **Contacting a customer** to fill a gap in the evidence: follow `learn-from-customers`, which holds the sending rules.

Never promise a customer that something will be built or when, in the spec or anywhere else.

## Output

One page, `specs/<slug>.html`, published as the task's **Spec**: the title, one line saying what the spec is for, the contents, the pictures (real before and after screenshots at phone and desktop width, at device pixel ratio 2, with numbered arrows), then Why now, Done when, Scope, Real cases, Plan, Release needs and Open questions, each an H2.

Plus: a ledger row for every Out and Later (`knowledge/not-building.md`), sub-tasks where the feature was split, and one comment on the task naming what is assumed and what is missing.

## Checks before you finish

- A stranger can say from the title and the line under it what will be built and what their OK means.
- Each arrow number appears in the before, the after and a criterion.
- Each criterion has one When, one observable Then and a check tag. None uses "fast", "easy" or "simple" without a number.
- Each Out names something a reader would have assumed, and why it is out.
- Each real case lists the criteria that cover it.
- Every number shows source, date and denominator. Every "missing" says how to get it.
- No sentence would fit another spec. No section restates the line under the title. Every bullet is one line.
- One term per thing, throughout.
- Every screenshot is real and sharp at 2x; none is a drawing or a 1x capture.
- No clear sentence or touched use case was cut to meet a word count.
- `check` reported nothing, or only the length warning with the reason in a task comment; the rendered page was opened and looked at, at 390 px and desktop width.
- The file id is attached where `tico task show` says the Spec goes.

## Example

`.claude/skills/build-the-page/examples/change-spec.html`: "One stall system", a real Tico spec cut from 2,055 words to about 230. Title, one line, contents, today and after, a chart of where the hours went, three criteria, scope and plan.
