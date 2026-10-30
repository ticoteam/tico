# Motion graphics

For explainers, kinetic type and product callouts, built as HTML and rendered frame by frame.

1. Start from the approved script: one beat per shot, each with its on-screen text and its length in milliseconds.
2. Build one composition per shot: `projects/<slug>/shots/<name>.html`, at the output size (1920x1080 or 1080x1920).
   Use the fonts the brand guide names, or the Noto fonts the runner ships.
3. Follow the seek contract. Set `window.__timeline = { duration_ms, fps, seek(ms) }`, where `seek` sets every animated
   state for time `ms` (synchronously, or returning a promise that resolves when it is ready). Without it, the renderer
   pauses CSS and Web Animations and sets `--t` (milliseconds) on `:root` for variable-driven motion. Never use
   `Date.now()`, a `requestAnimationFrame` loop or random values: the same frame must render the same every time.
4. Render: `node software/video/render.mjs projects/<slug>/shots/<name>.html --out projects/<slug>/shots/<name>.mp4
   --size 1920x1080 --fps 30`.
5. Write the sidecar: `kind` video, `tool` render.mjs, `parents` the HTML and the script version it follows.
6. Check the frames against `knowledge/production-checklist.md`. The shots stage is self-moved, so the cut uses the
   files as they are once they pass.
