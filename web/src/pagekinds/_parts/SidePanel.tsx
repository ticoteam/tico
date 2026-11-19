import { useLayoutEffect, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Panel } from '@/kit/Panel';

export interface SidePanelProps {
  /** The thing open in the panel: its name in the head. */
  title: string;
  /** `<kind>:<id>` for Tico's flights; set on the name. */
  target?: string;
  /** Before the name: a face or a mark. */
  lead?: ReactNode;
  /** Quiet buttons in the head, before the close button. */
  actions?: ReactNode;
  /** Buttons for the whole panel, at its foot. */
  foot?: ReactNode;
  /** Close button and Escape. */
  onClose: () => void;
  /** Added to the sheet's classes, for a panel of one kind (a record, a form). */
  className?: string;
  children: ReactNode;
}

/** A row opened as its own sheet right of the page (R24): the kit's panel with the name as a flight target. */
export function SidePanel({ title, target, lead, actions, foot, onClose, className, children }: SidePanelProps) {
  return (
    <Panel
      label={title}
      onClose={onClose}
      foot={foot}
      className={className}
      head={
        <>
          {lead}
          <b data-target={target}>{title}</b>
          {actions && <span className="grow" />}
          {actions}
        </>
      }
    >
      {children}
    </Panel>
  );
}

/* Where the panel goes. The shell owns the sheet beside the page: it asks `usePagePanelOpen()` and, when true, gives
   the frame `panel={<PanelHost />}`. The page's panel is drawn into that host, so it keeps the page's context. With no
   host (a phone's cards, a test, the gallery) the panel stays inside the page: beside the body, or a raised card. */
const slot = { host: null as HTMLElement | null, open: 0 };
const subs = new Set<() => void>();
const subscribe = (f: () => void) => {
  subs.add(f);
  return () => subs.delete(f);
};
const emit = () => subs.forEach((f) => f());

/** For the shell: true while a page shows a panel. */
export function usePagePanelOpen(): boolean {
  return useSyncExternalStore(subscribe, () => slot.open > 0, () => false);
}

/** For the shell: the place the open panel is drawn into. */
export function PanelHost() {
  return (
    <div
      className="pk-panel-host"
      ref={(el) => {
        slot.host = el;
        emit();
        return () => {
          if (slot.host === el) slot.host = null;
          emit();
        };
      }}
    />
  );
}

/** For the kinds: draws the panel in the shell's place, or inside the page when there is none. */
export function PanelSlot({ children }: { children: ReactNode }) {
  const host = useSyncExternalStore(subscribe, () => slot.host, () => null);
  useLayoutEffect(() => {
    slot.open += 1;
    emit();
    return () => {
      slot.open -= 1;
      emit();
    };
  }, []);
  return host ? createPortal(children, host) : <div className="pk-panel">{children}</div>;
}
