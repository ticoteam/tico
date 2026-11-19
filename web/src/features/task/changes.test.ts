import { describe, expect, it } from 'vitest';
import { diffWords, frameDoc, markChanges, parsePage, type Op } from './changes';

/** The words an edit keeps, removes and adds, read back from its ops. */
function apply(a: string[], b: string[], ops: Op[]) {
  let i = 0;
  let j = 0;
  const older: string[] = [];
  const newer: string[] = [];
  for (const op of ops) {
    if (op !== 2) older.push(a[i++]!);
    if (op !== 1) newer.push(b[j++]!);
  }
  return { older, newer };
}

describe('a page against an older version', () => {
  it('finds the shortest edit between two runs of words', () => {
    const a = 'the quick brown fox jumps over the dog'.split(' ');
    const b = 'the slow brown fox leaps over the lazy dog'.split(' ');
    const ops = diffWords(a, b);
    expect(apply(a, b, ops)).toEqual({ older: a, newer: b });
    // quick → slow, jumps → leaps, + lazy: five words changed, no more.
    expect(ops.filter((o) => o !== 0)).toHaveLength(5);
    expect(diffWords([], ['a'])).toEqual([2]);
    expect(diffWords(['a'], [])).toEqual([1]);
    expect(diffWords(['a', 'b'], ['a', 'b'])).toEqual([0, 0]);
  });

  it('marks what was added and removed in the newer page, and counts each change once', () => {
    const older = parsePage('<h1>Sign in</h1><p>Sign in with your email.</p><p>Then choose a team.</p>');
    const newer = parsePage('<h1>Sign in</h1><p>Sign in with your email or phone.</p><p>Then pick a team.</p><img src="data:image/png;base64,AA==">');
    const n = markChanges(older, newer);
    // "email." → "email or phone.", "choose" → "pick", and the new picture.
    expect(n).toBe(3);
    expect([...newer.querySelectorAll('ins')].map((x) => x.textContent)).toEqual(['email or phone.', 'pick']);
    expect([...newer.querySelectorAll('del')].map((x) => x.textContent)).toEqual(['email.', 'choose']);
    expect(newer.querySelectorAll('[data-tico-chg]')).toHaveLength(3);
    expect(newer.querySelector('h1')?.hasAttribute('data-tico-changed')).toBe(false);
    expect(newer.querySelector('img')?.hasAttribute('data-tico-new')).toBe(true);
    expect(newer.body.textContent).toContain('Sign in with your');
  });

  it('frames a page with nothing from outside and links that open a new tab', () => {
    const doc = parsePage('<meta http-equiv="refresh" content="0;url=https://example.com"><a href="https://example.com">Out</a><iframe src="https://example.com"></iframe>');
    const html = frameDoc(doc, true);
    expect(html.startsWith('<!DOCTYPE html>')).toBe(true);
    expect(html).toContain("default-src 'none'");
    expect(html).not.toContain('http-equiv="refresh"');
    expect(html).not.toContain('<iframe');
    expect(html).toContain('target="_blank"');
    expect(html).toMatch(/<script nonce="[a-z0-9]+">/);
  });

  it('reads a Markdown version as its text', () => {
    const doc = parsePage('# Title\n\n<b>not markup</b>', true);
    expect(doc.querySelector('pre')?.textContent).toBe('# Title\n\n<b>not markup</b>');
    expect(doc.querySelector('b')).toBeNull();
  });
});
