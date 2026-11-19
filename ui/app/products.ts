/* ui/app/products.ts — Company › Products: our products, the record that joins Finance, Legal, Growth and Customers.
   Each is a Contacts Product record our company owns, with eight fields: name, kind, status, tax kind, owner, the
   agreement it is sold under, its code (repositories) and the vendors it depends on. Each also carries its risks: the
   risks on matters about it and on the agreements it is sold under, and how many of those matters are open.
   Backend: backend/products.py (GET/POST /api/v2/products, GET/POST /api/v2/products/<key>); a product's matters are
   GET /api/v2/matters?product=<key> (backend/risk.py).
   Addresses:
     #/products          the list
     #/products/<key>    one product (key: its id after product/)
   The route and its test live in the shell (ui/app/pages-shell.ts). Classic script: globals are shared with ui/app/. */
'use strict';

interface PrFile { id: string; title: string; page: boolean }
interface PrLink { id: string; name: string; link_id: string; file?: PrFile | null }
interface PrRisk { id: string; key: string; label: string; via: ('matter' | 'agreement')[] }
interface Product {
  id: string; key: string; name: string;
  kind: string | null; status: string | null; tax_kind: string | null; owner_teammate: string | null;
  repositories: string[]; agreements: PrLink[]; depends_on: PrLink[];
  open_matters: number; risks: PrRisk[];
}
// A row of GET /v2/matters: the task (the fields this page reads) and the risks it carries.
interface PrMatter { task: { id: string; label?: string | null; title?: string | null; status: string } }
type PrChoice = 'kind' | 'status' | 'tax_kind';
type PrList = 'repositories' | 'agreements' | 'depends_on';

// The select fields' options, by the keys the server takes (migration product_fields), in its order.
const PR_CHOICES: Record<PrChoice, [string, string][]> = {
  kind: [['software', 'Software'], ['service', 'Service'], ['goods', 'Goods']],
  status: [['planned', 'Planned'], ['beta', 'Beta'], ['live', 'Live'], ['retired', 'Retired']],
  tax_kind: [['software_service', 'Software'], ['service', 'Service'], ['goods', 'Goods'], ['digital_goods', 'Digital goods'],
    ['not_taxed', 'Not taxed'], ['unknown', 'Unknown']],
};
const PR_FIELD_NAMES: Record<PrChoice | 'owner_teammate', string> = {kind: 'Kind', status: 'Status', tax_kind: 'Tax kind', owner_teammate: 'Owner'};

let PRP: { run: number } | null = null;   // the page on screen: a late answer for a page left behind draws nothing
let PR_MATTERS: { key: string; rows: PrMatter[] } | null = null;   // the matters of the product on screen
let PR_RUN = 0;   // never reset, so a page opened after leaving Products never takes an earlier page's answer

const prHref = (key: string): string => PRODUCTS_ROUTE + '/' + encodeURIComponent(key);
// A risk opens its page on Risk (#/risk/r/<slug>, the slug its key after risk:).
const prRiskLink = (r: PrRisk): string => `<a href="${RISK_ROUTE}/r/${encodeURIComponent(r.key.replace(/^risk:/, ''))}">${esc(r.label)}</a>`;
const prRisks = (p: Product): string => p.risks.map(prRiskLink).join(', ');
const prLive = (run: number): boolean => PRP !== null && PRP.run === run;
const prErr = (e: unknown): string => `<p class="err" data-pr-error>${esc(errorMessage(e, 'Something went wrong.'))}</p>`;
const prWord = (field: PrChoice, key: string | null): string => key ? (PR_CHOICES[field].find(([k]) => k === key)?.[1] ?? key) : '';
const prWho = (actor: string | null): string => actor ? String(actorLabel(actor)) : '';
// A document opens its file (a page in Tico, any other file as itself); without one it is its name alone.
const prAgreement = (a: PrLink): string => a.file
  ? `<a href="${esc(a.file.page ? pageHref(a.file.id) : `${API}/v2/files/${encodeURIComponent(a.file.id)}`)}"${a.file.page ? '' : ' target="_blank" rel="noopener"'}>${esc(a.name)}</a>`
  : esc(a.name);

