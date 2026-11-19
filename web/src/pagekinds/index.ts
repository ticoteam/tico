// The nine page kinds (tico/page-types.html). Every page in the app is one of them; a page fills its slots.
export { List, type ListProps } from './List';
export { Board, BoardColumn, type BoardProps, type BoardColumnProps } from './Board';
export { ListAndItem, ItemHead, type ListAndItemProps, type ItemHeadProps } from './ListAndItem';
export { Record, RecordPanel, type RecordProps, type RecordPanelProps } from './Record';
export { Document, type DocumentProps } from './Document';
export { Dashboard, type DashboardProps } from './Dashboard';
export { Settings, type SettingsProps } from './Settings';
export { Calendar, type CalendarProps } from './Calendar';
export { CenteredCard, type CenteredCardProps } from './CenteredCard';

// What pages fill the slots with, and what the shell needs for the side panel.
export {
  FilterRow,
  Section,
  SidePanel,
  PanelSlot,
  FiguresStrip,
  PageMore,
  PageBody,
  PanelHost,
  usePagePanelOpen,
  type FilterRowProps,
  type FilterChip,
  type SectionProps,
  type SidePanelProps,
  type FiguresStripProps,
  type PageMoreProps,
} from './_parts';
export type { Crumb, PageTab, HeadProps, PageState, StateProps, PageBaseProps, Figure } from './types';
