// An agent's chat in its own window: the agent's address with ?window=1, on its company's session, an ordinary window
// (never above the others). One per agent, at most CHAT_WINDOW_CAP per company (chatWindowPlan.ts). Closing one asks
// nothing: what was sent is already on the server. Sign-out and removing the company close that company's windows.
import { BrowserWindow, nativeTheme } from 'electron';
import { APP_PATH } from './address';
import { chatWindowStep, type OpenChat } from './chatWindowPlan';
import type { Company } from './companies';
import { guardNavigation, hardenSession, pageWebPreferences } from './contents';
import { log } from './log';
import { keepOnChat } from './navigation';

export type ChatHooks = {
  preloadArgs(): string[];
  userAgent: string;
  others(companyId: string): Array<{ id: string; origin: string }>;
  switchTo(companyId: string, url?: string): void;
};

type Open = OpenChat & { win: BrowserWindow; owner: Company };

export class ChatWindows {
  private readonly open: Open[] = [];

  constructor(private readonly hooks: ChatHooks) {}

  /** The company whose chat window this page is, if it is one. */
  ownerOf(contentsId: number): Company | null {
    return this.open.find((w) => !w.win.isDestroyed() && w.win.webContents.id === contentsId)?.owner ?? null;
  }

  /** Opens the agent's chat window, or brings its open one forward. 'full': the company holds the cap; none opened. */
  show(company: Company, agent: string): 'open' | 'full' {
    const step = chatWindowStep(this.open, company.id, agent);
    if (step === 'full') return 'full';
    if (step === 'focus') {
      const w = this.open.find((o) => o.companyId === company.id && o.agent === agent)!.win;
      if (w.isMinimized()) w.restore();
      w.show();
      w.focus();
      return 'open';
    }
    const mac = process.platform === 'darwin';
    const win = new BrowserWindow({
      width: 420,
      height: 720,
      minWidth: 320,
      minHeight: 420,
      show: false,
      title: 'Tico',
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#0B0B0B' : '#E9E9E9',
      ...(mac ? { titleBarStyle: 'hidden' as const, trafficLightPosition: { x: 14, y: 14 } } : {}),
      webPreferences: pageWebPreferences(company.partition, company.url, this.hooks.preloadArgs()),
    });
    hardenSession(win.webContents.session, company.url, this.hooks.userAgent);
    const url = new URL(`${APP_PATH}team/${encodeURIComponent(agent)}`, company.url);
    url.searchParams.set('window', '1');
    guardNavigation(win.webContents, {
      context: () => ({ origin: company.url, others: this.hooks.others(company.id), filesOrigin: null }),
      switchTo: (id, to) => this.hooks.switchTo(id, to),
      // the window stays on its chat: a task or any other page of the company opens in the main window
      rule: (raw, base, ctx) => keepOnChat(raw, base, { ...ctx, companyId: company.id, chatPath: url.pathname }),
    });
    win.once('ready-to-show', () => win.show());
    win.on('closed', () => {
      const at = this.open.findIndex((o) => o.win === win);
      if (at >= 0) this.open.splice(at, 1);
    });
    this.open.push({ companyId: company.id, agent, win, owner: company });
    win.loadURL(url.href).catch(() => log('warn', `chat window could not load for ${company.url}`));
    return 'open';
  }

  /** Closes every chat window of the company (sign-out, the company removed). */
  closeAll(companyId: string): void {
    for (const o of this.open.filter((w) => w.companyId === companyId)) if (!o.win.isDestroyed()) o.win.close();
  }
}
