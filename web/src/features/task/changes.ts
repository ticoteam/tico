/* A page's versions side by side: the words of one version against another, marked in the newer page itself
   (directions/c-mono/pages/qa-report-changes.html). <ins> for what was added, <del> for what was removed, a bar in the
   margin beside each block that changed, and a new picture marked whole. The page stays the agent's own: only these
   marks are added. Both versions are drawn in a sandboxed frame from their text (ui/app/viewer.ts htmlPreviewDoc). */

/** 0 the same, 1 only in the older version, 2 only in the newer one. */
export type Op = 0 | 1 | 2;

/** Past this many differences the middle reads as one change: replaced whole. */
const MAX_D = 3000;

/** The shortest edit from `a` to `b` (Myers), as one op per word kept, removed or added. */
export function diffWords(a: readonly string[], b: readonly string[]): Op[] {
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const A = a.slice(pre, a.length - suf);
  const B = b.slice(pre, b.length - suf);
  const mid = myers(A, B) ?? [...A.map((): Op => 1), ...B.map((): Op => 2)];
  return [...Array<Op>(pre).fill(0), ...mid, ...Array<Op>(suf).fill(0)];
}

function myers(a: readonly string[], b: readonly string[]): Op[] | null {
  const n = a.length;
  const m = b.length;
  if (!n) return b.map(() => 2);
  if (!m) return a.map(() => 1);
  const D = Math.min(n + m, MAX_D);
  const off = D + 1;
  const v = new Int32Array(2 * D + 3);
  // Each step keeps only the band it can read back: k from -(d+1) to d+1.
  const trace: Int32Array[] = [];
  for (let d = 0; d <= D; d++) {
    trace.push(v.slice(off - d - 1, off + d + 2));
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1]! < v[off + k + 1]!) ? v[off + k + 1]! : v[off + k - 1]! + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[off + k] = x;
      if (x >= n && y >= m) return back(trace, n, m);
    }
  }
  return null;
}

function back(trace: Int32Array[], n: number, m: number): Op[] {
  const ops: Op[] = [];
  let x = n;
  let y = m;
  for (let d = trace.length - 1; d >= 0; d--) {
    const band = trace[d]!;
    const at = (k: number) => band[k + d + 1]!;
    const k = x - y;
    const pk = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const px = at(pk);
    const py = px - pk;
    while (x > px && y > py) {
      ops.push(0);
      x--;
      y--;
    }
    if (d > 0) {
      ops.push(x === px ? 2 : 1);
      x = px;
      y = py;
    }
  }
  return ops.reverse();
}

/* ---------------------------------------------------------------- the page's words */

const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TITLE', 'HEAD']);

function textNodes(root: Node): Text[] {
  const out: Text[] = [];
  const doc = root.ownerDocument ?? (root as Document);
  const walk = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    let p = n.parentElement;
    let skip = false;
    while (p && !skip) {
      skip = SKIP.has(p.tagName);
      p = p.parentElement;
    }
    if (!skip && n.nodeValue && /\S/.test(n.nodeValue)) out.push(n as Text);
  }
  return out;
}

interface Word {
  node: Text;
  start: number;
  end: number;
  text: string;
}

function wordsOf(root: Node): Word[] {
  const out: Word[] = [];
  for (const node of textNodes(root)) for (const m of node.data.matchAll(/\S+/g)) out.push({ node, start: m.index, end: m.index + m[0].length, text: m[0] });
  return out;
}

/** A Markdown version, as text in a page: its lines kept. */
function markdownDoc(text: string): Document {
  const doc = new DOMParser().parseFromString('<!DOCTYPE html><html><head></head><body><pre></pre></body></html>', 'text/html');
  const pre = doc.querySelector('pre')!;
  pre.textContent = text;
  pre.setAttribute('style', 'white-space:pre-wrap;font:inherit;margin:0');
  return doc;
}

export const parsePage = (text: string, markdown = false): Document => (markdown ? markdownDoc(text) : new DOMParser().parseFromString(text, 'text/html'));

