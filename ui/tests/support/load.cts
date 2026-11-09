// One runner-selected factor for both default timeouts and scripts' explicit waits.
import type {BrowserContext, Page} from 'playwright';

const SLOWDOWN = Number(process.env.TICO_UI_SLOWDOWN ?? 1);
if (!Number.isFinite(SLOWDOWN) || SLOWDOWN <= 0) throw new Error('TICO_UI_SLOWDOWN must be a positive finite number');
const t = (ms: number): number => Math.round(ms * SLOWDOWN);
function applyTimeouts(contextOrPage: BrowserContext | Page): void {
  contextOrPage.setDefaultTimeout(t(30000));
  contextOrPage.setDefaultNavigationTimeout(t(30000));
}
const api = {SLOWDOWN, t, applyTimeouts};
export type Api = typeof api;
module.exports = api;
