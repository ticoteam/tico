// Phone dead ends on a hosted company's first day (ui/tests/support/phone-rules.cts), in one starting state:
// the company out of credit (cloud access frozen).
void (require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api).run('no-credit');
