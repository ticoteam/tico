# Bot or person

The test that decides, line by line, which parts of a role a bot does and which, if any, need a person. Run it on the
outcomes and the "Owns" list of the role scorecard. The default answer for every line is "bot".

## The test

For each line, ask in order. Stop at the first "yes".

1. **Can a bot do this with Tools the team already has?** Mark **bot**.
2. **Could a bot do it if one more Tool were connected?** Mark **bot, needs Tool: <name>**, and file a task to the
   Tools Owner (or the person who holds the account) to connect it. A missing connection is a setup job, not a reason
   to hire.
3. **Does one of the accepted reasons below apply?** Mark **person, because <reason>** and write the evidence.
4. **None of the above and you are unsure?** Mark **bot, trial**. Run the first week and let the day-7 check decide.

## The accepted reasons

A line goes to a person only for one of these. The settings in `knowledge/filling-roles.md` may add or remove reasons;
that file wins.

| Reason | What it means | Evidence to write down |
|--------|---------------|------------------------|
| Physical presence | Hands, eyes or a body must be somewhere: a dock, a site visit, a lab bench, a courtroom | What has to be touched or seen, and where |
| Licence or signature | The law, a regulator or a contract requires a licensed or named individual to do or sign it | The rule, with a link and the date read |
| The counterpart requires a person | A customer, partner, bank or authority has said they will only deal with a person | Their own words, with a link |
| The tool forbids automation | The only way to do the work is a system with no API whose terms forbid automated use | The terms clause, with a link and the date read |
| A real trial failed | A bot held this line and missed its target after one fix round | The task ids, the readings and the fix that was tried |

## Reasons that do not count

- "It is important" or "it is sensitive". Important work gets a tighter scorecard and an OK step, not a person.
- "A person would be faster to start." Setup time is a week.
- "We have always had a person do this."
- "Customers prefer a person", when no customer has said so. Ask, or run a trial.
- "The bot might make a mistake." So might a person. Put the check in the role: a guardrail outcome, "Ask first"
  sending, or a reviewer bot.
- "Nobody knows how to do it." Then the first task is to write down how. See the SOP steps in the
  `run-the-operating-cadence` skill.

## Split the role

Most roles come out mixed. Do not hand the whole role to a person because one line needs one.

1. Put every "bot" line into one scorecard and fill it with a bot.
2. Put the "person" lines into a second, smaller scorecard.
3. Look for a person already on the team who can take the small scorecard as part of their work. Ask them through a
   task, with the hours per week estimated from the record.
4. Only if nobody can, go to the contractor or hire path, for the small scorecard only.
5. Write the hand-offs between the two halves as steps: what the bot gives the person (a prepared pack, a checklist,
   the photo list), what the person gives back (the signed form, the photos, the yes or no), and where (a task).

Write the split as a table on the role page:

| Outcome or owned thing | Who | Reason | Hand-off |
|------------------------|-----|--------|----------|
| Claims filed in time | Bot | | |
| Photos of damaged freight | Warehouse lead (person, on the team) | Physical presence | Uploads to the task within 24 hours |

## What a fair trial looks like

"A real trial failed" is only evidence if the trial was fair. It was fair when:

- the bot had the Tools and access the scorecard lists, checked with one read of each source on day one;
- its first task was narrow and its instructions said what "done" means;
- someone read the failed runs end to end and found the cause;
- one fix was made (instructions first, then Tools, then reasoning effort, then model) and the review was repeated;
- the readings came from the KPI, not from an impression.

If any of those is missing, the verdict is "trial not fair, run again", not "a bot can't".
