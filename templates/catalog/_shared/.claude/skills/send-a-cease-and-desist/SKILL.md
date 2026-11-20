---
name: send-a-cease-and-desist
description: Decide whether a cease-and-desist letter is the right tool, then draft the strongest one and send it once the named approver OKs it. Covers trademark, copyright, defamation, contract breach and non-solicit. Use when a task says cease and desist, C&D, stop letter, infringement, knock-off, copied our content, someone is using our name or logo, false statements about us, breach of contract notice, notice to cure, or a former contractor or partner is soliciting our customers or staff. Checks the right, the dated conduct and the other side before drafting, weighs the risk that the recipient sues first (declaratory judgment), and blocks the letter when a required fact is missing. Not for payment reminders, for replying to a letter the team received (use answer-a-demand-or-complaint), or for patents (always a lawyer).
metadata:
  function: legal
  roles: [general-counsel, legal-owner, ip-paralegal, paralegal, contracts-manager, reputation-manager]
  actions: [read, draft, send]
  review: professional
---
# Send a cease-and-desist

When to use: someone outside the team is doing something the team has a right to stop, and a task asks for a letter or
asks what to do about it.

Words used here: **the company** is the legal entity that owns the right and signs the letter. **The team** is everyone
in Tico. **The approver** and **the signer** are the people named in `knowledge/legal-disputes.md`.

## What great looks like

The recipient hands the letter to a lawyer, and the lawyer says "comply". That happens when the letter names one right
the company can prove, conduct with dates and exhibits, a short list of demands the recipient can actually carry out, a
real deadline, and a consequence the company will follow through on. Nothing in it is false, inflated or quotable against
the company later.

The second half of the craft is deciding not to send. A letter is one tool among five. It tells the other side the
company expects a dispute, it can be published, and it can let the recipient sue first in a court they choose. An expert
weighs that before writing a word, and collects the evidence first, because the letter is what makes evidence vanish.

## First time at a company

Read `knowledge/legal-disputes.md`. If it has a "Sending letters" section, skip this. If not, ask the owner these in one
numbered message, then save the answers there with the date. Work on the defaults until the answers arrive.

1. Who signs letters for the company, with what title, and who approves them before they go? Default: the owner signs
   and approves.
2. Which lawyer or firm do we use when a matter is too big for a letter from us? Default: none named. The bot writes
   the counsel brief and the owner picks.
3. What is the company's exact legal name, state of formation and notice address? Default: read `knowledge/company.md`
   and the newest signed contract; if they disagree, ask.
4. If a recipient ignores the letter, is the company prepared to file suit or arbitration? Default: not decided, so
   every letter says the company "reserves all rights and remedies" and never says "will file suit".
5. Who must never get a letter without the owner's say first? Default: customers, investors, partners, current
   teammates, journalists, and anyone the company is negotiating with.
6. Where is the list of what we own (marks, registrations, copyrights, signed agreements)? Default:
   `knowledge/register.md` if `ip-paralegal` keeps it, else build `knowledge/register.md` from the public registers and
   the signed-agreements index, and mark every row "not yet confirmed by the owner".

## Steps

1. **Open the matter.** Add a row to `knowledge/matters.md` (id, date, other party, kind, amount at stake if known,
   status). Keep the work on one private task with the approver as a reader. Read the task with `tico task show <id>`.
   Search for history with the other party: `tico doc search "<name>"`, `tico email search <address> "<name>"`,
   `tico meeting search "<name>"`. An earlier licence, a consent, or a year of silence after the team first noticed
   changes everything; write down what you find.
2. **Preserve first.** Capture the conduct before anyone is warned: full-page screenshots with the URL and time, the
   page source or an archive copy, the listing, the ad, the post, a test purchase for a counterfeit. Save under
   `reports/matters/<id>/evidence/` with a one-line log entry per item: what, where, when captured, by whom. Then
   record a legal-hold decision for the company's own records in `knowledge/legal-holds.md`: a company that sends a
   C&D expects a dispute, so its own emails and files on the subject must be kept. (The hold steps are in
   `defend-a-consumer-claim`, `references/legal-hold.md`. Without that skill: tell the people involved in writing to
   delete nothing on the subject and pause any auto-delete that covers it.)
3. **Name the claim and fill its facts.** Pick one main type: trademark, copyright, defamation, contract breach or
   non-solicit. Open `references/elements-by-claim.md` and fill every required fact for that type from the records,
   each with its source. A required fact you cannot source is a blocker. Do not draft around it and do not use a
   placeholder. Get the fact, or stop and say which one is missing.
4. **Verify the right at its source.** Check the registration on the public register today: status live (for a
   trademark, no missed renewal: `check-a-trademark-deadline`), owner of record is the company's exact legal name,
   and the classes or the registered work cover what is being copied. For a contract, quote the signed version with
   `check-quotes-against-the-current-text`. For work made by a contractor or founder, check
   `verify-an-ip-assignment`. If the owner of record is a founder personally or an old entity, the wrong party is
   about to sign: stop and tell the approver.
