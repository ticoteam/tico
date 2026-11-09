# AI UGC

Short creator-style clips with a made-up person. The person is an AI character every time.

1. Never use a real person's photo, name or voice, and no lookalike of a real person. Label the clip AI-generated on
   screen and on the task.
2. Make the character once, in the storyboard stage, from a reference image if the owner gave one:
   `hub gateway image "PROMPT" --category character --ref <reference.png> --out projects/<slug>/storyboard/character.png`.
   The owner approves the character before any clip uses it.
3. Image-to-video needs the team's own video provider key. Without it, use the approved storyboard frames with
   motion graphics instead (playbooks/motion-graphics.md).
4. Each clip's sidecar keeps its prompt, the character file as `parents`, the model and `cost_usd`.
5. Scripts make no health, income, before-and-after or testimonial claim unless the owner has the source for it.
6. The product on screen is added in post from the real interface, never generated. A generated interface is not
   the product.
