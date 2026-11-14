/* ui/app/brand-page.ts — The Brand page (#/strategy/brand, Book; #/strategy/brand/edit, Edit) and the Brand strip on the
   Strategy page (brand-and-gtm spec §4). Book is the concise guide with the full guide folded under it; Edit (the owner)
   is the subsections on the left, one editor in the middle and a rail with the proposed changes (brand_draft proposals,
   decided through /api/v2/proposals), Check copy (POST /api/v2/brand/check) and the versions. The draft saves with its
   revision; a draft changed elsewhere (409) is read again and drawn as it is now. Publishing takes a note.
   Also its draft, publish and versions pieces and the list editor's rows.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// The brand document (GET /api/v2/brand): the published body, the owner's draft and the versions. Each section is
// optional on the server; the page reads it only through a check.
type BrandColor = { name?: string | null; hex?: string | null };
type BrandVoice = { trait: string; do?: string | null; dont?: string | null };
type BrandTone = { situation: string; guide: string };
type BrandWordUse = { word: string; not?: string | null };
type BrandWordAvoid = { word: string; why?: string | null; level?: string | null };
type BrandNaming = { name: string; rule?: string | null; wrong?: string[] | null };
type BrandVisual = { colors?: BrandColor[] | null; fonts?: string[] | null; logo_files?: string[] | null; imagery?: string | null };
type BrandClaim = { claim: string; proof?: string | null; check?: string | null; status?: string | null };
type BrandClaims = { allowed?: BrandClaim[] | null; never?: string[] | null };
type BrandWords = { use?: BrandWordUse[] | null; avoid?: BrandWordAvoid[] | null };
type BrandExample = { text: string; verdict?: string | null; why?: string | null };
type BrandBody = {
  name?: string | null; positioning?: string | null; voice?: BrandVoice[] | null; tone?: BrandTone[] | null;
  words?: BrandWords | null; naming?: BrandNaming[] | null; visual?: BrandVisual | null; claims?: BrandClaims | null;
  examples?: BrandExample[] | null;
};
type BrandSubKey = string;
type BrandRow = Record<string, string | string[] | null | undefined>;
type BrandColumn = [string, string, (string | string[])?];
type BrandDoc = {
  may_edit?: boolean | null;
  draft?: { body?: BrandBody | null; changed?: string[] | null; revision?: number | null } | null;
  published?: { n: number; published_at?: string | null; body?: BrandBody | null } | null;
  versions?: BrandVersion[] | null;
};
type BrandVersion = { n: number; note?: string | null; published_by?: unknown; published_at?: string | null; body?: BrandBody | null };
type BrandProposal = {
  id: string; kind?: string; reason?: string | null; card?: { title?: string | null } | null;
  payload?: { sections?: Record<string, unknown> | null } | null; proposed_by?: unknown; proposed_at?: string | null; may_decide?: boolean;
};
type BrandHit = { rule?: string | null; match: string; level?: string | null };
// The open Brand page: its read, the proposals, the copy checked and the publish form.
interface BrandState {
  load: number; data: BrandDoc | null; view: string; sub: string; props: BrandProposal[]; hits: BrandHit[] | null;
  text: string; publishing: boolean; drawn?: boolean;
}
// The forms the page draws, with the named fields it reads.
interface BrandPublishForm extends HTMLFormElement { readonly elements: HTMLFormControlsCollection & { note: HTMLInputElement }; }
interface BrandSectionForm extends HTMLFormElement {
  readonly elements: HTMLFormControlsCollection & {
    name: HTMLInputElement; positioning: HTMLInputElement; fonts: HTMLInputElement; logo_files: HTMLInputElement;
    imagery: HTMLTextAreaElement; never: HTMLTextAreaElement;
  };
}
interface BrandCheckForm extends HTMLFormElement { readonly elements: HTMLFormControlsCollection & { text: HTMLTextAreaElement }; }

let BR: BrandState | null = null;            // the open Brand page: {load, data, view, sub, props, hits, publishing}
const BR_SUBS: [BrandSubKey, string][] = [['positioning', 'Positioning'], ['voice', 'Voice'], ['tone', 'Tone'], ['words', 'Words'], ['naming', 'Naming'],
  ['visual', 'Visual'], ['claims', 'Claims'], ['examples', 'Examples']];
// Each list editor's columns: [key, label, options | 'list' | 'color' | 'long' | 'wide']. Long and wide are text that
// wraps and grows (wide also takes twice the room); the rest are one line.
const BR_COLS: Record<string, BrandColumn[]> = {
  voice: [['trait', 'Trait'], ['do', 'Do', 'long'], ['dont', "Don't", 'long']],
  tone: [['situation', 'Situation'], ['guide', 'Guide', 'wide']],
  use: [['word', 'Use'], ['not', 'Not']],
  avoid: [['word', 'Avoid'], ['why', 'Why', 'long'], ['level', 'Level', ['warn', 'block']]],
  naming: [['name', 'Name'], ['rule', 'Rule', 'long'], ['wrong', 'Never written', 'list']],
  colors: [['name', 'Name'], ['hex', 'Colour', 'color']],
  allowed: [['claim', 'Claim', 'long'], ['proof', 'Proof', 'long'], ['check', 'Check'], ['status', 'Status', ['proven', 'due']]],
  examples: [['text', 'Text', 'wide'], ['verdict', 'Verdict', ['on', 'off']], ['why', 'Why', 'long']],
};
const BR_LEVEL: Record<string, string> = {block: 'fail', warn: 'waiting', setting: ''};
const brHex = (h: unknown): string => /^#[0-9a-f]{3}([0-9a-f]{3})?([0-9a-f]{2})?$/i.test(String(h || '').trim()) ? String(h).trim() : '';
// Dark or light words on a colour, whichever reads.
function brInk(hex: string) {
  let h = brHex(hex).slice(1); if (!h) return '';
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  const [r = 0, g = 0, b = 0] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? '#172221' : '#fbfbf8';
}
const brSw = (c: BrandColor, size = '') => brHex(c.hex) ? `<span class="br-sw${size ? ' ' + size : ''}" style="background:${brHex(c.hex)}" title="${esc(c.name || c.hex)}"></span>` : '';

// ----------------------------------------------------------------- shared with the go-to-market: draft, publish, versions
const grDocMay = (d: BrandDoc | null | undefined): boolean => d?.may_edit ?? grAdmin();
const grDocChanged = (d: BrandDoc | null | undefined): number => d?.draft?.changed?.length || 0;
function grDocToolsHtml(d: BrandDoc | null, state: BrandState, publish = true) {
  const n = grDocChanged(d), p = d?.published, may = grDocMay(d);
  return `${may && n ? `<span class="pill waiting">${n === 1 ? '1 change' : `${n} changes`}</span>` : ''}${p ? `<span class="muted gr-meta">v${esc(p.n)} · ${esc(grDay(p.published_at))}</span>` : ''}
    ${(d?.versions || []).length ? '<button class="ghost" type="button" data-doc-versions>Versions</button>' : ''}
    ${may && publish && !state.publishing ? `<button class="primary" type="button" data-doc-publish${n || (!p && d?.draft) ? '' : ' disabled title="Nothing new to publish"'}>Publish</button>` : ''}`;
}
const grDocPublishHtml = () => `<form class="strat-publish gr-pub" data-doc-publish-form novalidate>
    <input type="text" name="note" maxlength="200" autocomplete="off" placeholder="What changed" aria-label="What changed" required>
    <button class="primary" type="submit">Publish</button><button class="ghost" type="button" data-doc-cancel>Cancel</button></form>`;
// Saves sections into the draft with the revision it was read at; a newer draft (409) is read again and drawn.
async function grDocSave(base: string, state: BrandState, sections: Record<string, unknown>, redraw: () => void): Promise<boolean> {
  try {
    state.data = await put(base + '/draft', {sections, revision: state.data?.draft?.revision ?? null});
    redraw(); return true;
  } catch (e) {
    if (errorStatus(e) === 409) {
      toast('Changed elsewhere, reloaded');
      try { state.data = await get<BrandDoc>(base, {fresh: true}); } catch { /* drawn as it was */ }
      redraw(); return false;
    }
    toast(errorMessage(e, 'Not saved'), true); return false;
  }
}
function grDocBind(root: HTMLElement, base: string, state: BrandState, redraw: () => void, bodyHtml: (b: BrandBody) => string) {
  const v = root.querySelector<HTMLElement>('[data-doc-versions]');
  if (v) v.onclick = () => void grDocVersions(base, state.data, bodyHtml);
  const p = root.querySelector<HTMLElement>('[data-doc-publish]');
  if (p) p.onclick = () => { state.publishing = true; redraw(); $('[data-doc-publish-form] input')?.focus(); };
  const form = document.querySelector<BrandPublishForm>('[data-doc-publish-form]');
  if (!form) return;
  const done = () => { state.publishing = false; redraw(); };
  const cancel = form.querySelector<HTMLButtonElement>('[data-doc-cancel]');
  if (cancel) cancel.onclick = done;
  form.onkeydown = ev => { if (ev.key === 'Escape') { ev.preventDefault(); done(); } };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const note = form.elements.note.value.trim();
    if (!note) { form.elements.note.focus(); return; }
    form.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      state.data = await post(base + '/publish', {note});
      state.publishing = false; redraw(); toast(`Published v${state.data?.published?.n ?? ''}`.trim());
    } catch (e) { toast(errorMessage(e, 'Not published'), true); form.querySelectorAll('button').forEach(b => { b.disabled = false; }); }
  };
}
async function grDocVersions(base: string, data: BrandDoc | null, bodyHtml: (b: BrandBody) => string, open: string | null | undefined = null) {
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
  const dlg = document.createElement('dialog');
  dlg.className = 'tmodal gr-dlg gr-vdlg'; dlg.setAttribute('aria-label', 'Versions');
  document.body.appendChild(dlg);
  dlg.onclose = () => dlg.remove();
  const list = () => {
    dlg.innerHTML = `<div class="tmodal-head"><b>Versions</b><span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div>
      <div class="tmodal-body"><ul class="gr-vers">${(data?.versions || []).slice().sort((a, b) => b.n - a.n).map(x =>
        `<li><button type="button" class="gr-ver" data-n="${esc(x.n)}"><b>v${esc(x.n)}</b><span>${esc(x.note || '')}</span><span class="muted">${esc(grWho(x.published_by))} · ${esc(grDay(x.published_at))}</span></button></li>`).join('')}</ul></div>`;
    const close = dlg.querySelector<HTMLButtonElement>('[data-close]');
    if (close) close.onclick = () => dlg.close();
    dlg.querySelectorAll<HTMLElement>('[data-n]').forEach(b => { b.onclick = () => void show(b.dataset.n); });
  };
  const show = async (n: string | undefined) => {
    let v: BrandVersion;
    try { v = await get<BrandVersion>(`${base}/versions/${encodeURIComponent(String(n))}`); } catch (e) { toast(errorMessage(e, 'Did not load'), true); return; }
    if (!dlg.isConnected) return;
    dlg.innerHTML = `<div class="tmodal-head"><button type="button" class="ghost" data-back aria-label="Back">‹</button><b>v${esc(v.n)}</b><span class="muted">${esc(v.note || '')}</span>
      <span class="spacer"></span><button type="button" class="ghost tmodal-x" data-close aria-label="Close">✕</button></div><div class="tmodal-body gr-vbody">${bodyHtml(v.body || {})}</div>`;
    const close = dlg.querySelector<HTMLButtonElement>('[data-close]');
    if (close) close.onclick = () => dlg.close();
    const back = dlg.querySelector<HTMLButtonElement>('[data-back]');
    if (back) back.onclick = list;
  };
  list();
  dlg.showModal();
  if (open) void show(open);
}