/** Marks the newer page with what changed since the older one. Returns how many changes it marked. */
export function markChanges(older: Document, newer: Document): number {
  const a = wordsOf(older.body).map((w) => w.text);
  const words = wordsOf(newer.body);
  const ops = diffWords(
    a,
    words.map((w) => w.text),
  );

  // Where each change sits in the newer page: words it added, and words removed before a word it kept.
  const added = new Map<number, number>();
  const removed = new Map<number, { text: string[]; group: number }>();
  let groups = 0;
  let inChange = false;
  let ia = 0;
  let ib = 0;
  for (const op of ops) {
    if (op === 0) {
      inChange = false;
      ia++;
      ib++;
      continue;
    }
    if (!inChange) groups++;
    inChange = true;
    if (op === 2) added.set(ib++, groups);
    else {
      const r = removed.get(ib) ?? { text: [], group: groups };
      r.text.push(a[ia++]!);
      removed.set(ib, r);
    }
  }

  const doc = newer;
  const first = new Set<number>();
  const mark = (el: Element, group: number) => {
    if (first.has(group)) return;
    first.add(group);
    el.setAttribute('data-tico-chg', '');
  };
  const del = (r: { text: string[]; group: number }) => {
    const el = doc.createElement('del');
    el.textContent = r.text.join(' ');
    mark(el, r.group);
    return el;
  };

  // Each text node with a change is rebuilt from its words: the same text, with the marks put in.
  const byNode = new Map<Text, number[]>();
  words.forEach((w, j) => {
    if (added.has(j) || removed.has(j)) byNode.set(w.node, [...(byNode.get(w.node) ?? []), j]);
  });
  for (const [node, js] of byNode) {
    const parts: Node[] = [];
    const data = node.data;
    let cur = 0;
    const text = (to: number) => {
      if (to > cur) parts.push(doc.createTextNode(data.slice(cur, to)));
      cur = Math.max(cur, to);
    };
    for (let i = 0; i < js.length; i++) {
      const j = js[i]!;
      const w = words[j]!;
      if (w.start < cur) continue;
      const r = removed.get(j);
      if (r) {
        text(w.start);
        parts.push(del(r), doc.createTextNode(' '));
      }
      const g = added.get(j);
      if (g !== undefined) {
        // The run of added words in this node, with the spaces between them.
        let last = j;
        while (added.has(last + 1) && words[last + 1]?.node === node && !removed.has(last + 1)) last++;
        text(w.start);
        const ins = doc.createElement('ins');
        ins.textContent = data.slice(w.start, words[last]!.end);
        mark(ins, g);
        parts.push(ins);
        cur = words[last]!.end;
      }
    }
    text(data.length);
    node.replaceWith(...parts);
  }
  // Words removed after the last word of the newer page.
  const tail = removed.get(words.length);
  if (tail) (words.at(-1)?.node.parentNode ?? doc.body).appendChild(del(tail));

  // A bar beside each block that changed.
  for (const el of doc.body.querySelectorAll('ins, del')) {
    const block = el.closest('p, li, h1, h2, h3, h4, h5, h6, td, th, dd, dt, figcaption, blockquote, pre, div, section');
    block?.setAttribute('data-tico-changed', '');
  }

  // A picture the older page did not have is marked whole.
  const had = new Set([...older.body.querySelectorAll('img')].map((i) => i.getAttribute('src')));
  let pictures = 0;
  for (const img of newer.body.querySelectorAll('img')) {
    if (had.has(img.getAttribute('src'))) continue;
    const box = img.closest('figure') ?? img;
    box.setAttribute('data-tico-new', '');
    box.setAttribute('data-tico-chg', '');
    pictures++;
  }
  return groups + pictures;
}

/* ---------------------------------------------------------------- the frame's document */

/** Nothing from outside, no script but the frame's own stepping. */
const policy = (nonce: string) => `default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline' data:; font-src data:; script-src 'nonce-${nonce}'`;

/** What a page with no styles of its own is drawn with; each rule of no weight, so the page's own wins. */
const BASE =
  ':where(html){-webkit-text-size-adjust:100%;text-size-adjust:100%}' +
  ':where(body){margin:0;padding:16px;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-wrap:break-word}' +
  ':where(img,video){max-width:100%;height:auto}:where(pre){overflow-x:auto}';

/** The marks, in the page's own ink (qa-report-changes.html). */
const MARKS =
  'ins{text-decoration:none;background:color-mix(in srgb,currentColor 13%,transparent);box-shadow:0 2px 0 currentColor;border-radius:2px;padding:0 3px;margin:0 -1px}' +
  'del{text-decoration:line-through;text-decoration-thickness:1.5px;opacity:.58;margin-right:.3em}' +
  '[data-tico-changed],[data-tico-new]{position:relative}' +
  '[data-tico-changed]::before,[data-tico-new]::before{content:"";position:absolute;left:-14px;top:0;bottom:0;width:3px;border-radius:2px;background:currentColor}';

/** Steps to the n-th change when the app asks (`{tico_change: n}`). */
const STEP =
  'var m=document.querySelectorAll("[data-tico-chg]");' +
  'addEventListener("message",function(e){var n=e.data&&e.data.tico_change;var el=typeof n==="number"&&m[n];' +
  'if(el)el.scrollIntoView({block:"center",behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth"})});';

/** The page as a frame draws it: links open a new tab, no refresh, the policy and base styles first. */
export function frameDoc(doc: Document, marked = false): string {
  for (const m of doc.querySelectorAll('meta[http-equiv]')) if (/^\s*refresh\s*$/i.test(m.getAttribute('http-equiv') ?? '')) m.remove();
  for (const a of doc.querySelectorAll('a, area')) {
    a.setAttribute('target', '_blank');
    a.setAttribute('rel', 'noopener noreferrer');
  }
  for (const f of doc.querySelectorAll('iframe, frame, object, embed')) f.remove();
  const nonce = Math.random().toString(36).slice(2);
  const meta = doc.createElement('meta');
  meta.httpEquiv = 'Content-Security-Policy';
  meta.content = policy(nonce);
  const base = doc.createElement('style');
  base.textContent = BASE;
  doc.head.prepend(meta, base);
  if (marked) {
    const marks = doc.createElement('style');
    marks.textContent = MARKS;
    doc.head.append(marks);
    const s = doc.createElement('script');
    s.setAttribute('nonce', nonce);
    s.textContent = STEP;
    doc.body.append(s);
  }
  return '<!DOCTYPE html>' + doc.documentElement.outerHTML;
}
