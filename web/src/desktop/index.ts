import { useSyncExternalStore } from 'react';

/* Its own folder, below the shell, so a feature (Settings' rows, the call page) can read it too.
   The desktop app's bridge, `window.tico` (desktop/src/bridge.d.ts, mirrored here so web/ imports nothing from desktop/).
   It exists only inside the desktop app; in a browser every accessor here gives null and every report does nothing.
   The page checks for the bridge and never sniffs the user agent (platforms.md 4.10). */

export type DesktopPlatform = 'mac' | 'windows' | 'linux';
export type UpdateState = { state: 'none' | 'downloading' | 'ready' | 'error'; version?: string };
/** 'return': the person closed the small window and chose to go back to the call in the main window. */
export type CallWindowState = 'open' | 'closed' | 'return';
export type CompanyRef = { id: string; origin: string; name: string; email: string; active: boolean; self: boolean; signedOut: boolean; needs: number };

export interface TicoDesktop {
  version: string;
  platform: DesktopPlatform;
  companies: {
    list(): Promise<CompanyRef[]>;
    switchTo(id: string, url?: string): Promise<void>;
    add(server?: string): Promise<void>;
    remove(id: string): Promise<void>;
    reportSelf(info: { name: string; email: string; signedOut: boolean }): void;
    onChange(cb: (all: CompanyRef[]) => void): () => void;
  };
  setBadge(needs: number): void;
  /** Optional: an older app has no such call. Whether this page is in a call; "Restart to update" waits for it. */
  reportCall?(live: boolean): void;
  callWindow: {
    open(callId: string): Promise<void>;
    close(ending?: 'closed' | 'return'): Promise<void>;
    setOnTop(on: boolean): Promise<void>;
    onState(cb: (s: CallWindowState) => void): () => void;
    /** Optional: an older app has no such call (the page comes from the server, the shell updates on its own). */
    current?(): Promise<string | null>;
  };
  signingOut?(): Promise<void>;
  app: {
    updateState(): Promise<UpdateState>;
    restartToUpdate(): Promise<void>;
    checkForUpdates(): Promise<void>;
    showLog(): Promise<void>;
    logTail(): Promise<{ version: string; lines: string[] }>;
    openAtLogin(on?: boolean): Promise<boolean>;
  };
  setFilesOrigin?(origin: string): void;
}

/** The bridge, or null in a browser. */
export const desktop = (): TicoDesktop | null =>
  (typeof window !== 'undefined' && (window as { tico?: TicoDesktop }).tico) || null;

/** Inside the older desktop app (the Tauri shell, app/): its bridge is `window.ticoNative`, and it has no `window.tico`.
    The page there gets none of the desktop features; it is only told a new app is ready (features/notices DesktopMove). */
export const inOldDesktopApp = (): boolean =>
  typeof window !== 'undefined' && !desktop() && (window as { ticoNative?: unknown }).ticoNative != null;

/** The desktop preload marks `<html data-desktop>` before the first paint; this covers a page the preload marked late. */
export function markDesktop(): void {
  const t = desktop();
  const root = document.documentElement;
  if (t && !root.dataset.desktop) root.dataset.desktop = t.platform;
}

/* ---------------------------------------------------------------- Settings' desktop rows */

/** What Settings shows for the desktop app: its version, updates, the log and open at login. */
export interface DesktopApp {
  version: string;
  platform: DesktopPlatform;
  updateState(): Promise<UpdateState>;
  /** Refused while a call is live. */
  restartToUpdate(): Promise<void>;
  checkForUpdates(): Promise<void>;
  showLog(): Promise<void>;
  logTail(): Promise<{ version: string; lines: string[] }>;
  /** Reads, or with `on` sets, open at login; resolves to the current value. */
  openAtLogin(on?: boolean): Promise<boolean>;
}

/** The desktop app's own controls for Settings, or null in a browser (no rows then). */
export function desktopApp(): DesktopApp | null {
  const t = desktop();
  if (!t) return null;
  const a = t.app;
  return {
    version: t.version,
    platform: t.platform,
    updateState: () => a.updateState(),
    restartToUpdate: () => a.restartToUpdate(),
    checkForUpdates: () => a.checkForUpdates(),
    showLog: () => a.showLog(),
    logTail: () => a.logTail(),
    openAtLogin: (on) => (on === undefined ? a.openAtLogin() : a.openAtLogin(on)),
  };
}

/* ---------------------------------------------------------------- who is signed in, the badge, the files origin */

export interface DesktopSelf {
  /** null while the person is not known yet: nothing is reported */
  signedIn: boolean | null;
  /** the company's name (config) */
  name: string;
  email: string;
  /** this company's Needs you count; undefined until it is read (the badge waits) */
  needs?: number;
  /** the company's files origin for user-made pages (config.visual_origin) */
  filesOrigin?: string;
}

let told = { self: '', badge: -1, files: '', call: false };
/** For tests: forget what was reported. */
export const resetDesktopReports = () => {
  told = { self: '', badge: -1, files: '', call: false };
};

/** Tells the desktop app whether this page is in a call (joining, live or reconnecting), so "Restart to update" waits
    for a call in any company, on screen or not. Sent only when it changed; nothing in a browser or an older app. */
export function reportCallToDesktop(live: boolean): void {
  const t = desktop();
  if (!t?.reportCall || live === told.call) return;
  told.call = live;
  t.reportCall(live);
}

/** Tells the desktop app who is signed in here, this company's count for the Dock badge, and its files origin.
    Each is sent only when it changed. Signed out keeps the company's stored name and email and clears the count. */
