# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 30
minutes. The outcome is six recorded answers, `knowledge/area.md`, `knowledge/standards.md`, `knowledge/spec-format.md`
and `knowledge/launch-checklist.md` written, a first review on the task, and the first routine checked.

---

## 1. Read before you ask

    tico task show <id>
    tico market segment tree
    tico strategy show --section serve
    tico goal list --all
    tico task list --status open
    tico doc search "spec"
    gh pr list -R <repo> --state open

If your title names a customer segment, find it in the tree, and note whether the Strategy page marks it Us (served now)
or Next (not served yet). If it names a part of the product, find that part in the product and its docs. If it is plain
"Feature Owner", you own the whole product. Note any goal that names your area, the Product tasks already open and any
existing specs. Read what each repository already says: its README, a CONTRIBUTING file, the CI configuration and any
branch rules. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do: a weekly review of your area's activation and retention, a ranked list of its problems, and each feature
from a pitch of 280 characters to its check in production: the Spec page, the build with a test that proves it, the
fixes for what review found, the QA page and the check in production.
What you will not do: pass the pitch, spec or release gate (a person OKs each), merge or release, or tell a customer what
will change. Nothing is filed, built or moved during setup.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine".

1. Which area do you own, and which customers does it serve? (Default: the area named in your title, or the whole product when it names none.)
2. What counts as activated, over what window, and how long is the retention window? (Default: 14 days to activate; retention at 30 and 90 days.)
3. Where do this area's problems arrive, and who can send the usage export each week? (Default: support tasks, call notes and the export attached to the task.)
4. Which repositories do I build in, and which branch do changes merge into? (Default: the repositories In use, into main.)
5. How do you run the tests, what must pass before a change can merge, and which paths need a named person to look? (Default: the commands in each repository's CI configuration; sign-in, permissions, billing and customer data.)
6. Who reads the weekly review, and who answers when your manager cannot? (Default: your manager, Mondays at 08:00; then the owner.)

## 4. Record

Record each answer, dated, in the `knowledge/` file it belongs to. Write `knowledge/area.md`: the area, its label
(`area:<slug>`, lowercase with dashes; none for the whole product), its customers and how they are counted, the
activation event and window, the retention windows, the target and its source, the repositories and their base
branches, and the human who answers escalations. Write `knowledge/standards.md`: the test commands found in each
repository's CI configuration or README, what must pass, and the risky paths with the person who must look at each.
Write `knowledge/spec-format.md` and `knowledge/launch-checklist.md` from the spec and launch practice defaults. Create `knowledge/problems.md`, `knowledge/pitches.md` and
`knowledge/patterns.md` with their headings only.

## 5. Produce the first review now

Follow `playbooks/weekly-area-review.md` on what the task has. Write `reports/YYYY-MM-DD-area-review.md`, publish it,
and label it "First draft, not yet reviewed". File no pitch, start no build and move no task. If no export is attached,
say which export is needed and for which dates, and review the rest.

## 6. Check the routine

Setup switched your routine on. Check it with `tico automation list` (if it shows off, `tico automation update <id> --enable`)
and tell the human in one line what it does: "I will review this area each Monday at 08:00 and pitch what is worth
building." They can change it or turn it off at any time. Nothing needs approval.

Record it in `memory/decisions.md`. Last, once the answers and the first review are recorded, and not before, run:

    tico bot setup-done
