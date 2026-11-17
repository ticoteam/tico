/* ui/app/tools-mcp.ts — A remote MCP server in Tools (backend/mcp_connect.py): Tools › Add › MCP server, and the MCP card
   on its tool's page (ui/app/tools-api.ts draws the page; an MCP tool is an API tool whose GET /api/v2/api-tools/<id>
   has `mcp`). Add asks the server what it offers (POST /api/v2/mcp-tools/probe {url} → {token, oauth, dcr}), then adds
   it (POST /api/v2/mcp-tools {name, url, auth: token | oauth, header, token, client_id, client_secret}): a token is
   checked at once; a sign-in goes on to POST /api/v2/api-tools/<id>/sign-in → {authorize_url}, where the browser goes,
   and comes back to the tool's page. The card: the server's address, how many tools its last check listed (and how many
   are read-only), who signed in, and Sign in (again).
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface ToolMcp {
  url: string; transport?: string; auth: 'token' | 'oauth'; header?: string; tools?: number | null; read_only?: number;
  names?: string[]; listed_at?: string | null; signed_in_by?: string | null; signed_in_at?: string | null; client?: string | null;
}
interface ToolMcpProbe {url: string; token: boolean; oauth: boolean; dcr: boolean; open: boolean; server?: string}

// A name from the address when none is typed: api.raygun.com → Raygun.
const tmName = (url: string): string => {
  try {
    const parts = new URL(url).hostname.split('.').filter(p => !['www', 'api', 'mcp', 'app'].includes(p));
    const word = (parts.length > 1 ? parts[parts.length - 2] : parts[0]) || '';
    return word.charAt(0).toUpperCase() + word.slice(1);
  } catch { return ''; }
};

// ----------------------------------------------------------------- the card
function tmCard(d: ToolApiData): string {
  const m = d.mcp;
  if (!m) return '';
  const row = (dt: string, dd: string) => `<dt>${esc(dt)}</dt><dd>${dd}</dd>`;
  const tools = m.tools == null ? '—' : `${m.tools}${m.read_only ? ` <span class="muted">· ${m.read_only} read-only</span>` : ''}`;
  const who = m.signed_in_by ? personDisplay(m.signed_in_by.replace(/^human:/, '')) : '';
  const signed = m.auth === 'oauth'
    ? row('Signed in', who ? `${esc(who)}${m.signed_in_at ? ` <span class="muted" title="${esc(fmt(m.signed_in_at))}">· ${esc(ago(m.signed_in_at))}</span>` : ''}` : '—')
    : '';
  const names = (m.names || []).length
    ? `<details class="tt-mcp-names"><summary>${(m.names || []).length}</summary><p class="mono muted">${(m.names || []).map(n => esc(n)).join(' · ')}</p></details>` : '';
  const act = m.auth === 'oauth' && d.can_manage ? `<button type="button" class="ghost small settings-head-act" data-tm-sign-in>${m.signed_in_by ? 'Sign in again' : 'Sign in'}</button>` : '';
  return `<section class="card" id="tool-mcp"><header><h2>MCP</h2>${act}</header>
    <dl class="tt-kv">${row('Server', `<span class="mono">${esc(m.url)}</span>`)}${row('Tools', tools)}${signed}${
      m.auth === 'token' ? row('Header', `<span class="mono">${esc(m.header === 'Authorization' ? 'Authorization: Bearer' : m.header || '')}</span>`) : ''}</dl>${names}</section>`;
}

async function tmSignIn(id: string): Promise<void> {
  const r: {authorize_url?: string} = await post(`/v2/api-tools/${encodeURIComponent(id)}/sign-in`, {});
  if (r?.authorize_url) location.href = r.authorize_url;
}

// The card's buttons, from tools-api.ts's click handler: true when the click was one of them.
function tmClick(b: HTMLButtonElement, id: string): boolean {
  if (b.dataset.tmSignIn === undefined) return false;
  b.disabled = true;
  tmSignIn(id).catch(e => { toast(errorMessage(e, ''), true); b.disabled = false; });
  return true;
}

// ----------------------------------------------------------------- Add › MCP server
function toolsMcpCreate(): void {
  const dialog = whDialog('Add MCP server', `<label>Address<input name="url" type="url" maxlength="500" required autocomplete="off" spellcheck="false" placeholder="https://mcp.acme.example/mcp"></label>
    <label>Name<input name="name" maxlength="80" autocomplete="off" placeholder="Acme"></label>
    <div data-tm-ways></div>`, '<button class="primary" type="submit" data-tm-go>Next</button>');
  const ways = dialog.querySelector<HTMLElement>('[data-tm-ways]')!, go = dialog.querySelector<HTMLButtonElement>('[data-tm-go]')!;
  const url = dialog.querySelector<HTMLInputElement>('[name=url]')!, nameBox = dialog.querySelector<HTMLInputElement>('[name=name]')!;
  let found: ToolMcpProbe | null = null;
  url.oninput = () => { if (found) { found = null; ways.innerHTML = ''; go.textContent = 'Next'; } };
  const draw = (f: ToolMcpProbe) => {
    const choose = f.oauth ? `<div class="tt-mcp-way" role="radiogroup" aria-label="Connect with">
        <label><input type="radio" name="auth" value="oauth" checked> Sign in</label><label><input type="radio" name="auth" value="token"> Paste a token</label></div>` : '<input type="hidden" name="auth" value="token">';
    const client = f.oauth && !f.dcr ? `<div data-tm-for="oauth"><label>Client id<input name="client_id" maxlength="500" autocomplete="off" spellcheck="false" required></label>
        <label>Client secret<input name="client_secret" type="password" maxlength="2000" autocomplete="off"></label></div>` : '';
    ways.innerHTML = `${choose}${client}<div data-tm-for="token"${f.oauth ? ' hidden' : ''}>
        <label>Header<select name="header_kind"><option value="">Authorization: Bearer</option><option value="other">Other header</option></select></label>
        <label data-tm-header hidden>Header name<input name="header" maxlength="60" autocomplete="off" spellcheck="false" placeholder="X-Api-Key"></label>
        <label>Token<input name="token" type="password" maxlength="100000" autocomplete="off" spellcheck="false"></label></div>`;
    const pick = () => {
      const auth = (ways.querySelector<HTMLInputElement>('[name=auth]:checked') || ways.querySelector<HTMLInputElement>('[name=auth]'))?.value || 'token';
      ways.querySelectorAll<HTMLElement>('[data-tm-for]').forEach(el => {
        el.hidden = el.dataset.tmFor !== auth;
        el.querySelectorAll<HTMLInputElement>('input[name=client_id], input[name=token]').forEach(i => { i.required = !el.hidden; });
      });
      go.textContent = auth === 'oauth' ? 'Sign in' : 'Add';
    };
    ways.querySelectorAll<HTMLInputElement>('[name=auth]').forEach(r => { r.onchange = pick; });
    const kind = ways.querySelector<HTMLSelectElement>('[name=header_kind]')!, named = ways.querySelector<HTMLElement>('[data-tm-header]')!;
    kind.onchange = () => { named.hidden = kind.value !== 'other'; };
    pick();
  };
  whSubmit(dialog, async (data: FormData) => {
    const address = String(data.get('url') || '').trim();
    const name = String(data.get('name') || '').trim() || tmName(address);
    if (!found) {
      found = await post('/v2/mcp-tools/probe', {url: address}) as ToolMcpProbe;
      if (!nameBox.value) nameBox.value = found.server || name;
      draw(found);
      return;
    }
    const auth = String(data.get('auth') || 'token');
    if (!name) throw new Error('Name it.');
    const header = String(data.get('header_kind') || '') === 'other' ? String(data.get('header') || '').trim() : '';
    if (auth === 'token' && String(data.get('header_kind') || '') === 'other' && !header) throw new Error('Name the header.');
    const body: Record<string, string> = {name, url: address, auth};
    if (auth === 'token') Object.assign(body, {token: String(data.get('token') || ''), ...(header ? {header} : {})});
    else {
      const id = String(data.get('client_id') || '').trim(), secret = String(data.get('client_secret') || '');
      if (id) body.client_id = id;
      if (secret) body.client_secret = secret;
    }
    const made: {id?: string} = await post('/v2/mcp-tools', body);
    if (!made?.id) throw new Error('Try again');
    if (auth === 'oauth') { await tmSignIn(made.id); return; }
    dialog.close(); toast('Added');
    location.hash = '#/tools/api/' + encodeURIComponent(made.id);
  });
}
