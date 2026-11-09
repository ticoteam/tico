---
name: route-a-sensitive-question
description: Decide whether a question must go to a named human untouched, because it touches pay, leave, discipline, performance, health, a complaint, harassment, immigration or a termination, and write the hand-off task with the question copied exactly and no opinion, so the person who owns it can answer.
---
# Route a sensitive question

When to use: before you answer or draft anything for a question from a new hire, a manager or an employee, and whenever a message
seems to carry distress or risk.

Steps:
1. Read `knowledge/hand-offs.md`. Find the human for the topic it names. If the topic is not listed, route to the human named as the
   default there, and say the topic was not listed.
2. Check the topic. If the question touches pay, leave entitlements, discipline, performance, health, a complaint, harassment,
   immigration or a termination, or asks what a specific person is owed or allowed, it is routed. Do not answer, even partly.
3. If the message seems to show distress or risk to someone, route it at once and stop. Do not reply to the person about it.
4. Create the hand-off as a task for the named human (`hub task create --owner <person>`), with the question copied exactly as
   received, the date it arrived, and the reference of the person who asked. Add no opinion, no summary and no suggested answer.
5. Tell the requester in one line that the question has gone to the named human. Give no other detail.
6. Keep nothing else about the question. Delete any copy you made in the working tree at the end of the run.
7. Record the hand-off in `state.md` by topic and date, without the question's content.

Checks before you finish:
- The question on the task is copied exactly, not paraphrased.
- The reply to the requester names only the human, not the content.
- No policy or outcome was stated, even as "usually".
- The hand-off is on a task with an owner, not in a chat message.

Example (invented, for illustration): "A question about a leave request came in on 2026-10-14 from a new hire. The topic is leave in
hand-offs.md, owner: the HR owner. Created one task for the HR owner with the question copied as received, and told the new hire
it had gone to the HR owner. No answer given. Recorded in state.md as 'leave, 2026-10-14, routed'."
