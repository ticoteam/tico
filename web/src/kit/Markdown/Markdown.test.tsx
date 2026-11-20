import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { headings, Markdown } from './Markdown';

afterEach(cleanup);
const draw = (text: string, compact = false) => render(<Markdown text={text} compact={compact} />).container;

describe('Markdown', () => {
  it('draws headings with ids, and h1 as the page h2', () => {
    const el = draw('# Plan\n\n## Plan\n\n### Steps');
    expect([...el.querySelectorAll('h2')].map((h) => h.id)).toEqual(['plan', 'plan-2']);
    expect(el.querySelector('h3')?.textContent).toBe('Steps');
    expect(headings('# Plan\n```\n# not one\n```\n## Plan')).toEqual([
      { id: 'plan', text: 'Plan' },
      { id: 'plan-2', text: 'Plan' },
    ]);
  });

  it('joins a paragraph in a doc and keeps line breaks in a chat', () => {
    expect(draw('one\ntwo').querySelector('p')?.textContent).toBe('one two');
    expect(draw('one\ntwo', true).querySelectorAll('br')).toHaveLength(1);
  });

  it('draws lists with a level inside, ordered and not', () => {
    const el = draw('1. first\n2. second\n   - inner\n   - more\n3. third');
    const ol = el.querySelector('ol');
    expect(ol?.children).toHaveLength(3);
    expect(ol?.querySelectorAll('ul > li')).toHaveLength(2);
  });

  it('draws quotes, fenced code and a rule', () => {
    const el = draw('> said\n> this\n\n```\n**not bold**\n```\n\n---');
    expect(el.querySelector('blockquote p')?.textContent).toBe('said this');
    expect(el.querySelector('pre code')?.textContent).toBe('**not bold**');
    expect(el.querySelector('pre b')).toBeNull();
    expect(el.querySelector('hr')).toBeTruthy();
  });

  it('draws bold, italic, inline code and links', () => {
    const el = draw('A **big** and *small* and _also_ with `x` see [docs](/library/docs) or [site](https://example.com)');
    expect(el.querySelector('b')?.textContent).toBe('big');
    expect([...el.querySelectorAll('em')].map((e) => e.textContent)).toEqual(['small', 'also']);
    expect(el.querySelector('code')?.textContent).toBe('x');
    const [inApp, out] = [...el.querySelectorAll('a')];
    expect(inApp?.getAttribute('href')).toBe('/library/docs');
    expect(out?.getAttribute('target')).toBe('_blank');
  });

  it('leaves snake_case words alone', () => {
    expect(draw('a snake_case_name here').querySelector('em')).toBeNull();
  });

  it('draws a table', () => {
    const el = draw('| Name | Size |\n| --- | ---: |\n| a | 1 |\n| b | 2 |');
    expect([...el.querySelectorAll('th')].map((t) => t.textContent)).toEqual(['Name', 'Size']);
    expect(el.querySelectorAll('tbody tr')).toHaveLength(2);
  });

  it('draws images only from /api/; one from outside is a link', () => {
    const el = draw('![chart](https://example.com/a.png) ![file](/api/v2/files/f1) ![bad](javascript:alert(1))');
    expect([...el.querySelectorAll('img')].map((i) => i.getAttribute('src'))).toEqual(['/api/v2/files/f1']);
    expect(el.querySelector('a[href="https://example.com/a.png"]')?.textContent).toBe('chart');
    expect(el.textContent).toContain('bad');
  });

  it('shows a javascript: link as plain words', () => {
    const el = draw('[click me](javascript:alert(1))');
    expect(el.querySelector('a')).toBeNull();
    expect(el.textContent).toBe('click me');
  });

  it('shows raw HTML as text', () => {
    const el = draw('<script>alert(1)</script><b>hi</b>');
    expect(el.querySelector('script')).toBeNull();
    expect(el.querySelector('b')).toBeNull();
    expect(el.textContent).toBe('<script>alert(1)</script><b>hi</b>');
    const inline = draw('Use <img src=x onerror=alert(1)> and <a href="javascript:x">a</a> as **text**');
    expect(inline.querySelector('img, a')).toBeNull();
    expect(inline.querySelector('b')?.textContent).toBe('text');
    expect(draw('<div>\n\n**inside**\n\n</div>').querySelector('div')).toBeNull();
  });

  it('draws a table the way agents write one: inline marks, pipes in code, a short row', () => {
    const el = draw(
      ['| Check | Result | Notes |', '|:--|:-:|--:|', '| `a \\| b` | **pass** | see [run](/runs/7) |', '| lint | ~~fail~~ fixed |'].join('\n'),
    );
    expect([...el.querySelectorAll('th')].map((t) => t.textContent)).toEqual(['Check', 'Result', 'Notes']);
    const rows = [...el.querySelectorAll('tbody tr')];
    expect(rows).toHaveLength(2);
    expect(rows[0]?.querySelector('code')?.textContent).toBe('a | b');
    expect(rows[0]?.querySelector('b')?.textContent).toBe('pass');
    expect(rows[0]?.querySelector('a')?.getAttribute('href')).toBe('/runs/7');
    expect(rows[1]?.querySelector('s')?.textContent).toBe('fail');
    expect(rows[1]?.querySelectorAll('td')).toHaveLength(3);
  });

  it('draws lists nested three deep, with code inside an item', () => {
    const el = draw(
      [
        '1. **Backend**',
        '   - Add the column',
        '     - with an index',
        '       1. then backfill',
        '   - Ship it:',
        '',
        '     ```sh',
        '     make migrate',
        '     ```',
        '2. **Web**',
        '- [ ] not ordered',
      ].join('\n'),
    );
    const ol = el.querySelector('ol');
    expect(ol?.children).toHaveLength(2);
    expect(ol?.querySelector('li > ul > li > ul > li > ol > li')?.textContent).toBe('then backfill');
    expect(ol?.querySelector('li pre code')?.textContent).toBe('make migrate');
    expect(el.querySelector('ol + ul li')?.textContent).toBe('[ ] not ordered');
  });

  it('keeps an ordered list\'s first number', () => {
    expect(draw('3. three\n4. four').querySelector('ol')?.getAttribute('start')).toBe('3');
  });

  it('draws fenced code with a language and tildes, untouched', () => {
    const el = draw('~~~ts\nconst a = 1 < 2 && **b**;\n\n// <b>x</b>\n~~~');
    expect(el.querySelector('pre code')?.textContent).toBe('const a = 1 < 2 && **b**;\n\n// <b>x</b>');
    expect(el.querySelector('b')).toBeNull();
  });

  it('draws strikethrough, escapes and bare web addresses', () => {
    const el = draw('~~old~~ new \\*not em\\* at https://example.com/a_b');
    expect(el.querySelector('s')?.textContent).toBe('old');
    expect(el.querySelector('em')).toBeNull();
    expect(el.textContent).toContain('*not em*');
    expect(el.querySelector('a')?.getAttribute('href')).toBe('https://example.com/a_b');
  });

  it('opens a protocol-relative link away from the app', () => {
    expect(draw('[x](//example.com)').querySelector('a')?.getAttribute('target')).toBe('_blank');
  });

  it('ends the last paragraph with the tail, or gives it its own line after a list', () => {
    const tail = <i data-tail="" />;
    const p = render(<Markdown text={'one\n\ntwo\n\n'} compact tail={tail} />).container;
    expect(p.querySelector('p:last-child [data-tail]')?.parentElement?.textContent).toBe('two');
    cleanup();
    const l = render(<Markdown text="- one" compact tail={tail} />).container;
    expect(l.querySelector('ul + p > [data-tail]')).toBeTruthy();
  });
});
