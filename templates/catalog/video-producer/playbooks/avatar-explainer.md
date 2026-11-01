# Avatar explainer

A talking head made from an approved script, with the screen or motion graphics picture-in-picture.

1. Needs the HeyGen grant. Without it, stop and tell the owner on the task: a person can record the talking head, or
   the video can be a motion graphics explainer. Never fake a talking head with a still image.
2. Use the approved script word for word. A changed word is a new script version and needs its own approval.
3. Create the avatar video through the HeyGen API with the bot's own key (`HEYGEN_API_KEY`). Use a stock avatar, or one
   the person has agreed to in writing. Label it AI-generated on screen and on the task.
4. Save it as `projects/<slug>/shots/avatar.mp4` with its sidecar: `model` (avatar and voice ids), `cost_usd` from the
   HeyGen response, `parents` the script version, `tool` heygen.
5. Voice from ElevenLabs only with its grant, under the same rules.
6. Compose in the cut: `node software/video/edit.mjs projects/<slug>/cut/edit.json --out projects/<slug>/cut/cut.mp4`,
   with the avatar as picture-in-picture over the screen recording or motion graphics, and captions burned in.
