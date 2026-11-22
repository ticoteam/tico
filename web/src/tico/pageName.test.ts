import { afterEach, describe, expect, it } from 'vitest';
import { pageName } from './pageName';

afterEach(() => {
  document.body.innerHTML = '';
});

describe('the open page’s name', () => {
  it('is the h1 of the page’s sheet', () => {
    document.body.innerHTML = '<aside class="pane tico"><h1>Tico</h1></aside><main class="pane page"><h1>Tasks</h1></main>';
    expect(pageName()).toBe('Tasks');
  });

  it('is a call’s title on a call’s own page, so the trail and the tab name it', () => {
    document.body.innerHTML = '<aside class="pane tico"><h1>Tico</h1></aside><main class="pane cw is-full"><h1>Pricing review</h1></main>';
    expect(pageName()).toBe('Pricing review');
  });
});
