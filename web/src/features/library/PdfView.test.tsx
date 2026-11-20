import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PdfView } from './PdfView';

const FRAG = '#toolbar=0&navpanes=0&view=FitH';

afterEach(() => {
  delete document.documentElement.dataset.layout;
});

describe('PdfView', () => {
  it('frames the PDF without the viewer’s toolbar or page list, fit to the width', () => {
    render(<PdfView src="/api/v2/files/f1?v=2#page=3" title="Lease" url="/api/v2/files/f1?v=2" name="lease.pdf" />);
    expect(screen.getByTitle('Lease').getAttribute('src')).toBe(`/api/v2/files/f1?v=2${FRAG}`);
  });

  it('expands over the whole window and comes back on Escape or Close', async () => {
    render(<PdfView src="blob:x/1" title="Lease" url="/api/v2/mail/s/2" name="lease.pdf" />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lease' });
    expect(screen.getByTitle('Lease, full window').getAttribute('src')).toBe(`blob:x/1${FRAG}`);
    expect(screen.getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/mail/s/2');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens in a tab of its own on a phone, where a frame shows only the first page', () => {
    document.documentElement.dataset.layout = 'cards';
    render(<PdfView src="/api/v2/files/f1" title="Lease" url="/api/v2/files/f1" />);
    const open = screen.getByRole('link', { name: 'Open PDF' });
    expect(open.getAttribute('href')).toBe('/api/v2/files/f1');
    expect(open.getAttribute('target')).toBe('_blank');
    expect(screen.queryByTitle('Lease')).toBeNull();
  });
});
