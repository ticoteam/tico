/* ui/app/settings-gateway.js — Settings > Gateway: the connection's status, the picks bots use, the models, the services, the API keys link
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
// "Text, images, PDFs" or "Text only": what a model reads.
const GATEWAY_INPUTS = {text: 'text', image: 'images', pdf: 'PDFs'};
function gatewayInputs(list) {
  const words = (list || []).map(k => GATEWAY_INPUTS[k] || k);
  if (!words.length || (words.length === 1 && words[0] === 'text')) return 'Text only';
  return words.join(', ').replace(/^./, c => c.toUpperCase());
}
const gatewayModelCell = m => m ? `${esc(m.model)}<span class="gw-sub">${esc(gatewayInputs(m.inputs))}</span>` : '';
const gatewayPriceCell = m => m ? `${esc(m.price)}${m.long ? `<span class="gw-sub">${esc(m.long)}</span>` : ''}` : '';
// The Fast model's window only where it differs from the normal one: "1M · Fast 1.05M".
function gatewayContext(r) {
  const own = modelContextWords(r.model?.context), fast = modelContextWords(r.fast?.context);
  return fast && fast !== own ? `${own || '—'} · Fast ${fast}` : own;
}
// The models by level (by department and level when a department differs), then Meeting, Decide and Classify.
function gatewayModelsHTML(m) {
  if (!m?.levels?.length) return '';
  const levels = `<table class="gw-table gw-levels" data-gateway-models><thead><tr><th>Level</th><th>Model</th><th>Price</th><th>Fast</th><th>Fast price</th><th>Context</th></tr></thead><tbody>${m.levels.map(r =>
    `<tr data-gateway-level="${esc(r.id)}"><td class="gw-name">${esc(r.name)}</td><td class="gw-model">${gatewayModelCell(r.model)}</td><td class="gw-price">${gatewayPriceCell(r.model)}</td>
      <td class="gw-fast">${r.fast ? `<span class="gw-word">Fast · </span>${gatewayModelCell(r.fast)}` : ''}</td><td class="gw-price gw-fast-price">${gatewayPriceCell(r.fast)}</td>
      <td class="gw-ctx">${gatewayContext(r) ? `<span class="gw-word">Context </span>${esc(gatewayContext(r))}` : ''}</td></tr>`).join('')}</tbody></table>`;
  const same = m.same && m.departments?.length ? `<p class="muted gw-same" data-gateway-departments>Same in every department: ${esc(m.departments.join(', '))}</p>` : '';
  const others = [m.meeting, ...(m.decisions || [])].filter(Boolean);
  const rest = others.length ? `<table class="gw-table gw-others" data-gateway-other><tbody>${others.map(r =>
    `<tr data-gateway-other-row="${esc(r.id || 'meeting')}"><td class="gw-name">${esc(r.name)}</td><td class="gw-model">${esc(r.model)}</td><td class="gw-price">${esc(r.price)}</td></tr>`).join('')}</tbody></table>` : '';
  return `<section class="card"><header><h2>Models</h2></header>${levels}${same}${rest}</section>`;
}
// Live first, then On request, then Coming soon (GATEWAY_STATUS's order). Each service's `for` sits under its name, and
// its details (label and value pairs) on a line of their own under the row.
function gatewayServicesHTML(rows) {
  const order = Object.keys(GATEWAY_STATUS), rank = r => (order.indexOf(r.status) + order.length + 1) % (order.length + 1);
  const sorted = rows.slice().sort((a, b) => rank(a) - rank(b));
  return `<table class="gw-table gw-services" data-gateway-services><thead><tr><th>Service</th><th>Status</th><th>Price</th><th>Tools</th></tr></thead><tbody>${sorted.map(r => {
    const details = r.details || [];
    return `<tr data-gateway-service="${esc(r.id)}"${details.length ? ' class="gw-has-details"' : ''}><td class="gw-name">${esc(r.name)}${r.for ? `<span class="gw-sub gw-for">${esc(r.for)}</span>` : ''}</td>
      <td class="gw-status-cell" data-status="${esc(r.status)}">${esc(GATEWAY_STATUS[r.status] || r.status)}</td>
      <td class="gw-price">${esc(r.price || '')}</td><td class="gw-tools">${(r.tools || []).map(t => `<code>${esc(t)}</code>`).join(' ')}</td></tr>${details.length
      ? `<tr class="gw-details-row" data-gateway-details="${esc(r.id)}"><td colspan="4"><dl class="gw-details">${details.map(d =>
        `<div><dt>${esc(d.label)}</dt><dd>${esc(d.value)}</dd></div>`).join('')}</dl></td></tr>` : ''}`;
  }).join('')}</tbody></table>`;
}
// API keys, to use the Gateway from other tools, are made and revoked on app.tico.dev (HQ's portal); the app only links
// there, for the owner (who sees the spend) while the install has a Gateway connection.
const GATEWAY_API_KEYS_URL = 'https://app.tico.dev/gateway#api-keys';
function gatewayApiKeysHTML() {
  return `<section class="card"><table class="gw-table" data-gateway-api-keys><tbody><tr><td class="gw-name">API keys</td>
      <td class="gw-note">Use the Gateway from other tools</td>
      <td class="gw-open"><a href="${GATEWAY_API_KEYS_URL}" target="_blank" rel="noopener noreferrer" data-gateway-api-keys-link>Open</a></td></tr></tbody></table></section>`;
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
  const keys = S.me?.role === 'owner' && v.connection?.connected ? gatewayApiKeysHTML() : '';
  const level = v.decide_level ? v.decide_level.charAt(0).toUpperCase() + v.decide_level.slice(1) : '';
  host.innerHTML = `<section class="card"><p class="billing-status gw-status" data-tone="${tone === 'fail' ? 'bad' : tone}" role="status" data-gateway-line>${esc(line)}</p>
      <p class="muted">${level ? `Decisions: ${esc(level)}. ` : ''}<a href="#/settings/models" data-gateway-key-link>Key and connection</a></p></section>
    ${keys}<section class="card"><header><h2>Picks in use</h2></header>${gatewayPicksHTML(v.picks || [])}</section>
    ${gatewayModelsHTML(v.models)}<section class="card"><header><h2>Services</h2></header>${gatewayServicesHTML(v.services || [])}</section>`;
}
