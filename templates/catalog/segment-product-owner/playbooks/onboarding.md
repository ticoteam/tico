# Setup

Runs once, on the first message or task you receive, while `state.md` says onboarding has not finished. Budget 20
minutes. The outcome is five recorded answers, `knowledge/segment.md` written, a first review on the task from a real
export, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub market segment tree
    hub strategy show --section serve
    hub goal list --all

Find the segment named in your title in the tree. Note whether the Strategy page marks it Us (served now) or Next (not
served yet), and any goal that names this segment or its activation. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do: a weekly review of this segment's activation and retention, a ranked list of its problems, and briefs for
the Product Team Manager to approve. What you will not do: decide what gets built, or tell a customer what will change.
Nothing is filed or changed during setup.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which customer segment do you own, and how is a customer counted in it? (Default: the segment named in your title.)
2. What counts as activated for this segment, over what window, and how long is the retention window? (Default: 14 days to activate; retention at 30 and 90 days.)
3. Where do this segment's problems arrive, and who can send the usage export each week? (Default: support tasks, call notes and the export attached to the task.)
4. Which goal or KPI does this segment's product outcome serve, and what is its target? (Default: the goal `hub goal list` names for it; if there is none, say so.)
5. Who reads the weekly review, and who answers when the Product Team Manager cannot? (Default: the Product Team Manager, Mondays at 08:00; then you.)

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/segment.md`: the segment, its label
(`segment:<slug>`, lowercase with dashes), the counting rule, the activation event and window, the retention windows, the
target and its source, and the human who answers escalations. Create `knowledge/problems.md` and `knowledge/briefs.md`
with their headings only.

## 5. Produce the first review now

Follow `playbooks/weekly-segment-product-review.md` on the exports attached to the task. Write
`reports/YYYY-MM-DD-segment-product-review.md`, publish it, and label it "First draft, not yet reviewed". File no brief.
If no export is attached, say which export is needed and for which dates, and stop there.

## 6. Check the routine

Setup switched your routine on. Check it with `hub automation list` (if it shows off, `hub automation update <id> --enable`)
and tell the human in one line what it does: "I will review this segment each Monday at 08:00 from the exports you
attach." They can change it or turn it off at any time. Nothing needs approval.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. Last, once the answers and the first review
are recorded, and not before, run:

    hub bot setup-done
