// The whole preload for company pages: `window.tico` and nothing else. No Node API passes through; every call is a
// named IPC message that main checks (sender and arguments). Sandboxed, so this file may import only 'electron'.
//
// It also marks the page for the window frame: <html data-desktop="mac|windows|linux"> and two CSS variables,
// --desktop-inset-left and --desktop-inset-top, the room to keep clear of the macOS window buttons at the window's top
// left corner (0px elsewhere and in full screen, where <html data-desktop-fullscreen> is set).
import { contextBridge, ipcRenderer } from 'electron';
import type { CallWindowState, CompanyRef, TicoDesktop, UpdateState } from './bridge';

const arg = (name: string): string => {
  const prefix = `--${name}=`;
  return process.argv.find((a) => a.startsWith(prefix))?.slice(prefix.length) ?? '';
};

const platform = (['mac', 'windows', 'linux'] as const).find((p) => p === arg('tico-platform')) ?? 'linux';
const [insetLeft = 0, insetTop = 0] = arg('tico-inset').split(',').map((n) => Number.parseInt(n, 10) || 0);

function listen<T>(channel: string) {
  return (cb: (value: T) => void): (() => void) => {
    const f = (_event: unknown, value: T) => cb(value);
    ipcRenderer.on(channel, f);
    return () => {
      ipcRenderer.off(channel, f);
    };
  };
}

const tico: TicoDesktop = {
  version: arg('tico-shell-version'),
  platform,
  companies: {
    list: () => ipcRenderer.invoke('companies:list') as Promise<CompanyRef[]>,
    switchTo: (id, url) => ipcRenderer.invoke('companies:switch', id, url) as Promise<void>,
    add: (server) => ipcRenderer.invoke('companies:add', server) as Promise<void>,
    remove: (id) => ipcRenderer.invoke('companies:remove', id) as Promise<void>,
    reportSelf: (info) => ipcRenderer.send('companies:self', info),
    onChange: listen<CompanyRef[]>('companies:changed'),
  },
  setBadge: (needs) => ipcRenderer.send('badge:set', needs),
  callWindow: {
    open: (callId) => ipcRenderer.invoke('call:open', callId) as Promise<void>,
    close: (ending) => ipcRenderer.invoke('call:close', ending) as Promise<void>,
    setOnTop: (on) => ipcRenderer.invoke('call:on-top', on) as Promise<void>,
    onState: listen<CallWindowState>('call:state'),
    current: () => ipcRenderer.invoke('call:current') as Promise<string | null>,
  },
  signingOut: () => ipcRenderer.invoke('session:signing-out') as Promise<void>,
  app: {
    updateState: () => ipcRenderer.invoke('app:update-state') as Promise<UpdateState>,
    restartToUpdate: () => ipcRenderer.invoke('app:restart') as Promise<void>,
    checkForUpdates: () => ipcRenderer.invoke('app:check') as Promise<void>,
    showLog: () => ipcRenderer.invoke('app:show-log') as Promise<void>,
    logTail: () => ipcRenderer.invoke('app:log-tail') as Promise<{ version: string; lines: string[] }>,
    openAtLogin: (on) => ipcRenderer.invoke('app:login-item', on) as Promise<boolean>,
  },
  setFilesOrigin: (origin) => ipcRenderer.send('files:origin', origin),
};

contextBridge.exposeInMainWorld('tico', tico);

let fullscreen = false;
function markFrame(): void {
  const root = document.documentElement;
  if (!root) return;
  root.dataset.desktop = platform;
  root.toggleAttribute('data-desktop-fullscreen', fullscreen);
  root.style.setProperty('--desktop-inset-left', `${fullscreen ? 0 : insetLeft}px`);
  root.style.setProperty('--desktop-inset-top', `${fullscreen ? 0 : insetTop}px`);
}
// The preload runs before <html> exists. Mark it the moment the parser adds it, so the page's first script and first
// paint already see the marks.
if (document.documentElement) {
  markFrame();
} else {
  const watch = new MutationObserver(() => {
    if (!document.documentElement) return;
    watch.disconnect();
    markFrame();
  });
  watch.observe(document, { childList: true });
}
document.addEventListener('DOMContentLoaded', markFrame);
ipcRenderer.on('frame:fullscreen', (_event, on: unknown) => {
  fullscreen = on === true;
  markFrame();
});
