A sample Go-live page for a fictional team. Every name, URL and number is invented.

```markdown
# Go-live: a three-field signup form

GX-21 · CRO Owner · Acme, studio booking software

## What goes live

On acme.example/signup, the form drops from seven fields to three: "Work email", "Studio name" and "Password". Phone,
company size, role and "How did you hear about us?" move to the first screen after signup, where each can be skipped.
The button reads "Start free trial" instead of "Submit". Nothing else on the page changes.

(The form as it will look, on a phone and on a computer, is attached: signup-form-3-fields.png.)

This is an A/B test. Half of visitors to the signup form see the new form, half see today's.

## The bet

- **Metric:** of the people who start the signup form, how many finish it (product analytics, "signup_started" to
  "signup_completed"). It sits under the KPI, visits that become signups.
- **Prediction:** from 42.0% to 46.0% of form starts finishing by Dec 21, 2026. At today's traffic that moves visits
  that become signups from 1.58% to 1.73%.
- **Why:** 630 of 1,500 form starts a week finish the form, the average of the 4 weeks to Nov 15 (product analytics,
  pulled Nov 16). 61% of the people who leave the form leave at the phone or company size field (field drop-off report,
  same weeks). Rivalbook and Bookly both ask for three fields or fewer (their signup pages, checked Nov 14).
- **Sample size:** 2,414 form starts in each version. Baseline 42.0%, smallest change worth having 4 points (to 46.0%),
  two-sided 5% test with 80% power: 7.849 × (0.42 × 0.58 + 0.46 × 0.54) ÷ 0.04² = 2,414. At 1,500 form starts a week,
  that is 4,828 ÷ 1,500, so 4 weeks.
- **Cost:** no money: the test runs in Acme's testing tool, already paid for. About an hour of Sam's time to set it up.
- **Runs:** 4 weeks, Nov 23 to Dec 21. The result is read once, on Dec 21, and not before.
- **Stop early if:** the form stops sending signups through (a broken page), or the two versions' visitor counts split
  worse than the sample ratio check allows (p below 0.001). Either stops the test, and today's form stays.
- **Guardrail:** the share of new signups who confirm their email, 52% today. Below 48% for a week goes to Sam at
  once, as a question.

## Checks

- **Brand check:** passed. Design Owner, GX-22, Nov 18.
- **Who puts it live:** Sam (edits the site and runs the testing tool). The CRO Owner has read access to product
  analytics only.
```
