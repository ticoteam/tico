# Release notes for customers

Release notes are read by people who use the product, not by people who build it. They want to know three things:
what is different for me, do I have to do anything, and is the thing that annoyed me fixed.

## Before writing

1. Start from the list of PRs and tasks in the release.
2. For each one ask: can a customer notice this? If no, it does not get its own entry.
3. For each one a customer can notice, find the evidence: the spec's "who is it for", the QA report's after
   screenshot, the production check.
4. Group what is left: **New**, **Improved**, **Fixed**, and **Action needed** when a customer must do something.

## The entry

```
**<Headline: what you can do now, in 8 words or fewer>**
<One or two sentences: what changed, from the customer's side, and where to find it.>
<Who will notice, if it is not everyone.>
<What to do, if anything.>
<Screenshot, for a change you can see.>
```

Rules for every entry:

- Lead with what the customer can do, not with what the team did. "You can now pause your subscription", not "We
  added pause support".
- Use the words on the screen. If the button says "Pause", write "Pause".
- No internal names: no code names, table names, ticket numbers, service names or teammate names.
- One idea per entry. Two changes are two entries.
- Say where: the menu path or the screen.
- Fixed entries describe what the customer saw go wrong, then say it is fixed. "Exports with more than 10,000 rows
  stopped halfway. They now finish."
- No promises about the future and no dates for things not yet shipped.
- Claim only what was seen working in production after the release.

## Action needed

Put these first, above New. Each one says who is affected, what to do, by when, and what happens if they do not.

```
**Action needed: update your API calls by <date>**
If you use the orders API, the field `total` is now `total_cents`. The old field keeps working until <date>.
Change your code to read `total_cents`. After that date, `total` will be missing from responses.
```

A breaking change that reaches customers without an Action needed entry is a release defect.

## What to leave out

- Refactors, test changes, dependency updates and build work, unless a customer can notice the effect (then write
  the effect: "Pages load faster").
- Security fixes in detail. Say "security improvements" unless customers must act; if they must, it is an Action
  needed entry and the wording gets an OK from the owner first.
- Anything behind a feature flag that is still off for customers.

A single closing line may cover the rest: "Plus smaller fixes and speed improvements."

## Tone

Plain, short, calm. No exclamation marks, no "we're excited", no jokes about bugs. A customer who was hurt by a bug
reads the Fixed entry; write it for them.

## Screenshots

Real captures from production after the release, at the size most customers use. Before and after side by side when
the change is to something that already existed. Use test accounts only, so no real customer's data appears.

## Check before publishing

- Would a customer who has never seen the code understand every line?
- Does each entry say what changed for them and where?
- Is every Action needed entry complete: who, what, by when, what if not?
- Is each claim backed by the production check?
- Does the version and date at the top match the release?

## Where it goes

Publish where `knowledge/engineering.md` says (by default the changelog or release page). The same text can feed an
in-app "What's new" or an email, but sending anything straight to customers needs an OK each time. Internal detail
(the PR list, risk notes, the thresholds) stays in the release report, not here.
