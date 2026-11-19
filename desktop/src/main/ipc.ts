// Every IPC handler (platforms.md 4.3, 4.10). Each one first checks the sender: the top frame of a page this app
// opened for a company, while that frame is on the company's origin. Then it checks every argument, and throws on
// anything else. The local pages (Add company, Cannot reach) have their own few channels and the same kind of check.
import { app, ipcMain, shell as electronShell, type IpcMainEvent, type IpcMainInvokeEvent, type WebFrameMain } from 'electron';
import { log, logPath, logTail } from './log';
import type { Caller, Shell } from './shell';
import type { Updater } from './updater';
import * as V from './validate';

type Kind = Caller['kind'];
type AnyEvent = IpcMainEvent | IpcMainInvokeEvent;

function isTopFrame(event: AnyEvent, frame: WebFrameMain | null): frame is WebFrameMain {
  return frame !== null && frame.parent === null && frame.frameTreeNodeId === event.sender.mainFrame.frameTreeNodeId;
}

function callerOf(shell: Shell, event: AnyEvent, kinds: readonly Kind[]): Caller {
  const caller = shell.callerOf(event.sender);
  const frame = event.senderFrame;
  if (!caller || !kinds.includes(caller.kind) || !V.senderAllowed(frame?.url ?? null, isTopFrame(event, frame), caller.company.url)) {
    log('warn', 'refused a call from a page that may not use the app');
    throw new Error('Refused: this page may not call the app.');
  }
  return caller;
}

const BOTH: readonly Kind[] = ['main', 'call'];
const MAIN: readonly Kind[] = ['main'];

export function registerIpc(shell: Shell, updater: Updater, version: string): void {
  const handle = (channel: string, kinds: readonly Kind[], fn: (caller: Caller, ...args: unknown[]) => unknown) =>
    ipcMain.handle(channel, (event, ...args) => fn(callerOf(shell, event, kinds), ...args));
  const listen = (channel: string, fn: (caller: Caller, arg: unknown) => void) =>
    ipcMain.on(channel, (event, arg) => {
      try {
        fn(callerOf(shell, event, MAIN), arg);
      } catch (error) {
        log('warn', `${channel}: ${(error as Error).message}`);
      }
    });

  handle('companies:list', BOTH, (c) => shell.list(c.company.id));
  handle('companies:switch', BOTH, (_c, id, url) => {
    const target = shell.company(V.companyIdArg(id));
    if (!target) throw new Error('No such company.');
    shell.switchTo(target.id, url === undefined ? undefined : V.urlOnOriginArg(url, target.url));
  });
  handle('companies:add', MAIN, (_c, server) => {
    const prefill = V.serverPrefillArg(server);
    shell.showLocal(prefill ? { page: 'add', prefill } : { page: 'add' });
  });
  handle('companies:remove', MAIN, (_c, id) => shell.remove(V.companyIdArg(id)));
  listen('companies:self', (c, info) => shell.reportSelf(c.company.id, V.selfInfoArg(info)));
  listen('badge:set', (c, n) => shell.setNeeds(c.company.id, V.countArg(n)));
  listen('files:origin', (c, origin) => shell.setFilesOrigin(c.company.id, V.filesOriginArg(origin, c.company.url)));

  handle('call:open', MAIN, (c, callId) => shell.openCall(c.company.id, V.callIdArg(callId)));
  // 'return': the person chose to go back to the call in the main window; anything else leaves the call
  handle('call:close', BOTH, (c, ending) => {
    if (shell.call.belongsTo(c.company.id)) shell.call.close(ending === 'return' ? 'return' : 'closed');
  });
  handle('call:on-top', BOTH, (c, on) => {
    const flag = V.boolArg(on);
    if (shell.call.belongsTo(c.company.id)) shell.call.setOnTop(flag);
  });

  handle('app:update-state', BOTH, () => updater.state);
  handle('app:restart', MAIN, () => updater.restart());
  handle('app:check', MAIN, () => updater.check());
  handle('app:show-log', MAIN, () => {
    const file = logPath();
    if (file) electronShell.showItemInFolder(file);
  });
  handle('app:log-tail', MAIN, () => ({ version, lines: logTail() }));
  handle('app:login-item', MAIN, (_c, on) => {
    const flag = V.optionalBoolArg(on);
    if (flag !== undefined) app.setLoginItemSettings({ openAtLogin: flag });
    return app.getLoginItemSettings().openAtLogin;
  });

  // The local pages.
  const local = (event: AnyEvent) => {
    const frame = event.senderFrame;
    if (!shell.isLocalPage(event.sender, frame?.url ?? null, isTopFrame(event, frame))) {
      throw new Error('Refused: not the app page.');
    }
  };
  ipcMain.handle('local:context', (event) => {
    local(event);
    return shell.localContext();
  });
  ipcMain.handle('local:add', (event, text) => {
    local(event);
    if (typeof text !== 'string' || text.length > 2048) throw new V.BadArgument('server');
    return shell.addServer(text);
  });
  ipcMain.handle('local:sign-in', (event) => {
    local(event);
    shell.startPortalSignIn();
  });
  ipcMain.handle('local:retry', (event) => {
    local(event);
    shell.retryLocal();
  });
  ipcMain.handle('local:cancel', (event) => {
    local(event);
    shell.cancelLocal();
  });
}
