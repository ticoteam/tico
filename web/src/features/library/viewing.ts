/* The picture `files.view` opened over the page, for FileViewerHost. Not in the address: a viewer over a task or a chat
   is a moment, and Back should not reopen it. */

export interface Viewing {
  file: string;
  version?: number;
  name: string;
}

let now: Viewing | null = null;
const subs = new Set<() => void>();

export const viewing = {
  get: (): Viewing | null => now,
  set(next: Viewing | null) {
    now = next;
    for (const f of subs) f();
  },
  subscribe(f: () => void) {
    subs.add(f);
    return () => {
      subs.delete(f);
    };
  },
};
