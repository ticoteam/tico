import { describe, expect, it } from 'vitest';
import { ROWS, rowFor } from './map';

/* Every address the old router knows has a row. Read from the old app's source as text: the router (ui/app/router.ts),
   the routers it asks first (redesign.ts; toolsRoute and the others in pages-shell.ts) and the files that name the
   addresses they use. A new address added to the old app without a row here fails this test. */

// Read with node's fs: Vite will not import a file outside web/ (its fs.allow), and web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const FILES = ['router', 'redesign', 'pages-shell', 'core', 'dashboard', 'threads', 'calendar-row', 'file-review-route', 'pages', 'style-guide'];
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const OLD: Record<string, string> = Object.fromEntries(FILES.map((name) => [name, fs.readFileSync(new URL(`../../../ui/app/${name}.ts`, here), 'utf8')]));
const source = (name: string): string => OLD[name] ?? '';
/** The files whose every '#/…' string is an address the old app opens (the others only declare constants). */
const ROUTERS = ['router', 'redesign', 'pages-shell'];

/** An address as a sample: a prefix (`'#/bot/'`) gets an id, a bare query mark goes. */
const sample = (s: string): string => (s.endsWith('/') && s.length > 2 ? s + 'x' : s.replace(/\?$/, ''));

function addresses(): string[] {
  const found = new Set<string>();
  // Every route constant (`const TASKS = '#/tasks'`, `GR_ROUTE = '#/growth', BRAND_ROUTE = …`).
  for (const src of Object.values(OLD)) for (const [, value] of src.matchAll(/\b[A-Z][A-Z0-9_]*\s*=\s*'(#\/[^']*)'/g)) found.add(sample(value!));
  for (const name of ROUTERS) {
    const src = source(name);
    for (const [, value] of src.matchAll(/'(#\/[^']*)'/g)) found.add(sample(value!));
    // a template with an id in it: `#/bot/${id}/settings` is #/bot/x/settings
    // (and a comment's `#/settings/<tab>` is #/settings/x)
    for (const [, value] of src.matchAll(/`(#\/[^`]*)`/g)) found.add(sample(value!.replace(/\$\{[^}]*\}|<[^>]*>/g, 'x')));
  }
  return [...found].sort();
}

describe('old addresses', () => {
  it('reads the old router', () => {
    expect(source('router')).toContain('function route(');
    expect(addresses().length).toBeGreaterThan(60);
  });

  it('has a row for every address the old router names', () => {
    expect(addresses().filter((a) => !rowFor(a))).toEqual([]);
  });

  it('has a row for every page a pattern in the old router names', () => {
    // regular expressions in route(): /^#\/computers\/[^/?]+\/screen$/ names computers
    const pages = new Set([...source('router').matchAll(/\/\^#\\\/([a-z-]+)/g)].map(([, p]) => p!));
    expect(pages.size).toBeGreaterThan(2);
    expect([...pages].filter((p) => !ROWS.some((r) => r.path.source.includes(p)))).toEqual([]);
  });
});
