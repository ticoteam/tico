# Write a spec page

Used in the Spec stage, for an idea you moved there when Spec had room, or one a person moved there. Budget 60 minutes
for a first version. The outcome is one Spec page attached to the Spec page requirement, which Jev checks and a person
then OKs from the page alone. You write it yourself: nobody else on this team writes one.

---

## 1. Confirm it was chosen

    tico task show <id>

Read the brief in the task and its evidence in `knowledge/briefs.md`. Check `tico goal list` and your manager's decisions
for the choice. If nobody chose it, say so on the task and ask who did; a spec is not a way to decide by stealth. Pull the
repository, and if a spec for this problem already exists, extend it instead of starting a second file.

## 2. Gather the evidence

Read your own ranked problem list, the latest feedback update, the UX Researcher's and Product Analyst's work for the
area where the team has them, and `tico meeting search "<feature>"` for what customers and the team said. Quote sparingly,
with source and date. Separate what was observed from what was inferred, and mark each inference.

## 3. Set the appetite

Follow `skills/shape-a-spec-to-an-appetite/SKILL.md`. Write the appetite (team-weeks, a small or big batch) before you
draft the solution. If the problem needs more than a big batch, say so and stop there: it is two problems, or a bet for a
later cycle.

## 4. Write the core page

In the order of `knowledge/spec-format.md`, in `specs/<slug>.md`:
- **Problem**: who, trying to do what, what it costs them today. Two or three sentences, from the brief.
- **Who has it**: accounts in the window, out of the segment's accounts in the cohort, with the window.
- **Evidence**: three to five lines, each with its source and count.
- **Appetite**: the time the problem is worth, and who set it.
- **Goal** and **How we will know**: the goal in Tico it moves, with the measure, baseline, target, read date and guard
  from `skills/write-how-we-will-know/SKILL.md`.
- **Scope** and **Non-goals**: bullets. Every non-goal is something someone might reasonably assume. A change to CI
  workflow files (`.github/workflows/`) is a person's, since bots cannot push them: the spec names it as a person's step,
  and the Engineering task leaves it out.
- **Acceptance criteria**: follow `skills/write-acceptance-criteria/SKILL.md`, each tagged before release or after launch.
- **Open questions**: each with the human who owes the answer and a date.
- **Risks**: what could make this the wrong bet, and the cheapest check.

The spec says what the team builds and how it is judged; it does not pick the design. Where the team has not decided,
write "open question" and name who decides. No customer's name, email address or other personal detail: name an account
by its id.

When the page describes its sign-off, identify the configured approver by their name. If you are writing the page for
that approver, address them as "you". Do not call the person "Product Manager" or another role unless the sentence is
about that role in general.

## 5. Check it with the builders' eyes

Read it as the engineer: can each criterion be tested? Is anything ambiguous ("quickly", "simple")? Is there a dependency
on another team or another repository? Is every rabbit hole fixed, cut or time-boxed? Fix those before handing over.

## 6. Build the page and attach it

Follow `skills/build-the-spec-page/SKILL.md`: one page, the change in its first line, every screen it touches before
and after at phone (390 px) and desktop (1280 px) width, with red numbered arrows on what changes. Publish it:

    tico page publish specs/<slug>.html --spec --task <id>

It prints the file id. Attach it to the requirement `tico task show` names:

    tico task attach <id> page:<file id> --req "<requirement>" --version N

Jev checks it, then a person OKs it, and the task moves to In development, where {{app_name}} files the Engineering task.
The stage asks the person for that OK: do not ask them again with `tico task ask`.
Put the open questions and who owes them in a note on the task. If the person sends it back, their notes are on the task:
change the spec, publish again and attach the new version.
