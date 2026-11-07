/* ui/health-state.ts — The Health answer the page keeps (health.js HL), for the TypeScript files that read it.
   Classic script: its globals are shared with the other files under ui/, loaded in the order index.html lists them.
   HL is reassigned, so a reader calls healthState() each time and narrows the result; it never keeps the object. */
'use strict';

// HL is null until the first Health read, and stays that way on a page where health.js did not load.
function healthState(): import('./types/api').Health | null {
  return typeof HL !== 'undefined' ? HL : null;
}
