// One runner-selected factor for both default timeouts and scripts' explicit waits.
const SLOWDOWN = Number(process.env.TICO_UI_SLOWDOWN ?? 1);
if (!Number.isFinite(SLOWDOWN) || SLOWDOWN <= 0) throw new Error('TICO_UI_SLOWDOWN must be a positive finite number');
const t = ms => Math.round(ms * SLOWDOWN);
function applyTimeouts(contextOrPage) {
  contextOrPage.setDefaultTimeout(t(30000));
  contextOrPage.setDefaultNavigationTimeout(t(30000));
}
module.exports = {SLOWDOWN, t, applyTimeouts};
