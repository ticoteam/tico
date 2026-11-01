// Tico Recorder's update banner. app.js hands it `status.update` on every view; the banner shows only when
// a downloaded build is ready. Restart installs it now when the recorder is idle, otherwise it says why it waits.
(() => {
  const invoke = (cmd, args = {}) => window.__TAURI__.core.invoke(cmd, args);
  let banner = null;
  let shown = '';

  function render(update) {
    const key = JSON.stringify(update ?? null);
    if (key === shown) return;
    shown = key;
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'update-banner';
      banner.hidden = true;
      banner.setAttribute('role', 'status');
      banner.addEventListener('click', (event) => {
        if (event.target.closest('.update-restart')) invoke('update_install').catch(() => {});
      });
      document.body.append(banner);
    }
    banner.replaceChildren();
    if (update?.state !== 'ready' && update?.state !== 'installed') {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    const text = document.createElement('span');
    const installed = update.state === 'installed';
    if (update.blocked) {
      // The build waits for this to end; nothing is restarted.
      text.textContent = `${installed ? 'Update installed' : 'Update ready'}. ${update.blocked}.`;
      banner.append(text);
      return;
    }
    text.textContent = installed ? 'Update installed · ' : 'Update ready · ';
    const restart = document.createElement('button');
    restart.type = 'button';
    restart.className = 'update-restart';
    restart.textContent = 'Restart';
    banner.append(text, restart);
  }

  window.ticoUpdate = render;
})();
