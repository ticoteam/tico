// One way to ask "are you sure?" before a change, the same in a browser and in the desktop app. ui/index.html loads
// this file before the scripts that call it.
//
// A browser's `confirm` answers at once with true or false; the desktop app's dialog (`window.ticoConfirm`, from the
// shell's bridge script) answers later, when the person chooses. So every caller awaits the answer, and only an
// explicit true lets the change go: Cancel, a closed or refused dialog, an error, or a dialog the page moved away
// from while it was up all answer false. `ui/tests/confirm-unit.cts` fails on a `confirm(` that is not
// this one, or on an `askConfirm(` that is not awaited (TypeScript catches `if (askConfirm(...))`, not `!askConfirm`).
'use strict';

let CONFIRM_OPEN = false;

// Asks `message`; true only when the person chose OK and the page is still where it was asked. `from`, the element
// that asked (a button, a checkbox), is disabled while the question is up. A second question while one is up answers
// false at once, so a double click cannot send twice.
async function askConfirm(message: string, from?: Element | null): Promise<boolean> {
  if (CONFIRM_OPEN) return false;
  const control = from?.closest('button, input, select') as HTMLButtonElement | HTMLInputElement | HTMLSelectElement | null | undefined;
  const wasDisabled = control?.disabled ?? true;
  const route = location.hash, person = typeof S === 'undefined' ? null : S.me?.id ?? null;
  CONFIRM_OPEN = true;
  if (control && !wasDisabled) control.disabled = true;
  try {
    const native = window.ticoConfirm;
    // Awaited either way: a browser answers with a boolean, and a webview whose dialog plugin replaced `confirm`
    // answers with a promise of one.
    const answer: unknown = await (typeof native === 'function' ? native(message) : window.confirm(message));
    if (answer !== true) return false;
    // The page moved on (another route, another person signed in) while the question was up: the answer was about
    // something no longer on screen.
    return location.hash === route && (typeof S === 'undefined' ? null : S.me?.id ?? null) === person;
  } catch {
    return false;
  } finally {
    CONFIRM_OPEN = false;
    if (control && !wasDisabled) control.disabled = false;
  }
}
