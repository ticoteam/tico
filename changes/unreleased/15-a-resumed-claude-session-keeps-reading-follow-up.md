### Fixed
- **A resumed Claude session keeps reading follow-ups**: Claude answers a resumed session with an empty result
  before it reads the new prompt. The runner took that as the turn's answer and stopped reading follow-up
  messages for the rest of the turn; it now waits for the answer to the prompt. This applies to every resumed
  Claude turn.
