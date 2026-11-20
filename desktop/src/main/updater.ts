// Updates (platforms.md 4.8): GitHub Releases through electron-updater. Checked at launch and every 6 hours,
// downloaded quietly, installed on quit; "Restart to update" never restarts during a call. Off in development, when
// TICO_NO_UPDATES is set, and in a build with no feed (no app-update.yml: a local `--dir` build, or any build made
// without a `publish` setting); then nothing is ever fetched and the state stays 'none'. The feed is the latest GitHub
// release's latest-*.yml (electron-builder.yml); a release without one yet is no update, not an error (updateErrors.ts).
// An unsigned macOS build cannot install updates; its state reads 'error'.
import { autoUpdater } from 'electron-updater';
import type { UpdateState } from '../bridge';
import { log } from './log';
import { feedMissing, stateAfterError } from './updateErrors';

const EVERY = 6 * 60 * 60 * 1000;

export class Updater {
  state: UpdateState = { state: 'none' };

  constructor(
    readonly enabled: boolean,
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
      if (feedMissing(error)) log('info', 'no update feed on the latest release yet');
      else log('warn', `update failed: ${error.message}`);
      const next = stateAfterError(this.state, error);
      if (next !== this.state) this.set(next);
    });
    void this.check();
    setInterval(() => void this.check(), EVERY).unref();
  }

  async check(): Promise<void> {
    if (!this.enabled || this.state.state === 'ready') return;
    try {
      await autoUpdater.checkForUpdates();
    } catch (error) {
      // The 'error' event has already logged it and set the state.
      if (!feedMissing(error)) log('warn', `update check failed: ${(error as Error).message}`);
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
