// The "Sign in to Tico" window: the Cloud portal on a throwaway in-memory session, with no preload and no IPC.
// Every sign-in step stays in it; its done page is cancelled and read (portal.ts), then the window and its session go.
import { randomBytes } from 'node:crypto';
import { BrowserWindow, nativeTheme, type BaseWindow, type Session } from 'electron';
import { isLoopback } from './address';
import { openExternal } from './contents';
import { log } from './log';
import { isDone, parsePicks, portalPage, type Picks } from './portal';

let open: BrowserWindow | null = null;

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

  const onNavigate = (e: Electron.Event<{ url: string; isMainFrame: boolean }>) => {
    if (!e.isMainFrame) return;
    if (!isDone(portal, e.url)) {
      if (!secureUrl(e.url)) e.preventDefault();
      return;
    }
    e.preventDefault();
    if (handled) return;
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
  wc.on('will-navigate', onNavigate);
  wc.on('will-redirect', onNavigate);
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
