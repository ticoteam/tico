import mermaid from 'mermaid';

/* The diagram frame (web/mermaid-sandbox.html): the app posts {id, code, dark, ground}, and this answers {id, svg, width,
   height} or {id, error}. It answers only its parent. The settings are locked: a diagram's own `%%{init}%%` or front
   matter cannot turn on HTML labels, loosen security, raise the caps or change the font the text was measured in. */

const FONT = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const LOCKED = [
  'secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'suppressErrorRendering', 'htmlLabels',
  'fontFamily', 'themeCSS', 'theme', 'themeVariables', 'darkMode', 'look', 'arrowMarkerAbsolute',
];
const CODE_MAX = 20_000;

let n = 0;
// one drawing at a time: each sets the theme it was asked for
let line: Promise<void> = Promise.resolve();

const COLOUR = /^#[0-9a-f]{3,8}$/i;

/** The drawing's own size from its viewBox, set on the SVG so an <img> of it has one, on the ground it sits on in the
    chat (so it stays readable on the viewer's dim). */
function sized(svg: string, ground: string) {
  const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
  const root = doc.documentElement;
  const box = (root.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number);
  const width = Math.ceil(box[2] ?? 0);
  const height = Math.ceil(box[3] ?? 0);
  if (root.nodeName !== 'svg' || !(width > 0 && height > 0)) throw new Error('no size');
  root.setAttribute('width', String(width));
  root.setAttribute('height', String(height));
  root.setAttribute('style', `background-color:${ground}`);
  return { svg: new XMLSerializer().serializeToString(root), width, height };
}

window.addEventListener('message', (e: MessageEvent) => {
  if (e.source !== window.parent) return;
  const { id, code, dark, ground } = (e.data ?? {}) as { id?: unknown; code?: unknown; dark?: unknown; ground?: unknown };
  if (typeof id !== 'number' || typeof code !== 'string') return;
  const under = typeof ground === 'string' && COLOUR.test(ground) ? ground : dark ? '#222222' : '#F3F3F3';
  line = line.then(async () => {
    try {
      if (code.length > CODE_MAX) throw new Error('too long');
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        htmlLabels: false,
        maxTextSize: CODE_MAX,
        maxEdges: 400,
        suppressErrorRendering: true,
        deterministicIds: true,
        theme: dark ? 'dark' : 'neutral',
        darkMode: dark === true,
        fontFamily: FONT,
        secure: LOCKED,
      });
      const { svg } = await mermaid.render(`d${++n}`, code);
      window.parent.postMessage({ id, ...sized(svg, under) }, '*');
    } catch {
      window.parent.postMessage({ id, error: true }, '*');
    }
  });
});

window.parent.postMessage({ ready: true }, '*');
