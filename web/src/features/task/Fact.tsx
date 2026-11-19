import { useState, type ReactNode } from 'react';
import { run } from '@/actions';
import { Icon } from '@/kit/Icon';
import { Menu } from '@/kit/Menu';
import { reasonOf } from './model';

/* One fact in Details (more.js dKv): a muted name, then its value. A value edits in place (a field opens where it
   was) or through a small pick (a menu on the value). */

export function Kv({ k, children, target }: { k: string; children: ReactNode; target?: string }) {
  return (
    <div className="dt-kv" data-target={target}>
      <span className="dt-k">{k}</span>
      <span className="dt-v">{children}</span>
    </div>
  );
}

/** Runs a change on the task; the refusal's reason, or null once it is saved. */
export async function saved(id: string, input: object): Promise<string | null> {
  const r = await run(id, input, { via: 'click' });
  return r.ok ? null : reasonOf(r.error);
}

/** A field written in place: Enter saves, Escape puts it back. A refusal reads under it and the words stay. */
export function Inline({
  label,
  initial = '',
  placeholder,
  numeric,
  onSave,
  onEnd,
}: {
  label: string;
  initial?: string;
  placeholder?: string;
  numeric?: boolean;
  onSave: (value: string) => Promise<string | null>;
  onEnd: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (busy) return;
    if (!value.trim() && !initial) return onEnd();
    setBusy(true);
    const refused = await onSave(value);
    setBusy(false);
    if (refused) setError(refused);
    else onEnd();
  };
  return (
    <span className="dt-in">
      <label className={error ? 'field is-wide is-error' : 'field is-wide'}>
        <input
          type="text"
          inputMode={numeric ? 'decimal' : undefined}
          aria-label={label}
          placeholder={placeholder}
          value={value}
          disabled={busy}
          autoFocus
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void save();
            else if (e.key === 'Escape') onEnd();
          }}
        />
      </label>
      {error && (
        <span className="f-err" role="alert">
          <Icon name="alert" />
          <span>{error}</span>
        </span>
      )}
    </span>
  );
}

/** A fact that edits in place: its value is the button that opens the field. `label` names the button when its
    value alone ("None") would not say which fact it is. */
export function EditKv({ k, label, target, value, edit }: { k: string; label?: string; target?: string; value: ReactNode; edit: (end: () => void) => ReactNode }) {
  const [on, setOn] = useState(false);
  if (on)
    return (
      <div className="dt-kv is-edit" data-target={target}>
        <span className="dt-k">{k}</span>
        <div className="dt-v">{edit(() => setOn(false))}</div>
      </div>
    );
  return (
    <div className="dt-kv" data-target={target}>
      <span className="dt-k">{k}</span>
      <button type="button" className="dt-v dt-val" aria-label={label} onClick={() => setOn(true)}>
        {value}
      </button>
    </div>
  );
}

/** A fact picked from a short list: its value opens the menu. */
export function PickKv({ k, label, target, value, children, after }: { k: string; label: string; target?: string; value: ReactNode; children: ReactNode; after?: ReactNode }) {
  return (
    <div className="dt-kv" data-target={target}>
      <span className="dt-k">{k}</span>
      <span className="dt-v">
        <Menu
          align="start"
          trigger={
            <button type="button" className="dt-val dt-pick" aria-label={label}>
              {value}
            </button>
          }
        >
          {children}
        </Menu>
        {after}
      </span>
    </div>
  );
}
