import { useRef, useState } from 'react';
import { act } from './parts';

/** The company's name, written in place (the mock's General › Name): press it, type, and Enter or leaving the field
    saves; Escape puts it back. The owner's only (the server lets only the owner save setup). */
export function CompanyName({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const done = useRef(false);
  const finish = (value: string | null) => {
    if (done.current) return;
    done.current = true;
    setOpen(false);
    const next = value?.trim();
    if (next && next !== name) void act('settings.renameCompany', { name: next });
  };
  if (!open)
    return (
      <button
        type="button"
        className="fact is-edit"
        data-target="setting:companyName"
        aria-label={`Company name, ${name}`}
        onClick={() => {
          done.current = false;
          setOpen(true);
        }}
      >
        <span>Name</span>
        <span className="fact-v">
          <span className="fact-t">{name}</span>
        </span>
      </button>
    );
  return (
    <div className="fact is-edit is-open" data-target="setting:companyName">
      <span>Name</span>
      <span className="fact-v">
        <label className="field is-wide">
          <input
            type="text"
            aria-label="Company name"
            defaultValue={name}
            maxLength={100}
            autoComplete="organization"
            autoFocus
            onBlur={(e) => finish(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') finish(e.currentTarget.value);
              else if (e.key === 'Escape') finish(null);
            }}
          />
        </label>
      </span>
    </div>
  );
}
