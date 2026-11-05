# Meet AX fixtures

`chrome-observed.json` is a reduced projection of a live Chrome Meet AX tree inspected on macOS. Names are fictional. The observed name path is `dkjMxf → … → XEazBc → … → AXStaticText.AXValue`. `atLQQ` and `tC2Wod` visual decorations were exposed through `AXDOMClassList`, without an explicit semantic speaking label. Their presence, mute state, pin state and layout are deliberately **not** interpreted as speech. `chrome-ended.json` is a direct, anonymized AX capture taken after that inspection, when the name containers were no longer present.

`gallery.json`, `presenting.json` and `others.json` are synthetic parser contracts based on Google's [documented Meet interface](https://support.google.com/meet/answer/15738543). The explicit speaking labels in them are test inputs, **not captured or documented AX attribute guarantees**. They exercise alignment and privacy behavior if a browser exposes such a label. They do not establish that passive speaker naming works in any browser.

Chrome was the only running supported browser during inspection. Arc, Edge, Brave and Safari still need real AX captures. Google documents a keyboard command to announce the speaker; this adapter never invokes it, changes Meet controls, opens the people panel, enables captions or reads caption/chat/presentation text.

A release needs a live recording with two remote voices and the local microphone in each browser: confirm names against actual speaker changes in gallery, presentation, pinned/spotlight and others layouts; inspect quiet gaps and overlapping speech; verify the permission prompt on an installed app with AX denied; verify Stop ends AX polling. A passive, explicit speaking signal remains unverified. Unknown evidence keeps the audio's anonymous speaker label.
