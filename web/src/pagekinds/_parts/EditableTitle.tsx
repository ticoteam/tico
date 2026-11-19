import { useRef, useState } from 'react';

export interface EditableTitleProps {
  title: string;
  /** Names the field ("Name"). */
  label: string;
  /**
   * Called once: the new words (trimmed) on Enter or on leaving the field, null on Escape. The caller saves only
   * when they changed.
   */
  onDone: (next: string | null) => void;
}

/** A title renamed in place: the field on the title's own line, in its own type (`.ih-title`). */
export function EditableTitle({ title, label, onDone }: EditableTitleProps) {
  const [value, setValue] = useState(title);
  // Enter ends it, and the field's leaving then fires blur too: only the first counts.
  const done = useRef(false);
  const end = (next: string | null) => {
    if (done.current) return;
    done.current = true;
    onDone(next);
  };
  return (
    <input
      className="ih-title"
      aria-label={label}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => end(value.trim())}
      onKeyDown={(e) => {
        if (e.key === 'Enter') end(value.trim());
        else if (e.key === 'Escape') end(null);
      }}
      autoFocus
    />
  );
}
