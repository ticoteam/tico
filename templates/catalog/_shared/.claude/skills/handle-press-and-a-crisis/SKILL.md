---
name: handle-press-and-a-crisis
description: Run the company's press work and its public response when something goes wrong. Press - decides whether news is a story, builds the kit (fact sheet, release, approved quotes, spokesperson briefing), picks a handful of reporters by their recent work, writes a short tailored pitch for each, sends and follows up once, handles replies and logs coverage. Crisis - sizes the event, keeps a time-stamped fact log, pauses scheduled marketing, writes the holding statement from confirmed facts only, says what to say and not say, brings in legal where there is exposure, publishes to affected people first and keeps the promised update times. Use when a task says press, PR, media, journalist, reporter, pitch, press release, announcement, embargo, exclusive, interview, media list, coverage, comment request, or crisis, incident statement, holding statement, outage statement, data breach statement, apology, backlash, bad press, viral complaint, "a reporter is asking" or "what do we say".
metadata:
  function: marketing
  roles: [pr-manager, reputation-manager, social-media-manager, escalations-manager, investor-relations-manager, general-counsel]
  actions: [read, draft, send]
  review: professional
---
# Handle press and a crisis

When to use: the company has news for the press, a reporter wrote in, or something has happened (or may come out)
that could hurt customers, people or the company's standing.

Words used here: **a pitch** is a short personal email offering one reporter one story. **A release** is the formal
written announcement. **An embargo** is a reporter's agreement, made before they get the details, not to publish
until a set time. **An exclusive** is a story offered to one outlet only. **A holding statement** is the first short
public statement in a crisis, made before everything is known. **The fact log** is the time-stamped list of what is
confirmed, unknown and rumour. **Counsel** is the company's lawyer: the legal owner in Tico, or an outside lawyer.

Tico words: **the owner** is the person who runs the team and gives OKs. `knowledge/<file>.md` is a file in your own
knowledge folder, kept between runs. **A task** is one piece of work with its own thread. **An ask** is a question put
to a person on a task, and **an OK** is their yes to the exact version shown (`tico task ask <task-id> --to <person>
--kind ok "<question>" --show page:<n> --version N`). **A page** is an HTML report published into Tico with `tico page publish
<file.html> --title "<title>"`. **A Tool** is a connection to an outside system (the site, mail, ads, analytics) that
this bot has been granted.

This skill has two parts. Part A is press work on an ordinary day. Part B is a crisis. In a crisis, go straight to
step 9.

A lawyer should check any crisis statement that touches personal data, injury, a legal claim, a regulator, a named
person, a cause or compensation. A public crisis statement never admits fault. This skill prepares the statement and
the brief. It does not replace counsel.

## What great looks like

- **Press:** five well-chosen reporters, each pitched with a reason that comes from their own recent work. Not a
  hundred sent the same email. The story is real news with proof and a person who will talk. Every reply is answered,
  every mention is logged, and no promise is made that the owner did not agree to.
- **Crisis:** the people affected hear first, from the company, within the hour of the facts being confirmed. The
  statement holds only confirmed facts, shows concern for the people affected, says what is being done and when the
  next update comes. The update arrives when promised. One voice. Nothing has to be taken back later. Counsel was in
  the loop before the first word wherever there was exposure.

## First time at a company

Read `knowledge/press.md` and `knowledge/rules.md`. If the setup answers are there, skip this. If not, ask the owner
these in one numbered message, save the answers in `knowledge/press.md` with the date, and start on the defaults.

1. Who may speak for the company, and who is the backup? Default: the owner only. With no backup, statements go out
   in writing and nobody gives interviews while the owner is away.
2. Who is our lawyer for urgent matters, and how are they reached out of hours? Default: the legal owner in Tico. If
   no outside lawyer is on record, I say so on every crisis draft.
3. What must not be said in public yet? Default: nothing non-public: customer and partner names, revenue, user
   counts, funding, and dates for future work.
