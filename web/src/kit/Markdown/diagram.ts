/* Mermaid diagrams are drawn away from the app: in one hidden frame of the page web/mermaid-sandbox.html, sandboxed
   without its own origin, under a policy that runs only the build's scripts and reaches no network (backend/
   web_routes.py). The frame gets the diagram's text and sends back SVG text, which the chat shows as an <img> from a
   blob: address: an SVG drawn as a picture runs no script and loads nothing. Only answers from that frame's own window
   count. A frame that does not answer in time is thrown away, and its diagrams show as their code. */

export interface Drawn {
  svg: string;
  width: number;
  height: number;
}

/** The longest diagram text sent to be drawn, and the biggest picture taken back. */
export const CODE_MAX = 20_000;
const SVG_MAX = 4_000_000;
const WAIT_MS = 20_000;
const KEEP = 24;

interface Job {
  id: number;
  code: string;
  dark: boolean;
  ground: string;
  done: (d: Drawn | null) => void;
  timer: ReturnType<typeof setTimeout>;
}

let frame: HTMLIFrameElement | null = null;
let ready = false;
let nextId = 1;
const jobs = new Map<number, Job>();
const drawn = new Map<string, Promise<Drawn | null>>();

const size = (n: unknown) => (typeof n === 'number' && Number.isFinite(n) && n > 0 ? Math.min(n, 20_000) : 0);

function send(job: Job) {
  frame?.contentWindow?.postMessage({ id: job.id, code: job.code, dark: job.dark, ground: job.ground }, '*'); // the frame's origin is opaque
}

function onMessage(e: MessageEvent) {
  if (!frame || e.source !== frame.contentWindow) return;
  const d: unknown = e.data;
  if (!d || typeof d !== 'object') return;
  if ('ready' in d) {
    ready = true;
    jobs.forEach(send);
    return;
  }
  const job = 'id' in d && typeof d.id === 'number' ? jobs.get(d.id) : undefined;
  if (!job) return;
  const svg = 'svg' in d && typeof d.svg === 'string' && d.svg.length <= SVG_MAX ? d.svg : null;
  const width = 'width' in d ? size(d.width) : 0;
  const height = 'height' in d ? size(d.height) : 0;
  finish(job, svg && width && height ? { svg, width, height } : null);
}

function finish(job: Job, d: Drawn | null) {
  clearTimeout(job.timer);
  jobs.delete(job.id);
  job.done(d);
}

function drop() {
  window.removeEventListener('message', onMessage);
  frame?.remove();
  frame = null;
  ready = false;
  for (const job of [...jobs.values()]) finish(job, null);
}

function open() {
  if (frame) return;
  const f = document.createElement('iframe');
  f.setAttribute('sandbox', 'allow-scripts');
  f.setAttribute('aria-hidden', 'true');
  f.tabIndex = -1;
  f.title = 'Diagram drawing';
  // laid out (text is measured) but never seen: visibility, not display:none
  f.style.cssText = 'position:fixed;left:-10000px;top:0;width:1200px;height:900px;border:0;visibility:hidden;pointer-events:none';
  f.src = `${import.meta.env.BASE_URL}mermaid-sandbox.html`;
  window.addEventListener('message', onMessage);
  document.body.append(f);
  frame = f;
}

/** The diagram as SVG text on the `ground` colour, or null when it cannot be drawn (bad text, too big, no answer). */
export function drawDiagram(code: string, dark: boolean, ground: string): Promise<Drawn | null> {
  if (code.length > CODE_MAX) return Promise.resolve(null);
  const key = `${dark ? 'd' : 'l'}${ground}\n${code}`;
  const known = drawn.get(key);
  if (known) return known;
  const p = new Promise<Drawn | null>((done) => {
    open();
    const id = nextId++;
    const job: Job = { id, code, dark, ground, done, timer: setTimeout(drop, WAIT_MS) };
    jobs.set(id, job);
    if (ready) send(job);
  });
  drawn.set(key, p);
  p.then((d) => d || drawn.delete(key)); // a failure is tried again on the next showing
  if (drawn.size > KEEP) drawn.delete(drawn.keys().next().value!);
  return p;
}
