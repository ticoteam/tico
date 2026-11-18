# Write a spec page

Used in the Spec stage: a feature starts as a chat. Budget 60 minutes for a first version. The outcome is the task's
Spec, one page a person approves from the page alone. You write it yourself, and you build
from it: after a compaction it is the reference for what you are building.

---

## 1. Read the chat

    tico task show <id>

Read the chat the feature started from, what the person said in it, and the evidence in the task's comments and
`knowledge/problems.md`. Pull the repository, and if a spec for this problem already exists, extend it instead of
starting a second file.

## 2. Gather the evidence

Read your own ranked problem list, the latest feedback update, the UX Researcher's and Product Analyst's work for the
area where the team has them, and `tico meeting search "<feature>"` for what customers and the team said. Quote sparingly,
with source and date. Separate what was observed from what was inferred, and mark each inference.

**A bug:** reproduce it first (a screenshot, the call and its output, or the query and its rows), then find the root
cause in the code, with `path:line`, before you write anything. If part of the cause is not known, say which part and
how it will be found. Never guess one.

## 3. Set the appetite

Follow `skills/shape-a-spec-to-an-appetite/SKILL.md`. Write the appetite (team-weeks, a small or big batch) before you
draft the solution. If the problem needs more than a big batch, say so and stop there: it is two problems, or a bet for a
later cycle.

## 4. Write the page

A spec is clear when someone who has never seen the task reads the top of the page and knows exactly what will be
built. The pictures do most of the work. Start from the skill's template, in `specs/<slug>.html`:
`templates/bug-spec.html` for a bug, `templates/screen-spec.html` for a feature or change on a screen,
`templates/change-spec.html` for a change with no screen (an API, a command, a job).

**The top, in this order, and nothing else above the details:**
- **A bug:** the report (what was reported, by whom, when, with a link; quoted, cut to two lines if long), the root
  cause, the goal (what is true once it is fixed, one or two sentences), then the solution: before and after
  screenshots with red numbered arrows on what changes, one line per arrow. A fix with no screen shows the real call
  or job output before and after instead.
- **A feature:** the goal (who it is for, and what they can do after that they cannot now), then the solution: before
  and after screenshots with arrows, one pair per screen in the order a person meets them.

The after picture is the app running with the smallest change that shows it, on a local branch that is never pushed
(the skill's "Real screenshots"); never a drawing.

**The details, below the line**, only what helps the build, the reviewers or QA:
- **Done when**: the acceptance criteria, numbered, from `skills/write-acceptance-criteria/SKILL.md`, each tagged
  before release or after launch. They become your QA plan, so each one is checkable by a bot: a test, a call, a
  screenshot.
- **Scope**: in and out. Every out is something someone might reasonably assume. A change to CI workflow files
  (`.github/workflows/`) is a person's, since bots cannot push them: name it as a person's step, and your pull request
  leaves it out.
- **Why now and how we will know**: the evidence (each line with its source and count), who has it, the appetite, and
  the measure, baseline, target, read date and guard from `skills/write-how-we-will-know/SKILL.md`. A bug that only
  needs fixing says who it hurts and how often, in one line.
- **Plan**: the steps in order, with the files they touch and the tests that will prove them. You build from it, so
  read the code before you write it.
- **Release needs**: the apps or services that ship it, data changes, settings, flags, a store release. "None" where
  none.
- **Open questions**: each with the person who owes the answer and a date. Where the team has not decided, write
  "open question" and name who decides. Empty once agreed.

Any section `knowledge/spec-format.md` adds goes in the details. Every sentence names something specific (a number, a
screen, a file, a person); cut sentences that would fit any spec, and any section that only restates the top. No
customer's name, email address or other personal detail: name an account by its id.

When the page describes its sign-off, identify the configured approver by their name. If you are writing the page for
that approver, address them as "you". Do not call the person "Product Manager" or another role unless the sentence is
about that role in general.

## 5. Check it with a stranger's eyes

Read the top alone, as someone who has never seen the task: do they know exactly what will be built, from the words
and the pictures? Then read it as a reviewer: can each criterion be tested? Is anything ambiguous ("quickly", "simple")? Is there a dependency
on another team or another repository? Is every rabbit hole fixed, cut or time-boxed? Fix those before handing over.

## 6. Build the page and write it on the task

Follow `.claude/skills/build-the-page/SKILL.md`: screenshots at phone (390 px) and desktop (1280 px) width, arrows, then
build and check it with the skill's `page.py` (`--kind bug-spec`, `screen-spec` or `change-spec`), then publish it as
the task's Spec, in one step. Publishing it as the Spec submits it, so the check comes first:

    tico page publish specs/<slug>.html --task <id> --as Spec

It prints the file id. Render that page with `page.py render <file id>` and look at it at both widths. If it is broken,
fix the file and publish it again with `--page <file id>`: that writes the next version of the same page.

Put the open questions and who owes them in a note on the task. In Related input, one or two bots the decision model
picks comment on the Spec once: change it where they are right, write the next version, and answer the rest in one line
on the task. Then a person approves it, and the task moves to Build, which is yours. Never ask for that approval
yourself. If the person sends it back, their notes are in the chat: change the spec and write the next version.
