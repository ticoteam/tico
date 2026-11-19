---
name: debug-and-handle-incidents
description: Find the real cause of a bug or outage and, when customers are affected, run the incident at the same time. Opens one task that holds the timeline, sets severity, counts who is affected, reduces the harm first, sends updates on a fixed rhythm, then debugs properly - one command that reproduces the customer's exact symptom before any fix, ranked guesses tested one at a time, a stop after three failed fixes to question the design - and closes with a blameless review whose actions change the system. Use when a task or message says bug, broken, failing, error, crash, regression, flaky, slow, timeout, cannot reproduce, root cause, why did this happen, incident, outage, down, degraded, SEV, alert, on-call, status update, postmortem or incident review, and when a deploy fails or is rolled back.
metadata:
  function: engineering
  roles: [site-reliability-engineer, devops-engineer, feature-owner, builder, qa-owner, technical-support-engineer]
  actions: [read, draft, send, change-records]
  review: professional
---
# Debug and handle incidents

When to use: something is broken and the cause is not known. If customers are affected right now, or data or
security is at risk, it is also an incident, and the people affected need to hear from the team while the cause is
being found. This skill does both, joined on one task.

Words used here: a **symptom** is what the customer or the check actually sees. A **repro** is a way to make the
symptom happen on demand. **Red-capable** means the repro fails when the bug is present and would pass if it were
gone. To **mitigate** is to reduce the harm without yet fixing the cause. A **hypothesis** is a guess about the cause
that a test could prove wrong.

Tico words: a **task** is one piece of work with an owner. A **pipeline** moves a task through **stages**. A **page**
is an HTML file a bot publishes onto a task for people to read. An **OK** is a named person's approval, requested
with an ask. Commands that change a task take `--version N`, the number that `tico task show <id>` prints.

`review: professional` applies to one case: an incident that exposed personal data or involved a security breach can
create legal duties to notify people. The wording and timing of those notices go to the owner and a lawyer before
anything is sent.

## What great looks like

Within minutes, the harm to customers is going down, one task holds everything known so far, and the people affected
know what is happening and when they will next hear. Nobody is guessing in public.

The fix removes the cause. It is proven by a command that failed before the fix and passes after it, on the same kind
of surface where the customer met the problem. No guess is shipped "to see if it helps". After three fixes that did
not work, the bot stops fixing and asks whether the design is the problem. The incident ends with changes to the
system that would have caught, shrunk or prevented it, each with an owner and a date.

## First time at a company

Read `knowledge/engineering.md` in your own repository. If it has an "Incidents" section, skip this. If not, ask the
owner these in one numbered message and save the answers under "Incidents" with the date. Work on the defaults
meanwhile.

1. What are our severity levels, how fast do we respond and how often do we update? Default: the table in
   `references/updates.md`.
2. Where do customers hear about incidents, and who approves what they are told? Default: the status page if there is
   one, otherwise replies through Support only. The bot drafts; the owner gives an OK to anything sent to customers.
   Updates inside the team go out without asking.
3. What may a bot do by itself to reduce harm? Default: turn a feature flag off, pause a job or a Routine (scheduled
   bot work), restart a service, and roll back a release marked safe to roll back. Not without an OK: delete or rewrite data, change
   customer records, rotate credentials other systems use.
4. Where are the logs, numbers and error reports? Default: find them under Tools and in the repo's docs, and record
   the list here.
5. Who leads when a person is needed, and how are they reached out of hours? Default: the owner, by a task ask.
6. How soon after an incident is the review due? Default: within two working days, for SEV1 and SEV2 always and for
   lower levels when the same thing has happened before.

## Steps

**Open and size it**

1. **Open one task, or find the open one.** Search first:
   `tico task list --q "Incident" --status open --snoozed all`. If none matches:
   `tico task create --owner <you> --title "Incident: <symptom>, since <time, UTC>"`. For a plain bug with no
   customer harm, use the bug's own task. Everything that happens goes on this task as comments with UTC times:
   `tico task comment <id> "14:32 UTC <what was seen or done>"`. This is the timeline; nobody rebuilds it from
   memory later.
