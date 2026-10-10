/* ui/app/markdown.js — safeMd: untrusted Markdown rendered to safe HTML
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

// Issue attachments are untrusted text. Render a small Markdown vocabulary and discard active
// content and attributes. Links are made readable in place: bare addresses become links, an s3:// URI opens at
// its bucket's view URL (TICO_S3_VIEW_URLS, clients/s3links.py) or is a file chip, documents are cards, and long
// addresses are shortened; image links get a thumbnail and open in the viewer (ui/app/viewer.js).
const SAFE_MD_TAGS = new Set(['P','BR','HR','H1','H2','H3','H4','H5','H6','STRONG','EM','DEL','UL','OL','LI','BLOCKQUOTE','PRE','CODE','TABLE','THEAD','TBODY','TR','TH','TD','A','IMG']);
const DROP_MD_TAGS = new Set(['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','SVG','MATH','FORM','INPUT','BUTTON','TEXTAREA','SELECT','OPTION','LINK','META']);
// This hub's own address (the page's, or the public one bots write in links).
function hubUrl(url) {
  return [location.origin, publicUrl()].some(o => { try { return new URL(o).host === url.host; } catch { return false; } });
}
// A route of the app (#/...) or a task (/tasks/<id>, which the task link handler opens), not a file or the API.
const hubPageUrl = url => hubUrl(url) && (url.hash.startsWith('#/') || /^\/tasks?\/[A-Za-z0-9-]{8,80}\/?$/.test(url.pathname));
// s3://bucket/key at the bucket's view URL, each segment encoded as Python's quote(safe="") does; '' when unmapped.
const S3_URI_RE = /^s3:\/\/([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])\/(.+)$/i;
const urlSeg = s => encodeURIComponent(s).replace(/[!'()*]/g, c => '%' + c.charCodeAt(0).toString(16).toUpperCase());
function s3ViewUrl(uri) {
  const m = S3_URI_RE.exec(uri), map = (typeof S !== 'undefined' && S.config?.s3_view_urls) || {};
  const base = m && Object.hasOwn(map, m[1].toLowerCase()) ? String(map[m[1].toLowerCase()]) : '';
  return /^https:\/\/[^\s?#]+$/i.test(base) ? base.replace(/\/+$/, '') + '/' + m[2].split('/').map(urlSeg).join('/') : '';
}
const decoded = s => { try { return decodeURIComponent(s); } catch { return s; } };
const lastSeg = path => decoded(String(path).split(/[?#]/)[0].replace(/\/+$/, '').split('/').pop() || '');
// Documents a link may point at, by extension: a card with this mark (the viewer's own marks where it shows the kind).
const MD_FILE_MARK = {pdf: 'PDF', md: 'DOC', markdown: 'DOC', txt: 'TEXT', csv: 'CSV', doc: 'DOC', docx: 'DOC',
  xls: 'XLS', xlsx: 'XLS', ppt: 'PPT', pptx: 'PPT', zip: 'ZIP'};
const MD_IMAGE_RE = /\.(png|jpe?g|gif|webp|avif)$/i;
const extOf = s => ((/\.([a-z0-9]{1,8})$/i.exec(String(s || '').split(/[?#]/)[0]) || [])[1] || '').toLowerCase();
const ticoFileUrl = url => url.host === location.host && /^\/api\/v2\/files\/[A-Za-z0-9-]{8,80}$/.test(url.pathname);
// An object nothing here can open: its name, the full URI in the tooltip and a Copy button, never a long raw line.
function s3Chip(uri, label) {
  const chip = document.createElement('span'), name = document.createElement('span'), copy = document.createElement('button');
  chip.className = 's3-chip'; chip.title = uri;
  name.className = 's3-chip-name'; name.textContent = label || lastSeg(uri) || uri;
  copy.type = 'button'; copy.className = 's3-chip-copy'; copy.dataset.copy = uri; copy.textContent = 'Copy';
  copy.setAttribute('aria-label', 'Copy ' + uri);
  chip.append(name, copy);
  return chip;
}
// A link to a document as a card: a type mark, the name, and where it lives. data-mi keeps mediaInline off it.
function fileCard(a, name, host, mark) {
  const part = (cls, text) => Object.assign(document.createElement('span'), {className: cls, textContent: text});
  const icon = part('md-card-icon', mark), body = part('md-card-text', '');
  icon.setAttribute('aria-hidden', 'true');
  body.append(part('md-card-name', name), part('md-card-host', host));
  a.className = 'md-card'; a.dataset.name = name; a.dataset.media = mark; a.dataset.mi = '1';
  a.setAttribute('aria-label', `${name} (${mark}, ${host})`);
  a.replaceChildren(icon, body);
}
// host + a shortened path for a long bare address; the full one stays in the tooltip.
function shortUrl(url) {
  const parts = url.pathname.split('/').filter(Boolean).map(decoded);
  let path = parts.length > 2 ? parts[0] + '/…/' + parts.at(-1) : parts.join('/');
  if (path.length > 36) path = path.slice(0, 16) + '…' + path.slice(-16);
  return url.hostname.replace(/^www\./, '') + (path ? '/' + path : '') + (url.search || url.hash ? '…' : '');
}
// Bare https:// and s3:// addresses in text become links; code and existing links are left as written.
const BARE_URL_RE = /\b(?:https?|s3):\/\/[^\s<>"'`]+/gi;
function autolink(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), hits = [];
  for (let n; (n = walker.nextNode());) if (/(?:https?|s3):\/\//i.test(n.data) && !n.parentElement?.closest('a, code, pre')) hits.push(n);
  for (const n of hits) {
    const frag = document.createDocumentFragment();
    let at = 0;
    for (const m of n.data.matchAll(BARE_URL_RE)) {
      let url = m[0];
      while (/[.,;:!?*_~]$/.test(url) || (url.endsWith(')') && url.split('(').length < url.split(')').length)) url = url.slice(0, -1);
      if (url.length < 9) continue;
      frag.append(n.data.slice(at, m.index));
      const a = document.createElement('a'); a.setAttribute('href', url); a.textContent = url;
      frag.append(a); at = m.index + url.length;
    }
    frag.append(n.data.slice(at));
    n.replaceWith(frag);
  }
}
function safeLink(el, options) {
  let href = el.getAttribute('href') || '';
  const text = el.textContent.trim();
  if (/^s3:\/\//i.test(href)) {
    const uri = decoded(href), view = s3ViewUrl(uri), own = text && text !== href && text !== uri ? text : '';
    if (!view) { el.replaceWith(s3Chip(uri, own)); return; }
    el.textContent = own || lastSeg(uri);
    href = view;
  }
  try {
    // A path on a bot's Mac (`/Users/...`) would resolve against this origin and look clickable while leading
    // nowhere. Files reach humans as `/api/v2/files/<id>` (`hub task attach`); only that root-relative form and
    // hash routes are in-app links.
    if (href.startsWith('/') && !href.startsWith('/api/')) throw new Error('unreachable link');
    const url = new URL(href, location.href);
    if (!['http:','https:','mailto:'].includes(url.protocol)) throw new Error('unsafe link');
    el.setAttribute('href', href); el.setAttribute('rel', 'noopener');
    const file = ticoFileUrl(url);
    // A page or a file of this hub opens in place (the viewer shows a file); in the desktop app a new window would go nowhere.
    if (url.protocol !== 'mailto:' && !hubPageUrl(url) && !file) el.setAttribute('target', '_blank');
    if (options.shortLinks) shortLink(el, href);
    if (url.protocol === 'mailto:' || el.classList.contains('ref')) return;
    const bare = el.textContent === href || el.textContent === url.href, words = el.textContent.trim();
    const mark = MD_FILE_MARK[extOf(file ? words : url.pathname)];
    if (mark) fileCard(el, bare || !words ? lastSeg(url.pathname) : words, file ? appName() : url.hostname, mark);
    else if (bare && href.length > 48) { el.title = href; el.textContent = shortUrl(url); }
  } catch { el.removeAttribute('href'); }
}
function safeMd(s, options = {}) {
  const tpl = document.createElement('template');
  try { tpl.innerHTML = marked.parse(String(s ?? '')); }
  catch { return `<div class="plain-message">${esc(s)}</div>`; }
  autolink(tpl.content);
  for (const el of [...tpl.content.querySelectorAll('*')]) {
    if (!el.parentNode) continue;
    if (!SAFE_MD_TAGS.has(el.tagName)) {
      if (DROP_MD_TAGS.has(el.tagName)) el.remove();
      else el.replaceWith(...el.childNodes);
      continue;
    }
    const allowedAttrs = el.tagName === 'A' ? new Set(['href','title'])
      : el.tagName === 'IMG' ? new Set(['src','alt','title']) : new Set();
    if (options.documentImages && /^H[1-6]$/.test(el.tagName)) allowedAttrs.add('id');
    for (const attr of [...el.attributes]) if (!allowedAttrs.has(attr.name.toLowerCase())) el.removeAttribute(attr.name);
    if (el.tagName === 'A') safeLink(el, options);
    if (el.tagName === 'IMG') {
      let src = el.getAttribute('src') || '';
      if (/^s3:\/\//i.test(src)) {
        src = s3ViewUrl(decoded(src));
        if (!src) { el.replaceWith(s3Chip(decoded(el.getAttribute('src')), el.getAttribute('alt') || '')); continue; }
      }
      try {
        const url = new URL(src, location.href);
        // Linked images show by default (not proxied): any https picture, or one of this server's files.
        if (!['http:','https:'].includes(url.protocol) || (url.origin !== location.origin && url.protocol !== 'https:')) throw new Error('unsafe image');
        el.setAttribute('src', src); el.setAttribute('loading', 'lazy'); el.setAttribute('decoding', 'async');
        if (url.origin !== location.origin) el.setAttribute('referrerpolicy', 'no-referrer');
      } catch { el.remove(); }
    }
  }
  return tpl.innerHTML;
}
// A pull request or an issue in a bot's reply, as the one thing
// anyone needs from it: a small mark saying which and its number, the full URL in the tooltip
// (#524, after bot-desk). Link text the bot chose on purpose stays; a bare URL is replaced.
function shortRef(href) {
  let m = /^https?:\/\/github\.com\/[\w.-]+\/([\w.-]+)\/(pull|issues)\/(\d+)/i.exec(href);
  if (m) return {kind: m[2] === 'pull' ? 'pr' : 'issue', label: '#' + m[3], title: `${m[1]} ${m[2] === 'pull' ? 'pull request' : 'issue'} #${m[3]}`};
  return null;
}
function shortLink(a, href) {
  const ref = shortRef(href); if (!ref) return;
  const text = a.textContent.trim();
  a.className = 'ref ref-' + ref.kind;
  a.title = ref.title + '\n' + href;
  if (!text || text === href || /^https?:\/\//i.test(text)) a.textContent = ref.label;
}
// The same Markdown vocabulary flattened to one plain line, for previews and summaries that get a
// single row and no room to render: headings, emphasis, lists, tables and links become their text.
function plainMd(s) {
  return String(s ?? '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')                                     // images -> alt
    .replace(/\bs3:\/\/\S+\/([^\s/]+)/gi, '$1')                                     // bucket URIs -> file name
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')                                      // links -> text
    .split('\n')
    .map(l => l.replace(/^\s*(?:```|~~~)\S*\s*$/, '')                             // fence markers
               .replace(/^\s*>+\s?/, '')                                          // blockquote
               .replace(/^\s*#{1,6}(\s+|$)/, '')                                  // heading markers
               .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, ''))                          // list markers
    .filter(l => !/^\s*(?:[-*_]\s*){3,}$/.test(l))                                // horizontal rules
    .map(l => l.includes('|')                                    // table cells, minus separator rows
      ? l.split('|').map(c => c.trim()).filter(c => c && !/^[:\s-]+$/.test(c)).join(' · ') : l)
    .join(' ')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')                                           // bold
    .replace(/(\*|_)(?=\S)(.*?\S)\1/g, '$2')                                      // italics
    .replace(/[*`]/g, '')                                                         // leftover markers
    .replace(/\s+/g, ' ').trim();
}
// The Copy button on a file chip; before row handlers, so copying never also opens the row.
document.addEventListener('click', ev => {
  const button = ev.target.closest?.('.s3-chip-copy');
  if (!button) return;
  ev.preventDefault(); ev.stopPropagation();
  copyText(button.dataset.copy).then(() => toast('Copied'), () => toast('Could not copy', true));
}, true);
