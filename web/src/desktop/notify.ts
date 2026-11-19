import { desktop } from './index';

/* A system notification from this page (platforms.md 4.7): the browser's, which the desktop app shows natively. Shown
   only while the page is out of sight and the browser allows it; a browser that refuses one shows nothing. In the
   desktop app with more than one company, the title starts with this company's name, and a click brings this
   company's view forward before the page goes to the address. */

export interface Notice {
  title: string;
  body?: string;
  /** One per item: a notice with the same tag replaces the last one. */
  tag: string;
  /** The app address a click opens, like `/flow/12`. */
  url: string;
}

/** Whether a notice may show now: the browser has them, they are allowed, and the page is not in front of the person. */
export const mayNotify = (): boolean =>
  typeof window !== 'undefined' &&
  'Notification' in window &&
  Notification.permission === 'granted' &&
  (document.hidden || !document.hasFocus());

/** Goes to an app address through the action layer. Loaded at the click: desktop/ sits below the actions, which are
    long loaded by then. */
const go = (url: string) => import('@/actions').then(({ run }) => run('shell.goTo', { to: url }, { via: 'click' }));

/** Shows `n`, or nothing when a notice may not show now. Returns the notification, so a ring can close its own. */
export async function notify(n: Notice): Promise<Notification | null> {
  if (!mayNotify()) return null;
  const t = desktop();
  const all = t ? await t.companies.list().catch(() => []) : [];
  const self = all.find((c) => c.self);
  const title = all.length > 1 && self?.name ? `${self.name} · ${n.title}` : n.title;
  try {
    const note = new Notification(title, { body: n.body ?? '', tag: n.tag, data: { url: n.url } });
    note.onclick = () => {
      note.close();
      try {
        window.focus();
      } catch {
        /* a browser may refuse */
      }
      void (async () => {
        if (t && self) await t.companies.switchTo(self.id).catch(() => undefined);
        await go(n.url);
      })();
    };
    return note;
  } catch {
    return null; // a browser that refuses a notification shows nothing; the page still has the item
  }
}
