import { describe, expect, it } from 'vitest';
import type { Actor } from '@/data';
import { mentionMatches, mentionText } from './mentions';

interface Case {
  query: string;
  first?: string[];
  only?: string[];
  want: string[];
}

// The case list the server's picker (backend/mention_search.py, GET /api/v2/mentions) passes too, so the two orders
// never drift. Read with node's fs: web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const list = JSON.parse(fs.readFileSync(new URL('../../../backend/tests/fixtures/mention_ranking.json', here), 'utf8')) as {
  me: string;
  actors: Actor[];
  handles: Record<string, string>;
  cases: Case[];
};

describe('the @ list', () => {
  it('writes each pick as the server reads it', () => {
    for (const [id, handle] of Object.entries(list.handles)) expect(mentionText(list.actors.find((a) => a.id === id)!)).toBe(handle);
  });

  it.each(list.cases)('ranks "$query" as the server does', ({ query, first, only, want }) => {
    expect(mentionMatches(list.actors, query, list.me, first, only).map((a) => a.id)).toEqual(want);
  });
});
