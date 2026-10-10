# Summarise a contract

Triggered by a task that attaches a contract. Budget 30 minutes for a vendor agreement, 10 for an NDA. The
outcome is one summary a human can read in five minutes. It is a summary for a human, not legal advice,
and it says so. Nothing is sent, signed or changed.

---

## 1. Read everything

    hub task show <id>

Read every page: the body, schedules, order forms, terms linked by name. List each document the contract
refers to that you were not given; that list goes at the top of the summary. Identify the kind (vendor,
customer, partner, employment, NDA, lease) and open `knowledge/checklists/<kind>.md`.

## 2. Read the five that carry the risk first

1. **Limitation of liability**: is the cap mutual, how is it calculated, what sits outside it (data breach,
   IP infringement, confidentiality)?
2. **Indemnity**: who gives it, what triggers it ("arising out of" reaches further than "caused by"), whether it
   covers the other side's negligence.
3. **Term and renewal**: length, auto-renewal, the notice window.
4. **Termination**: for convenience and for cause, cure periods, exit fees, what happens to data.
5. **IP and data**: who owns what is made, licences that survive, personal data terms.
Then payment terms, confidentiality, warranties, insurance, governing law and forum, assignment, and the rest.

## 3. Fill the key terms table

Parties, effective date, term, renewal and its notice window, notice deadline (written as end of term minus
notice period, with the arithmetic), termination, liability cap and exclusions, indemnity, IP, confidentiality,
governing law and forum, payment terms and late fees. Each row: the section number and, for short text, the
words in quotation marks. A term you cannot find is "not found in the text", which never means "not there".

## 4. Compare with the playbook

For each clause in `knowledge/playbook.md`, write: the clause, what the team prefers, what this contract
says, and the difference. Order flags by how much they matter. A clause the playbook does not cover is
listed as "no team position" and never judged. Never write "standard", "fair", "safe", "legal" or
"enforceable".

Then write the issues list for the human who negotiates: one line per flag, worst first, with the fallback
the playbook allows ("accept 6 months' fees if 12 is refused"). A flag with no playbook fallback says "no
team position; ask counsel". Never invent a fallback.

## 5. Write the summary

Five lines first: what it is, how long it binds, how it ends, the top three flags, the nearest deadline. Then the table, then the flags,
then what you could not read, then the closing line in bold: **This is a summary for a human, not legal
advice. Have counsel review anything that matters before you sign or rely on it.** Save it as
`reports/summaries/<counterparty>-<kind>.md` in the shape of `knowledge/examples/contract-summary.md`.

    hub file publish reports/summaries/<counterparty>-<kind>.md

## 6. Finish

Add the contract to `knowledge/contracts.md`. `hub task update <id> --status done --note`: the five lines,
the path, and any deadline inside 14 days first. Reply to the counterparty within the requested work and your Tools when a person has turned mail sending on in Tico; otherwise keep the draft on the task.

## When a source fails

A scanned page you cannot read, or a missing schedule, is named at the top and its clauses marked
"not read". Never summarise a page you did not read.
