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

// The parts of a thrown value, read with checks instead of casts. api.js throws an Error that carries `body` (the failure's
// JSON answer) and `status` (its HTTP status). Any other thrown value, or a part that is missing, gives the empty answer.
function isErrorRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

// body.error of a thrown ApiFailure: an object with a code, or a message string, which has no code. {} when there is none.
function errorBody(error: unknown): Record<string, unknown> {
  if (typeof error !== 'object' || error === null || !('body' in error)) return {};
  const { body } = error;
  if (typeof body !== 'object' || body === null || !('error' in body)) return {};
  const { error: detail } = body;
  return isErrorRecord(detail) ? detail : {};
}

// body.error.code of a thrown ApiFailure, or '' when it has none.
function errorCode(error: unknown): string {
  const code = errorBody(error).code;
  return typeof code === 'string' ? code : '';
}

// The HTTP status of a thrown ApiFailure, when it has one.
function errorStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null || !('status' in error)) return undefined;
  return typeof error.status === 'number' ? error.status : undefined;
}
