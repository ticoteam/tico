// The one window (platforms.md 4.2, 4.4): a BaseWindow holding a WebContentsView per company, all kept alive with
// only the active one shown, plus one local view for the Add company and Cannot reach pages.
import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';
import { app, BaseWindow, dialog, nativeImage, nativeTheme, screen, session, WebContentsView, type NativeImage, type Session, type WebContents } from 'electron';
import type { CallWindowState, CompanyRef } from '../bridge';
import { appUrl, checkAddress, hostOf } from './address';
import { anyCallLive, badgeFor, dotBitmap } from './badge';
import { CallWindow } from './callWindow';
import * as C from './companies';
import { guardNavigation, hardenSession, pageWebPreferences } from './contents';
import { parseDeepLink } from './deeplink';
import { checkServer, type Health } from './health';
import { log } from './log';
import { loadAtStart, viewsToUnload } from './memory';
import { issuerCookie, type Picks } from './portal';
import { onOwnOrigin } from './validate';
import { loadWindowState, MIN_HEIGHT, MIN_WIDTH, restoreFrame, saveWindowState } from './windowState';

/** Where macOS puts the window buttons: inside the first sheet's 60 high head, which starts 8 in from the corner. */
const TRAFFIC_LIGHTS = { x: 22, y: 31 };
/** What the page keeps clear of the buttons, from the window's top left corner (handed to the page as CSS vars). */
export const BUTTONS_INSET = { left: 92, top: 52 };

const LOCAL_DIR = path.join(__dirname, '..', 'local');
const LOCAL_PRELOAD = path.join(__dirname, '..', 'localPreload.js');

type Entry = {
  company: C.Company;
  view: WebContentsView;
  needs: number;
  signedOut: boolean;
  filesOrigin: string | null;
  failed: boolean;
  /** The page reported it is in a call; it ends with the page (another document, a crash, sign-out). */
  callLive: boolean;
  /** Its page is loaded (memory.ts: past six companies the one shown longest ago is unloaded until it is shown). */
  loaded: boolean;
  shownAt: number;
};

export type LocalState = { page: 'add'; prefill?: string } | { page: 'unreachable'; companyId: string };

export type Caller = { company: C.Company; kind: 'main' | 'call' };

export type ShellOptions = {
  dataDir: string;
  version: string;
  userAgent: string;
  file: C.CompaniesFile;
  portalSignIn(): void;
  changed(): void;
};

const background = () => (nativeTheme.shouldUseDarkColors ? '#0B0B0B' : '#E9E9E9');

let overlay: NativeImage | null = null;
const overlayIcon = () => (overlay ??= nativeImage.createFromBitmap(dotBitmap(16), { width: 16, height: 16 }));

export class Shell {
  readonly win: BaseWindow;
  readonly call: CallWindow;
  private file: C.CompaniesFile;
  private readonly entries = new Map<string, Entry>();
  private local: { view: WebContentsView; state: LocalState } | null = null;
  private quitting = false;
  private lastBadge = '';

  constructor(private readonly opts: ShellOptions) {
    this.file = opts.file;
    this.win = this.createWindow();
    this.call = new CallWindow({
      preloadArgs: () => this.preloadArgs(),
      userAgent: opts.userAgent,
      others: (id) => this.others(id),
      switchTo: (id, url) => void this.switchTo(id, url),
      notify: (id, state) => this.notifyCall(id, state),
      quitting: () => this.quitting,
    });
    const first = loadAtStart(
      this.file.companies.map((c) => c.id),
      this.file.active,
    );
    for (const company of this.file.companies) this.createEntry(company, first.has(company.id));
    const active = this.file.active;
    if (active) this.switchTo(active);
    else this.showLocal({ page: 'add' });
  }

  // ------------------------------------------------------------------------------------------------ the window

