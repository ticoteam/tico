/* ui/app/org-chart-shape.ts — The org chart's shape: who sits under whom, for Company's chart.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Moved from ui/app/sidebar.ts, whose other code Home runs, into the company page part (ui/parts.json): only that part
   calls these, so a cold open of Home does not download them (P05b). */
'use strict';

// The org chart's shape over a tree of nodes by parent key (`by`, changed in place): the top person (orgChartTop) at the
// root, their own group dissolved into them, and every top-level group under the one it answers to (orgGroupHead), so
// a department sits under the person who runs it. Org (redesign-org.js rdoTree) and Goals (goals-kpis.js) both draw it.
function orgChartShape<T extends {kind: string; id: string}>(by: Record<string, T[]>, keyOf: (n: T) => string, leadOf: (id: string) => string): Record<string, T[]> {
  const nodes = new Map<string, T>(), parentOf = new Map<string, string>();
  for (const [k, kids] of Object.entries(by)) for (const n of kids) { nodes.set(keyOf(n), n); parentOf.set(keyOf(n), k); }
  const take = (key: string) => { const p = parentOf.get(key)!; by[p] = (by[p] || []).filter(n => keyOf(n) !== key); parentOf.delete(key); };
  const put = (parent: string, n: T) => { by[parent] ||= []; by[parent].push(n); parentOf.set(keyOf(n), parent); };
  const person = orgChartTop(), topKey = person && nodes.has('p:' + person.id) ? 'p:' + person.id : '';
  const home = topKey ? parentOf.get(topKey) : '';
  if (topKey && home) {
    take(topKey); put('', nodes.get(topKey)!);
    if (home.startsWith('g:') && parentOf.get(home) === '') {
      for (const n of by[home] || []) put(topKey, n);
      delete by[home]; take(home); nodes.delete(home);
    }
  }
  // Under its head unless the head sits inside it (a loop); else it stays at the top, under the top person.
  const reaches = (from: string, to: string) => {
    const seen = new Set<string>(), walk = (k: string): boolean => (by[k] || []).some(n => { const key = keyOf(n); if (key === to) return true; if (seen.has(key) || seen.size > 4000) return false; seen.add(key); return walk(key); });
    return walk(from);
  };
  for (const g of (by[''] || []).filter(n => n.kind === 'group')) {
    const head = orgGroupHead(g.id, leadOf(g.id)), key = 'g:' + g.id;
    if (!head || head === topKey || !nodes.has(head) || reaches(key, head)) continue;
    take(key); put(head, g);
  }
  return by;
}
