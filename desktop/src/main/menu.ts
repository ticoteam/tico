// The app menu and the Dock menu. Cmd 1 to 9 switch companies here, in the main process, so they work wherever focus
// is (platforms.md 4.4). The page's own shortcuts live in web/; nothing here takes a key the page uses.
import { app, Menu, shell as electronShell, type MenuItemConstructorOptions } from 'electron';
import { hostOf } from './address';
import { logPath } from './log';
import type { Shell } from './shell';
import type { Updater } from './updater';

export function buildMenus(shell: Shell, updater: Updater): void {
  const mac = process.platform === 'darwin';
  const activeContents = () => {
    const id = shell.activeId;
    return id ? shell.contentsOf(id) : null;
  };
  const zoom = (by: number | null) => {
    const wc = activeContents();
    if (wc) wc.setZoomLevel(by === null ? 0 : wc.getZoomLevel() + by);
  };

  const companyItems: MenuItemConstructorOptions[] = shell.companies.map((c, i) => ({
    label: c.name || hostOf(c.url),
    type: 'radio',
    checked: c.id === shell.activeId,
    accelerator: i < 9 ? `CmdOrCtrl+${i + 1}` : undefined,
    click: () => shell.switchTo(c.id),
  }));
  const active = shell.companies.find((c) => c.id === shell.activeId);

  const template: MenuItemConstructorOptions[] = [
    ...(mac
      ? [
          {
            label: app.getName(),
            submenu: [
              { role: 'about' },
              { label: 'Check for updates…', click: () => void updater.check() },
              { type: 'separator' },
              { role: 'services' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          } satisfies MenuItemConstructorOptions,
        ]
      : [{ label: 'File', submenu: [{ label: 'Check for updates…', click: () => void updater.check() }, { role: 'quit' }] } satisfies MenuItemConstructorOptions]),
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => activeContents()?.reload() },
        { type: 'separator' },
        { label: 'Actual size', accelerator: 'CmdOrCtrl+0', click: () => zoom(null) },
        { label: 'Zoom in', accelerator: 'CmdOrCtrl+Plus', click: () => zoom(0.5) },
        { label: 'Zoom out', accelerator: 'CmdOrCtrl+-', click: () => zoom(-0.5) },
        { type: 'separator' },
        {
          label: 'Full screen',
          accelerator: mac ? 'Ctrl+Cmd+F' : 'F11',
          click: () => shell.win.setFullScreen(!shell.win.isFullScreen()),
        },
        ...(app.isPackaged
          ? []
          : [{ label: 'Developer tools', accelerator: 'Alt+CmdOrCtrl+I', click: () => activeContents()?.toggleDevTools() }]),
      ],
    },
    {
      label: 'Company',
      submenu: [
        ...companyItems,
        ...(companyItems.length ? [{ type: 'separator' as const }] : []),
        { label: 'Add company…', click: () => shell.showLocal({ page: 'add' }) },
        ...(active
          ? [{ label: `Remove ${active.name || hostOf(active.url)}…`, click: () => void shell.remove(active.id) }]
          : []),
      ],
    },
    {
      label: 'Window',
      submenu: [
        { label: 'Minimize', accelerator: 'CmdOrCtrl+M', click: () => shell.win.minimize() },
        { label: 'Zoom', click: () => (shell.win.isMaximized() ? shell.win.unmaximize() : shell.win.maximize()) },
        { label: 'Close', accelerator: 'CmdOrCtrl+W', click: () => shell.win.close() },
        ...(mac ? [{ type: 'separator' as const }, { role: 'front' as const }] : []),
      ],
    },
    {
      role: 'help',
      submenu: [
        {
          label: 'Show the app’s log',
          click: () => {
            const file = logPath();
            if (file) electronShell.showItemInFolder(file);
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));

  if (mac) {
    app.dock?.setMenu(
      Menu.buildFromTemplate([
        ...shell.companies.map((c) => ({ label: c.name || hostOf(c.url), click: () => shell.switchTo(c.id) })),
        { type: 'separator' },
        { label: 'Add company…', click: () => shell.showLocal({ page: 'add' }) },
      ]),
    );
  }
}
