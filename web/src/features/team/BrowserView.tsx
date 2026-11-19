import { useEffect, useEffectEvent, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useAction } from '@/actions';
import { Button } from '@/kit/Button';
import { Dialog } from '@/kit/Dialog';
import { Field } from '@/kit/Field';
import { Icon } from '@/kit/Icon';
import { Pill } from '@/kit/Pill';
import { toast } from '@/kit/Toast';
import { browserFrame, setBrowserControl } from './queries';

/* Open browser (old app: ui/app/browser-view.ts): a person's live view of a website account's browser, to sign in or
   finish a step by hand. POST …/browser opens or wakes it; GET …/frame is its picture about once a second (204 before
   the first); POST …/control {take} takes control or gives it back (agents wait meanwhile, or 5 idle minutes);
   POST …/input sends a click in page pixels, typed text, a key or a scroll. Control held when it closes is given back. */

const EVERY_MS = 1000;
const SOON_MS = 250;
const KEYS = ['Enter', 'Tab', 'Backspace'] as const;

export interface BrowserDialogProps {
  /** The website tool's id. */
  tool: string;
  /** One of its accounts the person may open (`can_open`). */
  account: string;
  /** The tool's name, or the ask's line. */
  title: string;
  onClose: () => void;
  /** Done, before it closes: answers an ask. `false` (or a throw, which is shown) keeps it open. */
  onDone?: () => unknown;
}

type Frame = { src: string; url: string; width: number; height: number };

/** The address as it reads best: host and path, no query. */
const shortUrl = (url: string) => {
  try {
    const u = new URL(url);
    return u.host + (u.pathname === '/' ? '' : u.pathname);
  } catch {
    return url;
  }
};