2. **Decide: bug or incident?** Customers affected now, or data or security at risk: incident, do every step. No
   customer harm: it is a bug. Skip steps 3 to 5 (severity, updates, mitigation) and steps 14 and 15 (all clear,
   review).
3. **Set the severity and count who is affected.** Use the table in `references/updates.md`. Count from sources
   (logs, error reports, support messages), giving who, how many and since when. Write "not known yet" for anything
   not counted; never estimate in an update. (Skill `count-impact-from-sources` if your role has it.)
4. **Send the first update inside the response time.** Use the template in `references/updates.md`: what is
   happening, who is affected, what we are doing, when the next update comes. Post it on the task. Draft the
   customer wording as a second comment and ask for the OK:
   `tico task ask <id> --to <owner> --kind ok "Send this incident update to customers?" --version N`. Then keep the
   rhythm: send the next update at the promised time even when there is nothing new. (Skills
   `set-severity-and-owner` and `write-a-customer-update` if your role has them, for a single customer's case.)

**Reduce the harm**

5. **Mitigate before diagnosing.** Ask what changed just before it started: a release, a flag, a config change, a
   dependency, a data import, a traffic spike. Take the cheapest action that can be undone. Before any restart or
   roll back, save what you will need later: the failing request, the logs around it, the state of the stuck
   record. Record each action and its time. Check with numbers that the symptom is falling.

**Find the cause**

6. **Build a red-capable repro.** One command the bot can run that fails with the customer's exact symptom, the same
   way every time, in seconds. Use the first of these that works: a failing test; a script that calls the API; the
   command line with a saved input, comparing output; a headless browser run; replaying a captured request; a small
   throwaway harness around the real function. Rules:
   - It must check for the symptom itself, not something near it.
   - If the bug comes and goes, raise the rate first (loop it, add load, narrow the input) until it fails reliably,
     and state the rate: "fails 9 runs in 10".
   - Remove secrets and personal data from anything saved.
   - No red-capable command, no fix. If you cannot build one, say so on the task and keep narrowing; do not start
     changing code.
7. **Shrink it.** Remove inputs, steps and settings until everything left is needed for the failure.
8. **Find when it started, where that is possible.** If it used to work, bisect: `git bisect start`, mark the bad
   and the last good commit, then `git bisect run <the repro command>`. For a system of several parts, log the
   value at each boundary to see which part first gets it wrong, then follow the bad value back to where it was
   made. For "slow", measure a baseline first, then narrow.
9. **Write three to five hypotheses, ranked.** Each in the form "If X is the cause, then changing Y turns the repro
   green." A guess that no test could prove wrong is not on the list.
10. **Test one thing at a time.** Change one variable, run the repro, record "confirmed" or "ruled out" on the task.
    Tag every temporary log line with one unique marker such as `[DEBUG-a4f2]` so a single search removes them all.
    Undo everything a ruled-out guess made you add.

**Fix it**

11. **Fix the cause, with the test first.** A guard such as "if empty, skip" is a fix only when empty is a
    legitimate state; otherwise it hides the bug. Follow `test-first`: a regression test at a real seam that is red
    on the old commit, then the smallest change, then green. Without that skill: commit a test that fails before
    the fix and passes after. Then run the repro from step 6: green. Then check on the surface the customer used
    (the real screen, the real API). Ship no extra changes "just in case".
12. **Three strikes.** If three fixes have each failed to turn the repro green, stop. Write what the three had in
    common: that shared belief is probably the wrong one. If each fix moved the problem somewhere else, the design
    is at fault, not the line. Keep the mitigation in place, take the design question to `plan-before-coding`, and
    put the options with your recommendation to the task's owner before trying a fourth.
13. **Release through the normal path** (`ship-a-release`). The fast lane is only for data loss, a security hole, an
    outage, or an install that cannot update.

**Close it**

14. **Give the all clear on evidence.** The symptom is gone in production, measured over a stated window (default:
    one full soak period after the fix, and the next run of any job involved), and the numbers are back at
    baseline. Send the final update. Remove the mitigation if the fix replaces it. (Skill `check-an-all-clear`.)
15. **Hold the review.** Fill `references/review-template.md` and publish it as a page on the task:
    `tico page publish review.html --task <id> --title "Incident review: <symptom>"`. Blameless: describe what the
    system allowed, not who slipped. Label each cause confirmed, suspected or ruled out. Every confirmed cause and
    contributing factor gets at least one action of kind Detect, Mitigate or Prevent, filed as its own task with an
    owner, a date and a "done when" test. Then run each action through `turn-corrections-into-gates`, so the fix is
    a check or a design change wherever one is possible, not a note.
16. **Clean up.** Search for the debug marker and remove every line. Remove temporary flags and scripts. Close the
    task with `tico task done <id> -m "<cause in one line; fix PR; review page>" --version N`.

## Gates

- No fix before a red-capable repro exists.
- One change per test of a hypothesis. Two changes at once prove nothing.
- No fix ships on a hypothesis that was not confirmed by the repro going green.
- Stop after three failed fixes. A fourth needs the design question answered first.
- A check that passes on retry has not passed.
- Every incident update states what is known from sources. No estimates of impact, no guessed cause, no promised
  fix time. "Cause not yet known" is a complete sentence.
- An update promised for a time is sent at that time.
- No public update admits fault or apologises for what the company did. It states facts, impact, what was done and
  the next update, under the statement rules of `handle-press-and-a-crisis` (see `references/updates.md`).
- Anything suggesting personal data was exposed, or a security breach: tell the owner at once and send nothing to
  customers until the owner, with legal advice, has approved the wording.
- A committed or leaked secret is revoked first and investigated second.
- The review names systems and decisions, never a teammate as the cause.

## Autonomy

Alone: open the incident, set severity, post team updates, take the mitigations listed in `knowledge/engineering.md`,
investigate, fix, release through the normal path, give the all clear, write the review and file its actions.

Needs an OK: anything sent to customers; any mitigation that deletes or rewrites data, changes customer records or
rotates shared credentials; a hotfix; the fourth fix attempt after three failures; any notice about exposed personal
data or a security breach.

## Output

- The **incident task**, with the timeline as dated comments and every update that was sent.
- The **fix PR**, whose description states the confirmed cause in one sentence, the repro command, and the test that
  was red before.
- The **Incident review page**: summary, impact in real numbers, a timeline chart, causes, and the action table.
- **Action tasks**, one per action.
- A line in `memory/learnings.md` for anything that would have made the diagnosis faster.

## Checks before you finish

- Does the repro command fail on the old commit and pass on the fix?
- Can you state the cause in one sentence, and was it confirmed by a test, not by the fix seeming to work?
- Was every update sent at the time promised, and does each say what, who, what we are doing and when next?
- Is every number in the impact section from a source you can link?
- Does each confirmed cause have an action with an owner, a date and a "done when"?
- Are all debug lines, temporary flags and scratch scripts gone?

## Example

Invented example.

14:32 UTC. Support reports three customers charged while their subscription was paused. Task created: "Incident:
paused subscriptions charged, since 13:00 UTC".

Severity: SEV2 (money, some customers). Count from the payments log: 11 charges on 11 paused subscriptions since
13:00. First update on the task at 14:40: "Some customers with a paused subscription were charged today. 11 known so
far. We have stopped the job that causes it and are finding the cause. Next update 15:40 UTC." Customer wording
drafted; owner gave the OK at 14:52.

Mitigation, 14:36: paused the payment retry job, which started at 13:00 and matches the timing. No new wrong charges
after 14:36.

Repro: a script that creates a paused test subscription with a failed earlier payment and runs the retry job. It
exits 1 because a charge is created. Fails 10 runs in 10.

Hypotheses: (1) the retry job does not check `paused_until`; (2) the pause was saved in the wrong time zone;
(3) a cache holds the old status. Test 1: added the condition to the retry query in a scratch branch, repro green.
Confirmed. 2 and 3 not needed.

Fix: test "a paused subscription with a failed payment is not retried", red on `9be1f04`; the retry job now calls
the shared `is_billable()`; green. Released as v2.4.1 through the normal path. All clear at 17:10 after the next
retry run charged no paused subscription. Final update sent. Support refunded the 11 charges.

Review actions: Prevent, a lint check that fails any new SQL that selects subscriptions to charge outside
`is_billable()`. Detect, an hourly count of charges on paused subscriptions that opens a task when above zero.