4. What may I send to reporters without an OK? Default, once mail sending is on for this bot: tailored pitches and
   one follow-up for a story already approved in `knowledge/stories.md`, and answers to factual questions that
   public material already answers. Everything in "Needs an OK" below stays an ask.
5. Which events would be a crisis for us? Default: the list in `references/crisis-playbook.md`. I pre-draft holding
   statements for the three most likely and ask you to pre-approve them.
6. Where do customers hear from us first when something breaks? Default: the status page or a site banner, then
   email to the customers affected, then social accounts.

## Steps

### Part A: press

1. **Decide whether it is a story.** Four tests: it is new; it matters to someone outside the company; there is proof
   (data, a customer who agreed to talk, something to see); a named person will speak. Failing one, it is not press
   news: publish it on the company's own channels with `write-marketing-copy` and say why. Record real stories in
   `knowledge/stories.md` with the angle, the outlets that fit and the date press work starts: four weeks before a
   major launch, two weeks before smaller news.
2. **Build the kit** from `references/press-kit.md`: a one-page fact sheet where every fact has a source; a release
   only when the news warrants one (under 500 words, the news in the first paragraph); quotes that are a real
   person's recorded words, approved by that person; the proof; pictures; and a briefing for the spokesperson with
   the hard questions and honest answers. Save it under `reports/<announcement>/`.
3. **Pick the reporters.** Use `pick-reporters-for-a-story` when the team has it. Otherwise: search for who wrote
   about this subject in the last six months, and keep those whose beat fits and whose audience includes the
   company's buyers. For each, note two recent relevant pieces with dates in `knowledge/media-list.md`. Aim for
   about five. Store nothing personal beyond their public work address.
4. **Write one pitch per reporter.** Under 150 words. Subject under eight words, saying the news. First line: the
   reporter's own dated piece and why this follows from it. Then the news in two sentences, the proof, who can talk
   and when, one ask with a time. No attachments on a first pitch: link to the kit. Full rules are in Gates.
5. **Send, or queue for the OK.** Inside the limit from question 4, send each pitch as its own email. Outside it,
   put the pitches on a page and ask once. An embargo or an exclusive is offered only when the owner agreed to it,
   and details go only after the reporter accepts in writing.
6. **Follow up once.** After three working days with no reply, one short follow-up that adds something new (a
   number, a customer now available, a date). Then stop. Silence is an answer.
7. **Handle replies and interviews.** Answer a reporter the same day: they work to deadlines. Ask what they need and
   by when. Send the spokesperson the briefing before any call. Assume everything said or written to a reporter can
   be published. If a published piece has a factual error, send a polite correction with the evidence, once. An
   opinion you dislike is not an error.