export function BrowserDialog({ tool, account, title, onClose, onDone }: BrowserDialogProps) {
  const open = useAction('browser.open');
  const input = useAction('browser.input');
  const take = useAction('browser.takeControl');
  const give = useAction('browser.giveBack');
  const [frame, setFrame] = useState<Frame | null>(null);
  const [line, setLine] = useState('');
  const [control, setControl] = useState(false);
  const [text, setText] = useState('');
  const [finishing, setFinishing] = useState(false);
  const held = useRef(false);
  const soon = useRef<() => void>(() => {});
  const img = useRef<HTMLImageElement>(null);
  const scroll = useRef({ by: 0, timer: 0 });
  const down = useRef<{ x: number; y: number; last: number; moved: boolean } | null>(null);
  const hold = (on: boolean) => {
    held.current = on;
    setControl(on);
  };

  const wake = useEffectEvent(() => open.run({ tool, account }));

  // Open (or wake) it, then its picture about once a second while the window is shown. Closing lets everything go.
  useEffect(() => {
    let stopped = false;
    let busy = false;
    let timer = 0;
    let shown = '';
    const abort = new AbortController();
    const next = (ms: number) => {
      window.clearTimeout(timer);
      if (!stopped) timer = window.setTimeout(() => void tick(), ms);
    };
    const tick = async () => {
      if (stopped || busy || document.hidden) return;
      busy = true;
      try {
        const f = await browserFrame(tool, account, abort.signal);
        if (stopped) return;
        if (f) {
          const src = URL.createObjectURL(f.image);
          if (shown) URL.revokeObjectURL(shown);
          shown = src;
          setFrame({ src, url: f.url, width: f.width, height: f.height });
          setLine('');
        }
      } catch (e) {
        if (stopped) return;
        // Offline for a moment: the next tick tries again. A refusal says why.
        if ((e as { status?: number }).status) setLine((e as Error).message || 'The browser did not load');
      } finally {
        busy = false;
      }
      next(EVERY_MS);
    };
    soon.current = () => next(SOON_MS);
    const onVisible = () => !document.hidden && next(0);
    document.addEventListener('visibilitychange', onVisible);
    void wake().then((r) => {
      if (stopped) return;
      if (r.ok) next(0);
      else setLine('The browser did not open');
    });
    const pending = scroll.current;
    return () => {
      stopped = true;
      abort.abort();
      window.clearTimeout(timer);
      window.clearTimeout(pending.timer);
      document.removeEventListener('visibilitychange', onVisible);
      if (shown) URL.revokeObjectURL(shown);
      // So an agent is not kept waiting the five idle minutes.
      if (held.current) void setBrowserControl(tool, account, false).catch(() => {});
      held.current = false;
    };
  }, [tool, account]);

  const send = async (what: { kind: 'click'; x: number; y: number } | { kind: 'type'; text: string } | { kind: 'key'; key: (typeof KEYS)[number] } | { kind: 'scroll'; dy: number }) => {
    if (!held.current) return;
    const r = await input.run({ tool, account, ...what });
    if (r.ok) soon.current();
    // Control ran out (five idle minutes) or someone else took it.
    else if (r.error === 'no_control' || r.error === 'busy') hold(false);
  };

  const scrollBy = (dy: number) => {
    const s = scroll.current;
    s.by += dy;
    if (s.timer) return;
    s.timer = window.setTimeout(() => {
      s.timer = 0;
      const by = Math.round(s.by);
      s.by = 0;
      if (by) void send({ kind: 'scroll', dy: by });
    }, 150);
  };

  /** A point on the picture in the page's own pixels. */
  const pagePoint = (x: number, y: number) => {
    const el = img.current;
    const r = el?.getBoundingClientRect();
    if (!el || !r?.width || !r.height) return null;
    const w = frame?.width || el.naturalWidth;
    const h = frame?.height || el.naturalHeight;
    const px = Math.round(((x - r.left) / r.width) * w);
    const py = Math.round(((y - r.top) / r.height) * h);
    return px < 0 || py < 0 || px > w || py > h ? null : { x: px, y: py };
  };
  const pageScale = () => {
    const r = img.current?.getBoundingClientRect();
    return r?.height ? (frame?.height || img.current?.naturalHeight || r.height) / r.height : 1;
  };

  // A tap is a click; a drag scrolls. The wheel scrolls too (a listener that may hold the page still).
  const onDown = (e: ReactPointerEvent) => {
    if (held.current && e.button === 0) down.current = { x: e.clientX, y: e.clientY, last: e.clientY, moved: false };
  };
  const onMove = (e: ReactPointerEvent) => {
    const d = down.current;
    if (!d) return;
    if (Math.abs(e.clientY - d.y) > 8 || Math.abs(e.clientX - d.x) > 8) d.moved = true;
    if (d.moved) {
      scrollBy((d.last - e.clientY) * pageScale());
      d.last = e.clientY;
    }
  };
  const onUp = (e: ReactPointerEvent) => {
    const d = down.current;
    down.current = null;
    if (!d || d.moved) return;
    const at = pagePoint(e.clientX, e.clientY);
    if (at) void send({ kind: 'click', ...at });
  };
  const stageRef = (el: HTMLDivElement | null) => {
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!held.current) return;
      e.preventDefault();
      scrollBy(e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  };

  const toggle = async () => {
    const r = control ? await give.run({ tool, account }) : await take.run({ tool, account });
    if (r.ok) hold(!control);
  };

  const done = async () => {
    setFinishing(true);
    try {
      if (onDone && (await onDone()) === false) return;
      if (held.current && (await give.run({ tool, account })).ok) hold(false);
      onClose();
    } catch (e) {
      toast({ text: e instanceof Error && e.message ? e.message : "It didn't finish", kind: 'failed' });
    } finally {
      setFinishing(false);
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      className="bv"
      title={title}
      actions={
        <>
          <Button busy={take.pending || give.pending} onClick={() => void toggle()}>
            {control ? 'Give back' : 'Take control'}
          </Button>
          <Button variant="primary" busy={finishing} onClick={() => void done()}>
            Done
          </Button>
        </>
      }
    >
      <div className="bv-url">
        <Icon name="lock" />
        <span className="mono" title={frame?.url}>
          {frame ? shortUrl(frame.url) : ''}
        </span>
        {control && <Pill>You have control</Pill>}
      </div>
      <div
        className={control ? 'bv-stage is-control' : 'bv-stage'}
        data-target={`browser:${account}`}
        ref={stageRef}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => (down.current = null)}
      >
        {frame && <img ref={img} src={frame.src} alt={`The page at ${shortUrl(frame.url)}`} draggable={false} />}
        {(line || !frame) && (
          <p className="bv-msg" role="status">
            {line || 'Loading…'}
          </p>
        )}
      </div>
      {control && (
        <form
          className="bv-type"
          onSubmit={(e) => {
            e.preventDefault();
            // An empty box sends Enter: a phone keyboard's Send is the page's Enter then.
            void send(text ? { kind: 'type', text } : { kind: 'key', key: 'Enter' });
            setText('');
          }}
        >
          <Field label="Type" labelHidden placeholder="Type" value={text} onValueChange={setText} autoComplete="off" autoFocus />
          {KEYS.map((k) => (
            <Button key={k} onClick={() => void send({ kind: 'key', key: k })}>
              {k}
            </Button>
          ))}
        </form>
      )}
    </Dialog>
  );
}
