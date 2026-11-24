import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { parseDiff } from './diff';
import { Markdown } from './Markdown';

const chat = (text: string) => render(<Markdown text={text} compact />).container;

describe('parseDiff', () => {
  it('reads headers, hunks, added, removed and context lines, and keeps a removed "---" line inside a hunk', () => {
    const lines = parseDiff(
      ['diff --git a/x.sql b/x.sql', '--- a/x.sql', '+++ b/x.sql', '@@ -1,3 +1,3 @@', ' select 1;', '--- old note', '+-- new note', '', '\\ No newline at end of file'].join('\n'),
    );
    expect(lines.map((l) => l.kind)).toEqual(['file', 'file', 'file', 'hunk', 'same', 'del', 'add', 'same', 'note']);
    expect(lines[5]).toEqual({ kind: 'del', sign: '−', text: '-- old note' });
    expect(lines[6]).toEqual({ kind: 'add', sign: '+', text: '-- new note' });
    expect(lines[4]!.text).toBe('select 1;');
  });

  it('treats a bare "@@" hunk and prose as written, and a second file after a counted hunk as headers again', () => {
    const lines = parseDiff(['@@', '-a', '+b', 'some words', '@@ -1 +1 @@', '-c', '+d', '--- a/y', '+++ b/y'].join('\n'));
    expect(lines.map((l) => l.kind)).toEqual(['hunk', 'del', 'add', 'same', 'hunk', 'del', 'add', 'file', 'file']);
    expect(lines[3]).toEqual({ kind: 'same', sign: '', text: 'some words' });
  });
});

describe('$$ math in a chat', () => {
  it('reads a $$ block on its own lines, a one-line $$ block, and a ```math fence, mid-paragraph too', () => {
    const el = chat('Energy:\n$$\nE = mc^2\n$$\nand $$a^2$$ inline stays text\n$$x+1$$\n\n```math\n\\frac{1}{2}\n```');
    const shown = [...el.querySelectorAll('.md-code code')].map((c) => c.textContent);
    // the formulas show as their source until KaTeX arrives
    expect(shown).toEqual(['E = mc^2', 'x+1', '\\frac{1}{2}']);
    expect(el.textContent).toContain('Energy:');
    expect(el.textContent).toContain('and $$a^2$$ inline stays text');
  });

  it('leaves currency and an unclosed block as text', () => {
    for (const text of ['It costs $5 and $10.', 'Pay $$5 to $$10 today', '$$5 to $$10', '$$\nE = mc^2\nno end', 'a\n$$ $$']) {
      const el = chat(text);
      expect(el.querySelector('.md-code, .md-math'), text).toBeNull();
      // a chat's line breaks are <br>s, so compare without the white space
      expect(el.textContent?.replace(/\s+/g, '')).toBe(text.replace(/\s+/g, ''));
    }
  });

  it('keeps a reply being written plain: a mermaid fence shows as code', () => {
    const el = render(<Markdown text={'```mermaid\ngraph TD; A-->B\n```'} compact streaming />).container;
    expect(el.querySelector('.md-code code')?.textContent).toBe('graph TD; A-->B');
    expect(el.querySelector('img')).toBeNull();
  });
});
