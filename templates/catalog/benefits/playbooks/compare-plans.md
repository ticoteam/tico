# Compare plans

Schedule: triggered by a task before open enrollment or when plans change at renewal. Budget 40 minutes. Inputs: the plan
summaries the Librarian cites and the plan year. Outcome: a plain-language comparison employees can read in five minutes, every
figure cited. It goes on the task, and it never recommends a plan.

Done when every figure in the comparison has a plan page and year, the three use levels are shown, the comparison is attached to the
task, and nothing has been sent to an employee or a broker.

---

## 1. Read the plan documents

    hub doc ask "What are the deductibles, premiums and out-of-pocket maximums for each plan in <year>?"
    hub doc read <path>

Read each plan summary the Librarian cites, whole. Note the plan year and page for every figure. A figure you cannot find
is a marked gap, never an estimate. If a plan has a Summary of Benefits and Coverage, read its coverage examples first.

## 2. Define the terms once

Premium (what is paid each pay period), deductible (what a person pays before the plan pays), coinsurance and copay,
out-of-pocket maximum (the most a person pays in a year), network. One sentence each, in the plan's own terms where it
defines them.

## 3. Build the table

Follow `skills/compare-plans-on-total-cost/SKILL.md`. One column per plan. The rows are the figures a person needs: employee
premium per pay period by coverage tier, deductible, out-of-pocket maximum, primary care visit, specialist visit,
prescriptions, network, and any account (HSA or FSA) the plan pairs with. Then the three use levels: routine care only, one
planned event, and a major event. Figures come only from the documents, with the page.

## 4. Show what changed

If a plan existed last year, one line per changed figure, old and new, so nobody is surprised.

## 5. Add the how, not the which

How to enroll, the window's dates (counted with `skills/count-a-life-event-window/SKILL.md`), what happens if nobody chooses
(the default in the plan rules), and who answers personal questions (the broker contact). No "best for" labels; people's
situations differ.

## 6. Hand over

Attach it to the task and share it with the intended audience within the requested work and your Tools. Messages to
outsiders stay drafts until a person turns mail sending on in Tico.
