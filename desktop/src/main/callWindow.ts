// The small call window (platforms.md 4.6): the app's call route with ?window=1, on its company's session, floating
// above other windows until the person turns that off. Only one at a time. The room lives in whichever window shows
// the call, so moving it reconnects; the main page hears 'open', 'closed' (left) or 'return' (go back to it there).
import { BrowserWindow, dialog, nativeTheme } from 'electron';
import type { CallWindowState } from '../bridge';
import { APP_PATH } from './address';
import type { Company } from './companies';
import { guardNavigation, hardenSession, pageWebPreferences } from './contents';
import { log } from './log';

export type CallHooks = {
  preloadArgs(): string[];
  userAgent: string;
  others(companyId: string): Array<{ id: string; origin: string }>;
  switchTo(companyId: string, url: string): void;
  notify(companyId: string, state: CallWindowState): void;
  quitting(): boolean;
};

export class CallWindow {
  private win: BrowserWindow | null = null;
  private owner: Company | null = null;
  private callId: string | null = null;
  private ending: CallWindowState | null = null;

  constructor(private readonly hooks: CallHooks) {}

  get live(): boolean {
    return this.win !== null;
  }

  /** The company whose call window this page is, if it is the call window. */
  ownerOf(contentsId: number): Company | null {
    return this.win && !this.win.isDestroyed() && this.win.webContents.id === contentsId ? this.owner : null;
  }

  belongsTo(companyId: string): boolean {
    return this.owner?.id === companyId;
  }

  open(company: Company, callId: string): void {
    if (this.win && this.owner?.id === company.id && this.callId === callId) {
      this.win.show();
      this.win.focus();
      return;
    }
    if (this.win) {
      // Another call: the old window goes without asking, and its page hears it left.
      const old = this.win;
      const oldOwner = this.owner;
      this.win = null;
      old.destroy();
      if (oldOwner) this.hooks.notify(oldOwner.id, 'closed');
    }
    this.ending = null;
    const mac = process.platform === 'darwin';
    const win = new BrowserWindow({
      width: 360,
      height: 640,
      minWidth: 300,
      minHeight: 420,
      show: false,
      title: 'Call',
      alwaysOnTop: true,
      fullscreenable: false,
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#0B0B0B' : '#E9E9E9',
      ...(mac ? { titleBarStyle: 'hidden' as const, trafficLightPosition: { x: 14, y: 14 } } : {}),
      webPreferences: pageWebPreferences(company.partition, this.hooks.preloadArgs()),
    });
    win.setAlwaysOnTop(true, 'floating');
    hardenSession(win.webContents.session, company.url, this.hooks.userAgent);
    guardNavigation(win.webContents, {
      context: () => ({ origin: company.url, others: this.hooks.others(company.id), filesOrigin: null }),
      switchTo: (id, url) => this.hooks.switchTo(id, url),
    });
    win.once('ready-to-show', () => win.show());
    win.on('close', (e) => {
      if (this.ending || this.hooks.quitting()) return;
      e.preventDefault();
      void dialog
        .showMessageBox(win, {
          type: 'question',
          message: 'Close the call window?',
          buttons: ['Back to the main window', 'Leave the call', 'Cancel'],
          defaultId: 0,
          cancelId: 2,
        })
        .then(({ response }) => {
          if (response === 2 || win.isDestroyed()) return;
          this.ending = response === 0 ? 'return' : 'closed';
          win.close();
        });
    });
    win.on('closed', () => {
      // A window replaced by a newer call closes after the new one is set up; it must not clear the new one.
      if (this.win !== win) return;
      const state = this.ending ?? 'closed';
      this.win = null;
      this.owner = null;
      this.callId = null;
      this.ending = null;
      this.hooks.notify(company.id, state);
    });
    this.win = win;
    this.owner = company;
    this.callId = callId;
    const url = new URL(`${APP_PATH}call/${encodeURIComponent(callId)}`, company.url);
    url.searchParams.set('window', '1');
    win.loadURL(url.href).catch(() => log('warn', `call window could not load for ${company.url}`));
    this.hooks.notify(company.id, 'open');
  }

  close(state: 'closed' | 'return' = 'closed'): void {
    if (!this.win || this.win.isDestroyed()) return;
    this.ending = state;
    this.win.close();
  }

  setOnTop(on: boolean): void {
    this.win?.setAlwaysOnTop(on, 'floating');
  }
}
