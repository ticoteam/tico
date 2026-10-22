### Fixed
- **A bot's answer that finished while its computer slept now arrives.** When a computer was asleep or paused past a
  turn's lease, the runner woke to the hub refusing the lease and dropped the answer the model had already finished,
  so the person saw "No reply" and the run ended interrupted. The runner now reads out what the model already said
  for up to ten seconds after that refusal and hands it in completed; the hub settles it as before (Dev 242). A model
  still working is not waited for, and nothing new starts in that window. A person's Stop still stands: a late answer
  from a stopped turn is kept on its run, not posted.
