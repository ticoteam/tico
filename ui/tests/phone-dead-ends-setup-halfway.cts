// Phone dead ends on a hosted company's first day (ui/tests/support/phone-rules.cts), in one starting state:
// setup not finished: the owner is halfway through it.
void (require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api).run('setup-halfway');
