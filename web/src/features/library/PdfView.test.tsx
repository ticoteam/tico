import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakePdf } from '@/test/fakePdf';
import { openPdf } from './pdf';
import { PdfView } from './PdfView';

vi.mock('./pdf', () => ({ openPdf: vi.fn() }));

beforeEach(() => {
  // jsdom lays nothing out: the scroll is as wide as a laptop's frame.
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(openPdf).mockReset();
});

describe('PdfView', () => {
  it('draws every page with PDF.js as one scroll, fit to the width, and frees the read when it closes', async () => {
    const pdf = fakePdf(3);
    vi.mocked(openPdf).mockResolvedValue(pdf.open);
    const { unmount } = render(<PdfView src="/api/v2/files/f1?v=2" title="Lease" url="/api/v2/files/f1?v=2" name="lease.pdf" />);
    const pages = await screen.findByRole('region', { name: 'Lease' });
    expect(openPdf).toHaveBeenCalledWith('/api/v2/files/f1?v=2');
    expect(pages.querySelectorAll('.fv-pdf-page')).toHaveLength(3);
    expect(screen.getByText('3 pages')).toBeTruthy();
    // 800 wide less the padding at each side; a letter page keeps its shape.
    const first = pages.querySelector<HTMLElement>('.fv-pdf-page');
    await waitFor(() => expect(first?.style.width).toBe('800px'));
    expect(first?.style.height).toBe('1035px');
    await waitFor(() => expect(pages.querySelectorAll('canvas')).toHaveLength(3));
    expect(new Set(pdf.drawn)).toEqual(new Set([1, 2, 3]));
    unmount();
    expect(pdf.destroy).toHaveBeenCalled();
  });

  it('zooms in a step at a time, never past its last step', async () => {
    vi.mocked(openPdf).mockResolvedValue(fakePdf(1).open);
    render(<PdfView src="blob:x/1" title="Lease" url="/api/v2/mail/s/2" />);
    const pages = await screen.findByRole('region', { name: 'Lease' });
    const page = () => pages.querySelector<HTMLElement>('.fv-pdf-page');
    await waitFor(() => expect(page()?.style.width).toBe('800px'));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(page()?.style.width).toBe('1000px');
    for (let i = 0; i < 5; i++) fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect((screen.getByRole('button', { name: 'Zoom in' }) as HTMLButtonElement).disabled).toBe(true);
    expect(page()?.style.width).toBe('2400px');
  });

  it('expands over the whole window and comes back on Escape or Close', async () => {
    vi.mocked(openPdf).mockResolvedValue(fakePdf(2).open);
    render(<PdfView src="blob:x/1" title="Lease" url="/api/v2/mail/s/2" name="lease.pdf" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lease' });
    expect(within(dialog).getByRole('region', { name: 'Lease, full window' }).querySelectorAll('.fv-pdf-page')).toHaveLength(2);
    expect(within(dialog).getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/mail/s/2');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('closes on Escape whichever control in its bar has focus, takes the key from the page around it, and gives focus back to Expand', async () => {
    vi.mocked(openPdf).mockResolvedValue(fakePdf(2).open);
    // the sheet around the PDF has its own Escape (shell/Frame.tsx); it must not also run
    const around = vi.fn();
    document.addEventListener('keydown', around);
    render(<PdfView src="blob:x/1" title="Lease" url="/api/v2/mail/s/2" name="lease.pdf" sign={{ file: 'f1', version: 2, name: 'lease.pdf' }} />);
    const expand = await screen.findByRole('button', { name: 'Expand' });
    expand.focus();
    fireEvent.click(expand);
    const dialog = await screen.findByRole('dialog', { name: 'Lease' });
    expect(within(dialog).getByRole('button', { name: 'Sign' })).toBeTruthy();
    const out = within(dialog).getByRole('button', { name: 'Zoom out' });
    out.focus();
    // a control that keeps its keys to itself: Escape still closes the view
    const keep = (e: KeyboardEvent) => e.stopPropagation();
    out.addEventListener('keydown', keep);
    fireEvent.keyDown(out, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(around).not.toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(expand));
    document.removeEventListener('keydown', around);
    out.removeEventListener('keydown', keep);
  });

  it('opens in a tab of its own when PDF.js cannot read it', async () => {
    vi.mocked(openPdf).mockRejectedValue(new Error('Invalid PDF structure'));
    render(<PdfView src="/api/v2/files/f1" title="Lease" url="/api/v2/files/f1" />);
    const open = await screen.findByRole('link', { name: 'Open PDF' });
    expect(open.getAttribute('href')).toBe('/api/v2/files/f1');
    expect(open.getAttribute('target')).toBe('_blank');
    expect(screen.queryByRole('region', { name: 'Lease' })).toBeNull();
  });
});
