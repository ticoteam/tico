import { QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { PDFDocument, PDFName, degrees } from 'pdf-lib';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { fakePdf } from '@/test/fakePdf';
import { openPdf } from './pdf';
import { PdfView } from './PdfView';
import { burnMarks, pagePoint, type Mark } from './signing';

vi.mock('./pdf', () => ({ openPdf: vi.fn() }));

/* The pad draws on a canvas jsdom does not have: a stroke is said by the test. */
const pads: Array<{ stroke: () => void }> = [];
vi.mock('signature_pad', () => ({
  default: class {
    private empty = true;
    private ended: (() => void) | undefined;
    constructor() {
      pads.push(this);
    }
    addEventListener(_: string, fn: () => void) {
      this.ended = fn;
    }
    stroke() {
      this.empty = false;
      this.ended?.();
    }
    isEmpty() {
      return this.empty;
    }
    clear() {
      this.empty = true;
    }
    off() {}
  },
}));

// A 1x1 PNG, as the pad's canvas would give it.
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgAAIAAAUAAWJFJs0AAAAASUVORK5CYII=';
const pngBytes = () => Uint8Array.from(atob(PNG.split(',')[1] ?? ''), (c) => c.charCodeAt(0));

async function letter(pages = 1, turn = 0) {
  const pdf = await PDFDocument.create();
  for (let i = 0; i < pages; i++) pdf.addPage([612, 792]).setRotation(degrees(turn));
  return pdf.save();
}

/** Each XObject (a placed picture) and font a page's resources name. */
async function resources(bytes: Uint8Array, page = 0) {
  const res = (await PDFDocument.load(bytes)).getPage(page).node.Resources();
  return { images: res?.lookup(PDFName.of('XObject'))?.toString() ?? '', fonts: res?.lookup(PDFName.of('Font'))?.toString() ?? '' };
}

describe('writing the marks into the PDF', () => {
  it('places a point of the page as shown in the page’s own space, whichever way the page is turned', () => {
    const box = { x: 0, y: 0, width: 612, height: 792 };
    // The page's top left as shown, at each turn.
    expect(pagePoint(box, 0, 0, 0)).toEqual([0, 792]);
    expect(pagePoint(box, 90, 0, 0)).toEqual([0, 0]);
    expect(pagePoint(box, 180, 0, 0)).toEqual([612, 0]);
    expect(pagePoint(box, 270, 0, 0)).toEqual([612, 792]);
    // A crop box's offset carries through.
    expect(pagePoint({ ...box, x: 10, y: 20 }, 0, 0.5, 0.5)).toEqual([316, 416]);
  });

  it('writes a signature as a picture and text and a date in the PDF’s Helvetica, on their own pages only', async () => {
    const marks: Mark[] = [
      { id: 'a', kind: 'signature', page: 2, x: 0.1, y: 0.8, w: 0.3, h: 0.05, text: '' },
      { id: 'b', kind: 'date', page: 2, x: 0.5, y: 0.85, w: 0.25, h: 0.026, text: 'November 20, 2026' },
      { id: 'c', kind: 'text', page: 2, x: 0.5, y: 0.9, w: 0.25, h: 0.026, text: 'Ana Example ✍' },
    ];
    const out = await burnMarks(await letter(2, 90), marks, pngBytes());
    expect((await PDFDocument.load(out)).getPageCount()).toBe(2);
    const first = await resources(out, 0);
    const second = await resources(out, 1);
    expect(first.images + first.fonts).toBe('');
    expect(second.images).toMatch(/\/Image/);
    expect(second.fonts).toMatch(/\/Helvetica/);
  });
});

describe('signing a task’s PDF', () => {
  let calls: Array<{ method: string; url: string; body: unknown }>;
  let saved: string | null;

  beforeAll(async () => {
    const original = await letter(1);
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      calls.push({ method, url, body: init?.body });
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url === '/api/v2/me/signature' && method === 'PUT') {
        saved = (JSON.parse(String(init?.body)) as { image: string }).image;
        return json({ signature: { image: saved, updated_at: '2026-11-20T13:40:00Z' } });
      }
      if (url === '/api/v2/me/signature') return json({ signature: saved && { image: saved, updated_at: '2026-11-20T13:40:00Z' } });
      if (url === '/api/v2/files/f1/versions/1') return new Response(original as BodyInit, { status: 200 });
      if (url === '/api/v2/files/f1/signings')
        return json({ file_id: 'f1', version: 2, signing: { id: 's1', file_id: 'f1', task_id: 't1', signer: 'human:ana', signer_name: 'Ana Example', before_version: 1, after_version: 2, created_at: '2026-11-20T13:40:00Z' } });
      return json({});
    });
  });

  beforeEach(() => {
    calls = [];
    saved = null;
    pads.length = 0;
    queryClient.clear();
    vi.mocked(openPdf).mockResolvedValue(fakePdf(2).open);
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
    HTMLCanvasElement.prototype.toDataURL = () => PNG;
    HTMLCanvasElement.prototype.getContext = (() => null) as never;
  });

  afterEach(() => vi.restoreAllMocks());

  it('draws the signature once, places it and the date, and saves the signed PDF as the next version', async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <PdfView src="/api/v2/files/f1/versions/1" title="Declaration" url="/api/v2/files/f1/versions/1" name="declaration.pdf" sign={{ file: 'f1', version: 1, name: 'declaration.pdf' }} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Sign' }));
    const mode = await screen.findByRole('dialog', { name: 'Sign Declaration' });
    expect((within(mode).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);

    // No signature yet: Signature asks for one, and Save keeps it on the person's profile.
    await waitFor(() => expect(calls.some((c) => c.url === '/api/v2/me/signature')).toBe(true));
    fireEvent.click(within(mode).getByRole('button', { name: 'Signature' }));
    const draw = await screen.findByRole('dialog', { name: 'Draw your signature' });
    await waitFor(() => expect(pads).toHaveLength(1));
    act(() => pads[0]?.stroke());
    fireEvent.click(within(draw).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Draw your signature' })).toBeNull());
    expect(saved).toBe(PNG);
    expect(within(mode).getByRole('button', { name: 'Signature' }).getAttribute('aria-pressed')).toBe('true');

    // A tap on the page places the armed mark there.
    const page = () => mode.querySelector<HTMLElement>('.sg-layer[data-page="1"]');
    await waitFor(() => expect(page()).not.toBeNull());
    fireEvent.pointerDown(page() as HTMLElement, { clientX: 200, clientY: 300 });
    expect(await within(mode).findByRole('group', { name: 'Signature' })).toBeTruthy();
    fireEvent.click(within(mode).getByRole('button', { name: 'Date' }));
    fireEvent.pointerDown(page() as HTMLElement, { clientX: 500, clientY: 320 });
    const date = within(mode).getByRole('textbox', { name: 'Date' }) as HTMLInputElement;
    expect(date.value).toMatch(/^[A-Z][a-z]+ \d{1,2}, \d{4}$/);

    fireEvent.click(within(mode).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Sign Declaration' })).toBeNull());
    const sent = calls.find((c) => c.method === 'POST' && c.url === '/api/v2/files/f1/signings');
    const form = sent?.body as FormData;
    expect(form.get('version')).toBe('1');
    expect(JSON.parse(String(form.get('marks')))).toEqual([
      { kind: 'signature', page: 1 },
      { kind: 'date', page: 1 },
    ]);
    const pdf = new Uint8Array(await (form.get('file') as Blob).arrayBuffer());
    expect(new TextDecoder().decode(pdf.slice(0, 5))).toBe('%PDF-');
    const written = await resources(pdf);
    expect(written.images).toMatch(/\/Image/);
    expect(written.fonts).toMatch(/\/Helvetica/);
  });

  it('leaves the file as it was on Cancel', async () => {
    saved = PNG;
    render(
      <QueryClientProvider client={queryClient}>
        <PdfView src="/api/v2/files/f1/versions/1" title="Declaration" url="/api/v2/files/f1/versions/1" sign={{ file: 'f1', version: 1, name: 'declaration.pdf' }} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Sign' }));
    const mode = await screen.findByRole('dialog', { name: 'Sign Declaration' });
    fireEvent.click(await within(mode).findByRole('button', { name: 'Redraw signature' }));
    fireEvent.keyDown(await screen.findByRole('dialog', { name: 'Draw your signature' }), { key: 'Escape' });
    fireEvent.click(within(mode).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Sign Declaration' })).toBeNull());
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('offers Sign only where the file can be signed', async () => {
    render(<PdfView src="blob:x/1" title="Attachment" url="/api/v2/email/sends/s1/attachments/2" />);
    expect(await screen.findByRole('button', { name: 'Expand' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Sign' })).toBeNull();
  });
});
