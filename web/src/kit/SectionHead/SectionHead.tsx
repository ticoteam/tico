import { useId, useState, type ReactNode } from 'react';
import { Icon } from '@/kit/Icon';
import './SectionHead.css';

export type FoldButtonProps = {
  /** Usually a string; a list that crosses pipelines names a section "pipeline › stage". */
  name: ReactNode;
  count?: number;
  open: boolean;
  onToggle: () => void;
  /** The id of what folds. */
  controls?: string;
};

/** The name that folds its section: name, count, then the chevron (pages/1-tasks/list.css `.sec-fold`). */
export function FoldButton({ name, count, open, onToggle, controls }: FoldButtonProps) {
  return (
    <button type="button" className="sec-fold" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <span>{name}</span>
      {count !== undefined && <span className="stage-n">{count}</span>}
      <Icon name="chevdown" />
    </button>
  );
}

export type SectionHeadProps = Omit<FoldButtonProps, 'open' | 'onToggle'> & {
  /** 1: a section, 16/650 over 14px rows. 2: a group inside a section, one step down. */
  level?: 1 | 2;
  /** Leave out `onToggle` for a name that does not fold. */
  open?: boolean;
  onToggle?: () => void;
  /** Quiet icon buttons at the right of a section's head. */
  actions?: ReactNode;
};

/** A section's head. Level 1 is the sticky `.stage-head`; level 2 is the name alone, in the page's own group head. */
export function SectionHead({ level = 1, name, count, open = true, onToggle, controls, actions }: SectionHeadProps) {
  const label = onToggle ? (
    <FoldButton name={name} count={count} open={open} onToggle={onToggle} controls={controls} />
  ) : (
    <>
      {name}
      {count !== undefined && <span className="stage-n">{count}</span>}
    </>
  );
  if (level === 2) return <h3 className="sec-name is-2">{label}</h3>;
  return (
    <header className="stage-head">
      <h2 className="sec-name">{label}</h2>
      <span className="grow" />
      {actions}
    </header>
  );
}

export type SectionProps = Omit<SectionHeadProps, 'open' | 'onToggle' | 'controls'> & {
  defaultOpen?: boolean;
  /** Fold without the head's toggle, e.g. a remembered choice. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
};

/** A section that folds under its name (`.stage`, `.is-folded`). Folded, its rows are not drawn. */
export function Section({ defaultOpen = true, open: openProp, onOpenChange, children, ...head }: SectionProps) {
  const [own, setOwn] = useState(defaultOpen);
  const open = openProp ?? own;
  const id = useId();
  const toggle = () => {
    setOwn(!open);
    onOpenChange?.(!open);
  };
  const body = (
    <div id={id} hidden={!open}>
      {open && children}
    </div>
  );
  if (head.level === 2) {
    return (
      <section>
        <SectionHead {...head} open={open} onToggle={toggle} controls={id} />
        {body}
      </section>
    );
  }
  return (
    <section className={open ? 'stage' : 'stage is-folded'}>
      <SectionHead {...head} open={open} onToggle={toggle} controls={id} />
      {body}
    </section>
  );
}
