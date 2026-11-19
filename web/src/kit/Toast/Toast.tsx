import { Toast } from '@base-ui/react/toast';
import { Icon } from '@/kit/Icon';
import './Toast.css';

export type ToastInput = {
  /** One line, such as "Task moved" or "Couldn't save". With `name`, the words before it: "Approved". */
  text: string;
  /** The thing it was done to, in bold after the words: "Approved: Spring launch". */
  name?: string;
  /** `done` shows a check, `failed` the alert mark (in ink; no amber). */
  kind?: 'done' | 'failed';
  /** One word at the right: "Undo" or "Retry". */
  action?: { label: string; run: () => void };
};

type Extra = { name?: string };

const manager = Toast.createToastManager<Extra>();

/** Show a toast at the foot of the page sheet. It stays 5 s (paused while the pointer is on it). Returns its id. */
export function toast({ text, name, kind = 'done', action }: ToastInput): string {
  const id: string = manager.add({
    title: text,
    type: kind,
    data: { name },
    actionProps: action
      ? {
          children: action.label,
          onClick: () => {
            manager.close(id);
            action.run();
          },
        }
      : undefined,
  });
  return id;
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
        className={['toast kit-toast', !t.actionProps && 'is-plain', name && 'is-named'].filter(Boolean).join(' ')}
      >
        <Icon name={t.type === 'failed' ? 'alert' : 'check'} />
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
      </Toast.Root>
    );
  });
}
