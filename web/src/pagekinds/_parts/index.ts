// The shared pieces every page kind is made of. Page builders use these to fill slots; the kinds use them for layout.
export { PageHead, type PageHeadProps } from './PageHead';
export { PageTabs, type PageTabsProps } from './PageTabs';
export { FilterRow, type FilterRowProps, type FilterChip } from './FilterRow';
export { PageBody, type PageBodyProps } from './PageBody';
export { Section, type SectionProps } from './Section';
export { SidePanel, PanelSlot, PanelHost, usePagePanelOpen, type SidePanelProps } from './SidePanel';
export { FiguresStrip, type FiguresStripProps } from './FiguresStrip';
export { Page, StateBody, SkRows, type PageProps, type PageKind, type StateBodyProps } from './Page';
