// What the routes use before a page's code loads: the address checks and the first reads. No components here: the
// first load imports this file, and a page comes through exports.ts with its route.
export { loadAutomation, loadListening, loadRoutines, loadRules, validateAutomationSearch, validateListeningSearch, validateRoutinesSearch, validateRulesSearch } from './load';
