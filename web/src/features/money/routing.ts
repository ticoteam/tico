// What the routes use before a page's code loads: the address checks and the first reads. No components here: the
// first load imports this file, and a page comes through exports.ts with its route.
export { validateFinanceSearch, validateGrowthSearch } from './search';
export type { FinanceSearch, GrowthSearch } from './search';
export { loadFinance, loadGrowth } from './load';
