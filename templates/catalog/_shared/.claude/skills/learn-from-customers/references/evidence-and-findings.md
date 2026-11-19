# Formats: snapshot, evidence log, findings, read log, findings page

Use it in steps 8, 10, 14 and 15. All examples are invented.

The chain that must never break:

    finding (F-id)  ->  evidence lines (E-ids)  ->  source record (call, thread, task or ticket id)

A reader starts at a finding and can reach the customer's own words in two steps. If a link is missing, the claim is not made.

If the team already keeps one of these files under another name (for example `knowledge/opportunities.md`, `knowledge/themes.md` or `knowledge/snapshots/`), keep using that file and add the missing fields to it. Never run two logs.

## Interview snapshot

`knowledge/customers/snapshots/YYYY-MM-DD-<account id>.md`. One per interview, written the same day, under one page.

    # Account 4417 · solo electrician · 2026-11-14
    Source: call M-1242 (consent on the recording at 00:20). Group: did it recently. Interviewer: UX Researcher.
    Question it served: what happens between finishing a job and sending its invoice?

    ## Their story, in order
    1. Finishes two to four jobs a day; marks each done in the van.
    2. Does invoices "on Sunday night, if I remember".
    3. Last week: four jobs on Monday, invoiced the following Saturday.

    ## What they did (strongest evidence first)
    - Keeps a paper list of jobs to invoice on the dashboard of the van.
    - Chased one customer by phone who "couldn't remember what the job was".

    ## Exact quotes
    - "I do the invoices on Sunday night, if I remember" (12:40)
    - "By Saturday I can't always remember what parts I used" (14:05)

    ## Surprises
    - Does not want invoices sent without looking: "I've sent a wrong one before and it cost me the customer."

    ## Not said
    - Nothing about payment terms or late payers.

    Evidence lines added: E-0388, E-0389, E-0390.

## Evidence log

`knowledge/customers/evidence/YYYY-MM.md`, one file per month the evidence was recorded. One line per distinct point. Ids run on across months and are never reused.

    id | date said | source | account | segment | kind | quote | context | theme

    E-0388 | 2026-11-03 | call M-1207 | 4417 | solo electrician | did, unprompted | "I do the invoices on Sunday night, if I remember" | invoices 4-6 days after the job | invoices-late
    E-0391 | 2026-10-22 | thread 18f2c9 | 3620 | solo gardener | said, unprompted | "I like doing them all in one go on a Friday" | prefers batching | invoices-late (against)
    E-0398 | 2026-10-30 | ticket T-2204 | 3310 | painting firm, 4 people | asked, unprompted | "Can we have our own colours and layout on invoices?" | request | invoice-look

Fields:

- **source**: the id of the record, so anyone can open it. Never a name.
- **kind**, first word: **did** (they describe something that happened), **said** (an opinion or preference), **asked** (a request). Second word: **unprompted** (they raised it) or **prompted** (it answered our question).
- **quote**: copied exactly, in quotation marks. Cut only with "..." and never so that the meaning changes. If the record is not in English, keep the original and add a translation marked as yours.
- **context**: a few words, yours, saying what the quote is about. This is the only place your words appear.
- **theme**: the theme slug; add "(against)" when the line speaks against the theme's finding.
- Add `secondhand` to **kind** when a teammate reported what a customer said.

Strength, when choosing which lines to lead with: did before said before asked; unprompted before prompted; firsthand before secondhand.

## Findings

`knowledge/customers/findings.md`. Updated in place. Newest check date first.

    ## F-03 · Solo tradespeople send invoices days after finishing the job, because invoicing happens at home, not on site.
    - Confidence: pattern. Checked: 2026-11-10. First written: 2026-10-06 (emerging).
    - Counts: 17 distinct accounts; 14 solo, 3 two-person firms; tickets and calls.
    - Share: solo trades are 71% of accounts and 82% of this theme. Top three accounts: 24% of lines.
    - Evidence: E-0388, E-0402, E-0405, E-0411 (and 19 more under theme invoices-late).
    - Quotes: "I do the invoices on Sunday night, if I remember" (E-0388). "By the time I bill them they've forgotten what I did" (E-0402).
    - Against: 2 accounts prefer to batch weekly (E-0391, E-0420).
    - Does not show: that they would send from the site if they could.
    - Behaviour check: median 3.2 days from done to sent, 1,904 jobs (query invoice-lag, 2026-11-12). Agrees.
    - Used by: task P-118 (spec, 2026-11-18).

Rules:

- The statement is one sentence that could turn out false. "Customers want better invoicing" cannot be false and is not a finding.
- Confidence: **anecdote** (1 account), **emerging** (2), **pattern** (3 or more distinct accounts from 2 or more kinds of source). A finding moves down as well as up.
- A finding not re-checked in 180 days is marked "stale" until someone reads recent evidence for it.
- A finding that turned out wrong stays, marked "withdrawn" with the date and the reason.

## Read log

`knowledge/customers/read-log.md`. One block per run, newest first.

    ## 2026-11-10 · question: what happens between finishing a job and sending its invoice?
    - Support mailbox, 2026-08-10 to 2026-11-10: 412 threads, all read. 61 bore on the question.
    - Calls matching "invoice", since 2026-08-10: 23, all read. 14 bore on the question.
    - Tasks matching "invoice": 31, all read. 9 bore on the question.
    - Not read: app-store reviews (none collected). Private meetings (not open to me).
    - Evidence lines added: E-0371 to E-0424.

When a pile was sampled, say how: "1,860 threads; read all 240 from the last 30 days and every 10th of the rest (162): 22% read."

## Findings page

One HTML page, published on the task. Top first, proof below. No names, no contact details.

1. **Top, three lines, at most 70 words.** The decision. The answer in one sentence with its confidence. What to do with it ("enough to spec"; "interview five more first").
2. **A count strip.** Records read; share of the pile; distinct accounts with evidence; findings by confidence.
3. **One block per finding**, strongest first:
   - the statement, with its confidence as a word (pattern, emerging, anecdote);
   - a small bar or table: accounts by segment beside each segment's share of customers;
   - two or three quotes, each with its evidence id, segment and kind;
   - **Against** and **Does not show**, one line each;
   - the behaviour check with its query and date.
4. **What was read and what was not** (from the read log).
5. **Open questions** that the evidence could not answer, and the cheapest way to answer each.

Use a real screenshot only where it shows where customers get stuck; follow the page rules in `.claude/skills/build-the-page/SKILL.md` for colour, size and no scripts. A table of counts beats a paragraph every time.