// ----------------------------------------------------------------- the list
async function pageProducts(): Promise<void> {
  const run = ++PR_RUN;
  PRP = { run };
  const main = document.getElementById('main');
  if (!main) return;
  const head = pageHead({title: 'Products', hook: 'pr-head', add: {label: 'Add product', attrs: 'data-pr-add'}, more: PAGE_HEAD_COPY});
  main.innerHTML = `<div class="tt pr" id="pr-root" data-pr-page="list">${head}<div data-pr-body>${skelRows(4)}</div></div>`;
  main.querySelector('[data-pr-add]')?.addEventListener('click', () => prAddDialog());
  let products: Product[];
  try {
    products = (await get<{ products: Product[] }>('/v2/products', { fresh: true })).products;
  } catch (e) {
    const body = main.querySelector('[data-pr-body]');
    if (prLive(run) && body) body.innerHTML = prErr(e);
    return;
  }
  const body = main.querySelector<HTMLElement>('[data-pr-body]');
  if (!prLive(run) || !body) return;
  if (!products.length) { body.innerHTML = '<div class="empty" data-pr-empty>No products yet.</div>'; return; }
  body.innerHTML = prTable(products);
  body.querySelector('tbody')?.addEventListener('click', ev => {
    const at = eventElement(ev);
    if (!at || at.closest('a, button')) return;
    const href = at.closest('tr')?.querySelector('a[href]')?.getAttribute('href');
    if (href) location.hash = href;
  });
}
// The name, then only the columns some row has a value for (style guide, Tables: no column that says nothing).
function prTable(rows: readonly Product[]): string {
  const cols: {head: string; cls: string; cell: (p: Product) => string}[] = [
    {head: 'Kind', cls: 'muted', cell: p => esc(prWord('kind', p.kind))},
    {head: 'Status', cls: 'muted', cell: p => esc(prWord('status', p.status))},
    {head: 'Tax kind', cls: 'muted', cell: p => esc(prWord('tax_kind', p.tax_kind))},
    {head: 'Agreement', cls: 'pr-agreement', cell: p => p.agreements.map(prAgreement).join(', ')},
    {head: 'Owner', cls: 'muted', cell: p => esc(prWho(p.owner_teammate))},
    {head: 'Risks', cls: '', cell: prRisks},
    {head: 'Matters', cls: 'muted', cell: p => p.open_matters ? `${p.open_matters} open` : ''},
  ];
  const shown = cols.filter(c => rows.some(r => c.cell(r) !== ''));
  return `<table class="tt-list pr-list"><thead><tr><th>Name</th>${shown.map(c => `<th>${c.head}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(p => `<tr data-pr-row="${esc(p.key)}"><td class="pr-name"><a href="${prHref(p.key)}"><strong>${esc(p.name)}</strong></a></td>
      ${shown.map(c => `<td class="${c.cls}" data-label="${c.head}">${c.cell(p)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// A small dialog with one name field: Add product, and Rename on a product's page.
function prNameDialog(title: string, save: string, value: string, work: (name: string) => Promise<void>): void {
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal pr-dlg';
  dlg.setAttribute('aria-label', title);
  dlg.innerHTML = `<div class="tmodal-head"><b>${esc(title)}</b><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
    <form class="tmodal-body pr-form" novalidate><input name="name" maxlength="200" aria-label="Name" placeholder="Name" value="${esc(value)}" required autocomplete="off">
      <p class="err" data-error hidden></p>
      <div class="pr-foot"><button type="button" class="ghost" data-close>Cancel</button><button type="submit" class="primary">${esc(save)}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll<HTMLElement>('[data-close]').forEach(b => { b.onclick = () => dlg.close(); });
  dlg.onclose = () => dlg.remove();
  const form = dlg.querySelector('form'), err = dlg.querySelector<HTMLElement>('[data-error]'), go = dlg.querySelector<HTMLButtonElement>('[type=submit]');
  if (!form || !err || !go) return;
  form.onsubmit = async ev => {
    ev.preventDefault();
    const name = String(new FormData(form).get('name') ?? '').trim();
    if (!name) return;
    err.hidden = true; go.disabled = true;
    try { await work(name); dlg.close(); }
    catch (e) { err.textContent = errorMessage(e, 'Something went wrong.'); err.hidden = false; go.disabled = false; }
  };
  dlg.showModal();
  form.querySelector('input')?.focus();
}
function prAddDialog(): void {
  prNameDialog('Add product', 'Add', '', async name => {
    const made = await post('/v2/products', {name}) as { product: Product };
    location.hash = prHref(made.product.key);
  });
}

// ----------------------------------------------------------------- one product
async function pageProduct(key: string): Promise<void> {
  const run = ++PR_RUN;
  PRP = { run };
  const main = document.getElementById('main');
  if (!main) return;
  main.innerHTML = `<div class="tt pr pr-one" id="pr-root" data-pr-page="product">${skelRows(6, { card: true })}</div>`;
  let product: Product;
  let rows: PrMatter[];
  // A product's matters come from the matters list (its most, 200); a failed read leaves the card out, not the page.
  const matters = get<{ matters: PrMatter[] }>(`/v2/matters?product=${encodeURIComponent(key)}&limit=200`, { fresh: true })
    .then(r => r.matters, () => [] as PrMatter[]);
  try {
    product = (await get<{ product: Product }>(`/v2/products/${encodeURIComponent(key)}`, { fresh: true })).product;
    rows = await matters;
  } catch (e) {
    if (prLive(run)) main.innerHTML = `<div class="tt pr">${pageHead({title: 'Product', hook: 'pr-ehead', back: {href: PRODUCTS_ROUTE, label: 'Products'}, more: PAGE_HEAD_COPY})}${prErr(e)}</div>`;
    return;
  }
  const root = document.getElementById('pr-root');
  if (!prLive(run) || !root) return;
  PR_MATTERS = { key: product.key, rows };
  prDraw(root, product);
  prBind(root, run, product);
}
function prDraw(root: HTMLElement, p: Product): void {
  const more = `<button type="button" data-pr-rename>Rename</button>${PAGE_HEAD_COPY}`;
  root.dataset['prKey'] = p.key;
  root.innerHTML = `${pageHead({title: p.name, hook: 'pr-ehead', back: {href: PRODUCTS_ROUTE, label: 'Products'}, more})}
    <div class="dir-main pr-main">
      <section class="card dir-card" data-pr-details><table class="dir-kv">
        ${(['kind', 'status', 'tax_kind'] as PrChoice[]).map(f => prSelectRow(f, PR_CHOICES[f], p[f])).join('')}
        ${prSelectRow('owner_teammate', prTeammates(p.owner_teammate), p.owner_teammate)}
      </table></section>
      ${prListCard('agreements', 'Agreement', p.agreements.map(a => ({value: a.id, html: prAgreement(a), label: a.name})))}
      ${prListCard('repositories', 'Code', p.repositories.map(r => ({value: r, html: `<a href="https://github.com/${esc(r)}" target="_blank" rel="noopener">${esc(r)}</a>`, label: r})))}
      ${prListCard('depends_on', 'Depends on', p.depends_on.map(d => ({value: d.id, html: `<a href="${DIR_ROUTE}/${encodeURIComponent(d.id)}">${esc(d.name)}</a>`, label: d.name})))}
      ${prRiskCards(p)}
    </div>`;
}
// Risks and Matters: read-only cards, shown only when the product has some. A matter is set on its task (Risk).
function prRiskCards(p: Product): string {
  const matters = PR_MATTERS?.key === p.key ? PR_MATTERS.rows : [];
  const risks = p.risks.length ? `<section class="card dir-card" data-pr-risks><header><h2>Risks</h2></header>
    <ul class="pr-items">${p.risks.map(r => `<li><span class="pr-item">${prRiskLink(r)}</span></li>`).join('')}</ul></section>` : '';
  const rows = matters.map(m => {
    const closed = m.task.status !== 'open';
    return `<li data-pr-matter="${esc(m.task.id)}"${closed ? ' class="muted"' : ''}><span class="pr-item"><a href="#/task/${encodeURIComponent(m.task.id)}">${esc(m.task.title || m.task.label || m.task.id)}</a></span>
      ${closed ? '<span class="muted">Closed</span>' : m.task.label ? `<span class="muted">${esc(m.task.label)}</span>` : ''}</li>`;
  }).join('');
  const open = p.open_matters ? `<span class="spacer"></span><span class="muted">${p.open_matters} open</span>` : '';
  return risks + (rows ? `<section class="card dir-card" data-pr-matters><header><h2>Matters</h2>${open}</header><ul class="pr-items">${rows}</ul></section>` : '');
}
// A field's row: its name, then its value as a quiet select (an empty choice clears it).
function prSelectRow(field: PrChoice | 'owner_teammate', options: readonly [string, string][], value: string | null): string {
  const name = PR_FIELD_NAMES[field];
  const known = !value || options.some(([k]) => k === value);
  const all: [string, string][] = known ? [...options] : [...options, [value, value]];
  return `<tr data-pr-field="${field}"><th scope="row">${esc(name)}</th><td><select class="pr-select" data-pr-set="${field}" aria-label="${esc(name)}">
    <option value=""${value ? '' : ' selected'}>—</option>${all.map(([k, label]) => `<option value="${esc(k)}"${k === value ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select></td></tr>`;
}
// The teammates a product's owner can be: people, then bots (never the built-in helpers).
function prTeammates(current: string | null): [string, string][] {
  const people = (S.people || []).filter(x => !x.hidden).map((x): [string, string] => ['human:' + x.id, x.id === S.me?.id ? `${x.name || x.id} (you)` : x.name || x.id]);
  const bots = (S.emps || []).filter(e => !isHelperBot(e)).map((e): [string, string] => ['bot:' + e.id, e.display_name || e.slug || e.id]);
  const all = [...people, ...bots];
  if (current && !all.some(([k]) => k === current)) all.push([current, prWho(current)]);
  return all;
}
// Agreement, Code and Depends on: each a card of rows with × to remove, and a quiet + that adds one in place.
function prListCard(field: PrList, title: string, items: {value: string; html: string; label: string}[]): string {
  return `<section class="card dir-card" data-pr-list="${field}"><header><h2>${esc(title)}</h2><span class="spacer"></span>
      <button type="button" class="ghost pr-add" data-pr-add-to="${field}" aria-label="${esc('Add to ' + title)}" title="Add"><span class="nav-icon" aria-hidden="true">add</span></button></header>
    ${items.length ? `<ul class="pr-items">${items.map(i => `<li data-pr-item="${esc(i.value)}"><span class="pr-item">${i.html}</span>
      <button type="button" class="ghost pr-x" data-pr-remove="${field}" data-value="${esc(i.value)}" aria-label="${esc('Remove ' + i.label)}" title="Remove"><span class="nav-icon" aria-hidden="true">close</span></button></li>`).join('')}</ul>` : ''}
  </section>`;
}
// The values a list field holds now, as the server takes them: a repository's name, an agreement's or a vendor's id.
const prValues = (p: Product, field: PrList): string[] => field === 'repositories' ? [...p.repositories] : p[field].map(x => x.id);

// One edit at a time: each sends a whole list built from the product on screen, so the page's controls wait until the
// answer redraws it (two quick removes would otherwise each send a list that still holds the other's item).
async function prSave(root: HTMLElement, run: number, p: Product, body: Record<string, unknown>): Promise<boolean> {
  const held = [...root.querySelectorAll<HTMLButtonElement | HTMLSelectElement>('.pr-main button, .pr-main select')].filter(el => !el.disabled);
  held.forEach(el => { el.disabled = true; });
  try {
    const answer = await post(`/v2/products/${encodeURIComponent(p.key)}`, body) as { product: Product };
    if (prLive(run)) { prDraw(root, answer.product); prBind(root, run, answer.product); }
    return true;
  } catch (e) {
    held.forEach(el => { el.disabled = false; });
    toast(errorMessage(e, 'Something went wrong.'), true);
    return false;
  }
}
// The in-place add: a field under the card's title (a select of the team's repositories for Code, a name otherwise).
async function prAddInPlace(root: HTMLElement, run: number, p: Product, field: PrList): Promise<void> {
  const card = root.querySelector<HTMLElement>(`[data-pr-list="${field}"]`);
  if (!card || card.querySelector('[data-pr-new]')) { card?.querySelector<HTMLElement>('[data-pr-new] input, [data-pr-new] select')?.focus(); return; }
  let control: string;
  if (field === 'repositories') {
    let names: string[] = [];
    try { names = (await get<{ repositories: { full_name: string }[] }>('/v2/repositories')).repositories.map(r => r.full_name); } catch { /* none to offer */ }
    const free = names.filter(n => !p.repositories.some(r => r.toLowerCase() === n.toLowerCase()));
    control = free.length
      ? `<select name="value" aria-label="Repository"><option value="">Repository</option>${free.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')}</select>`
      : '<input name="value" aria-label="Repository" placeholder="owner/name" autocomplete="off">';
  } else {
    control = `<input name="value" aria-label="Name" placeholder="${field === 'agreements' ? 'Agreement' : 'Vendor'}" maxlength="200" autocomplete="off">`;
  }
  if (!prLive(run) || card.querySelector('[data-pr-new]')) return;
  card.querySelector('header')?.insertAdjacentHTML('afterend', `<form class="pr-new" data-pr-new>${control}<button type="submit" class="primary small">Add</button>
    <button type="button" class="ghost small" data-pr-cancel>Cancel</button></form>`);
  const form = card.querySelector<HTMLFormElement>('[data-pr-new]');
  if (!form) return;
  const input = form.querySelector<HTMLInputElement | HTMLSelectElement>('[name=value]');
  input?.focus();
  form.querySelector('[data-pr-cancel]')?.addEventListener('click', () => form.remove());
  form.addEventListener('keydown', ev => { if (ev.key === 'Escape') { ev.stopPropagation(); form.remove(); } });
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const value = String(input?.value ?? '').trim();
    if (!value) return;
    const go = form.querySelector<HTMLButtonElement>('[type=submit]');
    if (go) go.disabled = true;
    if (!await prSave(root, run, p, {[field]: [...prValues(p, field), value]}) && go) go.disabled = false;
  });
}
function prBind(root: HTMLElement, run: number, p: Product): void {
  root.querySelectorAll<HTMLSelectElement>('[data-pr-set]').forEach(select => {
    select.addEventListener('change', () => {
      const field = select.dataset['prSet'];
      if (!field) return;
      select.disabled = true;
      void prSave(root, run, p, {[field]: select.value}).then(ok => { if (!ok) { select.disabled = false; select.value = String(p[field as PrChoice] ?? ''); } });
    });
  });
  root.querySelectorAll<HTMLButtonElement>('[data-pr-add-to]').forEach(b => {
    b.addEventListener('click', () => { void prAddInPlace(root, run, p, b.dataset['prAddTo'] as PrList); });
  });
  root.querySelectorAll<HTMLButtonElement>('[data-pr-remove]').forEach(b => {
    b.addEventListener('click', () => {
      const field = b.dataset['prRemove'] as PrList, value = b.dataset['value'] ?? '';
      b.disabled = true;
      void prSave(root, run, p, {[field]: prValues(p, field).filter(v => v !== value)}).then(ok => { if (!ok) b.disabled = false; });
    });
  });
  root.querySelector('[data-pr-rename]')?.addEventListener('click', () => {
    prNameDialog('Rename', 'Save', p.name, async name => {
      const answer = await post(`/v2/products/${encodeURIComponent(p.key)}`, {name}) as { product: Product };
      if (prLive(run)) { prDraw(root, answer.product); prBind(root, run, answer.product); }
    });
  });
}

// Leaving the page drops its state, so a late answer draws nothing.
function productsStop(r: string): void {
  if (!rdUnder((r || '').split('?')[0] ?? '', PRODUCTS_ROUTE)) { PRP = null; PR_MATTERS = null; }
}
