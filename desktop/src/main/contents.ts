// What every page the app opens from a company shares: its web preferences, its session's rules (user agent,
// permissions, screen share) and the navigation guard (platforms.md 4.3).
import * as path from 'node:path';
import {
  BaseWindow,
  clipboard,
  desktopCapturer,
  dialog,
  Menu,
  shell,
  systemPreferences,
  type DesktopCapturerSource,
  type DisplayMediaRequestHandlerHandlerRequest,
  type Session,
  type WebContents,
  type WebPreferences,
} from 'electron';
import { contextMenuItems, type ContextAction } from './contextMenu';
import { decideNavigation, decideNewWindow, signInOver, type NavContext, type NavDecision } from './navigation';
import { permissionAllowed } from './permissions';
import { log } from './log';

export const PRELOAD = path.join(__dirname, '..', 'preload.js');

/** A company page's preferences. `origin` goes to the preload, which gives `window.tico` only to pages on it. */
export function pageWebPreferences(partition: string, origin: string, preloadArgs: string[]): WebPreferences {
  return {
    partition,
    preload: PRELOAD,
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    nodeIntegrationInWorker: false,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
    navigateOnDragDrop: false,
    safeDialogs: true,
    spellcheck: true,
    backgroundThrottling: true,
    additionalArguments: [...preloadArgs, `--tico-origin=${origin}`],
  };
}

const hardened = new WeakSet<Session>();

/** A company's session: plain-browser user agent, its own origin's permissions only, no device access. */
export function hardenSession(ses: Session, origin: string, userAgent: string): void {
  if (hardened.has(ses)) return;
  hardened.add(ses);
  ses.setUserAgent(userAgent);
  ses.setPermissionRequestHandler((_wc, permission, callback, details) => {
    const mediaTypes = 'mediaTypes' in details ? details.mediaTypes : undefined;
    const allowed = permissionAllowed(permission, { requestingUrl: details.requestingUrl, isMainFrame: details.isMainFrame, mediaTypes }, origin);
    if (!allowed) {
      log('info', `permission ${permission} refused for a page of ${origin}`);
      callback(false);
      return;
    }
    if (permission !== 'media' || process.platform !== 'darwin') {
      callback(true);
      return;
    }
    // macOS asks the person once per app; the page's own request is the Join or camera tap.
    void (async () => {
      const types = mediaTypes ?? [];
      const mic = !types.includes('audio') || (await systemPreferences.askForMediaAccess('microphone'));
      const cam = !types.includes('video') || (await systemPreferences.askForMediaAccess('camera'));
      callback(mic && cam);
    })();
  });
  ses.setPermissionCheckHandler((_wc, permission, requestingOrigin, details) =>
    permissionAllowed(
      permission,
      {
        requestingUrl: details.requestingUrl ?? requestingOrigin,
        isMainFrame: details.isMainFrame,
        mediaTypes: details.mediaType && details.mediaType !== 'unknown' ? [details.mediaType] : [],
      },
      origin,
    ),
  );
  ses.setDevicePermissionHandler(() => false);
  // Screen share: the system picker (macOS 15 and later). Where it is not available (macOS 14, Windows, Linux) this
  // handler runs and the person picks a screen in a small dialog; nothing is shared unless they pick one.
  ses.setDisplayMediaRequestHandler(
    (request, callback) => {
      pickScreen(request, origin).then(
        (video) => callback(video ? { video } : null),
        () => callback(null),
      );
    },
    { useSystemPicker: true },
  );
}

/** Our own small picker (platforms.md 4.3): whole screens from desktopCapturer, for the company's own top frame only. */
async function pickScreen(request: DisplayMediaRequestHandlerHandlerRequest, origin: string): Promise<DesktopCapturerSource | null> {
  const top = request.frame !== null && request.frame.parent === null;
  if (!request.videoRequested || !permissionAllowed('media', { requestingUrl: request.securityOrigin, isMainFrame: top, mediaTypes: ['video'] }, origin)) {
    log('info', `screen share refused for a page of ${origin}`);
    return null;
  }
  const screens = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 0, height: 0 } });
  if (!screens.length) return null;
  const choices = screens.length === 1 ? ['Share screen'] : screens.map((s, i) => s.name || `Screen ${i + 1}`);
  const options = { type: 'question' as const, message: 'Share your screen?', buttons: [...choices, 'Cancel'], defaultId: 0, cancelId: choices.length };
  const parent = BaseWindow.getFocusedWindow();
  const { response } = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options);
  return screens[response] ?? null;
}

