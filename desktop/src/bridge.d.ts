// The desktop bridge, `window.tico`. It exists only inside the desktop app; in a browser it is undefined, so the
// page checks for it and never sniffs the user agent. web/ imports this file as a type only (platforms.md 4.10).

export type CompanyRef = {
  id: string;
  origin: string;
  name: string;
  email: string;
  active: boolean;
  /** This entry is the company of the page that asked (so a notification click can switch back to itself). */
  self: boolean;
  signedOut: boolean;
  needs: number;
};

export type UpdateState = { state: 'none' | 'downloading' | 'ready' | 'error'; version?: string };

/** 'return': the person closed the small window and chose to go back to the call in the main window. */
export type CallWindowState = 'open' | 'closed' | 'return';

export interface TicoDesktop {
  /** Shell version, shown in Settings next to the server's. */
  version: string;
  platform: 'mac' | 'windows' | 'linux';
  companies: {
    list(): Promise<CompanyRef[]>;
    /** Shows the window and that company; `url` must be on that company's origin. */
    switchTo(id: string, url?: string): Promise<void>;
    /** Opens the local Add company page, prefilled with `server` when it is a valid address. */
    add(server?: string): Promise<void>;
    /** Asks first, then deletes the company and its stored data. */
    remove(id: string): Promise<void>;
    /** Today's orgInfo: who is signed in here, for the company row and the badge. */
    reportSelf(info: { name: string; email: string; signedOut: boolean }): void;
    onChange(cb: (all: CompanyRef[]) => void): () => void;
  };
  /** This company's "needs you" count; the shell sums every company's for the Dock badge. */
  setBadge(needs: number): void;
  callWindow: {
    open(callId: string): Promise<void>;
    /** 'return' sends the call back to the main window; without it the call is left. */
    close(ending?: 'closed' | 'return'): Promise<void>;
    setOnTop(on: boolean): Promise<void>;
    onState(cb: (s: CallWindowState) => void): () => void;
    /** The call out in the small window for this company, if any: a page that loaded again learns it is out. */
    current(): Promise<string | null>;
  };
  /** This page is about to sign out: the app drops what it holds for the person (the count, the small call window).
      Carries nothing; the page redirects once it resolves. */
  signingOut(): Promise<void>;
  app: {
    updateState(): Promise<UpdateState>;
    /** Refused while a call is live. */
    restartToUpdate(): Promise<void>;
    checkForUpdates(): Promise<void>;
    showLog(): Promise<void>;
    logTail(): Promise<{ version: string; lines: string[] }>;
    /** Reads, or with `on` sets, open at login. Resolves to the current value. */
    openAtLogin(on?: boolean): Promise<boolean>;
  };
  /** Today's visualOrigin: the company's files origin for user-made pages; allowed in iframes only, never top level. */
  setFilesOrigin(origin: string): void;
}

declare global {
  interface Window {
    tico?: TicoDesktop;
  }
}