// A list editor: rows of labelled fields, + Add, × on each row. The caption shows over the first row only.
function grRowHtml(cols: BrandColumn[], row: BrandRow = {}) {
  return `<div class="gr-rowed" style="--cols:${cols.map(c => c[2] === 'wide' ? 'minmax(0,2fr)' : Array.isArray(c[2]) ? '96px' : c[2] === 'color' ? '64px' : 'minmax(0,1fr)').join(' ')}">${cols.map(([k, label, kind]) => {
    const v = row[k] ?? '';
    const control = Array.isArray(kind) ? `<select data-k="${k}">${kind.map(o => `<option${o === v ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>`
      : kind === 'color' ? `<input type="color" data-k="${k}" value="${esc(typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v) ? v : (typeof v === 'string' && brHex(v) && v.length === 4 ? '#' + v.slice(1).split('').map(c => c + c).join('') : '#888888'))}">`
      : kind === 'long' || kind === 'wide' ? `<textarea data-k="${k}" rows="1" data-grow>${esc(v)}</textarea>`
      : `<input type="text" data-k="${k}"${kind === 'list' ? ' data-list' : ''} value="${esc(Array.isArray(v) ? v.join(', ') : v)}">`;
    return `<label class="gr-cell"><span class="gr-cap">${esc(label)}</span>${control}</label>`;
  }).join('')}<button type="button" class="gr-x" data-row-x aria-label="Remove">×</button></div>`;
}
const grRowsHtml = (key: string, label: string, rows: BrandRow[] | null | undefined, addLabel = '+ Add') => `<fieldset class="gr-rows" data-rows="${key}"><legend class="gr-k">${esc(label)}</legend>
    <div class="gr-rows-list">${(rows || []).map(r => grRowHtml(BR_COLS[key] ?? [], r)).join('')}</div>
    <button type="button" class="ghost small" data-row-add="${key}">${esc(addLabel)}</button></fieldset>`;
// A long field is as tall as its text.
const grGrow = (t: HTMLElement) => { t.style.height = 'auto'; t.style.height = t.scrollHeight + 2 + 'px'; };
function grRowsBind(form: HTMLFormElement) {
  form.querySelectorAll<HTMLElement>('textarea[data-grow]').forEach(grGrow);
  form.addEventListener('input', ev => { const t = eventElement(ev); if (t?.matches?.('textarea[data-grow]') && t instanceof HTMLElement) grGrow(t); });
  form.addEventListener('click', ev => {
    const target = eventElement(ev);
    const x = target?.closest('[data-row-x]');
    if (x) { x.closest('.gr-rowed')?.remove(); return; }
    const add = target?.closest<HTMLElement>('[data-row-add]');
    if (add) {
      const list = form.querySelector<HTMLElement>(`[data-rows="${add.dataset.rowAdd}"] .gr-rows-list`);
      if (!list) return;
      list.insertAdjacentHTML('beforeend', grRowHtml(BR_COLS[add.dataset.rowAdd ?? ''] ?? []));
      list.lastElementChild?.querySelectorAll<HTMLElement>('textarea[data-grow]').forEach(grGrow);
      list.lastElementChild?.querySelector<HTMLElement>('input,select,textarea')?.focus();
    }
  });
}
function grRowsRead(form: HTMLFormElement, key: string): BrandRow[] {
  return [...form.querySelectorAll(`[data-rows="${key}"] .gr-rowed`)].map(r => {
    const o: BrandRow = {};
    for (const f of r.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('[data-k]')) o[String(f.dataset.k)] = f.hasAttribute('data-list') ? f.value.split(',').map(s => s.trim()).filter(Boolean) : f.value.trim();
    return o;
  }).filter(o => (BR_COLS[key] ?? []).some(([k, , kind]) => { const v = o[k]; return !Array.isArray(kind) && kind !== 'color' && (Array.isArray(v) ? v.length : v); }));
}
const grLines = (text: unknown): string[] => String(text || '').split('\n').map(s => s.trim()).filter(Boolean);

// ----------------------------------------------------------------- the Book
function brandBookHtml(body: BrandBody | null | undefined, {full = false}: {full?: boolean} = {}) {
  const b: BrandBody = body || {}, colors = (b.visual?.colors || []).filter(c => brHex(c.hex));
  const hero = colors[0]?.hex, voice = b.voice || [], words: BrandWords = b.words || {}, claims: BrandClaims = b.claims || {};
  const tile = (cls: string, title: string, inner: string) => inner ? `<section class="br-tile ${cls}">${title ? `<h3 class="gr-k">${esc(title)}</h3>` : ''}${inner}</section>` : '';
  const heroTile = `<section class="br-tile br-s8 br-hero"${hero ? ` style="background:${brHex(hero)};color:${brInk(hero)}"` : ''}>
      <span class="br-name">${esc(b.name || companyName())}</span>${b.positioning ? `<span class="br-pos">${esc(b.positioning)}</span>` : ''}
      ${voice.length ? `<span class="br-traits">${voice.map(v => `<span>${esc(v.trait)}</span>`).join('')}</span>` : ''}</section>`;
  const colorTile = tile('br-s4', 'Colour', colors.length ? `<ul class="br-colors">${colors.map(c =>
    `<li>${brSw(c, 'br-sw-l')}<span>${esc(c.name || '')}</span><code class="muted">${esc(c.hex)}</code></li>`).join('')}</ul>` : '');
  const voiceTile = tile('br-s6', 'Voice', voice.length ? `<ul class="br-voice">${voice.map(v => `<li><b>${esc(v.trait)}</b>
      ${v.do ? `<span class="br-do">“${esc(v.do)}”</span>` : ''}${v.dont ? `<span class="br-dont">“${esc(v.dont)}”</span>` : ''}</li>`).join('')}</ul>` : '');
  const wordsTile = tile('br-s6', 'Words', (words.use || []).length || (words.avoid || []).length ? `${(words.use || []).length ? `<div class="br-wl"><span class="muted">Say</span>${(words.use || []).map(w =>
      `<span class="br-wu" title="${esc(w.not ? 'Not ' + w.not : '')}">${esc(w.word)}</span>`).join('')}</div>` : ''}${(words.avoid || []).length ? `<div class="br-wl"><span class="muted">Never</span>${(words.avoid || []).map(w =>
      `<span class="br-wa${w.level === 'block' ? ' br-block' : ''}" title="${esc(w.why || '')}">${esc(w.word)}</span>`).join('')}</div>` : ''}` : '');
  const claimsTile = tile('br-s12', 'Claims', (claims.allowed || []).length || (claims.never || []).length ? `${(claims.allowed || []).length ? `<ul class="br-claims">${(claims.allowed || []).map(c =>
      `<li><span class="br-claim">${esc(c.claim)}</span><span class="muted">${esc([c.proof, c.check].filter(Boolean).join(' · '))}</span>${c.status ? `<span class="pill ${c.status === 'proven' ? 'ok' : 'waiting'}">${c.status === 'proven' ? 'Proven' : 'Due'}</span>` : ''}</li>`).join('')}</ul>` : ''}
      ${(claims.never || []).length ? `<div class="br-wl"><span class="muted">Never claim</span>${(claims.never || []).map(n => `<span class="br-wa">${esc(n)}</span>`).join('')}</div>` : ''}` : '');
  const tone = (b.tone || []).length ? `<section class="br-tile br-s6"><h3 class="gr-k">Tone</h3><dl class="br-dl">${(b.tone || []).map(t => `<dt>${esc(t.situation)}</dt><dd>${esc(t.guide)}</dd>`).join('')}</dl></section>` : '';
  const naming = (b.naming || []).length ? `<section class="br-tile br-s6"><h3 class="gr-k">Naming</h3><dl class="br-dl">${(b.naming || []).map(n => `<dt>${esc(n.name)}</dt><dd>${esc(n.rule || '')}${(n.wrong || []).length ? ` ${(n.wrong || []).map(w => `<s class="br-wa">${esc(w)}</s>`).join(' ')}` : ''}</dd>`).join('')}</dl></section>` : '';
  const vis: BrandVisual = b.visual || {};
  const visual = (vis.fonts || []).length || (vis.logo_files || []).length || vis.imagery ? `<section class="br-tile br-s6"><h3 class="gr-k">Visual</h3><dl class="br-dl">
      ${(vis.fonts || []).length ? `<dt>Fonts</dt><dd>${esc((vis.fonts || []).join(', '))}</dd>` : ''}${(vis.logo_files || []).length ? `<dt>Logo</dt><dd>${(vis.logo_files || []).map(f => esc(f)).join(', ')}</dd>` : ''}
      ${vis.imagery ? `<dt>Imagery</dt><dd>${esc(vis.imagery)}</dd>` : ''}</dl></section>` : '';
  const examples = (b.examples || []).length ? `<section class="br-tile br-s6"><h3 class="gr-k">Examples</h3><ul class="br-ex">${(b.examples || []).map(x =>
      `<li><span class="pill ${x.verdict === 'off' ? 'fail' : 'ok'}">${x.verdict === 'off' ? 'Off brand' : 'On brand'}</span><span>“${esc(x.text)}”</span>${x.why ? `<span class="muted">${esc(x.why)}</span>` : ''}</li>`).join('')}</ul></section>` : '';
  const rest = tone + naming + visual + examples;
  return `<div class="br-bento">${heroTile}${colorTile}${voiceTile}${wordsTile}${claimsTile}</div>
    ${rest ? `<details class="br-full"${full ? ' open' : ''}><summary>Full guide</summary><div class="br-bento">${rest}</div></details>` : ''}`;
}

// ----------------------------------------------------------------- the Strategy page's strip
// The brand a reader sees on Strategy: the published one, or the owner's draft before any publish; null when empty.
function brandStripBody(d: BrandDoc | null | undefined): BrandBody | null | undefined {
  const body = d?.published?.body || (grDocMay(d) ? d?.draft?.body : null);
  return body && Object.values(body).some(v => v && (typeof v !== 'object' || Object.keys(v).length)) ? body : null;
}
// With no brand yet, the owner's way in (the Strategy page's setup row); others see nothing.
const brandSetupHtml = (d: BrandDoc | null | undefined) => d && grDocMay(d) && !brandStripBody(d) ? `<a class="ghost gr-btn" href="${BRAND_ROUTE}/edit">Set up brand</a>` : '';
function brandStripHtml(d: BrandDoc | null | undefined) {
  const body = brandStripBody(d);
  if (!body) return '';
  const may = grDocMay(d);
  const colors = (body.visual?.colors || []).filter(c => brHex(c.hex)), hero = colors[0]?.hex, n = may ? grDocChanged(d) : 0;
  const traits = (body.voice || []).map(v => v.trait).filter(Boolean), never = (body.words?.avoid || []).map(w => w.word).filter(Boolean).slice(0, 4);
  return `<a class="br-strip" href="${BRAND_ROUTE}"><span class="br-strip-hero"${hero ? ` style="background:${brHex(hero)};color:${brInk(hero)}"` : ''}>
      <span class="br-name">${esc(body.name || companyName())}</span>${body.positioning ? `<span class="br-pos">${esc(body.positioning)}</span>` : ''}</span>
    <span class="br-strip-rest">${colors.length ? `<span class="br-sws" aria-label="Colours">${colors.slice(0, 6).map(c => brSw(c)).join('')}</span>` : ''}
      ${traits.length ? `<span class="br-strip-k"><span class="gr-k">Voice</span>${esc(traits.join(' · '))}</span>` : ''}
      ${never.length ? `<span class="br-strip-k"><span class="gr-k">Never say</span>${never.map(w => `<s class="br-never">${esc(w)}</s>`).join(' ')}</span>` : ''}
      <span class="spacer"></span>${n ? `<span class="pill waiting">${n === 1 ? '1 change' : `${n} changes`}</span>` : ''}<span class="br-go">Brand book →</span></span></a>`;
}

// ----------------------------------------------------------------- the page
function pageBrand() {
  const [path, query] = (S.route || '').split('?');
  const load = ++GR_LOAD;
  document.querySelectorAll<HTMLDialogElement>('dialog.gr-dlg').forEach(d => d.close());
  BR = {load, data: BR?.data || null, view: path === BRAND_ROUTE + '/edit' ? 'edit' : 'book',
    sub: new URLSearchParams(query || '').get('s') || BR?.sub || 'positioning', props: BR?.props || [], hits: null, text: BR?.text || '', publishing: false};
  const main = $('#main');
  main.classList.add('strategy-layout', 'growth-layout');
  main.innerHTML = redesignStrategyHead('brand') + `<div id="gr-body" class="gr-body">${BR.data ? '' : skelRows(6, {dot: false})}</div>`;
  strategyChatMount();
  if (BR.data) { brandDraw(); BR.drawn = true; }
  void brandRead(BR);
}
async function brandRead(state: BrandState) {
  try {
    const [data, props] = await Promise.all([get<BrandDoc>('/v2/brand', {fresh: true}),
      grAdmin() ? get<{proposals?: BrandProposal[]}>('/v2/proposals', {fresh: true}).then(r => (r.proposals || []).filter(p => p.kind === 'brand_draft')).catch((): BrandProposal[] => []) : []]);
    if (BR !== state) return;
    // A page already drawn from the last read keeps what is being typed when nothing changed.
    const same = state.drawn && JSON.stringify([data, props]) === JSON.stringify([state.data, state.props]);
    state.data = data; state.props = grDocMay(data) ? props : [];
    if (same) return;
  } catch (e) { if (BR === state) $('#gr-body').innerHTML = grFail(e); return; }
  if (state.view === 'edit' && !grDocMay(state.data)) { location.replace(BRAND_ROUTE); return; }
  brandDraw();
}
function brandDraw() {
  const state = BR, body: HTMLElement | null = $('#gr-body');
  if (!state || !body || !state.data) return;
  const d = state.data, may = grDocMay(d), editing = state.view === 'edit';
  const seg = may ? `<nav class="gr-tabs" aria-label="View"><a href="${BRAND_ROUTE}"${editing ? '' : ' aria-current="page"'}>Book</a><a href="${BRAND_ROUTE}/edit"${editing ? ' aria-current="page"' : ''}>Edit</a></nav>` : '';
  const head = grCrumbs('#/strategy', 'Strategy', 'Brand', `${seg}${grDocToolsHtml(d, state, editing)}`) + (state.publishing ? grDocPublishHtml() : '');
  const book = editing ? (d.draft?.body || d.published?.body) : (d.published?.body || (may ? d.draft?.body : null));
  body.innerHTML = head + (editing ? brandEditHtml(state) : book && Object.keys(book).length ? brandBookHtml(book) : '');
  grDocBind(body, '/v2/brand', state, brandDraw, b => brandBookHtml(b, {full: true}));
  if (editing) brandEditBind(state);
}

// ----------------------------------------------------------------- Edit
// The list a section's editor draws (voice, tone, naming, examples); the others are drawn from their own fields.
const brandRowsOf = (b: BrandBody | null | undefined, sec: string) => sec === 'voice' ? b?.voice : sec === 'tone' ? b?.tone
  : sec === 'naming' ? b?.naming : sec === 'examples' ? b?.examples : undefined;
function brandCount(b: BrandBody | null | undefined, key: string) {
  if (key === 'positioning') return b?.positioning ? '' : '';
  if (key === 'words') return (b?.words?.use || []).length + (b?.words?.avoid || []).length || '';
  if (key === 'visual') return (b?.visual?.colors || []).length || '';
  if (key === 'claims') return (b?.claims?.allowed || []).length || '';
  return (brandRowsOf(b, key) || []).length || '';
}
function brandEditorHtml(sec: string, b: BrandBody | null | undefined) {
  if (sec === 'positioning') return `<label class="gr-f"><span>Name</span><input name="name" maxlength="60" placeholder="${esc(companyName() || '')}" value="${esc(b?.name || '')}"></label>
      <label class="gr-f"><span>Positioning</span><input name="positioning" maxlength="120" value="${esc(b?.positioning || '')}"></label>
      <p class="muted gr-count" data-count>${String(b?.positioning || '').length} / 120</p>`;
  if (sec === 'words') return grRowsHtml('use', 'Use', b?.words?.use) + grRowsHtml('avoid', 'Avoid', b?.words?.avoid);
  if (sec === 'visual') return grRowsHtml('colors', 'Colours', b?.visual?.colors) + grField('Fonts', `<input name="fonts" value="${esc((b?.visual?.fonts || []).join(', '))}">`)
    + grField('Logo files', `<input name="logo_files" value="${esc((b?.visual?.logo_files || []).join(', '))}">`)
    + grField('Imagery', `<textarea name="imagery" rows="3">${esc(b?.visual?.imagery || '')}</textarea>`);
  if (sec === 'claims') return grRowsHtml('allowed', 'Claims', b?.claims?.allowed) + grField('Never claim', `<textarea name="never" rows="3">${esc((b?.claims?.never || []).join('\n'))}</textarea>`);
  return grRowsHtml(sec, BR_SUBS.find(s => s[0] === sec)?.[1] || sec, brandRowsOf(b, sec));
}
// What Save sends: the open section, and on Positioning the brand name with it.
function brandSectionPatch(sec: string, form: BrandSectionForm) {
  const patch: Record<string, unknown> = {[sec]: brandSectionValue(sec, form)};
  if (sec === 'positioning') { const field = form.elements.namedItem('name'); if (field instanceof HTMLInputElement) patch.name = field.value.trim() || null; }
  return patch;
}
function brandSectionValue(sec: string, form: BrandSectionForm) {
  const split = (n: 'fonts' | 'logo_files') => (form.elements[n]?.value || '').split(',').map(s => s.trim()).filter(Boolean);
  if (sec === 'positioning') return form.elements.positioning.value.trim() || null;
  if (sec === 'words') { const v = {use: grRowsRead(form, 'use'), avoid: grRowsRead(form, 'avoid')}; return v.use.length || v.avoid.length ? v : null; }
  if (sec === 'visual') {
    const v = {colors: grRowsRead(form, 'colors'), fonts: split('fonts'), logo_files: split('logo_files'), imagery: form.elements.imagery.value.trim()};
    return v.colors.length || v.fonts.length || v.logo_files.length || v.imagery ? v : null;
  }
  if (sec === 'claims') { const v = {allowed: grRowsRead(form, 'allowed'), never: grLines(form.elements.never.value)}; return v.allowed.length || v.never.length ? v : null; }
  const rows = grRowsRead(form, sec);
  return rows.length ? rows : null;
}
const brandHitsHtml = (hits: BrandHit[] | null) => !hits ? '' : hits.length ? hits.map(h => `<span class="br-hit" title="${esc(h.rule || '')}">“${esc(h.match)}”<span class="pill ${BR_LEVEL[h.level ?? ''] || ''}">${h.level === 'block' ? 'Block' : 'Warn'}</span></span>`).join('')
  : '<span class="pill ok">No hits</span>';
function brandEditHtml(state: BrandState) {
  const d = state.data;
  if (!d) return '';
  const b: BrandBody = d.draft?.body || d.published?.body || {}, changed = new Set(d.draft?.changed || []);
  const nav = `<nav class="br-subs" aria-label="Sections">${BR_SUBS.map(([k, label]) => `<button type="button" data-sub="${k}"${state.sub === k ? ' aria-current="true"' : ''}>
      <span>${esc(label)}</span>${changed.has(k) || (k === 'positioning' && changed.has('name')) ? '<span class="gr-dot gr-ts-review" title="Changed" aria-label="Changed"></span>' : ''}<span class="muted">${brandCount(b, k)}</span></button>`).join('')}</nav>`;
  const editor = `<form class="card br-editor gr-form" data-sec="${esc(state.sub)}" novalidate>${brandEditorHtml(state.sub, b)}
      <div class="gr-foot"><button class="primary" type="submit">Save</button></div></form>`;
  const props = state.props.length ? `<section class="card gr-sec"><h3 class="gr-k">Proposed</h3>${state.props.map(p => `<div class="br-prop" data-prop="${esc(p.id)}">
      <span>${esc(p.reason || p.card?.title || Object.keys(p.payload?.sections || {}).join(', ') || 'Brand change')}</span>
      <span class="muted">${esc(grWho(p.proposed_by))} · ${esc(grDay(p.proposed_at))}</span>
      ${p.may_decide !== false ? '<span class="br-prop-b"><button class="primary small" type="button" data-decide="confirm">Add to draft</button><button class="ghost small" type="button" data-decide="reject">Decline</button></span>' : ''}</div>`).join('')}</section>` : '';
  const check = `<section class="card gr-sec"><h3 class="gr-k">Check copy</h3><form class="br-check" data-check novalidate>
      <label class="sr-only" for="br-check-t">Copy</label><textarea id="br-check-t" name="text" rows="3">${esc(state.text)}</textarea>
      <button class="ghost small" type="submit">Check</button></form><div class="br-hits" data-hits>${brandHitsHtml(state.hits)}</div></section>`;
  const vers = (d.versions || []).length ? `<section class="card gr-sec"><h3 class="gr-k">Versions</h3><ul class="gr-vers">${(d.versions || []).slice().sort((a, b) => b.n - a.n).slice(0, 6).map(x =>
      `<li><button type="button" class="gr-ver" data-ver="${esc(x.n)}"><b>v${esc(x.n)}</b><span>${esc(x.note || '')}</span><span class="muted">${esc(grDay(x.published_at))}</span></button></li>`).join('')}</ul></section>` : '';
  return `<div class="br-edit">${nav}${editor}<aside class="br-rail" aria-label="Brand tools">${props}${check}${vers}</aside></div>`;
}
function brandEditBind(state: BrandState) {
  const body: HTMLElement = $('#gr-body');
  body.querySelectorAll<HTMLButtonElement>('[data-sub]').forEach(b => { b.onclick = () => { state.sub = String(b.dataset.sub); history.replaceState(history.state, '', `${BRAND_ROUTE}/edit?s=${state.sub}`); brandDraw(); }; });
  const form = body.querySelector<BrandSectionForm>('form[data-sec]');
  if (!form) return;
  grRowsBind(form);
  const pos = form.elements.positioning;
  if (pos) pos.oninput = () => { const count = form.querySelector<HTMLElement>('[data-count]'); if (count) count.textContent = `${pos.value.length} / 120`; };
  form.onsubmit = async ev => {
    ev.preventDefault();
    const sec = String(form.dataset.sec), btn = form.querySelector<HTMLButtonElement>('[type=submit]');
    if (!btn) return;
    btn.disabled = true;
    if (await grDocSave('/v2/brand', state, brandSectionPatch(sec, form), brandDraw)) toast('Saved');
    else if (form.isConnected) btn.disabled = false;
  };
  const check = body.querySelector<BrandCheckForm>('[data-check]');
  if (!check) return;
  check.onsubmit = async ev => {
    ev.preventDefault();
    state.text = check.elements.text.value;
    if (!state.text.trim()) return;
    try { state.hits = (await post('/v2/brand/check', {text: state.text})).hits || []; } catch (e) { toast(errorMessage(e, 'Not checked'), true); return; }
    // Only the results change, so a section being edited keeps what is typed.
    const out = check.parentElement?.querySelector<HTMLElement>('[data-hits]');
    if (BR === state && out?.isConnected) out.innerHTML = brandHitsHtml(state.hits);
  };
  body.querySelectorAll<HTMLElement>('[data-ver]').forEach(b => { b.onclick = () => void grDocVersions('/v2/brand', state.data, x => brandBookHtml(x, {full: true}), b.dataset.ver); });
  body.querySelectorAll<HTMLButtonElement>('[data-prop] [data-decide]').forEach(b => {
    b.onclick = async () => {
      const row = b.closest<HTMLElement>('[data-prop]'); if (!row) return;
      const id = String(row.dataset.prop);
      b.closest<HTMLElement>('.br-prop-b')?.querySelectorAll<HTMLButtonElement>('button').forEach(x => { x.disabled = true; });
      try { await post(`/v2/proposals/${encodeURIComponent(id)}/decide`, {decision: b.dataset.decide}); }
      catch (e) { toast(errorMessage(e, 'Not decided'), true); }
      toast(b.dataset.decide === 'confirm' ? 'Added to draft' : 'Declined');
      void brandRead(state);
    };
  });
}
