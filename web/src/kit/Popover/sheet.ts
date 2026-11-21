import { createContext, type CSSProperties, use, useSyncExternalStore } from 'react';
import { popLayer, popMotion } from './Popover';

const root = () => document.documentElement;
const read = () => root().dataset.layout === 'cards';
const no = () => false;
const unwatched = () => () => {};
function subscribe(changed: () => void) {
  const watch = new MutationObserver(changed);
  watch.observe(root(), { attributes: true, attributeFilter: ['data-layout'] });
  return () => watch.disconnect();
}

/** The shell's own cards-or-sheets, given to what it draws: read in render, it is the window's mode at once, where the
    html attribute (kept for CSS) is written only once the shell has drawn. */
export const CardsContext = createContext<boolean | null>(null);

/** True on the phone's cards (`html[data-layout="cards"]`, which the shell sets): a menu is then a sheet. */
export function useCards() {
  const shell = use(CardsContext);
  // the attribute only outside the frame (a call's own window), where nothing gives the mode
  const html = useSyncExternalStore(shell == null ? subscribe : unwatched, shell == null ? read : no, no);
  return shell ?? html;
}

/* Base UI writes the anchored position inline; on the cards a menu's positioner is pinned to the bottom edge instead. */
const PINNED: CSSProperties = { position: 'fixed', top: 'auto', right: 0, bottom: 0, left: 0, transform: 'none' };

export type Placement = {
  /** On the cards: a sheet from the bottom edge over a scrim. */
  sheet: boolean;
  /** The positioner's class. */
  layer: string;
  /** The positioner's style: the pin on the cards, nothing otherwise. */
  style: CSSProperties | undefined;
  /** The popup's added classes: `is-sheet` and its rise, or the anchored pop. */
  look: string;
};

/** Where a menu or a select's list opens: beside its button, or on a phone as a sheet rising from the bottom. */
export function usePlacement(): Placement {
  return useCards()
    ? { sheet: true, layer: `${popLayer} kit-sheet`, style: PINNED, look: 'is-sheet kit-rise' }
    : { sheet: false, layer: popLayer, style: undefined, look: popMotion };
}

/** The scrim behind a sheet; a tap on it is a tap outside, so it closes the sheet. */
export const sheetScrim = 'scrim kit-dim';
