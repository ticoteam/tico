# Using a contractor

Use only after the OK in step 10 of the skill. A contractor is the right answer when the part a bot cannot do is
**bounded**: it has a deliverable, an end, and the contractor decides how to do it.

Legal facts here were checked on 2026-11-18 from secondary summaries. Re-read them at the source before relying on
them, and record the date in `knowledge/filling-roles.md`.

## Contractor or employee

| Question | Points to contractor | Points to employee |
|----------|----------------------|--------------------|
| Is there a defined deliverable and an end date? | Yes | No, the work is ongoing |
| Who decides how, when and where the work is done? | They do | The team does |
| Do they work for other clients and use their own tools? | Yes | No |
| Is the work the core, everyday business of the company? | No | Yes |
| Would the team want them full time, indefinitely? | No | Yes |

If the answers are mixed, treat it as employment or ask a lawyer. Getting this wrong is expensive: back taxes, wages
and penalties. The tests differ by place:

- **US federal.** The Department of Labor's 2024 "economic reality" rule has not been enforced since May 2025, and a
  replacement was proposed on 26 February 2026. Check whether it is final. The IRS uses its own common-law test of
  behavioural control, financial control and the relationship.
- **States can be stricter.** California and several others use the "ABC" test, under which a worker is an employee
  unless the work is outside the usual course of the company's business, among other conditions. Check the state where
  the contractor lives.
- **Outside the US.** Rules vary widely. Use a contractor-of-record or employer-of-record service, or local counsel.

## Steps

1. **Write the statement of work** from the small scorecard.

   ```text
   Work: <what is delivered, in terms a third party could check>
   Acceptance: <the test for each deliverable, taken from the scorecard outcomes>
   Dates: <start, each milestone, end>
   Price: <per deliverable or milestone; a cap if hourly>
   Inputs we provide: <files, access, the bot's prepared packs>
   Out of scope: <what they do not do>
   Contact: <one teammate; where questions go (a task)>
   ```

   Price by deliverable where you can. Hourly work needs a cap and a weekly report.

2. **Find three candidates** and compare them on the same criteria: evidence of the same deliverable done before,
   price for this statement of work, earliest finish date. Record evidence as shown, partly shown or not shown, as for
   a hire. A person picks.

3. **Agreement.** Use the team's contractor template. It must cover: who owns the work product (assigned to the
   company), confidentiality, payment terms, how either side ends it, and that the contractor is responsible for their
   own taxes. A lawyer checks the template the first time and any change. The `review-a-contract` skill covers the
   review if the contractor sends their own paper. A person signs.

4. **Access.** Give the least that does the job, with an end date. Prefer a guest seat or shared files over an account
   on the team's systems. Record what was given on the task.

5. **Paperwork (US).** Collect Form W-9 before the first payment. A Form 1099-NEC is due for a contractor paid at or
   above the reporting threshold in a year: 2,000 US dollars for payments made after 31 December 2025 (raised from 600
   by the 2025 tax law, and indexed from 2027). Check the current figure. Foreign contractors use Form W-8BEN instead.
   The finance bots handle the filing; you make sure the form is on file.

6. **Pay** through the team's normal bill process (the `pay-bills-and-buy` skill): invoice matched to an accepted
   deliverable, inside the approved total.

7. **Judge by the deliverable.** Run the acceptance test for each milestone. Accept, or send back with the specific
   gap. At the end, write a verdict on the role page: was the work done, and should this stay a contractor's job,
   become a bot's (now that it is written down), or end.

8. **Close.** Remove access on the end date and confirm it is gone. Collect the files. Update
   `knowledge/roles/<role-slug>.md`.

## Signs it has stopped being a contractor

Raise it with the owner when any of these become true: the end date keeps moving, the team sets their hours, they have
a company email and attend the team's regular meetings, or they have no other clients. Either bound the work again or
move to the hire path.
