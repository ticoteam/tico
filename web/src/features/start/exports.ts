// Start: where a person lands, sign-in and its refusals, the boot failures, and the first run over the app.
export { StartPage } from './StartPage';
export { landing, needsQuery, todayQuery, type StartScreenKind } from './queries';
// For the root: `useStartGate()` says whether a card stands in place of the app (`StartScreen`) and whether the first
// run is open over it (`FirstRun`, mounted beside the frame).
export { useStartGate, BOOT_WAIT_MS, type StartGate } from './gate';
export { StartScreen, PartFault, type StartScreenProps } from './Cards';
export { FirstRun } from './FirstRun';
// For Tools › GitHub: the App's state and fixes for the owner; Connect opens the dialog over the app (`?setup=github`).
export { GithubConnection } from './GithubConnection';
