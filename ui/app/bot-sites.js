/* ui/app/bot-sites.js — A bot's site list: sites whose traffic leaves through an exit computer (backend/exits.py).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Drawn for the people who manage the bot; the server answers 403 to anyone else, and the card stays hidden.
// A row is a host (`shop.acme.example`, or `*.acme.example` for the names under it) and where it goes: Direct, or one
// of the computers that take this bot as an exit. Only listed sites ever go through an exit.
function botSitesRowHTML(site, exits) {
  const options = [{exit: 'direct', name: 'Direct'}, ...exits];
  if (site.exit !== 'direct' && !exits.some(x => x.exit === site.exit)) options.push({exit: site.exit, name: `${site.exit_name || 'Computer'} (off)`});
  return `<div class="row bot-site" data-bot-site>
    <input type="text" class="settings-inline-select" data-site-host value="${esc(site.host)}" placeholder="shop.acme.example" aria-label="Site" maxlength="300">
    <select class="settings-inline-select" data-site-exit aria-label="Goes out through">${options.map(x =>
      `<option value="${esc(x.exit)}"${x.exit === site.exit ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}</select>
    <button type="button" class="ghost" data-site-remove aria-label="Remove site">✕</button></div>`;
}
async function botSitesPaint(slug) {
  const card = $('#bot-sites-card'), host = $('#bot-sites'); if (!card || !host) return;
  let view;
  try { view = await get(`/v2/bots/${encodeURIComponent(slug)}/sites`); } catch { card.hidden = true; return; }
  if (!Array.isArray(view?.sites) || !Array.isArray(view?.exits)) { card.hidden = true; return; }
  if (BOT?.slug !== slug || !host.isConnected) return;
  card.hidden = false;
  const draw = sites => {
    host.innerHTML = `${view.exits.length ? '' : '<p class="muted">No computer is an exit for this bot. Turn on Exit for bots in a computer\'s settings.</p>'}
      <div data-bot-site-rows>${sites.map(s => botSitesRowHTML(s, view.exits)).join('')}</div>
      <div class="row"><button type="button" class="ghost" data-site-add>Add site</button><button type="button" class="primary" data-sites-save>Save</button></div>`;
  };
  draw(view.sites);
  host.onclick = async event => {
    if (event.target.closest('[data-site-add]')) {
      host.querySelector('[data-bot-site-rows]').insertAdjacentHTML('beforeend', botSitesRowHTML({host: '', exit: view.exits[0]?.exit || 'direct'}, view.exits));
      host.querySelector('[data-bot-site]:last-child [data-site-host]')?.focus();
      return;
    }
    const remove = event.target.closest('[data-site-remove]');
    if (remove) { remove.closest('[data-bot-site]').remove(); return; }
    const save = event.target.closest('[data-sites-save]'); if (!save) return;
    const sites = [...host.querySelectorAll('[data-bot-site]')].map(row => ({
      host: row.querySelector('[data-site-host]').value.trim(), exit: row.querySelector('[data-site-exit]').value})).filter(s => s.host);
    save.disabled = true;
    try {
      view = await put(`/v2/bots/${encodeURIComponent(slug)}/sites`, {sites});
      draw(view.sites); toast('Saved');
    } catch (error) { toast(error.message, true); save.disabled = false; }
  };
}