export function reportToDesktop(s: DesktopSelf): void {
  const t = desktop();
  if (!t || s.signedIn === null) return;
  const needs = !s.signedIn ? 0 : s.needs === undefined ? null : Math.max(0, Math.floor(s.needs) || 0);
  if (needs !== null && needs !== told.badge) {
    told.badge = needs;
    t.setBadge(needs);
  }
  if (s.signedIn && s.filesOrigin && s.filesOrigin !== told.files && t.setFilesOrigin) {
    told.files = s.filesOrigin;
    t.setFilesOrigin(s.filesOrigin);
  }
  const key = s.signedIn ? `in|${s.name}|${s.email}` : 'out';
  if (key === told.self || (s.signedIn && (!s.name || !s.email))) return;
  told.self = key;
  if (s.signedIn) return t.companies.reportSelf({ name: s.name, email: s.email, signedOut: false });
  // signed out: the row keeps the name and email it had, so the person sees which account to sign back in to
  void t.companies.list().then(
    (all) => {
      const me = all.find((c) => c.self);
      t.companies.reportSelf({ name: me?.name || s.name, email: me?.email ?? '', signedOut: true });
    },
    () => t.companies.reportSelf({ name: s.name, email: '', signedOut: true }),
  );
}

/* ---------------------------------------------------------------- the small call window (platforms.md 4.6) */

/** This page is the small call window: `/call/<id>?window=1`. It draws the call page alone. */
export const isCallWindow = (pathname: string, search: string): boolean =>
  /^(\/next)?\/call\/[^/]+\/?$/.test(pathname) && new URLSearchParams(search).get('window') === '1';

export interface CallWindowNow {
  /** the call shown in the small window, while it is out */
  id: string | null;
  open: boolean;
}
let callWin: CallWindowNow = { id: null, open: false };
const callWinListeners = new Set<() => void>();
let returnTo: ((id: string) => void) | null = null;
let offState: (() => void) | null = null;

function setCallWin(next: CallWindowNow) {
  callWin = next;
  for (const l of callWinListeners) l();
}

/** Listens to the small window once: 'open', 'closed' (left the call there) and 'return' (go back to it here,
    which `onReturn` does). Returns the stop. */
export function watchCallWindow(onReturn: (id: string) => void): () => void {
  returnTo = onReturn;
  const t = desktop();
  if (!t || offState) return () => undefined;
  offState = t.callWindow.onState((st) => {
    const id = callWin.id;
    if (st === 'open') return setCallWin({ id, open: true });
    setCallWin({ id: null, open: false });
    if (st === 'return' && id) returnTo?.(id);
  });
  // A page that loaded again (a link from the small window, a deep link) learns which call is out there.
  void t.callWindow.current?.().then(
    (id) => {
      if (id && !callWin.open) setCallWin({ id, open: true });
    },
    () => undefined,
  );
  return () => {
    offState?.();
    offState = null;
    returnTo = null;
  };
}

/** Opens a call in the small window (only one at a time; the main process replaces an older one). */
export async function openCallWindow(id: string): Promise<void> {
  const t = desktop();
  if (!t) throw new Error('only the desktop app opens a call in its own window');
  callWin = { id, open: callWin.open };
  await t.callWindow.open(id);
}

/** The small window's own controls: keep it above other windows, or close it. */
export const callWindowControls = () => {
  const t = desktop();
  return t ? { setOnTop: (on: boolean) => t.callWindow.setOnTop(on), close: (ending?: 'closed' | 'return') => t.callWindow.close(ending) } : null;
};

/** Whether a call is out in the small window, and which (for the main window's call bar: "In the small window"). */
export function useCallWindow(): CallWindowNow {
  return useSyncExternalStore(
    (cb) => {
      callWinListeners.add(cb);
      return () => callWinListeners.delete(cb);
    },
    () => callWin,
  );
}

/** Where a link pressed in the small window goes. It never moves that window off its call: the app's own pages open in
    the main window, outside https pages and the app's files in the person's browser (as from the main window), and
    anything else (mail, other schemes, in-page anchors) is not followed. */
export type SmallWindowLink = { kind: 'app' | 'outside' | 'file'; url: string } | { kind: 'none' };

export function smallWindowLink(href: string, origin: string): SmallWindowLink {
  if (!href || href.startsWith('#')) return { kind: 'none' };
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return { kind: 'none' };
  }
  if (url.origin === origin) return { kind: url.pathname.startsWith('/api/') ? 'file' : 'app', url: url.href };
  return url.protocol === 'https:' ? { kind: 'outside', url: url.href } : { kind: 'none' };
}

/** Follows a link pressed in the small window. The app's own page: the main window loads it and comes forward. The
    rest: a new window, which the desktop app hands to the browser (https only) and never opens itself. */
export async function followFromSmallWindow(link: SmallWindowLink): Promise<void> {
  if (link.kind === 'none') return;
  if (link.kind !== 'app') {
    window.open(link.url, '_blank', 'noopener,noreferrer');
    return;
  }
  const t = desktop();
  const me = t && (await t.companies.list()).find((c) => c.self);
  if (t && me) await t.companies.switchTo(me.id, link.url);
}

/* ---------------------------------------------------------------- sign-out */

/** Tells the desktop app this page is signing out, so it drops what it holds for the person (the count, the small
    call window) before the page leaves. Nothing in a browser or an older app; never holds sign-out up for long. */
export async function tellDesktopSigningOut(): Promise<void> {
  const t = desktop();
  if (!t?.signingOut) return;
  await Promise.race([t.signingOut().catch(() => undefined), new Promise((done) => setTimeout(done, 1000))]);
}
