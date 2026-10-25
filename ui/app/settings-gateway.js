/* ui/app/settings-gateway.js — Settings > Gateway: the connection's status, the picks bots use, the services
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// One flat page (GET /v2/gateway). The key and the connect controls stay in Settings > Computers > Models.
const GATEWAY_STATUS = {live: 'Live', approval: 'On request', next: 'Coming soon', later: 'Coming soon', planned: 'Coming soon'};
let GATEWAY_ASKED = 0;
const gatewayMoney = usd => `$${Number(usd).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
// "Connected · $12.40 of $100.00 this month", or why it is not.
function gatewayStatusLine(c) {
  if (!c.connected) return ['fail', 'Not connected'];
  if (c.state === 'cap_reached') return ['fail', 'Cap reached'];
  if (c.state === 'revoked' || c.working === false) return ['fail', 'Not working'];
  const spend = c.spent_usd != null && c.cap_usd != null ? ` · ${gatewayMoney(c.spent_usd)} of ${gatewayMoney(c.cap_usd)} this month`
    : c.spent_usd != null ? ` · ${gatewayMoney(c.spent_usd)} this month` : '';
  return ['ok', `Connected${spend}`];
}
function gatewayPicksHTML(rows) {
  if (!rows.length) return '<p class="muted">No bot uses a Gateway model.</p>';
  return `<table class="gw-table" data-gateway-picks><thead><tr><th>Pick</th><th>Model</th><th>Version</th><th class="num">Bots</th><th>Price</th></tr></thead><tbody>${rows.map(r =>
    `<tr data-gateway-pick="${esc(r.id)}"><td class="gw-name">${esc(r.name)}</td><td class="gw-model">${esc(r.model)}</td>
      <td class="gw-version">${r.version === 'auto' ? 'Auto' : esc(r.version)}</td><td class="num gw-bots">${r.bots}<span class="gw-bots-word"> ${r.bots === 1 ? 'bot' : 'bots'}</span></td><td class="gw-price">${esc(r.price)}</td></tr>`).join('')}</tbody></table>`;
}
function gatewayServicesHTML(rows) {
  return `<table class="gw-table" data-gateway-services><thead><tr><th>Service</th><th>Status</th><th>Price</th><th>Tools</th></tr></thead><tbody>${rows.map(r =>
    `<tr data-gateway-service="${esc(r.id)}"><td class="gw-name">${esc(r.name)}</td><td class="gw-status-cell" data-status="${esc(r.status)}">${esc(GATEWAY_STATUS[r.status] || r.status)}</td>
      <td class="gw-price">${esc(r.price || '')}</td><td class="gw-tools">${(r.tools || []).map(t => `<code>${esc(t)}</code>`).join(' ')}</td></tr>`).join('')}</tbody></table>`;
}
async function renderSettingsGateway() {
  const host = $('#set-gateway-page');
  if (!host) return;
  const asked = ++GATEWAY_ASKED;
  let v;
  try { v = await get('/v2/gateway', {fresh: true}); }
  catch (e) { if (asked === GATEWAY_ASKED) host.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (asked !== GATEWAY_ASKED) return;
  const [tone, line] = gatewayStatusLine(v.connection || {});
  const level = v.decide_level ? v.decide_level.charAt(0).toUpperCase() + v.decide_level.slice(1) : '';
  host.innerHTML = `<section class="card"><p class="billing-status gw-status" data-tone="${tone === 'fail' ? 'bad' : tone}" role="status" data-gateway-line>${esc(line)}</p>
      <p class="muted">${level ? `Decisions: ${esc(level)}. ` : ''}<a href="#/settings/models" data-gateway-key-link>Key and connection</a></p></section>
    <section class="card"><header><h2>Picks in use</h2></header>${gatewayPicksHTML(v.picks || [])}</section>
    <section class="card"><header><h2>Services</h2></header>${gatewayServicesHTML(v.services || [])}</section>`;
}
