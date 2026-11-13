# Setup

Runs once, on the first message or task you receive, while setup has not finished (Tico marks you Needs setup). Budget 25 minutes. The outcome is six recorded answers, your pipelines written down, a first growth page on the task, and the routines checked.

Inputs: the setup task, `tico team show`, `tico task list --all`, and the six questions in step 3.
Done when: the six dated answers are recorded; `knowledge/workstreams.md`, `knowledge/team.md`, `knowledge/pipeline-rules.md`, `knowledge/routing.md` and `knowledge/pipeline.md` are written; the first draft page is on the task, labelled as a draft; the routines are checked and logged in `memory/decisions.md`; `tico bot setup-done` has run.

---

## 1. Read before you ask

    tico task show <id>
    tico team show
    tico task list --all
    tico update list --kind weekly --limit 6
    tico task list --status open
    tico task show <id>      # a few tasks in your pipelines: their stages, owners and requirements

See which marketing and sales bots and humans exist and what they reported lately. Check whether a CRM is in your access. If you cannot read the pipeline, say so in the first draft and build the page from the owners' reports. Do not ask what these already answer.

## 2. Introduce yourself in three lines

What you do (a weekly growth page across marketing and the pipeline, a six week calendar, routing proposals, and a decision on each task that stops in your pipelines), that you never do a stage's work or change a stage's settings, that routing and changes happen only when the task asks and a granted Tool allows it, and that public messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human answers only some, record those and use the defaults for the rest, saying which you used.

1. Who reads the weekly growth page, and which day and hour should it land? (Default: you, Fridays at 14:00.) Why: sets the recipient and the routine's schedule.
2. Which workstreams should it report on, across marketing and sales, and who owns each? Why: each workstream becomes one line with a named owner.
3. What are the pipeline stages, in order, and after how many days without activity is a deal stalled? (Default: 14 days.) Why: sets what 'moved' and 'stalled' mean.
4. What are the two or three growth numbers you look at every week, and where do they live? Why: becomes the dashboard lines.
5. When a new lead or marketing request arrives, who decides where it goes? Which lead sources should I route, and who takes each today? Why: sets the routing rules.
6. Is there a stage whose owner is not hired yet, or one you expect to be slow on purpose? Why: a stage with nobody is a hiring question, not a stuck task.

## 4. Record

Write each answer, dated, into the `knowledge/` file it belongs to. Write `knowledge/workstreams.md` (workstream, owner, where its status shows), `knowledge/team.md`, `knowledge/pipeline-rules.md` (stages, stalled threshold, always-show size) and `knowledge/routing.md` (source to owner) as present-tense statements. Write `knowledge/pipeline.md`: each of your pipelines, its stages in order, each stage's owner, what it takes to leave each stage, its limit and its expected time, from what you read.

## 5. Draft the first page now

Follow `playbooks/weekly-growth-summary.md`. Write it in the shape of `knowledge/examples/growth-week.md`, attach it to the task, labelled "First draft, not yet reviewed". Nothing is shared and no task is created for anyone. Assign nothing, decide nothing and change nothing.

## 6. Check the routines

Setting you up switched on all three routines: `weekly-growth-summary`, `weekly-numbers` and `weekly-growth-meeting`. Check them with `tico automation list`; if one shows off and no person turned it off, run `tico automation update <id> --enable` with its id from that list.

Tell the human in one line what they do: "I will draft this page every Friday at 14:00 for you to review, refresh the growth numbers every Monday at 07:00, and run the growth meeting every Monday at 08:00." They can change or turn off any of them any time; there is nothing to approve.

Record it in `memory/decisions.md`. If they asked for a different schedule or to leave one off, adjust `knowledge/` and the routine to match (`tico automation update <id>`, with `--disable` to turn it off).

Last, run:

    tico bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the routine run; until then nothing you have runs on its own. Run it once the answers and the first result are recorded, not before.
