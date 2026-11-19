export { Frame, foldSheet } from './Frame';
export type { FrameProps } from './Frame';
export { Sheet, SheetHead, useSheet } from './Sheet';
export { PageFault } from './PageFault';
export { tabTitle } from './title';
export type { SheetProps, SheetHeadProps, SheetControls } from './Sheet';
export { Menu } from './Menu';
export type { MenuProps, MenuTeam, MenuMate, MenuCompany, MenuCounts, MenuRowId } from './Menu';
export { useArrangement, arrangementFor, isTeammate } from './arrangement';
export { layout, modeFor, press, C, SPEC, ARR } from './layout';
export type { Arrangement, ArrangementSpec, Saved, SheetId, SheetLayout, SheetState, Layout } from './layout';
export { resetSheetSizes } from './memory';
export { useCompanies, type Company } from './companies';
export {
  desktop,
  desktopApp,
  isCallWindow,
  markDesktop,
  openCallWindow,
  reportToDesktop,
  useCallWindow,
  useDesktopInset,
  watchCallWindow,
  callWindowControls,
  type DesktopApp,
  type DesktopSelf,
  type UpdateState,
  type CallWindowNow,
} from '@/desktop';
