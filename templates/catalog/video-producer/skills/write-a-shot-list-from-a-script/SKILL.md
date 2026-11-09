---
name: write-a-shot-list-from-a-script
description: Turn an approved script into a shot list with one line per shot, naming the length, what is shown, where each visual comes from and the caption, so every claim in the script maps to a shot with a known source before any image is made.
---
# Write a shot list from a script

When to use: after the script is approved and before the storyboard or any shot is generated, recorded or drawn.

Steps:
1. Start from the approved script only. Do not add a claim, a number or a name that is not in it. If the script is
   missing a fact a shot needs, ask the owner on the task.
2. Split the script into shots. A shot is one idea a viewer can take in, usually two to six seconds of spoken line.
3. For each shot write one line: number, length in seconds, the spoken or on-screen words, what is on screen, and the
   visual's source (see the labels in `skills/label-real-generated-and-staged-footage/SKILL.md`).
4. Mark every shot that shows the product. Name the screen and the data it uses: the demo workspace with fictional
   names, or a recorded run with its date.
5. Check the total. The sum of the shot lengths should be within the brief's length. A 30-second brief with 42 seconds
   of shots is cut before any image is made.
6. Check the last shot. It shows the one next step the brief names.
7. Save it as `projects/<slug>/storyboard/shots.md` and link each shot to its script line.

Checks before you finish:
- Every claim in the script maps to at least one shot, and each shot names its source.
- No shot shows a real customer, a real person, a logo or a number that is not in the approved source files.
- Staged, sample or time-compressed shots are marked in the list, not only in the final.
- The total length fits the brief, and the last shot carries the next step.

Example (invented, for illustration): "Shot 3, 4 s. Spoken: 'Set up in one afternoon.' On screen: the setup screen in the
demo workspace, sped up 4x (labelled). Source: recorded demo run, 2026-10-28, fictional studio 'Harbour Studio'. Caption:
'Set up in one afternoon'. The claim is in the approved script, line 6, and cites the setup log for 2026-09."