8. **Log and read back.** Every mention goes in `knowledge/coverage.md`: date, outlet, reporter, angle, tone,
   whether it was pitched, the link. A month after an announcement, report what it did: pieces published, pitch
   replies out of pitches sent, visits and sign-ups referred (from the company's records). Update the media list
   with what each reporter responded to.

### Part B: crisis

9. **Size it and open the fact log.** Use the severity table in `references/crisis-playbook.md`. Open one task for
   the crisis and start the fact log on it: each line has the time, the fact, who confirmed it and from what
   source. Keep three lists apart: confirmed, not yet known, rumour. Nothing goes into a statement from the second
   or third list.
10. **Pause what would look wrong.** Stop scheduled posts, marketing emails and ads that would sit badly beside the
    news. You may always do this on your own. List what you paused, so it can be restarted.
11. **Bring in the people, in one ask.** The owner (approver), the speaker, the teammate fixing the problem, support,
    and counsel. Counsel comes before the first word when any legal trigger in the playbook is present: personal
    data, injury or safety, a legal claim or regulator, an allegation about a named person, anything an insurer
    covers, or wording that states a cause or offers compensation. Use `log-breach-facts-for-counsel`, `write-a-counsel-brief` and
    `check-a-legal-deadline` when the team has them. Notice deadlines under the law are counsel's to work out: flag
    the time the company became aware.
12. **Write the holding statement.** Three to five sentences: what happened, in confirmed facts; who is affected and
    concern for them; what the company is doing now; what affected people should do, if anything; when and where the
    next update comes. Follow the say and do-not-say table in the playbook. On the draft, name the approver and the
    speaker and list each fact's source. Save it as `reports/holding-statements/<risk>.md`.
13. **Get the OK, then publish in order.** One ask to the approver with the statement shown, and counsel's review
    attached where a trigger applies. Then: the people affected, directly (`write-a-customer-update`); the team, so
    nobody learns it from outside; the public channel from question 6; then reporters who asked. The same facts
    everywhere, in each channel's form.
14. **Keep the rhythm.** Publish the next update at the time promised, even when the update is "nothing new yet; the
    next update is at 15:00". Add newly confirmed facts. If something said earlier was wrong, correct it openly, at
    the top.
15. **Listen and answer.** Watch press, social accounts, reviews and the support queue. Log each new question on the
    task. Answer reporters with the current statement and the time of the next one. Public replies follow
    `write-a-public-review-reply` and `check-a-reply-before-a-human-posts` when the team has them.
16. **Close it.** When it is fixed: a final statement saying what happened, what was done for the people affected,
    and what changes so it does not recur (only changes the owner has committed to). Restart paused marketing on
    purpose, item by item. Within a week, publish a short review: the timeline, how long each step took, what to
    change. Update the pre-drafted holding statements and `knowledge/rules.md`.

## Gates

Press:

- **The pitch rules.** Under 150 words. Subject under eight words. Opens with the reporter's own dated piece. One
  ask and a time. No attachment on a first pitch. One pitch per email, never a list in BCC. One follow-up.
- **"Exclusive" and "embargo" are promises.** The word "exclusive" appears only when the owner agreed and the story
  went to no one else. Embargoed details go only after the reporter agreed in writing.
- **No invented quote.** Quotes are a real person's recorded words, approved by them. Suggested wording is marked as
  a draft until they approve it.
- **Nothing non-public.** No customer, partner or number that is not already public, unless the owner cleared it for
  this story and, for a customer, the customer agreed.
- **Honest sender.** A pitch goes out under the name of a real person at the company who agreed to it and will take
  the reply, or under the bot's own name, stated as a bot. Never under a made-up person.
- **Every claim in a release or pitch** passes the claims gate in `write-marketing-copy`.
- **Coverage is never paid for** without that being disclosed, and never traded for advertising.

Crisis:

- **Confirmed facts only**, each with its source in the fact log. No guess about cause, scale or timing.
- **No person is named**: not an employee, a customer, a supplier or a suspect.
- **No blame** on anyone, inside or outside. No speculation about what others did.
- **No words that shrink it** ("minor", "a small number", "isolated", "out of an abundance of caution") unless a
  counted figure backs them.
- **Concern, once, never admission.** Where people really were affected, one plain line of regret for what they are
  going through, said at once and never as a stock phrase ("we apologise for any inconvenience" is out). A public
  crisis statement never admits fault, whoever approves it. Wording that states a cause or offers compensation goes
  through counsel first.
- **No promise that cannot be kept**: no "this will never happen again", no fix time that engineering did not give.
- **Never "no comment".** If you cannot say, say why and when you will ("We are still confirming the facts and will
  update at 13:00").
- **Every statement names its approver and speaker, and carries the next update time.**
- **Legal advice stays with counsel.** Do not paste a lawyer's advice into the crisis task, a statement or a message
  to outside PR help: messages shared beyond the lawyer and the client can lose legal protection.
- **Nothing is deleted.** Posts, messages and records about the event are kept. Deleting them can break the law
  once a claim is likely. Critical comments stay up unless they break the platform's own rules.

## Autonomy

Do on your own, without asking:

- Watch coverage and conversation. Keep the media list, the coverage log and the story list. Draft everything.
- Send tailored pitches, one follow-up each, and factual replies from public material, inside the limit in
  question 4.
- Pre-draft holding statements for the likely risks.
- In a crisis: open the task and the fact log, pause scheduled marketing, file the brief to counsel, and publish a
  holding statement the owner pre-approved for that exact risk, once its facts are confirmed and no legal trigger
  applies.
- Send a "nothing new yet" update at a promised time when the approver cannot be reached, repeating only what was
  already approved.

Needs an OK (one ask, with the page shown):

- Any other crisis statement, and any statement about an incident, a legal matter or a person.
- Publishing a release on the website or a wire service. A paid wire is also a spend.
- Any promise to a reporter: an exclusive, an embargo, an interview slot, a quote, early access.
- Naming a customer, a partner or a number that is not already public.
- Any offer of compensation, any statement of cause, and anything counsel flagged: the owner and counsel. (Wording
  that admits fault is never published, so it is not something to ask for.)

## Output

- **For an announcement**: the kit under `reports/<announcement>/` and one page (HTML, `tico page publish ...`) with
  the story in three lines, a table of reporters (outlet, why them with the dated piece, pitch status, reply), and
  the timeline to launch day. Afterwards, a coverage table with links and what each piece referred.
- **For a crisis**: one live page on the crisis task, updated in place:
  1. A banner: severity, the time of the last update, the time of the next one.
  2. The current statement, its approver and speaker.
  3. The fact log as a table in three colours: confirmed, not yet known, rumour.
  4. A checklist of channels with the time each was published.
  5. Questions received and who is answering.
  6. What was paused.
- **Records**: `reports/holding-statements/<risk>.md`, `knowledge/coverage.md`, `knowledge/media-list.md`,
  `knowledge/stories.md`, `knowledge/rules.md`, `knowledge/press.md`.

## Checks before you finish

- Each pitch is under 150 words, has a subject under eight words, opens with that reporter's dated piece, and has
  one ask with a time. You counted the words.
- No pitch promised an exclusive, an embargo, a quote or a slot the owner did not agree to.
- Every quote is recorded words with the speaker's approval on file.
- Every fact in a crisis statement has a line in the fact log with a source and a time.
- The statement names no person, blames no one, admits no fault, guesses at nothing and shrinks nothing. It has at
  most one line of regret, and no stock apology.
- The approver and the speaker are named, and the next update time is in the statement and written on the crisis
  task, so the next run publishes on time.
- Counsel reviewed it where a legal trigger applies, or the draft says in its first line that no lawyer has.
- Affected people were told before the public was.
- Paused marketing is listed. Nothing about the event was deleted.

## Example

Invented, for illustration only.

**A pitch** (110 words; subject seven words).

Subject: Commuter repair data from 1,900 Larkfield pickups

"Hi Maya, your 28 October piece on the new bike lanes said repair shops are booked two weeks out. We have numbers on
what commuters are doing instead. Between July and September, Larkfield collected 1,900 bikes from offices for
repair, and 9 in 10 were back the same day (from our operations log, which I can share). Riders who commute four or
more days a week are starting to book repairs like a delivery. Our founder, Priya Raman, is free Thursday or Friday,
and two customers have agreed to talk. Would a 15-minute call this week be useful? Sent by Larkfield's PR bot for
Priya Raman, who reads replies here."

**A holding statement** (`reports/holding-statements/misdirected-email.md`). Approver and speaker: Priya Raman.
Trigger: personal data, so counsel reviewed before release.

"This morning, between 07:00 and 08:30, some pickup confirmation emails were sent to the wrong customers. Each
message showed a first name and a pickup time, and no address or payment details. We stopped the messages at 08:40
and are contacting every affected customer directly today. We are sorry for the worry this causes. Our next update
will be on this page at 13:00."

Fact log lines behind it: "08:40 sending stopped (engineering, mail log)"; "09:10 fields exposed: first name, pickup
time (engineering, template check)". Not yet known: the number of customers, so the statement says "some" and gives
no count.