5. **Look hard at the other side.** Write a short diligence note: exact legal entity and address (state business
   register), who is behind it, rough size, whether it has a lawyer, its own registrations and first-use date (search
   the registers as in `run-a-clearance-search`), any relationship with the team, and whether it has a history of
   fighting or publishing letters. Then rate two risks in words, with the reason:
   - **Sue-first risk.** Could this recipient afford to file for a declaratory judgment in its home court? High if it
     is funded, has counsel, has an arguable right of its own, or the conduct is central to its business.
   - **Publicity risk.** Would the letter look heavy-handed if posted? High for a customer, a small creator, a critic,
     a journalist or a nonprofit.
6. **Choose the tool.** Compare these and recommend one, with the reason:
   - A platform report (marketplace brand tools, a host's or social network's infringement form, a DMCA notice). Often
     faster than a letter for copies and counterfeits. Can run alongside a letter.
   - A plain, friendly note or call. Right for an innocent small user or a business relationship worth keeping.
   - An offer to license or coexist. Lowers sue-first risk; needs the owner's OK since it gives something away.
   - A cease-and-desist letter. Right when the right is clear, the conduct is documented and the demand is modest.
   - Counsel files first, or counsel sends the letter. Right when sue-first risk is high or the stakes are large.
7. **Set the demands, the deadline and the consequence.**
   - Demands are numbered, specific and proportionate: stop a named act, remove named items, confirm in writing. Ask
     for an accounting, destruction, a transfer or money only when the facts support it and the approver wants it.
   - Deadline: 10 to 14 calendar days is the usual range; 5 to 7 when harm is ongoing and the fix is easy. For a
     contract, use the cure period the contract gives, counted with `count-a-notice-deadline`.
   - Consequence: say only what the company has decided it will do (question 4). A threat that is not carried out
     teaches the recipient to ignore the next letter.
8. **Draft the letter** from `references/letter-template.md`, in the house tone: formal, firm, exact, unemotional.
   Numbered paragraphs. Dates, quotes and exhibit numbers instead of adjectives. No statement of law the bot has not
   read at the source today. Close with a full reservation of rights. Admit nothing: say nothing about weaknesses,
   past tolerance, or what the company "might accept".
9. **Read it as the recipient's lawyer.** Look for: a fact that is wrong or unproven, a right described more broadly
   than the register shows, a demand larger than the claim supports, a sentence that could be quoted on social media,
   anything that sounds like a threat outside the dispute, and any hint about how the company would settle. Fix each.
10. **Pass the gate.** Build the matter page (see Output). Do not ask for a separate OK: the approver OKs the letter
    once, on the email in step 11, which shows it inline. That OK covers that exact letter and those exact recipients.
    Any change needs a new OK.
11. **Send and prove delivery.** Send as the signer by email from the company's address (`tico email draft`, then
    `tico email send <mailbox> --draft <draft-id> --task <matter task id>`, so the OK is asked on the matter) and by a
    tracked postal method to the notice address; a contract's notice clause decides the
    method when there is one. If the team has no letter Tool, file a sub-task for a person to post it. Save the sent
    copy and the proof of delivery in the matter folder. Add the deadline to the calendar with
    `tico calendar add --title "<matter>: their deadline" --start <YYYY-MM-DD> --end <the next day, YYYY-MM-DD> --all-day`.
12. **Follow through.** A reply goes to `answer-a-demand-or-complaint`. Compliance: capture the changed pages, thank
    them in one line, close the matter. Silence at the deadline: check whether the conduct stopped anyway, then give
    the approver the next step that was decided in step 7, with the counsel brief (`write-a-counsel-brief`) if that
    step is a lawyer. Do not send a second letter that only repeats the first.

## Gates

Nothing is sent unless every line is true. Write the answers on the matter page.

1. **The right is real and ours.** Verified at the source today; the owner of record is the company.
2. **The claim holds up.** Every required fact in `references/elements-by-claim.md` is filled and sourced, and the
   known defenses for that type were considered in writing.
3. **The demand is proportionate.** Each demand follows from the claim. Nothing is demanded that a court could not
   order.
4. **The other side was checked.** Diligence note written; sue-first and publicity risk rated.
5. **A named person approved this exact letter.** The OK is on the email, asked on the matter task.

Hard stops. Any one of these means no letter from the bot; write the counsel brief and tell the approver:

- A patent, or a trade secret taken by a former teammate.
- A non-compete or a staff non-solicit against a person who works in California, or in any state the bot has not
  checked this month. In California, trying to enforce a void restraint is itself unlawful (Business and Professions
  Code sections 16600, 16600.1 and 16600.5).
