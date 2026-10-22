### Fixed
- **A company out of Tico credit no longer holds its bots off Tico models for 30 minutes**: when Tico HQ's Gateway
  pauses Tico models for want of credit (`out_of_credit`), the bot says "Tico models are paused: this company is out
  of credit. Your bots use their other models until credit is added", runs on its fallback meanwhile, and asks the
  Gateway again after a minute, so it is back on Tico models within about a minute of credit being added. It used to
  show the Gateway's raw 429 JSON and hold the Tico login like a provider's usage limit.
