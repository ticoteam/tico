---
name: review-a-contract
description: Review a contract clause by clause against the company's playbook and produce a status, a ranked issues list and exact redlines. Use when a task or message says review this contract, review this agreement, check this NDA, redline, mark up, MSA, master services agreement, SaaS agreement, vendor agreement, order form, terms of service to accept, statement of work, playbook, fallback position, is this OK to sign, or what should we push back on. Covers NDAs, services and SaaS agreements on either side (buying or selling), and vendor paper. Works from knowledge/playbook.md and the team's standard NDA; when the team has none, it uses the default positions in references/default-playbook.md and labels every result as a default. Finds missing clauses, counts notice dates, compares each returned version in full, and sends playbook-only redlines. Signing or accepting always needs an OK. Not for data processing agreements (use review-a-dpa-against-the-position) or for drafting a new contract from nothing.
metadata:
  function: legal
  roles: [contracts-manager, paralegal, general-counsel, legal-owner, procurement-manager, vendor-manager, account-manager, inbound-sales-owner, outbound-sales-owner]
  actions: [read, draft, send]
  review: professional
---
# Review a contract

When to use: a contract, an NDA or a set of online terms needs a decision before someone signs, accepts or sends it
back. Also when the other side returns a markup.

Words used here: **the company** is the legal entity that will sign. **The playbook** is `knowledge/playbook.md`: the
team's written position on each clause, as preferred, fallback and walk-away. **The signer** is the person allowed to
bind the company, named in `knowledge/playbook.md`.

## What great looks like

- **It compares; it does not opine.** Every finding is a difference between the text and a written position: this
  clause, this section number, these words, against this playbook line. A reviewer who writes "this looks standard"
  has done nothing.
- **It reads everything.** Definitions, exhibits, order forms, and terms pulled in by a link. The risk is usually in
  a definition or an exhibit, not in the clause with the alarming heading.
- **It finds what is not there.** A missing cap on liability or a missing right to leave costs more than a bad clause.
- **It asks for little and knows what it will trade.** A good markup has few changes, each with a reason the other
  side can accept and a fallback already decided. Fifty changes get a slow, hostile answer.
- **It makes the smallest change that fixes the problem.** A word before a phrase, a phrase before a sentence, a
  sentence before a clause.
- **It ends in a decision.** Ready to sign, needs changes, needs a decision, or needs counsel, with the person and the
  date for each open point.
- **It never lets the signed version drift from the reviewed version.**

## First time at a company

Read `knowledge/playbook.md`. If it exists and names a signer, skip this. If not, ask the owner these in one numbered
message and save the answers at the top of `knowledge/playbook.md`, dated. Work on the defaults until they arrive.

1. Does the team have written positions, a standard NDA or its own contract templates? Default: none. The bot copies
   `references/default-playbook.md` into `knowledge/playbook.md` under the heading "Default positions, not yet
   adopted" and reviews against it, labelling every result as a default.
2. Who signs contracts, and who decides when a clause is outside the playbook? Default: the owner, for both.
3. What is the company's legal name, its home state for governing law, and the insurance it carries? Default: read
   `knowledge/company.md`; anything missing is marked "not known" in the review.
4. Which lawyer do we use for contracts? Default: none named. Points that need new wording are marked "needs
   counsel" and the signer decides whether to hire one.
5. May the bot send a markup to the other side on its own? Default: yes, when every change is taken from the
   playbook's text, every red row is answered by a change, and the task's owner is copied. Otherwise it asks.
6. Below what contract value is a quick check enough? Default: no shortcut by value. Online terms that cannot be
   negotiated get the red-flag check in step 2 instead of a full markup.

## Steps

1. **Get the deal, not just the document.** Read the task (`tico task show <id>`) and its thread for: the other
   party's legal name, what is bought or sold, the price and term, who asked, and the date they need it. Search
   earlier dealings with `tico doc search` and `tico email search`. List every document that makes up the contract
   (main agreement, order form, statement of work, exhibits, linked terms). A document that is referenced and not
   supplied is named at the top of the review and its subjects are marked "not read". For terms pulled in by a link,
   save a dated copy and note that the other side can change them.
