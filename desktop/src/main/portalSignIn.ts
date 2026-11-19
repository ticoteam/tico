// The "Sign in to Tico" window: the Cloud portal on a throwaway in-memory session, with no preload and no IPC.
// Every sign-in step stays in it; its done page is cancelled and read (portal.ts), then the window and its session go.
import { randomBytes } from 'node:crypto';
import { BrowserWindow, nativeTheme, type BaseWindow, type Session } from 'electron';
import { isLoopback } from './address';
import { openExternal } from './contents';
import { log } from './log';
import { doneFromPortal, isDone, parsePicks, portalPage, type Picks } from './portal';

let open: BrowserWindow | null = null;

/** The origin of the frame that started a navigation; null when none did (the app's own load) or it is gone. */
function originOf(frame: Electron.WebFrameMain | null | undefined): string | null {
  try {
    return frame && !frame.isDestroyed() ? frame.origin : null;
  } catch {
    return null;
  }
}

function secureUrl(raw: string): boolean {
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' || (url.protocol === 'http:' && isLoopback(url.hostname));
  } catch {
    return false;
  }
}

export function openPortalSignIn(
  parent: BaseWindow,
  portal: string,
  userAgent: string,
  done: (picks: Picks, from: Session) => Promise<void>,
): void {
  if (open && !open.isDestroyed()) {
    open.focus();
    return;
  }
  const win = new BrowserWindow({
    parent,
    width: 480,
    height: 720,
    title: 'Sign in to Tico',
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0B0B0B' : '#E9E9E9',
    webPreferences: {
      partition: `tico-signin-${randomBytes(8).toString('hex')}`, // no "persist:": memory only
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      webviewTag: false,
      navigateOnDragDrop: false,
    },
  });
  open = win;
  const wc = win.webContents;
  const ses = wc.session;
  ses.setUserAgent(userAgent);
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  let handled = false;
  // The address the top frame's navigation is on right now: the start, then each redirect. A redirect to the done
  // page counts only when it comes from the portal itself.
  let chain: string | null = null;
  const track = (e: Electron.Event<{ url: string; isMainFrame: boolean; isSameDocument: boolean }>) => {
    if (e.isMainFrame && !e.isSameDocument) chain = e.url;
  };
  wc.on('did-start-navigation', track);
  wc.on('did-redirect-navigation', track);

  const onNavigate = (e: Electron.Event<{ url: string; isMainFrame: boolean; initiator?: Electron.WebFrameMain | null }>, from: string | null) => {
    if (!e.isMainFrame) return;
    if (!isDone(portal, e.url)) {
      if (!secureUrl(e.url)) e.preventDefault();
      return;
    }
    e.preventDefault();
    if (handled) return;
    if (!doneFromPortal(portal, e.url, originOf(e.initiator), from)) {
      log('warn', 'refused a sign-in finish that the portal did not start');
      return;
    }
    handled = true;
    const picks = parsePicks(portal, e.url);
    void (async () => {
      try {
        if (picks && picks.servers.length) await done(picks, ses);
        else log('warn', 'the portal sign-in returned no usable companies');
      } finally {
        if (!win.isDestroyed()) win.close();
      }
    })();
  };
  wc.on('will-navigate', (e) => onNavigate(e, wc.getURL()));
  wc.on('will-redirect', (e) => onNavigate(e, chain));
  wc.setWindowOpenHandler(({ url }) => {
    openExternal(url);
    return { action: 'deny' };
  });
  wc.on('will-attach-webview', (e) => e.preventDefault());
  win.on('closed', () => {
    open = null;
    void ses.clearStorageData().catch(() => undefined);
  });
  void win.loadURL(portalPage(portal)).catch(() => log('warn', 'could not load the Tico sign-in page'));
}
