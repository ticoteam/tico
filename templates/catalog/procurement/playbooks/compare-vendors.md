# Compare vendors

Triggered by a purchase-request task, and used by `playbooks/weekly-purchase-digest.md`. Budget 40 minutes for three to
five vendors. Follows the method in `knowledge/purchasing-practice.md`: must-haves first, weighted scores, whole-term
cost, then security and references.

Inputs: the request task, `knowledge/criteria.md` (the locked must-haves and weights), `knowledge/vendors.md`, any quote
attached to the task, and public vendor pages.

Outcome: `reports/R-<id>-comparison.md` in the shape of `knowledge/examples/vendor-comparison.md`, with a draft email for
the questions still open. The suggestion is labelled as one; a human decides.

Done when every shortlisted vendor has a must-have result with its page and date, every score has its basis, every
price is priced over the whole term, every security line is marked claimed, evidenced or missing, the report is
published, and the task note carries the suggestion.

---

## 1. Read the request and what the team has

    hub task show <id>

Restate the need in one line: the job, for whom, how many seats or units, by when. Check `knowledge/vendors.md` for a tool
the team already owns that does the job. If one exists, compare it too. Ask the requester one question with
`hub task ask <id>` only if the need is unclear.

## 2. Shortlist three to five

From the request, public sources and attached quotes, choose the realistic options. Drop any vendor on the avoid list
and say why. More than five buries the decision.

## 3. Apply the must-haves

For each vendor, pass or fail each must-have, with the page and date. A failure drops the vendor here, with the evidence.
"Not stated" is a gap to ask about, not a pass.

## 4. Score and price the term

Score 1 to 5 on each criterion in `knowledge/criteria.md`, using the rubric: 1 does not meet the need, 3 meets it, 5 clearly
exceeds it. Weighted total is the score times the weight, summed. Score each vendor against the same weights; a weight that
changed since the request started is noted, not applied silently.

Price the whole term with `skills/price-the-whole-term/SKILL.md`.

## 5. Check security and claims

For any vendor that will hold team or customer data, put the standing questions in `knowledge/security-questions.md` to
it, and read each answer with `skills/read-a-vendor-security-answer/SKILL.md`. The starter list, used until the team
edits that file: data location, encryption at rest and in transit, single sign-on, access logs, breach notification
time, sub-processors, data export and deletion, an independent audit report. A vendor's trust page or certificate is
"claimed" until its report or certificate has been read, is current and covers the product. Reviews are a signal, with
their date, not proof.

## 6. Write it

Write `reports/R-<id>-comparison.md`: headline with the suggestion, the score and the decide-by date; must-haves; weighted
scores; the whole-term cost; claims and their sources; checks before deciding (two references from each finalist, a
sample export, prices beyond the quote); the draft email; what could not be read.

Where a contract's terms are the question, the request goes to `hub task create --owner legal-review`. It is not decided
here.

## 7. Finish

    hub file publish reports/R-<id>-comparison.md
    hub task update <id> --status done --note "..."

Attach the draft email to the task. The note holds the suggestion, the decide-by date and what could not be read. A page
that failed to load makes its score provisional, and the report says so. The human contacts vendors and decides.
