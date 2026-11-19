/* What went wrong in this browser since it opened, for diagnostics: only the kind, the file's own name, the line and
   column, and a count. No error message, rejection value, address, stack text or anything typed enters it (old
   support.ts browserFailure). The newest twenty are kept. */

export interface BrowserFailure {
  kind: 'error' | 'unhandledrejection';
  at: string;
  file: string;
  line: number;
  column: number;
  count: number;
}

const failures: BrowserFailure[] = [];
const clamp = (n: number | undefined) => Math.max(0, Math.min(10_000_000, n || 0));

export function noteFailure(kind: BrowserFailure['kind'], event?: { filename?: string; lineno?: number; colno?: number }): void {
  let file = '';
  try {
    file = new URL(event?.filename || '', location.href).pathname.split('/').pop() ?? '';
  } catch {
    // no address: no file
  }
  const item: BrowserFailure = {
    kind,
    at: new Date().toISOString(),
    file: /^[A-Za-z0-9_.-]+\.(?:js|ts|tsx|mjs)$/.test(file) ? file : '',
    line: clamp(event?.lineno),
    column: clamp(event?.colno),
    count: 1,
  };
  const prior = failures.find((x) => x.kind === item.kind && x.file === item.file && x.line === item.line && x.column === item.column);
  if (prior) {
    prior.at = item.at;
    prior.count = Math.min(1_000_000, prior.count + 1);
    return;
  }
  failures.push(item);
  if (failures.length > 20) failures.shift();
}

/** A copy: what goes with the next capture. */
export const browserFailures = (): BrowserFailure[] => failures.map((f) => ({ ...f }));

if (typeof window !== 'undefined') {
  window.addEventListener('error', (e) => noteFailure('error', e));
  window.addEventListener('unhandledrejection', () => noteFailure('unhandledrejection'));
}
