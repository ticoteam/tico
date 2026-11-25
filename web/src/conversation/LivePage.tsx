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
export const MIN_HEIGHT = 120;
export const MAX_HEIGHT = 480;
/** A sidebar widget's card: the small size, as tall as docs/pages.md says a small card is. */
const SMALL_HEIGHT = 180;
/** Before the page reports its height. */
const LOADING_HEIGHT = 240;
/** No height this long after the card comes into view: the address did not serve a page (an edge that does not know
    the page's policy yet answers 404), so the plain card shows instead of an empty box. */
export const WAIT_MS = 8000;

export const clampHeight = (h: number, max = MAX_HEIGHT) => Math.min(max, Math.max(MIN_HEIGHT, Math.ceil(h)));

/** The last height each file version reported, so a card drawn again opens at its size. Only a size: whether this
    frame has answered is its own (a card drawn again still waits for its page, and still falls back if none comes). */
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

const hostOf = (url: string | null | undefined) => {
  try {
    return url ? new URL(url).hostname : null;
  } catch {
    return null;
  }
};

/**
 * The address a page shows live from, or null for the plain card. Only one of this install's own visual addresses
 * counts, so a sender can't put another site in a chat as a Tico page: the visual address or the company's files
 * address, or, with Tico file hosting, the app's own `/visuals/<token>` route, which checks the reader and sends the
 * frame on to the files address (it never serves a page itself). Never a page on the app's own host name otherwise,
 * whatever its port: cookies are kept per host name, so a visual address on another port of the app's host (or its
 * loopback twin, 127.0.0.1 and localhost, with the app opened there) would run the page beside the app's cookies.
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
  if (url.hostname === location.hostname) {
    const hosted = at === location.origin && !config.visual_origin && config.visual_files && src.startsWith('/visuals/');
    return hosted && hostOf(config.visual_files) !== location.hostname ? url.href : null;
  }
  return at === originOf(config.visual_origin) || at === originOf(config.visual_files) ? url.href : null;
}

/** The layer is a portal, but React bubbles its presses up through the message it opened from: stopped here, a long
    press or right click inside it never opens that message's Copy and Reply sheet. */
const stop = (e: SyntheticEvent) => e.stopPropagation();

/** A page from its signed address when it is one of the install's own, else `fallback` (the plain page card). */
export function LivePage({ src, title, id, fallback, size = 'medium' }: { src: string | null | undefined; title: string; id: string; fallback: ReactNode; size?: 'small' | 'medium' }) {
  const config = useQuery(configQuery).data;
  const live = liveSrc(src, config);
  if (!live) return fallback;
  // A new file version is a new frame; a renewed address for the same version is not.
  return <LiveFrame key={id} src={live} title={title} id={id} fallback={fallback} size={size} />;
}

function LiveFrame({ src, title, id, fallback, size }: { src: string; title: string; id: string; fallback: ReactNode; size: 'small' | 'medium' }) {
  const max = size === 'small' ? SMALL_HEIGHT : MAX_HEIGHT;
  // The first address stays while the card is drawn: a renewed token in a later read never reloads the page.
  const [first] = useState(src);
  const [height, setHeight] = useState<number | null>(() => heights.get(id) ?? null);
  const [heard, setHeard] = useState(false);
  const [failed, setFailed] = useState(false);
  // The address the large view opened with, held while it is open for the same reason.
  const [open, setOpen] = useState<string | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const full = useRef<HTMLIFrameElement>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const hear = (e: MessageEvent) => {
      const data = e.data as { ticoVisualHeight?: unknown; ticoVisualKey?: unknown } | null;
      // Escape inside the large view's page closes it, as Escape outside it does (the reporter passes it on).
      if (full.current && e.source === full.current.contentWindow && data?.ticoVisualKey === 'Escape') setOpen(null);
      // only this card's own frame sizes it: another page's report, or the app's own messages, are not its height
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const h = data?.ticoVisualHeight;
      // 0 is a page laid out out of the flow (fixed to the viewport): it is there, at the smallest size
      if (typeof h !== 'number' || !Number.isFinite(h) || h < 0) return;
      if (h > 0) heights.set(id, h);
      setHeight(h);
      setHeard(true);
    };
    window.addEventListener('message', hear);
    return () => window.removeEventListener('message', hear);
  }, [id]);

  useEffect(() => {
    if (heard || failed) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const start = () => {
      timer ??= setTimeout(() => setFailed(true), WAIT_MS);
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
  }, [heard, failed]);

  const cut = height !== null && height > max;
  // An open large view stays when the card falls back: a page whose own policy blocks the height reporter still runs
  // there. The card is the fragment's first child either way, so the large view is never drawn again.
  return (
    <>
      {failed ? (
        fallback
      ) : (
        <div ref={box} className={heard ? 'pcard lpage' : 'pcard lpage is-loading'}>
          <div className="lpage-view" style={{ height: height === null ? Math.min(LOADING_HEIGHT, max) : clampHeight(height, max) }}>
            <iframe ref={frame} src={`${first}#tico-view=card&tico-size=${size}`} title={title} sandbox={LIVE_SANDBOX} allow={ALLOW} loading="lazy" referrerPolicy="no-referrer" />
            {cut && <span className="lpage-fade" aria-hidden="true" />}
          </div>
          <span className="pcard-foot">
            <Icon name="tobar" />
            <span>{title}</span>
            <IconButton icon="expand" label="Expand" size="sm" quiet onClick={() => setOpen(src)} />
          </span>
        </div>
      )}
      {open && (
        <span className="fcard-layer" onClick={stop} onContextMenu={stop} onTouchStart={stop} onPointerDown={stop}>
          <Viewer
            open
            onClose={() => setOpen(null)}
            title={title}
            className="lpage-full"
            actions={
              <a className="ib" href={open} target="_blank" rel="noreferrer" aria-label="Open in new tab" title="Open in new tab">
                <Icon name="popout" />
              </a>
            }
          >
            <iframe ref={full} src={open} title={title} sandbox={LIVE_SANDBOX} allow={ALLOW} referrerPolicy="no-referrer" />
          </Viewer>
        </span>
      )}
    </>
  );
}
