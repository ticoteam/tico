import { useEffect, useState, type RefObject } from 'react';
import { ApiError, api } from '@/api/client';

/* A page app's bridge, the app's side (docs/pages.md#apps, backend/page_apps.py). The page is framed sealed (no
   same-origin), and Tico's script in it posts one MessageChannel port with its key. The frame that drew it takes that
   port once, from its own frame only; every call on it goes to the server as the viewer, for this page and version.
   A reload or navigation inside the frame, or the frame going away, closes the port: a new page there gets nothing. */

/** An app frame: no same-origin, so a sibling page on the same host reaches neither this frame nor its storage. */
export const APP_SANDBOX = 'allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals';

export interface AppPage {
  page: string;
  version: number;
}

interface Call {
  id?: unknown;
  op?: unknown;
  args?: unknown;
}

const go = (href: string, live: () => boolean) => {
  // the router imports most of the app: loaded when an app first opens something; a frame gone meanwhile goes nowhere
  void import('@/router').then(({ router }) => {
    if (live()) return router.navigate({ href });
  });
};

/** Listens for `frame`'s handshake and serves its calls; returns the cleanup. `onOpen` takes the app to an address. */
export function bridge(frame: HTMLIFrameElement, app: AppPage, onOpen: (href: string, live: () => boolean) => void = go): () => void {
  let port: MessagePort | null = null;
  let done = false;
  const close = () => {
    done = true;
    port?.close();
    port = null;
    window.removeEventListener('message', hear);
    frame.removeEventListener('load', reload);
  };
  // The first load is the page that posted its port; any later one is a new page in the frame, which gets nothing (its
  // own handshake is not heard: one port per frame drawn).
  let loads = 0;
  const reload = () => {
    loads += 1;
    if (loads > 1) close();
  };
  const hear = (e: MessageEvent) => {
    if (done || port || e.source !== frame.contentWindow) return;
    const data = e.data as { ticoApp?: unknown } | null;
    const nonce = data?.ticoApp;
    const given = e.ports[0];
    if (typeof nonce !== 'string' || !given) return;
    port = given;
    window.removeEventListener('message', hear);
    port.onmessage = (m: MessageEvent) => {
      const call = (m.data ?? {}) as Call;
      const args = call.args && typeof call.args === 'object' ? call.args : {};
      if (typeof call.op !== 'string') return;
      api
        .post<{ result: Record<string, unknown> }>(`/api/v2/pages/${encodeURIComponent(app.page)}/app`, { version: app.version, nonce, op: call.op, args }, { once: true })
        .then((r) => {
          if (done) return;
          if (call.op === 'open' && typeof r.result.href === 'string') onOpen(r.result.href, () => !done);
          port?.postMessage({ id: call.id, result: r.result });
        })
        .catch((err: unknown) => {
          const code = err instanceof ApiError ? err.code : 'failed';
          const detail = err instanceof Error ? err.message : 'The call failed';
          port?.postMessage({ id: call.id, error: { code, detail } });
        });
    };
  };
  window.addEventListener('message', hear);
  frame.addEventListener('load', reload);
  return close;
}

/** The ref for an app page's frame: its bridge runs while that frame is drawn, and a new frame element (one drawn
    later, as the large view's in its portal, or drawn again) gets its own. Nothing for any other page. */
export function useAppBridge(app: AppPage | null | undefined): (frame: HTMLIFrameElement | null) => void {
  const [frame, setFrame] = useState<HTMLIFrameElement | null>(null);
  const page = app?.page;
  const version = app?.version;
  useEffect(() => {
    if (!frame || !page || !version) return;
    return bridge(frame, { page, version });
  }, [frame, page, version]);
  return setFrame;
}

/** App frames a sidebar runs at once; the rest show their still until one is let go. */
export const APP_FRAMES = 6;
const running = new Set<object>();
const waiting = new Set<() => void>();
const letGo = (who: object) => {
  if (running.delete(who)) for (const next of waiting) next();
};

/** Whether the app drawn in `box` may run now: in view, and one of the APP_FRAMES running. Out of view it lets its
    frame go, so the next one waiting runs. */
export function useAppSlot(box: RefObject<HTMLElement | null>, on: boolean): boolean {
  const [runs, setRuns] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!on || !el) return;
    const me = {};
    let seen = typeof IntersectionObserver === 'undefined';
    const settle = () => {
      if (!seen) letGo(me);
      else if (running.size < APP_FRAMES) running.add(me);
      setRuns(running.has(me));
    };
    const watch =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver((all) => {
            seen = all.some((e) => e.isIntersecting);
            settle();
          });
    watch?.observe(el);
    waiting.add(settle);
    settle();
    return () => {
      watch?.disconnect();
      waiting.delete(settle);
      letGo(me);
    };
  }, [box, on]);
  return on && runs;
}
