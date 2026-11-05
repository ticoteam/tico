# Meet AX fixtures

All three are reduced, anonymized projections of live Chrome Meet AX trees on macOS. No real names.

`chrome-live-rows.json` comes from a read-only probe during a real call (Nov 5 2026): 591 polls 250 ms apart, with `AXManualAccessibility` set on Chrome first (its tree was empty until then). It keeps the poll times, the People-panel rows (`KV1GEc cxdMu`, AXDescription = name) as deduplicated subtrees, and the frames where the rows changed. Names are `Participant 1`; the probe's other anonymized tokens (`P38`, `P39`, …) are kept as they were. One account was signed in on two devices, so both rows share one name.

The speaking signal is the row's `IisKdb` indicator: `gjg47c` while silent, cycling `OgVli`/`wEsLMd`/`HX2H7`/`Oaajhc` about every 250 ms while that participant is heard. Speech bursts in the capture: 51 s, 61–70 s, 93–102 s, 115 s, 137–147 s.

Only the first row ever showed the indicator. That row has an extra text (`P38`) where Meet shows "(You)", and no mute control; the other device's row has "Mute …'s microphone" / "You can't unmute someone else" and never exposed a level. So the captured bursts are most likely the local person's, which the Recorder excludes (the mic is Me). A remote participant's indicator was not captured.

`chrome-observed.json` (video tiles, `dkjMxf → XEazBc → AXStaticText`) supplies names only; tiles expose no speaking state. `chrome-ended.json` is the tree after the call, with no rows or tiles.

Still to check live: a different remote person speaking with the People panel open (does their row show `IisKdb`?), the panel closed (expected: names only, no speakers), Arc, Edge, Brave and Safari, and the Accessibility prompt on an installed app. Unknown evidence keeps the audio's anonymous speaker label.