- A letter addressed to the other side's new employer, customers, platform or investors that accuses them of
  wrongdoing. That is how a sender becomes a defendant.
- Defamation where the statement is an opinion, is substantially true, or is a customer's review of their own
  experience. Do not send. Offer a public reply or a correction request instead (see the elements file).
- Sue-first risk rated high.
- The recipient is outside the United States, or is a government body.
- The team first learned of the conduct more than a year ago and did nothing.

Never, in any letter:

- Threaten to report someone to the police, a regulator, a tax or immigration authority, or to "go public", in order
  to get what the letter demands. Depending on the state, that can be extortion.
- Cite a statute, a case or a registration the bot has not read at the source in this run.
- Sign as, or imply the letter is from, an attorney or a law firm. The letter is from the company, signed by the
  signer with their real title. If a licensed lawyer will sign, the lawyer rewrites and sends it.
- Write to a person the team knows has a lawyer on this matter. Write to the lawyer.
- Copy anyone outside the two parties, or post the letter or the dispute publicly.
- State as fact anything about the recipient that the evidence folder does not prove.

Where law varies by place: all of the above is the United States default. Trademark and copyright are mostly federal.
Defamation, contract, non-solicit, anti-SLAPP and extortion rules are state law. Name the state whose law applies on the
matter page and mark each state rule "checked <date>, <source>" or "not checked".

## Autonomy

The bot does on its own: open the matter, capture and log evidence, record the hold decision, verify the right, write
the diligence note, choose and recommend the tool, draft the letter, build the page, calendar the dates, track the
reply, and write the counsel brief.

The bot asks for an OK only for:

- Sending the letter (always; it is a legal position taken in the company's name and it cannot be recalled).
- Any notice that is signed under penalty of perjury, such as a DMCA takedown notice. The signer makes that statement.
- Offering a licence, a coexistence deal, money, or any promise not to sue.
- Hiring or instructing a lawyer, or any spend outside the bot's budget.

An OK is not needed to decide against sending. Record the reason on the matter and close it.

## Output

- **The matter page** (`tico page publish reports/matters/<id>/matter.html --title "<Other party>: cease-and-desist" --task <id>`):
  one screen with the right (register excerpt), the conduct (evidence thumbnails with dates), a short timeline, the
  two risk ratings, the tool recommended and why, the gate checklist, and the letter exactly as it will be sent. Mark
  the page "Prepared for the approver. Not legal advice. Have counsel review anything that matters." That line never
  appears on the letter itself.
- **The letter**, as a PDF or document on company letterhead, plus numbered exhibits.
- **The matter row** in `knowledge/matters.md`, the hold decision in `knowledge/legal-holds.md`, and the deadline on
  the calendar.
- **A counsel brief** when a hard stop applies.

## Checks before you finish

- Every factual sentence in the letter points to an exhibit or a register entry that exists in the matter folder.
- The recipient's legal name and address match the state business register or the contract's notice clause.
- The right is described no more broadly than the register or the contract shows.
- No placeholder, bracket or "TBD" is left in the letter.
- The demands are numbered and each one can be checked as done or not done.
- The deadline is a calendar date, not "within 10 days", and it is on the calendar.
- The consequence matches the owner's answer to question 4.
- No threat outside the dispute, no admission, no settlement hint, no adjectives about the recipient's character.
- The letter has no internal label ("draft", "not legal advice", "privileged").
- The OK on the email is for the same letter that was sent, and proof of delivery is saved.

## Example

Invented, for illustration.

Task: "A shop called Brightloom Home is selling 'Lumora' candles. That's our name."

- Right: LUMORA, registered word mark, class 4 (candles), owner of record Lumora Goods LLC, status live, read on the
  public register 2026-11-18. First use 2021-03-02 per `knowledge/register.md`.
- Conduct: product listing at brightloomhome.example/lumora-candle, captured 2026-11-17 14:02 (exhibit 1); order
  confirmation for a test purchase dated 2026-11-17 (exhibit 2). Earliest archived copy of the listing: 2026-09-30.
- Other side: Brightloom Home LLC, two people, no registrations found, no lawyer known, no relationship with the team.
  Sue-first risk: low (small, no right of its own found). Publicity risk: medium (a small shop).
- Tool: a letter plus the marketplace's brand report for the same listing.
- Demands: (1) stop using LUMORA or any similar name on candles; (2) remove the listing and the two social posts in
  exhibit 3; (3) confirm both in writing. Deadline: 2026-12-03. Consequence: "Lumora Goods LLC reserves all rights
  and remedies" (the owner has not decided to sue).
- Gate: all five true. The approver OKs the letter on its email. The letter goes by email and tracked mail on
  2026-11-19, and 2026-12-03 is on the calendar.
