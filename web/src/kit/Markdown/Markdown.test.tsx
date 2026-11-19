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

  it('draws lists with one level inside, ordered and not', () => {
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
  });
});
