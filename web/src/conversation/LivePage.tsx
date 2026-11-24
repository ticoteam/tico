import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode, type SyntheticEvent } from 'react';
import { configQuery } from '@/data';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { Viewer } from '@/kit/Viewer';
import './livepage.css';

/* An agent's page in a chat, running as its author wrote it (backend/visuals.py `policy`): a card that fits the page's
   height, and Expand to a large view. What keeps it from the app is its host: it is only ever framed from the
   install's visual address or the company's files address, never the app's own, so its same-origin is that host's
   storage, not the app's cookies or window. Anything else is today's plain page card. */

export const LIVE_SANDBOX = 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals';
const ALLOW = 'fullscreen; clipboard-write';
const CARD = '#tico-view=card&tico-size=medium';
export const MIN_HEIGHT = 120;
export const MAX_HEIGHT = 480;
/** Before the page reports its height. */
const LOADING_HEIGHT = 240;
/** No height this long after the card comes into view: the address did not serve a page (an edge that does not know
    the page's policy yet answers 404), so the plain card shows instead of an empty box. */
export const WAIT_MS = 8000;

export const clampHeight = (h: number) => Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(h)));

/** The last height each file version reported (0: it never did), so a card drawn again opens at its size. */
const heights = new Map<string, number>();

interface Visuals {
  visual_origin?: string | null;
  visual_files?: string | null;
}

const originOf = (url: string | null | undefined) => {
  try {
    return url ? new URL(url).origin : null;
  } catch {
    return null;
  }
};

/**
 * The address a page shows live from, or null for the plain card. Only one of this install's own visual addresses
 * counts, so a sender can't put another site in a chat as a Tico page: the visual address or the company's files
 * address, or, with Tico file hosting, the app's own `/visuals/<token>` route, which checks the reader and sends the
 * frame on to the files address (it never serves a page itself).
 */
export function liveSrc(src: unknown, config: Visuals | undefined): string | null {
  if (typeof src !== 'string' || !config) return null;
  let url: URL;
  try {
    url = new URL(src, location.origin);
  } catch {
    return null;
  }
  if (!/^\/visuals\/[A-Za-z0-9_.-]+$/.test(url.pathname) || url.search || url.hash || url.username || url.password) return null;
  const at = url.origin;
  if (at === originOf(config.visual_origin) || at === originOf(config.visual_files)) return url.href;
  if (config.visual_files && at === location.origin && src.startsWith('/visuals/')) return url.href;
  return null;
}

/** The layer is a portal, but React bubbles its presses up through the message it opened from: stopped here, a long
    press or right click inside it never opens that message's Copy and Reply sheet. */
const stop = (e: SyntheticEvent) => e.stopPropagation();

/** A page from its signed address when it is one of the install's own, else `fallback` (the plain page card). */
export function LivePage({ src, title, id, fallback }: { src: string | null | undefined; title: string; id: string; fallback: ReactNode }) {
  const config = useQuery(configQuery).data;
  const live = liveSrc(src, config);
  if (!live) return fallback;
  return <LiveFrame src={live} title={title} id={id} fallback={fallback} />;
}

function LiveFrame({ src, title, id, fallback }: { src: string; title: string; id: string; fallback: ReactNode }) {
  // The first address stays while the card is drawn: a renewed token in a later read never reloads the page.
  const [first] = useState(src);
  const [height, setHeight] = useState(() => heights.get(id) ?? null);
  const [open, setOpen] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const failed = height === 0;
  const reported = height !== null && height > 0;

  useEffect(() => {
    const hear = (e: MessageEvent) => {
      // only this card's own frame sizes it: another page's report, or the app's own messages, are not its height
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const h: unknown = (e.data as { ticoVisualHeight?: unknown } | null)?.ticoVisualHeight;
      if (typeof h !== 'number' || !Number.isFinite(h) || h <= 0) return;
      heights.set(id, h);
      setHeight(h);
    };
    window.addEventListener('message', hear);
    return () => window.removeEventListener('message', hear);
  }, [id]);

  useEffect(() => {
    if (reported || failed) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const start = () => {
      timer ??= setTimeout(() => {
        heights.set(id, 0);
        setHeight(0);
      }, WAIT_MS);
    };
    // a lazy frame below the fold has not started loading: its wait starts when it comes into view
    const seen =
      typeof IntersectionObserver === 'undefined' || !box.current
        ? null
        : new IntersectionObserver((all) => {
            if (all.some((e) => e.isIntersecting)) start();
          });
    if (seen && box.current) seen.observe(box.current);
    else start();
    return () => {
      seen?.disconnect();
      clearTimeout(timer);
    };
  }, [id, reported, failed]);

  if (failed) return fallback;
  const cut = reported && height > MAX_HEIGHT;
  return (
    <>
      <div ref={box} className={reported ? 'pcard lpage' : 'pcard lpage is-loading'}>
        <div className="lpage-view" style={{ height: reported ? clampHeight(height) : LOADING_HEIGHT }}>
          <iframe ref={frame} src={first + CARD} title={title} sandbox={LIVE_SANDBOX} allow={ALLOW} loading="lazy" referrerPolicy="no-referrer" />
          {cut && <span className="lpage-fade" aria-hidden="true" />}
        </div>
        <span className="pcard-foot">
          <Icon name="tobar" />
          <span>{title}</span>
          <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setOpen(true)} />
        </span>
      </div>
      {open && (
        <span className="fcard-layer" onClick={stop} onContextMenu={stop} onTouchStart={stop} onPointerDown={stop}>
          <Viewer
            open
            onClose={() => setOpen(false)}
            title={title}
            className="lpage-full"
            actions={
              <a className="ib" href={src} target="_blank" rel="noreferrer" aria-label="Open in new tab" title="Open in new tab">
                <Icon name="popout" />
              </a>
            }
          >
            <iframe src={src} title={title} sandbox={LIVE_SANDBOX} allow={ALLOW} referrerPolicy="no-referrer" />
          </Viewer>
        </span>
      )}
    </>
  );
}