/** The right-click menu (contextMenu.ts) on any page the app opens. */
export function attachContextMenu(wc: WebContents): void {
  wc.on('context-menu', (_e, params) => {
    const items = contextMenuItems(params);
    if (!items.length) return;
    const run = (action: ContextAction, arg = ''): void => {
      if (wc.isDestroyed()) return;
      if (action === 'cut') wc.cut();
      else if (action === 'copy') wc.copy();
      else if (action === 'paste') wc.paste();
      else if (action === 'selectAll') wc.selectAll();
      else if (action === 'replace') wc.replaceMisspelling(arg);
      else if (action === 'learn') wc.session.addWordToSpellCheckerDictionary(arg);
      else clipboard.writeText(arg);
    };
    Menu.buildFromTemplate(
      items.map((i) => (i === 'separator' ? { type: 'separator' as const } : { label: i.label, enabled: i.enabled, click: () => run(i.action, i.arg) })),
    ).popup();
  });
}

/** Hands the system browser only what navigation.ts allowed: https and mailto. */
export function openExternal(url: string): void {
  if (!/^(https:|mailto:)/i.test(url)) return;
  shell.openExternal(url).catch(() => log('warn', 'could not open a link in the default browser'));
}

export type GuardHooks = {
  context(): Omit<NavContext, 'signingIn'>;
  switchTo(companyId: string, url?: string): void;
  committed?(url: string): void;
  /** A window's own rule on top of the company view's (the small call window's `keepOnCall`). */
  rule?(raw: string, base: NavDecision, ctx: NavContext): NavDecision;
};

/** Applies the navigation rules to one page. Returns whether a sign-in chain is under way (for tests and logs). */
export function guardNavigation(wc: WebContents, hooks: GuardHooks): { signingIn(): boolean } {
  let signingIn = false;
  const ctx = (): NavContext => ({ ...hooks.context(), signingIn });
  const decide = (raw: string, base: (raw: string, c: NavContext) => NavDecision): NavDecision => {
    const c = ctx();
    const d = base(raw, c);
    return hooks.rule ? hooks.rule(raw, d, c) : d;
  };

  const act = (d: NavDecision, prevent: () => void): void => {
    switch (d.kind) {
      case 'stay':
        return;
      case 'sign-in':
        signingIn = true;
        return;
      case 'switch':
        prevent();
        hooks.switchTo(d.companyId, d.url);
        return;
      case 'external':
        prevent();
        openExternal(d.url);
        return;
      case 'block':
        prevent();
        return;
    }
  };

  wc.setWindowOpenHandler(({ url }) => {
    const d = decide(url, decideNewWindow);
    if (d.kind === 'switch') hooks.switchTo(d.companyId, d.url);
    else if (d.kind === 'external') openExternal(d.url);
    return { action: 'deny' };
  });
  wc.on('will-navigate', (e) => {
    if (e.isMainFrame) act(decide(e.url, decideNavigation), () => e.preventDefault());
  });
  wc.on('will-redirect', (e) => {
    if (e.isMainFrame) act(decide(e.url, decideNavigation), () => e.preventDefault());
  });
  wc.on('did-navigate', (_e, url) => {
    if (signInOver(url, hooks.context().origin)) signingIn = false;
    hooks.committed?.(url);
  });
  wc.on('did-navigate-in-page', (_e, url, isMainFrame) => {
    if (isMainFrame) hooks.committed?.(url);
  });
  wc.on('will-attach-webview', (e) => e.preventDefault());
  return { signingIn: () => signingIn };
}
