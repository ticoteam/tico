# Storyboard

One image per shot, in one sheet the owner can read in a minute.

1. From the approved script, write `projects/<slug>/storyboard/shots.md` with `skills/write-a-shot-list-from-a-script/SKILL.md`:
   one line per shot with its number, its length, what is on screen, the words spoken or shown, and the shot's source class.
2. One image per shot, on the best model: `tico gateway image "<shot prompt>" --model high --count 1 --size 1280x720
   --out projects/<slug>/storyboard/s01.png`.
   The prompt says it is a storyboard sketch, not final art. The sidecar keeps the prompt, model and cost.
3. Only when the owner wants a choice for a shot, make three (`--count 3`) and ask which:
   `tico task ask <task> "Which frame for shot 1?" --choice 1 --choice 2 --choice 3`.
4. Build the sheet: `projects/<slug>/storyboard/sheet.html`, each frame with its line and length, and "Storyboard, not
   final" at the top.
5. Put the sheet up for review. The storyboard mover decides it: `python3 software/pipeline.py next projects/<slug>`
   prints the exact command.
