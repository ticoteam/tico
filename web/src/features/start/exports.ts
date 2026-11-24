// Start: sign-in and its refusals, the boot failures, and the first run in Tico's sheet.
export { landing, type StartScreenKind } from './queries';
// For the root: `useStartGate()` says whether a card stands in place of the app (`StartScreen`).
export { useStartGate, BOOT_WAIT_MS, type StartGate } from './gate';
export { StartScreen, type StartScreenProps } from './Cards';
// For Tico's sheet while the first run is owed (`useSetupOwed`).
export { SetupChat } from './SetupChat';
// For Tools › GitHub: the App's state and fixes for the owner; Connect opens its dialog.
export { GithubConnection } from './GithubConnection';
