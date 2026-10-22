### Fixed
- **Questions that close on their own leave every count right.** The hourly sweep closes each leftover question on
  its own, so one that fails stays for the next sweep instead of undoing the rest. When questions close in bulk (a
  task above ends, the hourly sweep, the weekday "Still needed?" batch), the bot that asked them has its "needs a
  person" count updated at once, and "Still needed?" keeps a task waiting on a person while another question to them
  is still open on it. A stuck task gets no extra stall wake between its retry and its report to BotOps, and BotOps's
  "Find why" task says how many wake-ups it really got that day.
