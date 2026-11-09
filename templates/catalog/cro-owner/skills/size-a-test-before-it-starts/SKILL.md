---
name: size-a-test-before-it-starts
description: Work out a test's sample size, run time and stop rules before it starts, from a dated baseline, the smallest change worth shipping and the eligible traffic, and say so when the page is too small to test.
---
# Size a test before it starts

When to use: before you write a test brief, hand one to a page owner, or say that a test will finish by a date.

Steps:
1. Write the baseline: the control rate, its count, its window and the export date. Pick a clean window. If the window
   holds a known change to the page, say so, or pick another.
2. Write the smallest change worth shipping, in percentage points. If nobody can name it, ask the page owner. Do not guess.
3. Set p1 to the baseline rate and p2 = p1 + that change, both as fractions.
4. Compute n per arm = 7.849 × [p1(1 − p1) + p2(1 − p2)] ÷ (p2 − p1)². The constant is (1.96 + 0.8416)², for a two-sided 5%
   test with 80% power. Round up to a whole person.
5. Eligible entrants a week: the average of the last four full weeks, after the exclusions the brief names. Say which weeks.
6. Weeks = ceil(2 × n ÷ eligible entrants a week). The minimum is two and the maximum is six. If the answer is above six, the
   page is too small for this change: say so, and propose a bigger change or another page.
7. Write the stop rules: the planned end; the sample ratio check; the guardrail limits; the one reason to stop early, a
   broken page.

Checks before you finish:
- The baseline has its count and window.
- The smallest change is written in points, and it is one a person would act on.
- The run time is six weeks or fewer, or the brief says the page is too small.
- The primary metric is one step.

Example (invented numbers, for illustration only): baseline 25.0% from 1,880 pricing views, 2 to 8 Nov. Smallest change
worth shipping 2.8 points, so p2 = 27.8%. n per arm = 7.849 × (0.1875 + 0.2007) ÷ 0.028² = 3,887. Eligible entrants 1,340 a
week. Weeks = ceil(2 × 3,887 ÷ 1,340) = 6.
