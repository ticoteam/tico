// The preload for the app's own two local pages (Add company, Cannot reach). Main checks that only those pages call.
import { contextBridge, ipcRenderer } from 'electron';

const platform = process.argv.find((a) => a.startsWith('--tico-platform='))?.slice('--tico-platform='.length) ?? '';

contextBridge.exposeInMainWorld('ticoLocal', {
  platform,
  context: () => ipcRenderer.invoke('local:context'),
  add: (text: string) => ipcRenderer.invoke('local:add', String(text)),
  signIn: () => ipcRenderer.invoke('local:sign-in'),
  retry: () => ipcRenderer.invoke('local:retry'),
  cancel: () => ipcRenderer.invoke('local:cancel'),
});
