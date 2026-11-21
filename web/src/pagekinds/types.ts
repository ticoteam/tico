import type { ReactNode } from 'react';

/** One tab of the page's tab row. Tabs are links: the address decides which one is on. */
export interface PageTab {
  label: string;
  /** A resolved address ("/company/finance/pnl"). */
  to: string;
  /** A muted count after the words. */
  count?: number;
  /** On only at this exact address (default). False keeps it on for addresses under it too. */
  exact?: boolean;
  /** `<kind>:<id>` for Tico's flights. */
  target?: string;
  /** Before the words: the tab's state (approved, waits on you, not yet). */
  mark?: ReactNode;
  /** Merged into the link's search. The tab is on only while the address's search holds each of these too. */
  search?: Record<string, string>;
  /** A class beside `doc-tab`: `tab-chat`, a tab only a phone shows (parts.css). */
  className?: string;
}

/** The head every kind but the centered card has: one height, the title on the left, controls and "⋯" on the right. */
export interface HeadProps {
  /** The page's name, the h1. Only the name: where it sits is the frame's trail, which ends with it. */
  title: string;
  /** The page's address ("/company/finance"). The title carries `data-target="page:<path>"` so Tico's flights can land. */
  path: string;
  /** Before the title: a back button, a face (36 at most), the Dot. */
  lead?: ReactNode;
  /** Right after the title: a date range and its steps, a version. */
  after?: ReactNode;
  /**
   * Right of the title, in this order: scope pick, owner, a view switch, the one filled button. On a phone only the
   * filled button and anything marked `data-phone` stay; the rest belong under "⋯".
   */
  actions?: ReactNode;
  /** The page's search or filter field: first of the controls on a wide screen, on its own line under the head (and
      tabs) on a phone, where the head has no room for it. */
  search?: ReactNode;
  /** "⋯", always last: a menu node that brings its own trigger, or a function the plain "⋯" button calls. */
  more?: ReactNode | (() => void);
  /** The title edits in place: click it, type, Enter saves, Escape puts it back. */
  onRename?: (next: string) => void;
  /**
   * A small where line by the title: a task's stage, owner, age and Late. On a phone the where line sits
   * under the title.
   */
  sub?: ReactNode;
  /** 'large': the title at figure size, for a page that stands as a column beside its chat (an agent's own page). */
  size?: 'large';
  /** A row over the head, drawn only when given; the page's own CSS says where it shows (a task's phone row). */
  top?: ReactNode;
  /** ✕, last in the head, back to where you were (plan/layers.html: an artifact opened from a thread closes). Left out,
      it shows while the address says the page was opened from a thread (`via=thread`, lib besideThread). On a phone the
      back arrow does it instead. */
  close?: boolean;
}

/** Which body a page shows. The head (and tabs) stay in every state except no access. */
export type PageState = 'ready' | 'loading' | 'empty' | 'fault' | 'no-access';

export interface StateProps {
  /** Leave out for the slots. Loading draws grey bars in the kind's own shape, after 150 ms. */
  state?: PageState;
  /** Empty: its one line ("Nothing in Review") and its one action. */
  empty?: { title: string; action?: ReactNode };
  /** Fault: "<title> did not load" with Try again, which calls this. */
  onRetry?: () => void;
  /** No access: who can share it ("Maya Okafor can share it with you.") and the ways out. */
  noAccess?: { text?: string; actions?: ReactNode };
}

/** What every sheet kind takes: the head, its states, and an optional class for the page's own CSS. */
export interface PageBaseProps extends HeadProps, StateProps {
  /** Extra classes on the page sheet, for the page's own CSS (the mock's `rec`, `page-thread`...). */
  className?: string;
}

/** One figure in a figures strip. */
export interface Figure {
  /** "Cash" */
  label: string;
  /** "$181,000": format it before it comes here. */
  value: ReactNode;
  /** "was $176,000" */
  sub?: ReactNode;
  /** A `<Sparkline>` from `@/kit/Chart`. */
  spark?: ReactNode;
  target?: string;
}
