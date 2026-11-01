# Weekly PR review

Schedule: Tuesdays at 09:00 team time (routine `weekly-pr-review`), after setup. Also run by hand. Budget 30 minutes. The outcome is one page: what was covered, what is
coming, and what is waiting on a human. Nothing is sent.

---

## 1. Read where things stand

    hub task show <id>
    hub calendar list

Then `knowledge/stories.md`, `knowledge/rules.md` and last week's review. Check which pitches were
sent, and whether any reporter replied (from the mailbox Tool or the task).

## 2. Find coverage

Search the public web for the team, its products and its spokespeople since the last review. For
each hit: outlet, date, headline, angle, tone (positive, neutral, negative), accurate or not, and
whether it came from a pitch. Add it to `knowledge/coverage.md`. An inaccurate article is the first
line of the review, with the correction a human could ask for.

## 3. Watch the beat

Search for new articles on the team's topics and its competitors' news. A reporter who wrote two
relevant pieces this month is a candidate for `knowledge/media-list.md`, with both links.

## 4. Walk the story pipeline

For each upcoming story: days until press work must start (default: four weeks before a tier 1
launch, two for smaller news), what is missing (a customer, data, a spokesperson slot), and who owns it.

## 5. Write and hand over

Write `reports/YYYY-MM-DD-pr.md` in the shape of `knowledge/examples/pr-review.md`, then
`hub file publish reports/YYYY-MM-DD-pr.md`. Commit, and `hub task update <id> --status done
--note`: the headline, the path, what is waiting on a human.

Done when: coverage since the last review is logged with dates and tone, each inaccurate article has a correction
request drafted, the story pipeline has start dates, and every blocked source is named.
