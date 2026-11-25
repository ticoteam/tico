import { Toast } from '@base-ui/react/toast';
import { useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '@/kit/Icon';
import './Toast.css';

export type ToastInput = {
  /** One line, such as "Task moved" or "Couldn't save". With `name`, the words before it: "Approved". */
  text: string;
  /** The thing it was done to, in bold after the words: "Approved: Spring launch". */
  name?: string;
  /** `done` shows a check, `failed` the alert mark (in ink; no amber), `sending` the send mark. */
  kind?: 'done' | 'failed' | 'sending';
  /** One word at the right: "Undo" or "Retry". */
  action?: { label: string; run: () => void };
  /** How long it stays, in ms; 0 keeps it until it is changed or dismissed. */
  timeout?: number;
  /** A close mark at the right, for a toast that stays. */
  dismiss?: boolean;
};

type Extra = { name?: string; dismiss?: boolean };

const manager = Toast.createToastManager<Extra>();

/** The manager's options for a toast: its action closes it, then runs. */
const options = (id: () => string, { text, name, kind = 'done', action, timeout, dismiss }: ToastInput) => ({
  title: text,
  type: kind,
  data: { name, dismiss },
  ...(timeout !== undefined && { timeout }),
  actionProps: action
    ? {
        children: action.label,
        onClick: () => {
          manager.close(id());
          action.run();
        },
      }
    : undefined,
});

/** Show a toast at the foot of the page sheet. It stays 5 s (paused while the pointer is on it). Returns its id. */
export function toast(input: ToastInput): string {
  const id: string = manager.add(options(() => id, input));
  return id;
}

/** Change a toast in place: its words, mark, action and how long it stays from now. */
export function updateToast(id: string, input: ToastInput) {
  manager.update(id, options(() => id, input));
}

export function closeToast(id: string) {
  manager.close(id);
}

/* Where the toasts stand: at the page's foot, or in the layer open over it (a Viewer), whose focus trap and dim would
   leave them, and an Undo, out of reach. The top layer holds them, and Cmd+K's search opened over it, which its hiding
   of all else from a screen reader would otherwise reach: the deepest (a file opened from a task's layer is inside it),
   and of the same depth the last opened. */
const hosts: { el: HTMLElement; depth: number }[] = [];
const heard = new Set<() => void>();
const hear = (on: () => void) => {
  heard.add(on);
  return () => void heard.delete(on);
};
const host = () => hosts.at(-1)?.el ?? null;

/** A layer over the page holds the toasts while it is open (`depth`: 1 over the page, 2 opened from a layer, and so on);
    the returned function gives them back. */
export function holdToasts(el: HTMLElement, depth = 1): () => void {
  let at = hosts.length;
  while (at > 0 && hosts[at - 1]!.depth > depth) at--;
  hosts.splice(at, 0, { el, depth });
  heard.forEach((on) => on());
  return () => {
    const i = hosts.findIndex((h) => h.el === el);
    if (i >= 0) hosts.splice(i, 1);
    heard.forEach((on) => on());
  };
}

/** The layer open over the page that holds what shows over it (toasts, Cmd+K's search), or null: none open. */
export const useTopLayer = (): HTMLElement | null => useSyncExternalStore(hear, host, () => null);

/** Where toasts show. The shell mounts this once. */
export function Toasts() {
  const over = useTopLayer();
  const viewport = (
    <Toast.Viewport className="toasts">
      <List />
    </Toast.Viewport>
  );
  return (
    <Toast.Provider toastManager={manager} timeout={5000} limit={3}>
      {over ? createPortal(viewport, over) : viewport}
    </Toast.Provider>
  );
}

function List() {
  const { toasts } = Toast.useToastManager<Extra>();
  return toasts.map((t) => {
    const name = t.data?.name;
    return (
      <Toast.Root
        key={t.id}
        toast={t}
        className={['toast kit-toast', !t.actionProps && !t.data?.dismiss && 'is-plain', name && 'is-named'].filter(Boolean).join(' ')}
      >
        <Icon name={t.type === 'failed' ? 'alert' : t.type === 'sending' ? 'send' : 'check'} />
        <Toast.Title render={<span />}>
          {name ? (
            <>
              {t.title}: <b>{name}</b>
            </>
          ) : (
            t.title
          )}
        </Toast.Title>
        {t.actionProps && <Toast.Action />}
        {t.data?.dismiss && (
          <Toast.Close aria-label="Dismiss">
            <Icon name="close" />
          </Toast.Close>
        )}
      </Toast.Root>
    );
  });
}
