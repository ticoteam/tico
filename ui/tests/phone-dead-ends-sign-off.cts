// Phone dead ends on a hosted company's first day (ui/tests/support/phone-rules.cts), in one starting state:
// a task waits on the owner's OK.
void (require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api).run('sign-off');
