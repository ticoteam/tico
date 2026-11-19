// Updates (platforms.md 4.8): GitHub Releases through electron-updater. Checked at launch and every 6 hours,
// downloaded quietly, installed on quit; "Restart to update" never restarts during a call. Off in development and
// when TICO_NO_UPDATES is set. An unsigned macOS build cannot install updates; its state reads 'error'.
import { autoUpdater } from 'electron-updater';
import type { UpdateState } from '../bridge';
import { log } from './log';

const EVERY = 6 * 60 * 60 * 1000;

export class Updater {
  state: UpdateState = { state: 'none' };

  constructor(
    private readonly enabled: boolean,
    private readonly callLive: () => boolean,
    private readonly changed: () => void,
  ) {}

  start(): void {
    if (!this.enabled) return;
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.logger = null;
    autoUpdater.on('update-available', (info) => this.set({ state: 'downloading', version: info.version }));
    autoUpdater.on('update-downloaded', (info) => this.set({ state: 'ready', version: info.version }));
    autoUpdater.on('error', (error) => {
      log('warn', `update failed: ${error.message}`);
      if (this.state.state !== 'ready') this.set({ state: 'error' });
    });
    void this.check();
    setInterval(() => void this.check(), EVERY).unref();
  }

  async check(): Promise<void> {
    if (!this.enabled || this.state.state === 'ready') return;
    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      log('warn', `update check failed: ${(error as Error).message}`);
    }
  }

  restart(): void {
    if (this.state.state !== 'ready') throw new Error('No update is ready.');
    if (this.callLive()) throw new Error('A call is live. Restart after it ends.');
    autoUpdater.quitAndInstall();
  }

  private set(state: UpdateState): void {
    this.state = state;
    this.changed();
  }
}
