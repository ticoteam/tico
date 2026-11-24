/* Which build of the app this page runs, and which one the server serves now. A release replaces the build's files and
   every page is its own chunk, so a tab opened before it asks for files that are gone. Each live stream's `ready`
   names the server's build (backend/web_routes.py build_name); once that is not this page's, the next page change is
   a full load (features/notices/recovery.ts). */

let served: string | null = null;

/** This page's build: its entry script (`/next/assets/index-<hash>.js`). None from the dev server. */
export function thisBuild(): string | null {
  const entry = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/next/assets/"]');
  return entry ? new URL(entry.src, location.href).pathname : null;
}

/** The server's build, as a stream's `ready` names it. */
export function sawBuild(name: unknown): void {
  if (typeof name === 'string' && name) served = name;
}

/** The server serves another build than this page loaded. */
export function behind(): boolean {
  const mine = thisBuild();
  return served !== null && mine !== null && served !== mine;
}

/** Tests only: no stream has named a build yet. */
export function resetBuild(): void {
  served = null;
}
