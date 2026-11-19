// What the routes and the shell use before a page's code loads: where a person lands, the gate, and the cards that
// stand in place of the app (sign-in, a refusal, a boot failure), which must draw before any page's code arrives.
// The start page and the first run come through exports.ts.
export { landing, needsQuery, todayQuery, type StartScreenKind } from './queries';
export { useStartGate, BOOT_WAIT_MS, type StartGate } from './gate';
export { StartScreen, PartFault, type StartScreenProps } from './Cards';