2. **Name the type, the side and the paper.**
   - Type: NDA; services or SaaS agreement; statement of work or order form under an existing agreement; online terms.
   - Side: the company is buying, or selling. For an NDA: mutual, or one-way, and from whom to whom. Compare that with
     who will actually share information.
   - Paper: the company's own template (compare it with the template and review only what changed), or the other
     side's.
   - Online terms that cannot be negotiated: do steps 4, 6 and 7, then report "accept", "accept with these risks
     noted" or "do not accept", with the alternatives.
3. **Load the positions, with their dates.** `knowledge/playbook.md` for contracts. For NDAs also
   `knowledge/standard-nda.md` and `knowledge/variations.md`. If the playbook is the unadopted default, say so in the
   first line of the review.
4. **Read the whole text once before judging any clause.** Definitions first. Note every defined term the key
   clauses rely on, every cross-reference, and the order of precedence between documents.
5. **Go through the clause list for the type**, in the playbook's order (the default order is in
   `references/default-playbook.md`). For each clause write one row:
   - the section number and the operative words, quoted;
   - the playbook line and its date;
   - the difference in one line;
   - the rating.

   | Rating | Meaning | NDA wording (kept from the team's NDA desk) |
   |---|---|---|
   | Green | Matches the preferred position | same as the standard |
   | Yellow | Matches the fallback or the variations list: acceptable, though the preferred position may still be asked for. Also: the playbook has no position on it. | inside the variations (quote the line), or "no team position" |
   | Red | Worse than the fallback: needs a change, and only the signer can accept it. If it is in the walk-away column or is an always-escalate term (step 7), the advice is not to sign as written. | outside |

   Never rate a clause green without a playbook line to cite. A clause the playbook does not cover is yellow, "no team
   position", and goes to the signer.
6. **Check for what is missing.** Use `references/clause-checklist.md` for the type. A missing clause gets a row like
   any other: "not found in the text", the playbook line, the rating.
7. **Check the always-escalate terms.** Each of these is red whatever else the contract says, unless the playbook
   expressly allows it: exclusivity; non-compete; non-solicit of staff or customers; most-favored-customer pricing;
   unlimited liability; an indemnity for the other side's own fault; liquidated damages or penalties; assignment of
   the company's existing intellectual property; a right to use the company's confidential data to train models or
   for the other side's own products; a personal guarantee; a change-of-control right against the company; residuals
   in an NDA; audit rights on the company's premises or systems; governing law or forum outside the United States;
   automatic renewal for more than one year at a time; a right for the other side to change the terms alone.
8. **Work out the dates.** Start, initial term, renewal mechanics, the last day to give notice
   (`count-a-notice-deadline`), price-change notice, and any cure periods. Put the notice date on the calendar
   (`tico calendar add`) once the contract is signed.
9. **Write the changes.** One unit per yellow or red row that needs a change:
   - the section number and the current words, quoted;
   - the replacement, as exact "strike" and "insert" text;
   - a one-sentence reason written for the other side (their interest, not the company's fear);
   - the fallback the company will accept, from the playbook, kept in the internal review only;
   - priority, owner and due date.

   Rules: make the smallest edit that works (a word, then a phrase, then a subclause, then a sentence). Replace a
   whole clause only when nothing smaller works, and say so. Insert only words taken from the playbook, the standard
   NDA or the company's template, and cite the line. Where those have no wording, write "needs drafting: counsel" and
   do not draft it. For a clause missing from the other side's paper, state the point in one plain sentence from the
   playbook and ask them to draft it.
10. **Rank and trim.** Three tiers. Tier 1: must change (red). Tier 2: should change (yellow with a fallback). Tier 3:
    nice to have; list them as the first things to give up. If the markup has more than about ten changes, cut tier 3
    from what is sent and keep it in the internal review.
11. **Give the status**, and check it against the rows.
    - **Ready to sign:** every row is green, or yellow inside a fallback.
    - **Needs changes:** each row that is outside has a fix in the playbook.
    - **Needs a decision:** a "no team position" row, or a red row where the signer must choose to accept, trade or
      walk away.
    - **Needs counsel:** a row needs wording nobody has, or a term in the hand-to-counsel list is present.

    The number of changes in the header must equal the number of rows marked for change.
12. **Verify every quote and number** against the text under review with `check-quotes-against-the-current-text`.
13. **Publish and act.** Publish the review page (see Output). Then:
    - Ready to sign: ask the signer for the OK, bound to the exact version
      (`tico task ask <id> --to <signer> --kind ok "OK to sign <document> v<N> with <party>?" --show page:<id> --version N`).
    - Needs changes, every change taken from the playbook and every red row answered by a change: send the markup
      and a short cover note to the other side (`tico email draft`, then `tico email send`), copying the task's
      owner. The note says the markup is for discussion and that nothing binds either side until both have signed.
      Asking for the company's written position commits it to nothing; giving one up does, so that needs an OK.
    - Needs a decision or counsel: ask the signer one question per decision, each with the options and the bot's
      pick. For counsel, use `write-a-counsel-brief`.
14. **When a version comes back,** compare the whole returned text with the last version sent, word for word. Do not
    rely on the other side's tracked changes to show everything that moved. Review only what changed, update the
    rows, and repeat from step 9. Keep a version table on the page: version, date, who sent it, what changed.
15. **Before signature, compare once more.** The signing copy must equal the reviewed version, including exhibits
    and the party names. Then hand the packet to the signer. After signature, file it with
    `file-a-signed-agreement-in-the-index`, add the dates from step 8, and record any deviation the signer accepted
    as a dated line under "Accepted once" in `knowledge/playbook.md`. An accepted deviation is not a new position
    until the signer says it is.

## Gates

- **No signature, click-to-accept or "agreed" without the signer's OK** on that exact version. A bot never signs.
- **No green without a cited playbook line.** A review on the unadopted default says "default, not yet adopted" in
  its first line and beside its status.
- **No invented wording.** Inserts come from the playbook, the standard NDA or the company's template. Anything else
  is "needs drafting: counsel".
- **Differences, not opinions.** Do not write "standard", "fair", "safe", "legal", "enforceable" or "market" as a
  finding. Say what the text says and how it differs.
- **Every row has a section number or "not found in the text".**
- **Nothing unread is implied to be read.** Missing exhibits and linked terms are named at the top.
- **The contract stays private.** It lives on the task and in the bot's repository. Do not paste it into outside
  services or public threads.
- **Party names are never guessed.** The legal entity names come from the other side or a public register, with the
  source noted.

Hand to counsel, with the review attached: a contract the signer calls strategic; anything with a government body;
employment, contractor-status and equity terms; a merger, investment, loan, lease of premises or settlement; any
always-escalate term the signer wants to accept; a dispute already running with the other party; any clause where the
question is whether it would be enforced.

Where law varies by place: whether a clause is enforceable (non-competes, limits on liability, indemnities, automatic
renewal, penalty clauses, jury waivers) depends on the state or country and sometimes on the industry. This skill
compares text with the company's positions; it does not say whether a court would enforce either. Name the governing
law in the review and flag the clause for counsel where enforceability is the question.

## Autonomy

The bot does on its own: read everything; build the review; write the redlines; copy the default playbook into place
when there is none; calendar dates; send a playbook-only markup to the other side; move from the preferred position
to the playbook's fallback in a later round; compare returned versions; prepare the signature packet; file the signed
copy.

The bot asks for an OK only for:

- Signing or accepting anything, including online terms and renewals.
- Sending a markup that concedes below the fallback, contains new wording, or leaves a red row unaddressed.
- Accepting a red or "no team position" term.
- Engaging a lawyer.
- Changing a position in the playbook.

## Output

One review page, published with
`tico page publish reports/contracts/<party>-<document>.html --title "<Party>: <document> review" --task <id>`:

- **Header:** the status in one line; the three points that matter most; the documents read and not read; the
  playbook used with its date (or "default, not yet adopted").
- **Deal strip:** parties, what, price, term, renewal, last day for notice.
- **Clause map:** one row per clause, colored green, yellow or red: section, what it says (quoted), the position,
  the difference, the proposed change. Missing clauses appear as rows.
- **Changes to send:** numbered strike and insert text with the reason for the other side.
- **Internal only:** fallbacks, what to give up first, decisions needed with options and the bot's pick.
- **Versions:** the table from step 14.
- Last line: "Summary for a human, not legal advice."

Also: the markup itself in the format the other side sent (a tracked-changes file where the Tools allow, otherwise a
numbered change list), and the cover note. For an NDA, also write `reports/ndas/<party>.md` so the NDA desk report
keeps working.

## Checks before you finish

- The first line states the status, and the count of changes equals the rows marked for change.
- Every row cites a section number (or "not found in the text") and a playbook line with its date.
- No clause is green without a position, and no insert is new wording.
- Every document that forms the contract is listed as read or not read.
- The missing-clause list for the type was run and each item has a row.
- Each always-escalate term was searched for by meaning, not only by heading.
- Every quote, number and date was checked against the text under review.
- The notice deadline shows its arithmetic.
- Each open point has an owner and a date.
- Nothing was signed, accepted or agreed without an OK on the exact version.

## Example

Invented, for illustration.

Task: "Review Parcelry's SaaS agreement before Friday. $18,000 a year, shipping labels." The company is buying. Paper:
Parcelry's. Documents: Master Subscription Agreement v4.2 (read), Order Form (read), "Security Policy at
parcelry.example/security" (linked; saved 2026-11-18; can be changed by Parcelry). Playbook: default, not yet adopted.

Status: **Needs changes (4) and one decision.** Default positions, not yet adopted.

| Clause | Section | Says | Position (default, buying) | Rating | Change |
|---|---|---|---|---|---|
| 1. Liability cap | 9.2 | "fees paid in the three (3) months preceding the claim" | B1: 12 months' fees | Red | Strike "three (3)", insert "twelve (12)" |
| 2. Renewal notice | 3.1 | renews for 12 months unless notice "at least ninety (90) days" before | B7: 30 days, fallback 60 | Red | Strike "ninety (90)", insert "thirty (30)" |
| 3. Use of data | 6.4 | may use Customer Data "to improve and train its services" | always-escalate term | Red | Strike "and train" |
| 4. Data export on exit | none | not found in the text | B7: 30 days to export | Red | Ask Parcelry to add "30 days after the end to export data" |
| Price on renewal | 3.3 | Parcelry "may adjust fees on renewal" | B12: 60 days' notice and a stated cap; the playbook states no cap figure | Red | Decision: name a cap, or accept |
| Confidentiality, IP, warranties, governing law (Delaware) | 5, 7, 8, 12 | as quoted on the page | match B5, B3, B6, B8 fallback | Green or yellow inside fallback | none |

Dates: start 2026-12-01; first renewal 2027-12-01; under 3.1 as written, the last day for notice is 2027-09-02
(90 days before; arithmetic on the page).

Asked the signer first, because one red row has no change: "3.3 lets Parcelry raise the price at renewal with no cap.
Accept it for year one and set a reminder to renegotiate at month nine, or tell me the cap to ask for? My pick:
accept." Answer the same day: accept, recorded under "Accepted once".

Then sent to Parcelry with the task owner copied: changes 1 to 4, each with a one-line reason ("A twelve-month cap
matches the annual term and applies to both parties"). Internal note: on change 2 the fallback is 60 days; change 4 is
the first to give up, and giving it up needs the signer's OK.

Summary for a human, not legal advice.
