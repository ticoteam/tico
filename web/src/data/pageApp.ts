import { useEffect, useState, useSyncExternalStore, type RefObject } from 'react';
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

/** One line of a confirm sheet, as the server words it from Tico's own records (backend/page_app_writes.py). */
export interface ActLine {
  kind: string;
  label: string;
  detail: string;
}

/** What a page app asks to write, waiting for the viewer's tap. `answer` sends it, and settles when the server has. */
export interface ActSheet {
  page: string;
  lines: ActLine[];
  answer: (confirm: boolean) => Promise<void>;
}

/* The sheet on screen: one at a time for the whole app, drawn outside every frame (conversation/AppActSheet). */
let shown: ActSheet | null = null;
// One ask at a time in the whole app, taken before the server is asked: a second page (or a second click) never swaps
// the sheet under the viewer's finger.
let slot: object | null = null;
const watchers = new Set<() => void>();
const show = (sheet: ActSheet | null) => {
  shown = sheet;
  for (const w of watchers) w();
};
const watch = (w: () => void) => {
  watchers.add(w);
  return () => watchers.delete(w);
};
export const useActSheet = (): ActSheet | null => useSyncExternalStore(watch, () => shown);

const go = (href: string, live: () => boolean) => {
  // the router imports most of the app: loaded when an app first opens something; a frame gone meanwhile goes nowhere
  void import('@/router').then(({ router }) => {
    if (live()) return router.navigate({ href });
  });
};

/** Listens for `frame`'s handshake and serves its calls; returns the cleanup. `onOpen` takes the app to an address. */
export function bridge(frame: HTMLIFrameElement, app: AppPage, onOpen: (href: string, live: () => boolean) => void = go): () => void {
  const base = `/api/v2/pages/${encodeURIComponent(app.page)}/app` as const;
  let port: MessagePort | null = null;
  let done = false;
  let asking: ActSheet | null = null;
  const close = () => {
    done = true;
    // Closing the page cancels what it asked; the server holds it only for its key.
    void asking?.answer(false);
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
      const fail = (err: unknown) => {
        const code = err instanceof ApiError ? err.code : 'failed';
        const detail = err instanceof Error ? err.message : 'The call failed';
        port?.postMessage({ id: call.id, error: { code, detail } });
      };
      if (call.op === 'act') {
        if (slot) return fail(new ApiError(409, 'busy', 'An action is already waiting'));
        const mine = {};
        slot = mine;
        const free = () => void (slot === mine && (slot = null));
        // The server checks the actions as the viewer and holds them under a key that stays here, never in the frame.
        void api
          .post<{ result: { key: string; page: string; lines: ActLine[] } }>(base, { version: app.version, nonce, op: 'act', args }, { once: true })
          .then(({ result }) => {
            if (done) {
              free();
              return void api.post(`${base}/act`, { key: result.key, confirm: false }, { once: true }).catch(() => undefined);
            }
            let settled = false;
            const sheet: ActSheet = {
              page: result.page,
              lines: result.lines,
              answer: (confirm) => {
                if (settled) return Promise.resolve();
                settled = true;
                return api
                  .post<{ status: string; items: unknown[] }>(`${base}/act`, { key: result.key, confirm }, { once: true })
                  .then((r) => void port?.postMessage({ id: call.id, result: r }))
                  .catch(fail)
                  .finally(() => {
                    asking = null;
                    free();
                    if (shown === sheet) show(null);
                  });
              },
            };
            asking = sheet;
            show(sheet);
          })
          .catch((e: unknown) => {
            free();
            fail(e);
          });
        return;
      }
      api
        .post<{ result: Record<string, unknown> }>(base, { version: app.version, nonce, op: call.op, args }, { once: true })
        .then((r) => {
          if (done) return;
          if (call.op === 'open' && typeof r.result.href === 'string') onOpen(r.result.href, () => !done);
          port?.postMessage({ id: call.id, result: r.result });
        })
        .catch(fail);
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