  private createWindow(): BaseWindow {
    const stateFile = path.join(this.opts.dataDir, 'window-state.json');
    const areas = screen.getAllDisplays().map((d) => d.workArea);
    const frame = restoreFrame(loadWindowState(stateFile, fs.readFileSync), areas, screen.getPrimaryDisplay().workArea);
    const mac = process.platform === 'darwin';
    const win = new BaseWindow({
      x: frame.x,
      y: frame.y,
      width: frame.width,
      height: frame.height,
      minWidth: MIN_WIDTH,
      minHeight: MIN_HEIGHT,
      title: app.getName(),
      backgroundColor: background(),
      ...(mac ? { titleBarStyle: 'hidden' as const, trafficLightPosition: TRAFFIC_LIGHTS } : {}),
    });
    if (frame.maximized) win.maximize();

    let timer: NodeJS.Timeout | null = null;
    const save = () => {
      if (win.isDestroyed() || win.isFullScreen() || win.isMinimized()) return;
      try {
        saveWindowState(stateFile, { ...win.getNormalBounds(), maximized: win.isMaximized() }, fs);
      } catch {
        log('warn', 'could not save the window frame');
      }
    };
    const later = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(save, 400);
    };
    win.on('resize', () => {
      this.layout();
      later();
    });
    win.on('move', later);
    win.on('maximize', later);
    win.on('unmaximize', later);
    win.on('enter-full-screen', () => this.sendFrame(true));
    win.on('leave-full-screen', () => this.sendFrame(false));
    win.on('close', (e) => {
      save();
      // macOS keeps the app and every company's page alive with the window closed, so rings and the badge go on.
      if (process.platform === 'darwin' && !this.quitting) {
        e.preventDefault();
        win.hide();
        return;
      }
      // Windows and Linux have no app without its window: closing it quits (the small call window goes too).
      if (process.platform !== 'darwin' && !this.quitting) app.quit();
    });
    nativeTheme.on('updated', () => win.setBackgroundColor(background()));
    return win;
  }

  setQuitting(): void {
    this.quitting = true;
  }

  showWindow(): void {
    if (this.win.isMinimized()) this.win.restore();
    this.win.show();
    this.win.focus();
  }

  private layout(): void {
    const { width, height } = this.win.getContentBounds();
    const bounds = { x: 0, y: 0, width, height };
    for (const e of this.entries.values()) e.view.setBounds(bounds);
    this.local?.view.setBounds(bounds);
  }

  preloadArgs(): string[] {
    const platform = process.platform === 'darwin' ? 'mac' : process.platform === 'win32' ? 'windows' : 'linux';
    const inset = process.platform === 'darwin' ? `${BUTTONS_INSET.left},${BUTTONS_INSET.top}` : '0,0';
    return [`--tico-shell-version=${this.opts.version}`, `--tico-platform=${platform}`, `--tico-inset=${inset}`];
  }

  private sendFrame(fullscreen: boolean): void {
    for (const e of this.entries.values()) e.view.webContents.send('frame:fullscreen', fullscreen);
  }

  // ------------------------------------------------------------------------------------------------ companies

  private persist(): void {
    const file = path.join(this.opts.dataDir, 'companies.json');
    try {
      fs.writeFileSync(`${file}.tmp`, JSON.stringify(this.file, null, 2));
      fs.renameSync(`${file}.tmp`, file);
    } catch {
      log('error', 'could not save companies.json');
    }
  }

  private others(id: string): Array<{ id: string; origin: string }> {
    return this.file.companies.filter((c) => c.id !== id).map((c) => ({ id: c.id, origin: c.url }));
  }

  company(id: string): C.Company | undefined {
    return this.entries.get(id)?.company;
  }

  contentsOf(id: string): WebContents | null {
    const wc = this.entries.get(id)?.view.webContents;
    return wc && !wc.isDestroyed() ? wc : null;
  }

  private createEntry(company: C.Company, load = true): Entry {
    const view = new WebContentsView({ webPreferences: pageWebPreferences(company.partition, company.url, this.preloadArgs()) });
    view.setBackgroundColor(background());
    const wc = view.webContents;
    hardenSession(wc.session, company.url, this.opts.userAgent);
    const entry: Entry = { company, view, needs: 0, signedOut: false, filesOrigin: null, failed: false, callLive: false, loaded: load, shownAt: 0 };
    guardNavigation(wc, {
      context: () => ({ origin: company.url, others: this.others(company.id), filesOrigin: entry.filesOrigin }),
      switchTo: (id, url) => void this.switchTo(id, url),
      committed: (url) => {
        entry.failed = false;
        this.file = C.setLastUrl(this.file, company.id, url);
        this.persistSoon();
      },
    });
    wc.on('did-fail-load', (_e, code, _description, _url, isMainFrame) => {
      if (!isMainFrame || code === -3) return; // -3: a navigation replaced by another
      entry.failed = true;
      log('warn', `could not load ${company.url} (error ${code})`);
      if (this.file.active === company.id) this.showLocal({ page: 'unreachable', companyId: company.id });
    });
    wc.on('dom-ready', () => wc.send('frame:fullscreen', this.win.isFullScreen()));
    // A new document has no room: whatever call the old one reported is over.
    wc.on('did-navigate', () => (entry.callLive = false));
    wc.on('render-process-gone', (_e, details) => {
      entry.callLive = false;
      log('error', `page of ${company.url} stopped: ${details.reason}`);
      if (details.reason !== 'clean-exit' && !wc.isDestroyed()) wc.reload();
    });
    this.entries.set(company.id, entry);
    this.win.contentView.addChildView(view);
    view.setVisible(false);
    this.layout();
    if (load) void wc.loadURL(company.lastUrl ?? appUrl(company.url)).catch(() => undefined);
    return entry;
  }

  private persistTimer: NodeJS.Timeout | null = null;
  private persistSoon(): void {
    if (this.persistTimer) return;
    this.persistTimer = setTimeout(() => {
      this.persistTimer = null;
      this.persist();
    }, 1000);
  }

  list(forId: string | null): CompanyRef[] {
    return C.companyRefs(this.file, forId, (id) => {
      const e = this.entries.get(id);
      return { signedOut: e?.signedOut ?? false, needs: e?.needs ?? 0 };
    });
  }

  get companies(): readonly C.Company[] {
    return this.file.companies;
  }

  get activeId(): string | null {
    return this.file.active;
  }

  switchTo(id: string, url?: string): void {
    const entry = this.entries.get(id);
    if (!entry) throw new Error('No such company.');
    this.file = C.setActive(this.file, id);
    this.persist();
    for (const e of this.entries.values()) e.view.setVisible(e === entry);
    entry.shownAt = Date.now();
    const wc = entry.view.webContents;
    const to = url && url !== wc.getURL() ? url : entry.loaded ? null : (entry.company.lastUrl ?? appUrl(entry.company.url));
    if (to) {
      entry.failed = false;
      entry.loaded = true;
      void wc.loadURL(to).catch(() => undefined);
    }
    this.unloadExtra();
    if (entry.failed) this.showLocal({ page: 'unreachable', companyId: id });
    else this.hideLocal();
    this.showWindow();
    wc.focus();
    this.changed();
  }

  /** Past six loaded companies, the one shown longest ago with no call unloads; it loads again where it was when shown. */
  private unloadExtra(): void {
    const views = [...this.entries.values()].map((e) => ({
      id: e.company.id,
      loaded: e.loaded,
      shownAt: e.shownAt,
      inCall: e.callLive || this.call.belongsTo(e.company.id),
    }));
    for (const id of viewsToUnload(views, this.file.active)) {
      const e = this.entries.get(id);
      if (!e) continue;
      e.loaded = false;
      e.failed = false;
      log('info', `unloaded ${e.company.url} to save memory`);
      // about:blank is never kept as its last place, so it loads again at the page it was on
      void e.view.webContents.loadURL('about:blank').catch(() => undefined);
    }
  }

  /** Adds a server (or finds it), its page's session and view, and shows it. `beforeLoad` runs before its first page. */
  async addOrigin(origin: string, beforeLoad?: (company: C.Company) => Promise<void>, show = true): Promise<C.Company> {
    const added = C.addCompany(this.file, origin, C.companyId(randomBytes(8).toString('hex')));
    if (!added.existed) {
      this.file = { ...added.file, active: show ? added.file.active : this.file.active };
      this.persist();
      if (beforeLoad) await beforeLoad(added.company);
      this.createEntry(added.company);
      log('info', `added ${origin}`);
    }
    if (show) this.switchTo(added.company.id);
    return added.company;
  }

  /** "Use a server address" on the Add company page. */
  async addServer(text: string): Promise<Health> {
    const address = checkAddress(text);
    if (!address.ok) return address;
    const existing = C.findByOrigin(this.file, address.origin);
    if (existing) {
      this.switchTo(existing.id);
      return { ok: true };
    }
    const health = await checkServer(address.origin);
    if (!health.ok) return health;
    await this.addOrigin(address.origin);
    return { ok: true };
  }

  /** The Cloud portal's picks: each new server gets the issuer's cookies before its first page, so it opens signed in. */
  async addPicks(picks: Picks, from: Session): Promise<void> {
    const host = new URL(picks.issuer).hostname;
    const cookies = (await from.cookies.get({})).filter((c) => issuerCookie(c.domain, host));
    let first: string | null = null;
    for (const origin of picks.servers) {
      const existing = C.findByOrigin(this.file, origin);
      const company =
        existing ??
        (await this.addOrigin(
          origin,
          async (c) => {
            const target = session.fromPartition(c.partition);
            for (const k of cookies) {
              await target.cookies
                .set({
                  url: `${picks.issuer}${k.path ?? '/'}`,
                  name: k.name,
                  value: k.value,
                  domain: k.hostOnly ? undefined : k.domain,
                  path: k.path,
                  secure: k.secure,
                  httpOnly: k.httpOnly,
                  expirationDate: k.expirationDate,
                  sameSite: k.sameSite,
                })
                .catch(() => log('warn', `could not copy a sign-in cookie for ${origin}`));
            }
          },
          false,
        ));
      first ??= company.id;
    }
    if (first) this.switchTo(first);
  }

  async remove(id: string): Promise<void> {
    const entry = this.entries.get(id);
    if (!entry) throw new Error('No such company.');
    const name = entry.company.name || hostOf(entry.company.url);
    const { response } = await dialog.showMessageBox(this.win, {
      type: 'warning',
      message: `Remove ${name} from this app?`,
      detail: 'Its sign-in and what it stored on this computer are deleted. You can add it again later.',
      buttons: ['Remove', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
    });
    if (response !== 0 || !this.entries.has(id)) return;
    if (this.call.belongsTo(id)) this.call.close('closed');
    this.win.contentView.removeChildView(entry.view);
    entry.view.webContents.close();
    this.entries.delete(id);
    await session
      .fromPartition(entry.company.partition)
      .clearStorageData()
      .catch(() => log('warn', `could not clear the data of ${entry.company.url}`));
    this.file = C.removeCompany(this.file, id);
    this.persist();
    log('info', `removed ${entry.company.url}`);
    if (this.file.active) this.switchTo(this.file.active);
    else this.showLocal({ page: 'add' });
    this.changed();
  }

  reportSelf(id: string, info: { name: string; email: string; signedOut: boolean }): void {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.signedOut = info.signedOut;
    const c = entry.company;
    if (c.name !== info.name || c.email !== info.email) {
      this.file = C.reportSelf(this.file, id, info);
      entry.company = this.file.companies.find((x) => x.id === id) ?? c;
      this.persist();
    }
    this.changed();
  }

  /** The page is signing out: its count goes, the small call window closes, and the company shows signed out. */
  signingOut(id: string): void {
    const entry = this.entries.get(id);
    if (!entry) return;
    if (this.call.belongsTo(id)) this.call.close('closed');
    entry.needs = 0;
    entry.signedOut = true;
    entry.callLive = false;
    log('info', `signing out of ${entry.company.url}`);
    this.changed();
  }

  setNeeds(id: string, needs: number): void {
    const entry = this.entries.get(id);
    if (!entry || entry.needs === needs) return;
    entry.needs = needs;
    this.changed();
  }

  setCallLive(id: string, live: boolean): void {
    const entry = this.entries.get(id);
    if (entry) entry.callLive = live;
  }

  /** A call is live in the small window or in any company's page: "Restart to update" waits for it. */
  get callLive(): boolean {
    return anyCallLive(this.call.live, [...this.entries.values()]);
  }

  setFilesOrigin(id: string, origin: string): void {
    const entry = this.entries.get(id);
    if (entry) entry.filesOrigin = origin;
  }

  /** Which company page is calling: one of the company views, or the call window. */
  callerOf(wc: WebContents): Caller | null {
    for (const e of this.entries.values()) if (e.view.webContents.id === wc.id) return { company: e.company, kind: 'main' };
    const owner = this.call.ownerOf(wc.id);
    return owner ? { company: owner, kind: 'call' } : null;
  }

  openCall(companyId: string, callId: string): void {
    const company = this.company(companyId);
    if (!company) throw new Error('No such company.');
    this.call.open(company, callId);
  }

  /** Sends to a company's view only while it shows the company's own page: never to a provider's page it reached. */
  private sendToPage(e: Entry, channel: string, value: unknown): void {
    const wc = e.view.webContents;
    if (!wc.isDestroyed() && onOwnOrigin(wc.getURL(), e.company.url)) wc.send(channel, value);
  }

  private notifyCall(companyId: string, state: CallWindowState): void {
    const entry = this.entries.get(companyId);
    if (entry) this.sendToPage(entry, 'call:state', state);
    if (state === 'return') this.switchTo(companyId);
  }

  /** Tells every page the list changed (each sees itself marked), and updates the badge and the menus. */
  changed(): void {
    for (const [id, e] of this.entries) this.sendToPage(e, 'companies:changed', this.list(id));
    const badge = badgeFor([...this.entries.values()]);
    if (badge.text !== this.lastBadge && !this.win.isDestroyed()) {
      this.lastBadge = badge.text;
      if (process.platform === 'darwin') app.dock?.setBadge(badge.text);
      else if (process.platform === 'win32') this.win.setOverlayIcon(badge.count ? overlayIcon() : null, badge.count ? `${badge.count} waiting` : '');
      else app.setBadgeCount(badge.count);
    }
    this.opts.changed();
  }

  // ------------------------------------------------------------------------------------------------ local pages

  get localState(): LocalState | null {
    return this.local?.view.getVisible() ? this.local.state : null;
  }

  isLocalPage(wc: WebContents, frameUrl: string | null, isTopFrame: boolean): boolean {
    if (!this.local || wc.id !== this.local.view.webContents.id || !isTopFrame || !frameUrl) return false;
    return frameUrl.split(/[?#]/)[0]!.startsWith(pathToFileURL(LOCAL_DIR).href + '/');
  }

  showLocal(state: LocalState): void {
    if (!this.local) {
      const view = new WebContentsView({
        webPreferences: {
          partition: 'tico-local',
          preload: LOCAL_PRELOAD,
          contextIsolation: true,
          sandbox: true,
          nodeIntegration: false,
          webSecurity: true,
          webviewTag: false,
          navigateOnDragDrop: false,
          additionalArguments: this.preloadArgs(),
        },
      });
      view.setBackgroundColor(background());
      view.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      view.webContents.on('will-navigate', (e) => e.preventDefault());
      view.webContents.session.setPermissionRequestHandler((_wc, _p, cb) => cb(false));
      this.local = { view, state };
    }
    this.local.state = state;
    const file = state.page === 'add' ? 'add.html' : 'unreachable.html';
    // Adding a view it already holds puts it on top.
    this.win.contentView.addChildView(this.local.view);
    this.local.view.setVisible(true);
    this.layout();
    void this.local.view.webContents.loadFile(path.join(LOCAL_DIR, file)).catch(() => undefined);
    this.showWindow();
    this.local.view.webContents.focus();
  }

  hideLocal(): void {
    this.local?.view.setVisible(false);
  }

  /** What the local page shows: the address to prefill, or which server cannot be reached. */
  localContext(): { page: string; prefill: string; host: string; canCancel: boolean } {
    const state = this.local?.state;
    if (state?.page === 'unreachable') {
      const company = this.company(state.companyId);
      return { page: 'unreachable', prefill: '', host: company ? company.name || hostOf(company.url) : '', canCancel: false };
    }
    const server = state?.page === 'add' ? state.prefill : undefined;
    const prefill = !server ? '' : server.startsWith('https://') ? hostOf(server) : server;
    return { page: 'add', prefill, host: '', canCancel: this.entries.size > 0 };
  }

  retryLocal(): void {
    const state = this.local?.state;
    if (state?.page !== 'unreachable') return;
    const entry = this.entries.get(state.companyId);
    if (!entry) return;
    entry.failed = false;
    this.hideLocal();
    void entry.view.webContents.loadURL(entry.company.lastUrl ?? appUrl(entry.company.url)).catch(() => undefined);
  }

  cancelLocal(): void {
    if (this.file.active && this.entries.size) this.switchTo(this.file.active);
  }

  // ------------------------------------------------------------------------------------------------ deep links

  openDeepLink(raw: string): void {
    const link = parseDeepLink(
      raw,
      this.file.companies.map((c) => ({ id: c.id, origin: c.url })),
      this.file.active,
    );
    if (!link) return;
    log('info', `deep link: ${link.kind}`);
    if (link.kind === 'open') this.switchTo(link.companyId, link.url);
    else if (link.kind === 'add') this.showLocal({ page: 'add', prefill: link.server });
    else this.showWindow();
  }

  startPortalSignIn(): void {
    this.opts.portalSignIn();
  }
}
