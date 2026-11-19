# Sources

The text of this skill is original. These are the works whose ideas it adapts. All were read on 2026-11-18.

| Source | Licence | What was drawn from it |
| --- | --- | --- |
| superpowers, `test-driven-development`. https://github.com/obra/superpowers | MIT | No shipped code without a failing test first; check the red is for the right reason (behaviour missing, not a typo); least code to green; code written before its test is taken out, not tested around; run the project's suite and report failures by name. |
| Matt Pocock skills, `tdd`. https://github.com/mattpocock/skills | MIT | Test behaviour through public interfaces; one test then one implementation, never all tests first; the named bad patterns (tied to internals, expected value recomputed the way the code does). |
| pstack, `tdd` and the `bug-fix` playbook. https://github.com/backnotprop/pstack | MIT | Failing-first only where a clear, cheap test path exists; prefer no new test over a bad one and fall back to the closest executable check; report failing-before and passing-after evidence; land the failing repro before the fix. |
| addyosmani agent-skills, `code-review-and-quality`. https://github.com/addyosmani/agent-skills | MIT | Flip one condition the change adds, run the tests, restore; a flip that stays green means a test is missing. |
| Tico `skills/honest-code` | Tico | A pass on retry is a failure; double only what is outside the process; a test fails when the named behaviour breaks and passes when only internals change; expected values are literals; a bug fix test fails on the old commit and the PR names it; 20 of 20 runs to prove a flake fix; the evidence line format. |

The "20 of 20" figure is Tico's own rule in `skills/honest-code` as of 2026-11-18. A team may set its own number in
`knowledge/engineering.md`.
