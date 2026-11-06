/* ui/app/settings-gateway.js — the Tico Gateway card on Settings > Models: its status line, prices by level, the picks bots
   use, the API keys link and the services (GET /v2/gateway). settings-providers.js draws the card around them.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const GATEWAY_STATUS = {live: 'Live', approval: 'On request', next: 'Coming soon', later: 'Coming soon', planned: 'Coming soon'};
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
// This month's spend against the cap, as the subscriptions' usage bar draws it; none without a cap.
function gatewaySpendBar(c) {
  if (!c?.connected || c.spent_usd == null || !(Number(c.cap_usd) > 0)) return '';
  const used = Math.min(100, Math.round(Number(c.spent_usd) / Number(c.cap_usd) * 100));
  return `<span class="subs-bar models-gw-bar${used >= 100 ? ' bad' : used >= 80 ? ' warn' : ''}" data-gateway-bar aria-hidden="true"><b style="width:${used}%"></b></span>`;
}
// "$0.15 / $0.75": the catalog's price per 1M tokens in and out, which the table's head names once.
const gatewayPerM = price => String(price || '').replace(/ per 1M$/, '');
// Our prices by level and mode, never the model a level runs on. A level per row, by department only if one differs.
function gatewayPricesHTML(m) {
  if (!m?.levels?.length) return '';
  const cell = row => row ? `<td class="num" title="${esc(row.long || '')}">${esc(gatewayPerM(row.price))}</td>` : '<td class="num muted">—</td>';
  return `<table class="models-gw-table" data-gateway-prices><thead><tr><th>Price per 1M, in / out</th><th class="num">Regular</th><th class="num">Fast</th></tr></thead>
    <tbody>${m.levels.map(r => `<tr data-gateway-level="${esc(r.id)}"><td>${esc(r.name)}</td>${cell(r.model)}${cell(r.fast)}</tr>`).join('')}</tbody></table>`;
}
function gatewayPicksHTML(rows) {
  if (!rows?.length) return '';
  return `<table class="models-gw-table" data-gateway-picks><thead><tr><th>Picks in use</th><th>Version</th><th class="num">Bots</th></tr></thead><tbody>${rows.map(r =>
    `<tr data-gateway-pick="${esc(r.id)}"><td>${esc(r.name)}</td><td class="gw-version">${r.version === 'auto' ? 'Auto' : esc(r.version)}</td><td class="num">${r.bots}</td></tr>`).join('')}</tbody></table>`;
}
// Live first, then On request, then Coming soon (GATEWAY_STATUS's order), each with what it is for under its name. A
// service's details name the models it runs on, which this card never shows, so they stay with the catalog.
function gatewayServicesHTML(rows) {
  const order = Object.keys(GATEWAY_STATUS), rank = r => (order.indexOf(r.status) + order.length + 1) % (order.length + 1);
  const sorted = rows.slice().sort((a, b) => rank(a) - rank(b));
  return `<table class="gw-table gw-services" data-gateway-services><thead><tr><th>Service</th><th>Status</th><th>Price</th><th>Tools</th></tr></thead><tbody>${sorted.map(r =>
    `<tr data-gateway-service="${esc(r.id)}"><td class="gw-name">${esc(r.name)}${r.for ? `<span class="gw-sub gw-for">${esc(r.for)}</span>` : ''}</td>
      <td class="gw-status-cell" data-status="${esc(r.status)}">${esc(GATEWAY_STATUS[r.status] || r.status)}</td>
      <td class="gw-price">${esc(r.price || '')}</td><td class="gw-tools">${(r.tools || []).map(t => `<code>${esc(t)}</code>`).join(' ')}</td></tr>`).join('')}</tbody></table>`;
}
// API keys, to use the Gateway from other tools, are made and revoked on app.tico.dev (HQ's portal); the app only links
// there, for the owner (who sees the spend) while the install has a Gateway connection.
const GATEWAY_API_KEYS_URL = 'https://app.tico.dev/gateway#api-keys';
const gatewayApiKeysHTML = () => `<p class="models-gw-keys" data-gateway-api-keys><span>API keys</span>
  <a href="${GATEWAY_API_KEYS_URL}" target="_blank" rel="noopener noreferrer" data-gateway-api-keys-link>Open</a></p>`;
// What the card shows from GET /v2/gateway (owners and admins): prices, the picks in use when there are any, the API
// keys row for the owner of a connected install, and the services folded away.
function gatewayOverviewHTML(v) {
  if (!v) return '';
  const keys = S.me?.role === 'owner' && v.connection?.connected ? gatewayApiKeysHTML() : '';
  const services = v.services?.length ? `<details class="models-adv" data-gateway-services-row><summary>Services</summary>${gatewayServicesHTML(v.services)}</details>` : '';
  return `${gatewayPricesHTML(v.models)}${gatewayPicksHTML(v.picks)}${keys}${services}`;
}
