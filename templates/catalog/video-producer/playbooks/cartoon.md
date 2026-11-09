# Cartoon

Characters and scenes drawn as SVG or CSS, animated with motion.

1. Keep characters simple and on model. Make a character sheet first, as SVG or with `--category graphic`, and get it
   approved in the storyboard stage before any shot uses it.
2. Build characters as SVG in `projects/<slug>/shots/`, and animate them with CSS or the seek contract
   (playbooks/motion-graphics.md), so every frame renders the same.
3. Gateway images (`--category graphic` for flat art, `--category photo` for backgrounds only) are stills. Animate them in
   the composition; do not generate motion for them.
4. Render and check as in playbooks/motion-graphics.md, with a sidecar for each file.
