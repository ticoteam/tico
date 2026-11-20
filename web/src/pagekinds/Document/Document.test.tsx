import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { expectHeadAndTabs, showPage, stateTests, TABS } from '@/pagekinds/_parts/test-page';
import { Document } from './Document';

describe('Document', () => {
  it('reads the body as an article, with the outline beside it and the decision under it', async () => {
    const main = await showPage(
      <Document
        title="Spec"
        path="/tasks/t1/spec"
        tabs={TABS}
        body={<p>Reading text</p>}
        side={<a href="#goals">Goals</a>}
        foot={<button type="button">Approve</button>}
      />,
    );
    expectHeadAndTabs(main, 'Spec', '/tasks/t1/spec');
    expect(within(within(main).getByRole('article')).getByText('Reading text')).toBeTruthy();
    expect(within(within(main).getByRole('complementary', { name: 'Outline' })).getByRole('link', { name: 'Goals' })).toBeTruthy();
    expect(within(main).getByRole('button', { name: 'Approve' })).toBeTruthy();
  });

  it('keeps a framed page as it is', async () => {
    const main = await showPage(<Document title="Report" path="/r" body={<iframe title="Report page" />} framed />);
    expect(within(main).queryByRole('article')).toBeNull();
    expect(within(main).getByTitle('Report page')).toBeTruthy();
  });

  it('draws a row over the head only when given one', async () => {
    const plain = await showPage(<Document title="Report" path="/r" body={null} />);
    const head = plain.querySelector('header.pk-head');
    expect(head?.previousElementSibling).toBeNull();
    const html = plain.innerHTML;
    plain.ownerDocument.body.innerHTML = '';
    const topped = await showPage(<Document title="Report" path="/r" body={null} top={<div className="row-over">Over</div>} />);
    expect(topped.querySelector('header.pk-head')?.previousElementSibling?.textContent).toBe('Over');
    topped.querySelector('.row-over')?.remove();
    expect(topped.innerHTML).toBe(html);
  });

  describe('on a phone', () => {
    afterEach(() => {
      delete document.documentElement.dataset.layout;
    });

    it('puts facts under the body, in its scroll, and keeps an outline beside it for the jump menu', async () => {
      document.documentElement.dataset.layout = 'cards';
      const main = await showPage(<Document title="File" path="/f" body={<p>Preview</p>} side={<p>Size 2 KB</p>} sideFacts framed />);
      const facts = within(main).getByRole('complementary', { name: 'Facts' });
      expect(facts.parentElement?.classList.contains('pk-body')).toBe(true);
      expect(facts.previousElementSibling?.textContent).toBe('Preview');
      main.ownerDocument.body.innerHTML = '';
      const doc = await showPage(<Document title="Spec" path="/s" body={<p>Text</p>} side={<a href="#goals">Goals</a>} />);
      expect(within(doc).getByRole('complementary', { name: 'Outline' }).parentElement?.classList.contains('pk-body')).toBe(false);
    });
  });

  describe('opened from a thread', () => {
    afterEach(() => {
      delete document.documentElement.dataset.layout;
    });
    const CASE = '/tasks/p1/12/email?chat=agent%3Agm&via=thread';
    const open = async (entries: string[]) => {
      const root = createRootRoute({ component: () => <Document title="Case" path="/tasks/p1/12" crumb={[{ label: 'Support', to: '/tasks/p1' }]} body={null} /> });
      const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: entries, initialIndex: entries.length - 1 }) });
      render(<RouterProvider router={router} />);
      return { router, main: await screen.findByRole('main') };
    };

    it('draws ✕ last in the head, which goes back to the thread', async () => {
      const { router, main } = await open(['/team/gm', CASE]);
      const head = main.querySelector<HTMLElement>('header.pk-head')!;
      const close = within(head).getByRole('button', { name: 'Close' });
      expect(head.lastElementChild).toBe(close);
      fireEvent.click(close);
      await waitForPath(router, '/team/gm');
    });

    it('with nothing in the app before it, closes up to its parent and keeps the thread beside', async () => {
      const { router, main } = await open([CASE]);
      fireEvent.click(within(main).getByRole('button', { name: 'Close' }));
      await waitForPath(router, '/tasks/p1');
      expect(router.state.location.search).toEqual({ chat: 'agent:gm' });
    });

    it("on a phone, the head's back arrow goes back to the thread too", async () => {
      document.documentElement.dataset.layout = 'cards';
      const { router, main } = await open(['/team/gm', CASE]);
      fireEvent.click(within(main).getByRole('button', { name: 'Back' }));
      await waitForPath(router, '/team/gm');
    });

    it('opened any other way, has no ✕ and its back arrow goes up the trail', async () => {
      const main = await showPage(<Document title="Case" path="/tasks/p1/12" crumb={[{ label: 'Support', to: '/tasks/p1' }]} body={null} />, '/tasks/p1/12');
      expect(within(main).queryByRole('button', { name: 'Close' })).toBeNull();
      expect(within(main).getByRole('link', { name: 'Back to Support' }).getAttribute('href')).toBe('/tasks/p1');
    });
  });

  stateTests('Spec', (s) => <Document title="Spec" path="/tasks/t1/spec" body={null} {...s} />);
});

async function waitForPath(router: { state: { location: { pathname: string } } }, path: string) {
  await waitFor(() => expect(router.state.location.pathname).toBe(path));
}
