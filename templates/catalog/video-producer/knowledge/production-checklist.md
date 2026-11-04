# Production checklist

Every final passes every line here before the owner sees it. `software/video/qa.mjs` reports the loudness and the
contact sheet; a person looks at the rest.

- **Captions read with the sound off.** Each line is on screen long enough to read (at least two seconds), in high
  contrast, burned into the picture.
- **Safe zones.** Text and faces stay clear of the edges. For 9:16, keep the top 14% and bottom 20% free of text.
- **Loudness -14 LUFS**, true peak under -1 dBTP. `qa.mjs` reports both.
- **No decoration in place of content.** No small label above a headline, no tagline under every title. Each line says
  something a viewer can use.
- **Exact things come from the source.** UI, prices, logos and names are added in post from the source files, never
  generated. Check each one against its source.
- **People are labelled.** AI-generated people say so on screen and on the task. No real person's face, voice or name
  without written agreement.
- **Every claim is in the approved script,** with a source the owner can name.
- **Demo is labelled.** Demo data says it is demo data. Sped-up or compressed time says so.
- **Every shot has its class.** Real, recorded demo, compressed or sample, generated or drawn, with the label visible in the final frame.
- **Platform disclosure is answered truthfully.** Where a platform asks whether a realistic video is synthetic, the answer matches the classes in the cut. Check the platform's current help page.
- **Generated clips are finished, not shipped raw.** Assembled and finished in an editor, with the finish step recorded
  in the file's sidecar. A generator is never asked to render UI or printed text.
- **One next step.** The last shot shows the step the brief names, and nothing else competes with it.
- **Length fits the brief.** The cut is within the brief's length; a longer cut is shortened before review, not after.
- **Every part is traceable.** Each file in the cut has an approved version or its own sidecar.
- **The file is the one approved.** Same version and digest as the one on the final task.

## Sources
- Owner's standard for finals, from the setup answers in `state.md`.
- Platform disclosure rules for realistic synthetic video, read 2026-11-01 from news and third-party guides; check each platform's own help page before relying on them.
