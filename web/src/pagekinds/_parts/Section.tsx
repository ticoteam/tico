import { useId, useState, type ReactNode } from 'react';
import { SectionHead } from '@/kit/SectionHead';

export interface SectionProps {
  /** The section's name: 16/650 ink, the button that folds it. */
  name: string;
  /** Muted, after the name. */
  count?: number;
  /** Quiet icon buttons at the right of the section's head ("+"). */
  actions?: ReactNode;
  defaultOpen?: boolean;
  /** Controlled: a remembered fold. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** `<kind>:<id>` for Tico's flights. */
  target?: string;
  children: ReactNode;
}

/**
 * The one section: same edge, same height, the fold chevron after the count, on every kind (page-types: Section name).
 * Its name sits on `--page-x` and a row's mark hangs in the margin, so row titles line up under the name. Folded, its
 * rows are not drawn.
 */
export function Section({ name, count, actions, defaultOpen = true, open: openProp, onOpenChange, target, children }: SectionProps) {
  const [own, setOwn] = useState(defaultOpen);
  const open = openProp ?? own;
  const id = useId();
  const toggle = () => {
    setOwn(!open);
    onOpenChange?.(!open);
  };
  return (
    <section className={open ? 'stage pk-sec' : 'stage pk-sec is-folded'} data-target={target} aria-label={name}>
      <SectionHead name={name} count={count} actions={actions} open={open} onToggle={toggle} controls={id} />
      <div id={id} hidden={!open}>
        {open && children}
      </div>
    </section>
  );
}
