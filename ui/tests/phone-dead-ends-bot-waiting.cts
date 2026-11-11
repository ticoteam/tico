// Phone dead ends on a hosted company's first day (ui/tests/support/phone-rules.cts), in one starting state:
// a bot asked the owner a question and waits for the answer.
void (require('./support/phone-rules.cts') as import('./support/phone-rules.cts').Api).run('bot-waiting');
