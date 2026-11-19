// The readiness tracker's counts (transition.md 4.1). Runs with plain node: `node web/scripts/parity.ts`.
// Fails when a row is invalid, or says "checked" while no spec file under web/ carries its tag (@<id>).
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative } from 'node:path';

type Row = { id: string; area: string; status: string; tier: number; track: string; state: string; specs: string[] };

const WEB = join(import.meta.dirname, '..');
const TRACKS = ['base', 'tasks', 'task', 'chat', 'calls', 'team', 'company', 'strategy', 'money', 'routines', 'library', 'settings', 'start'];
const STATES = ['not started', 'built', 'checked'];
const SKIP = new Set(['node_modules', 'dist', 'build', 'test-results', 'playwright-report', '.vite']);

const rows: Row[] = JSON.parse(readFileSync(join(WEB, 'parity/rows.json'), 'utf8'));

function specFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    if (e.isDirectory()) return SKIP.has(e.name) ? [] : specFiles(join(dir, e.name));
    return /\.(spec|test)\.tsx?$/.test(e.name) ? [join(dir, e.name)] : [];
  });
}

// Every @tag in every spec, so @s1 never counts for @s10.
const tagged = new Map<string, string[]>();
for (const file of specFiles(WEB)) {
  for (const m of readFileSync(file, 'utf8').matchAll(/@([a-z][\w:-]*)/g)) {
    const id = m[1]!;
    tagged.set(id, [...(tagged.get(id) ?? []), relative(WEB, file)]);
  }
}

const problems: string[] = [];
const seen = new Set<string>();
for (const r of rows) {
  if (seen.has(r.id)) problems.push(`${r.id}: listed twice`);
  seen.add(r.id);
  if (r.tier !== 1 && r.tier !== 2) problems.push(`${r.id}: tier ${r.tier} is not 1 or 2`);
  if (!TRACKS.includes(r.track)) problems.push(`${r.id}: unknown track ${r.track}`);
  if (!STATES.includes(r.state)) problems.push(`${r.id}: unknown state ${r.state}`);
  if (r.state === 'checked' && !tagged.has(r.id)) problems.push(`${r.id}: checked, but no spec carries @${r.id}`);
}

function table(title: string, key: (r: Row) => string, order?: string[]) {
  type Counts = { all: number; 'not started': number; built: number; checked: number };
  const counts = new Map<string, Counts>();
  for (const r of rows) {
    const c = counts.get(key(r)) ?? { all: 0, 'not started': 0, built: 0, checked: 0 };
    c.all++;
    c[r.state as keyof Counts]++;
    counts.set(key(r), c);
  }
  const keys = order ? order.filter((k) => counts.has(k)) : [...counts.keys()];
  const width = Math.max(title.length, ...keys.map((k) => k.length));
  console.log(`\n${title.padEnd(width)}   rows  not started  built  checked`);
  for (const k of keys) {
    const c = counts.get(k)!;
    console.log(`${k.padEnd(width)}  ${String(c.all).padStart(5)}  ${String(c['not started']).padStart(11)}  ${String(c.built).padStart(5)}  ${String(c.checked).padStart(7)}`);
  }
}

console.log(`${rows.length} rows; ${tagged.size} ids tagged in specs`);
table('tier', (r) => `tier ${r.tier}`);
table('track', (r) => r.track, TRACKS);
table('tier 1 by track', (r) => (r.tier === 1 ? r.track : 'tier 2'), [...TRACKS, 'tier 2']);
table('area', (r) => r.area);
table('state', (r) => r.state, STATES);

if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
