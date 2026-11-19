# Weekly quality check

The point: find out whether replies the team thinks are good are the ones customers experience as good. A scorecard
that is never compared with outcomes drifts into grading politeness.

## 1. Pull last week's replies

From the help desk Tool or the mailbox (`tico email sends --mailbox <mailbox>`), list every reply sent to a customer
in the last seven full days. Record the count. If a channel could not be read, write "not read" for it.

Sample:
- up to 50 replies a week: score all of them;
- more than 50: score 50, picked by taking every nth reply in time order, plus every reply that was reopened and
  every reply that got a low rating. Say on the page how the sample was drawn.

## 2. Score each reply on six points

Each point is yes, no, or cannot tell. Score against the thread and the source, using the
`score-a-reply-against-its-source` skill for point 3.

| # | Point | Yes means |
|---|---|---|
| 1 | Understood | The reply addresses what the customer was trying to get done, and every question asked |
| 2 | Resolved | After this reply the customer had the outcome, a dated promise, or a clear no with an alternative |
| 3 | True | Every fact, amount, date and policy line matches the source as it stood that day |
| 4 | Effortless | It asked for nothing the team already had, and at most one step from the customer |
| 5 | Short and clear | Answer first, within the length limit, dates as dates, their words for their problem |
| 6 | Fast | First reply inside the promise in `knowledge/support.md` |

A reply's score is the count of yes out of six. A "no" on point 3 makes the reply a fail whatever the total, and
gets a correction sent to the customer the same day.

If two scorers (two bots, or a bot and a person) score the same replies, run `calibrate-two-scorers` once a month so
the scores mean the same thing.

## 3. Read what happened next

For each scored reply, from the help desk or mailbox:

- **Reopened:** did the customer write again about the same issue within 7 days? (A thank-you is not a reopen.)
- **Rated:** did they answer a satisfaction survey, and with what?
- **Waited:** minutes from their message to the first real reply.
- **Contacts to close:** how many customer messages it took.

## 4. Put the two side by side

| Reply score | Replies | Reopened within 7 days | Rated, and average | Median wait |
|---|---|---|---|---|
| 6 of 6 | | | | |
| 4 or 5 | | | | |
| 3 or fewer, or failed on True | | | | |

Fill it with counts, not just percentages. With fewer than 20 replies in a row, show the counts and draw no
conclusion from the percentage.

What to look for:
- **High scores, still reopened.** The scorecard is missing something. Read those threads and name what the customer
  came back for. Usually it is the unanswered next question. Add or reword a point.
- **Low scores, never reopened, rated well.** The scorecard is punishing something customers do not mind. Reword it.
- **One point failing across many replies.** That is a cause, not a set of slips. Fix the source: a doc, a Tool
  permission, a rule in this skill's knowledge file.
- **Slow and low-rated together.** Check where the time went: waiting on a teammate, a missing Tool, a send held for
  approval.

## 5. Act

- For each failed reply: fix it with the customer if it left them worse off.
- For each pattern: one task with an owner, or a change to `knowledge/support.md`, dated.
- For a teammate or bot whose replies show the same miss three times: `draft-a-coaching-note`.
- Change the scorecard itself when step 4 says so, and record the change with its date and reason at the bottom of
  this file's copy in `knowledge/support.md`. Do not change it to make a week look better.

## 6. Help articles

The same weekly pass keeps the help docs honest:

- List the top eight question causes of the week. For each, is there a help article that answers it fully? If not,
  write it. If yes and customers still ask, the article is hard to find or unclear; fix the title or the first line.
- Any article you relied on that turned out wrong: correct it the same day and note the correction.
- Hand new and changed articles to the Librarian so the team's docs stay in one place.
- An article states what is true today. No promises about future features, no dates you cannot source.

## Figures on the weekly page

Each with its source, date range and base count:

- Median time to first reply, and the share inside the promise.
- Share of contacts resolved with one reply from the team.
- Share reopened within 7 days.
- Satisfaction: the score, the number of responses, and the response rate. Fewer than 20 responses: show the count
  and the comments, not a percentage.
- Contacts by root cause, top eight.
- The score-against-outcome table above.

None of these has a target invented here. After four weeks, propose targets to the owner from the team's own numbers.
