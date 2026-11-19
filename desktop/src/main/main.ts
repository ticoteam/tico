// The desktop app's entry: one instance, tico:// links, then the window (shell.ts), the bridge (ipc.ts), the menus
// and updates. The pages come from each company's server; nothing of web/ is bundled (platforms.md 4.1).
import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { app } from 'electron';
import { checkAddress } from './address';
import { cleanUserAgent } from './badge';
import * as C from './companies';
import { attachContextMenu } from './contents';
import { deepLinkFromArgv } from './deeplink';
import { registerIpc } from './ipc';
import { log, openLog } from './log';
import { buildMenus } from './menu';
import { portalOrigin } from './portal';
import { openPortalSignIn } from './portalSignIn';
import { Shell } from './shell';
import { Updater } from './updater';

// TICO_USER_DATA keeps a test run's data apart; a development run never shares the installed app's data.
if (process.env.TICO_USER_DATA) app.setPath('userData', path.resolve(process.env.TICO_USER_DATA));
else if (!app.isPackaged) app.setPath('userData', path.join(app.getPath('appData'), 'Tico Dev'));

app.enableSandbox();
// Windows ties notifications and the taskbar to this id; the installer's shortcut carries the same (appId).
if (process.platform === 'win32') app.setAppUserModelId('team.tico.desktop');

let shell: Shell | null = null;
const pending: string[] = [];
const openLink = (raw: string) => (shell ? shell.openDeepLink(raw) : void pending.push(raw));
const newId = () => C.companyId(randomBytes(8).toString('hex'));

function loadCompanies(dataDir: string): C.CompaniesFile {
  const file = path.join(dataDir, 'companies.json');
  if (fs.existsSync(file)) {
    try {
      return C.parseCompanies(JSON.parse(fs.readFileSync(file, 'utf8')));
    } catch {
      log('error', 'companies.json is not readable; it is kept as companies.json.corrupt');
      fs.renameSync(file, `${file}.corrupt`);
      return C.emptyFile();
    }
  }
  // None yet: the Tauri app's list, once (platforms.md 10, R1). Only an installed app reads it, never a dev or test run.
  if (app.isPackaged && !process.env.TICO_USER_DATA) {
    try {
      const migrated = C.migrateOrgs(JSON.parse(fs.readFileSync(path.join(app.getPath('appData'), 'team.tico.app', 'orgs.json'), 'utf8')), newId);
      if (migrated) {
        log('info', `moved ${migrated.companies.length} companies from the previous app`);
        return migrated;
      }
    } catch {
      // No earlier app.
    }
  }
  return C.emptyFile();
}

function start(): void {
  const dataDir = app.getPath('userData');
  openLog(path.join(dataDir, 'logs'));
  const version = app.getVersion();
  log('info', `start ${version}, Electron ${process.versions.electron}, ${process.platform}`);
  const userAgent = cleanUserAgent(app.userAgentFallback, app.getName());
  app.userAgentFallback = userAgent;

  let file = loadCompanies(dataDir);
  // npm run dev: TICO_SERVER is the company to open (added if new).
  if (process.env.TICO_SERVER) {
    const address = checkAddress(process.env.TICO_SERVER);
    if (address.ok) file = C.addCompany(file, address.origin, newId()).file;
    else log('warn', `TICO_SERVER is not used: ${address.error}`);
  }

  // The feed is the app-update.yml electron-builder writes from `publish`; without it updates stay off.
  const feed = app.isPackaged && fs.existsSync(path.join(process.resourcesPath, 'app-update.yml'));
  if (app.isPackaged && !feed) log('info', 'updates off: this build has no update feed');
  const updater = new Updater(feed && !process.env.TICO_NO_UPDATES, () => shell?.callLive ?? false, () =>
    log('info', `update ${updater.state.state} ${updater.state.version ?? ''}`),
  );
  const portal = portalOrigin(process.env.TICO_PORTAL_URL);
  shell = new Shell({
    dataDir,
    version,
    userAgent,
    file,
    changed: () => {
      if (shell) buildMenus(shell, updater);
    },
    portalSignIn: () => {
      const s = shell;
      if (s) openPortalSignIn(s.win, portal, userAgent, (picks, from) => s.addPicks(picks, from));
    },
  });
  buildMenus(shell, updater);
  registerIpc(shell, updater, version);
  updater.start();
  for (const link of pending.splice(0)) shell.openDeepLink(link);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('open-url', (e, url) => {
    e.preventDefault();
    openLink(url);
  });
  app.on('second-instance', (_e, argv) => {
    const link = deepLinkFromArgv(argv);
    if (link) openLink(link);
    else shell?.showWindow();
  });
  const first = deepLinkFromArgv(process.argv);
  if (first) pending.push(first);
  // A development run must not take tico:// links from the installed app.
  if (app.isPackaged) app.setAsDefaultProtocolClient('tico');
  // Whatever page is made, by anyone: no <webview>, no new windows unless its own handler says so.
  app.on('web-contents-created', (_e, wc) => {
    wc.on('will-attach-webview', (e) => e.preventDefault());
    wc.setWindowOpenHandler(() => ({ action: 'deny' }));
    attachContextMenu(wc);
  });
  app.on('before-quit', () => shell?.setQuitting());
  app.on('activate', () => shell?.showWindow());
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
  void app.whenReady().then(start);
}
