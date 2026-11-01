# Same-day change

Triggered by a task during the working day: a technician is sick, a job overran, a customer cancelled,
an urgent job came in. Budget 10 minutes. The outcome is a revised plan for the rest of the day with
the customer messages it needs, in front of the dispatcher.

---

## 1. What changed

    hub task show <id>

Who or what, from when, and which jobs it touches. Read the day's current plan.

## 2. Options

For each affected job: move to another crew free and qualified in the area, move later the same day
inside the customer's window, or move to another day. Say for each the driving added and whether the
window still holds. An urgent job goes to the nearest qualified crew whose next job can slip least.

## 3. Recommend

One revised plan, the jobs that move, and the messages to the customers whose window changes. Run
`skills/check-a-job-before-you-assign-it/SKILL.md` on each job that moves. Apply a change to the schedule only when the
task asks for it and a granted Tool allows it; otherwise the revised plan stays on the task for the dispatcher. Customer
messages stay drafts until a person turns mail sending on in Tico.

## 4. Apply the requested work

The dispatcher or the job tool applies the change. You record it in the day's report, and note the cause in
`knowledge/job-types.md` if a job length was the reason.

Done when the revised plan is on the task, every affected customer has a notice drafted, and the task note names the
change and what it moved.
