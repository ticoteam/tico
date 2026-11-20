/* The picture `files.view` opened over the page, or any file `files.preview` opened full screen (a chat's), for
   FileViewerHost. Not in the address: a viewer over a task or a chat is a moment, and Back should not reopen it. */

export interface Viewing {
  file: string;
  version?: number;
  name: string;
  /** Shown whatever it is, in the full-screen layer (`files.preview`); without it only a picture shows. */
  full?: {
    mime: string;
    kind?: string;
    size?: number | null;
    /** A message's own file (an attachment), read from this address: it is not one of the library's files. */
    url?: string;
  };
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
