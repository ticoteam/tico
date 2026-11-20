import { Toast } from '@base-ui/react/toast';
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

/** Where toasts show. The shell mounts this once. */
export function Toasts() {
  return (
    <Toast.Provider toastManager={manager} timeout={5000} limit={3}>
      <Toast.Viewport className="toasts">
        <List />
      </Toast.Viewport>
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
