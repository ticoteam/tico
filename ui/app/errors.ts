/* ui/app/errors.ts — The message a person reads for a thrown value.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   TypeScript: npm run build:ui compiles it to build/ui/app/errors.js, which the page runs in this file's place. */
'use strict';

// The same text the pages have always drawn: an error's own message when it has one (`e?.message || fallback`),
// else the fallback. A string thrown as the error is not a message here; it gets the fallback like any other value.
function errorMessage(error: unknown, fallback: string): string {
  const message = typeof error === 'object' && error !== null && 'message' in error ? error.message : undefined;
  return typeof message === 'string' && message ? message : fallback;
}
