// What the routes and the shell use before a page's code loads: where a person lands, the gate, whether the first run is
// owed and its kept lines, and the cards that stand in place of the app (sign-in, a refusal, a boot failure), which
// must draw before any page's code arrives. The first run itself comes through exports.ts.
export { landing, needsSetup, type StartScreenKind } from './queries';
export { useStartGate, useSetupOwed, BOOT_WAIT_MS, type StartGate } from './gate';
export { useSetupTranscript, wentLive } from './transcript';
export { StartScreen, type StartScreenProps } from './Cards';
