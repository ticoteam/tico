import { beforeAll, describe, expect, it } from 'vitest';

/* The page kit Tico adds to an agent's page (backend/page-kit/1.js, docs/pages.md "The page kit"), run in a page. */

// Read with node's fs, as mentions.test.ts reads the server's fixture: web's types leave node out.
const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync(file: URL, encoding: 'utf8'): string };
const here = import.meta.url; // not inline: Vite would turn `new URL('…', import.meta.url)` into an asset import
const KIT = fs.readFileSync(new URL('../../../backend/page-kit/1.js', here), 'utf8');

const shadow = (html: string) => {
  document.body.innerHTML = html;
  return document.body.firstElementChild!.shadowRoot!;
};

describe('The page kit', () => {
  beforeAll(() => {
    document.body.style.color = 'rgb(34, 34, 34)'; // a page's own dark text, no background: the reporter leaves it light
    new Function(KIT)();
  });

  it('draws on a light page in its light look', () => {
    expect(document.documentElement.getAttribute('data-tico-kit')).toBe('light');
  });

  it('reads figures as written: grouped, with k and M, and a typeset minus', () => {
    const bars = shadow('<tico-chart data="Jan 800, Feb 1.2k, Mar $1,500, Apr 2M"></tico-chart>');
    const widths = [...bars.querySelectorAll('i')].map((i) => i.style.width);
    expect(widths).toEqual(['0.04%', '0.06%', '0.075%', '100%']);
    expect([...bars.querySelectorAll('.c')].map((c) => c.textContent)).toEqual(['800', '1.2k', '$1,500', '2M']);
    const loss = shadow('<tico-chart><ul><li>Gain 50</li><li>Loss −50</li></ul></tico-chart>');
    expect([...loss.querySelectorAll('i')].map((i) => i.style.width)).toEqual(['100%', '0%']);
    expect([...loss.querySelectorAll('.n')].map((n) => n.textContent)).toEqual(['Gain', 'Loss']);
  });

  it('keeps a one-point line inside its chart', () => {
    const line = shadow('<tico-chart kind="line" data="W1 $3,100"></tico-chart>');
    const now = line.querySelector('text.now')!;
    expect(now.textContent).toBe('$3,100');
    expect(now.getAttribute('text-anchor')).toBe('start');
  });
});
