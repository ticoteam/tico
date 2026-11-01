# Storyboard

One image per shot, in one sheet the owner can read in a minute.

1. From the approved script, write `projects/<slug>/storyboard/shots.md` with `skills/write-a-shot-list-from-a-script/SKILL.md`:
   one line per shot with its number, its length, what is on screen, the words spoken or shown, and the shot's source class.
2. One image per shot: `hub gateway image "<shot prompt>" --category storyboard --count 1 --size 1280x720 --out
   projects/<slug>/storyboard/s01.png`. The prompt says it is a storyboard sketch, not final art. The sidecar keeps the
   prompt, model and cost.
3. When the owner wants a choice, use `--count 3` and put the review with `--pick`.
4. Build the sheet: `projects/<slug>/storyboard/sheet.html`, each frame with its line and length, and "Storyboard, not
   final" at the top.
5. Put the sheet up for review. The storyboard mover decides it: `python3 software/pipeline.py next projects/<slug>`
   prints the exact command.
