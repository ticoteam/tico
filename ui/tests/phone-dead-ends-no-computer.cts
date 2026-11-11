// Phone dead ends on a hosted company's first day (ui/tests/support/phone-rules.cts), in one starting state:
// setup finished, the bots on the shared cloud computer, and no computer online yet.
void (require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api).run('no-computer');
