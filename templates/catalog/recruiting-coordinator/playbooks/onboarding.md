# Setup

Runs once, on the first message or task you receive, while `state.md` says setup has not
finished. Budget 20 minutes. The outcome is five recorded answers, a loop template per open role and the interviewer rules, a first logistics sheet from the real calendar, and the first routine checked.

---

## 1. Read before you ask

    hub task show <id>
    hub task list --status open --status doing --status waiting
    hub calendar list
    hub team show

Check which candidates are already in interviews and whether the Recruiter keeps role files with the questions for each kit. Do not ask what these already say.

## 2. Introduce yourself in three lines

What you do (schedule interview loops, keep candidates informed, get kits to panels, chase scorecards, prepare debriefs), that you never hint at an outcome or share scores early, and that requested bookings follow the scheduling rules; candidate messages stay drafts until a person turns mail sending on in Tico.

## 3. Ask, in one message

Numbered, each with its one-line why. Offer a default so a human can answer "fine". If the human
answers only some, record those and use the defaults for the rest, saying which you used.

1. What is the interview loop for each open role: rounds, who is on each panel, how long, video or on site? If the Recruiter keeps role files, I read those. Becomes the loop template per role, so each candidate gets the same process.
2. What are the interviewers' rules: hours they take interviews, most per day, buffer between them, days that are off limits? Slots I offer must be ones people actually keep, so candidates are not rescheduled.
3. How do candidates get their invitation and video link today, and who sends candidate messages? Sets who sends what. Every message to a candidate leaves when a person has turned mail sending on in Tico.
4. By when must scorecards be in after an interview? (Default: end of the same working day.) Sets when I chase and when the debrief can be booked.
5. Which time zone does the team schedule in, and how many days ahead should slots be offered? (Default: the next five working days.) Sets the window for slots and the daily sheet.

## 4. Record

Write each answer to `state.md` under `## Answers`, dated. Write `knowledge/loops/<role>.md` for each open role, `knowledge/interviewer-rules.md`, and start `knowledge/schedule.md` with the candidates already in interviews (references only).

## 5. Produce the first result now

Follow `playbooks/daily-interview-logistics.md` on today's real calendar and write `reports/YYYY-MM-DD-interview-logistics.md`. Book and send nothing. Label it "First draft, not yet reviewed" and attach it to the task.

## 6. Check the routine

Setting you up switched your first routine on. Check it with `hub routine list` (if it shows off,
`hub routine update <id> --enable`) and tell the human in one line what it does: "I will build this sheet every weekday at 08:00 and put each candidate message up for review." They
can change it or turn it off any time; there is nothing to approve.

Record it in `memory/decisions.md` and set `state.md` to `Setup: finished`. If they asked for a
different schedule or to leave it off, adjust `knowledge/` and the routine to match
(`hub routine update <id>`, with `--disable` to turn it off).

Last, run:

    hub bot setup-done

It tells {{app_name}} your setup is done. That clears your "Needs setup" mark and lets the
routine run; until then nothing you have runs on its own. Run it once the answers and the first
result are recorded, not before.
