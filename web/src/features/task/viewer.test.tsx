import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { FileFrame } from '@/features/library/exports';
import type { TaskData } from './queries';
import { tabLabel } from './model';
import { TaskPage } from './TaskPage';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* A task's tabs are its pages, its Markdown files, its other files and the emails it asks about, each drawn by type in
   one file viewer. Made-up rows only; every answer is stubbed. */

type Reply = { status?: number; body: unknown; type?: string };

/** A zip holding one deflated file, as a .docx holds word/document.xml. */
async function zipOf(name: string, text: string): Promise<Uint8Array> {
  const raw = new TextEncoder().encode(text);
  const data = new Uint8Array(await new Response(new Response(raw).body?.pipeThrough(new CompressionStream('deflate-raw'))).arrayBuffer());
  const n = new TextEncoder().encode(name);
  const local = new DataView(new ArrayBuffer(30));
  local.setUint32(0, 0x04034b50, true);
  local.setUint16(8, 8, true);
  local.setUint32(18, data.length, true);
  local.setUint32(22, raw.length, true);
  local.setUint16(26, n.length, true);
  const central = new DataView(new ArrayBuffer(46));
  central.setUint32(0, 0x02014b50, true);
  central.setUint16(10, 8, true);
  central.setUint32(20, data.length, true);
  central.setUint32(24, raw.length, true);
  central.setUint16(28, n.length, true);
  const at = 30 + n.length + data.length;
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, 1, true);
  end.setUint16(10, 1, true);
  end.setUint32(12, 46 + n.length, true);
  end.setUint32(16, at, true);
  const out = new Uint8Array(30 + n.length + data.length + 46 + n.length + 22);
  let at2 = 0;
  for (const part of [new Uint8Array(local.buffer), n, data, new Uint8Array(central.buffer), n, new Uint8Array(end.buffer)]) {
    out.set(part, at2);
    at2 += part.length;
  }
  return out;
}
const calls: string[] = [];
let reply: (url: string) => Reply | null = () => null;

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  URL.createObjectURL = vi.fn(() => 'blob:held');
});
beforeEach(() => {
  calls.length = 0;
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${String(url)}`);
    const r = reply(String(url)) ?? base(String(url));
    const type = r.type ?? 'application/json';
    return new Response(type.includes('json') ? JSON.stringify(r.body) : (r.body as string | Uint8Array<ArrayBuffer>), { status: r.status ?? 200, headers: { 'Content-Type': type } });
  });
});
afterEach(() => vi.unstubAllGlobals());

const v = (n: number, mime: string, id: string, size = 2048) => ({ n, by: 'bot:a1', created_at: '2026-11-01T10:00:00Z', mime, size, url: `/api/v2/files/${id}?v=${n}`, answers: [], ask: null });
const file = (id: string, name: string, mime: string, current = 1) => ({
  id,
  name,
  title: name,
  mime,
  archived: false,
  current_version: current,
  links: [],
  writers: [],
  versions: Array.from({ length: current }, (_, i) => v(current - i, mime, id)),
});

const task = (more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    title: 'Answer the letter',
    status: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Legal' },
    stage_id: 's1',
    stage: { id: 's1', name: 'Draft' },
    owner_id: 'bot:a1',
    labels: [],
    created_at: '2026-11-01T10:00:00Z',
    documents: [
      { file_id: 'md1', name: 'Deadlines', kind: 'markdown', version: 2, versions: [] },
      { file_id: 'pg1', name: 'Reply', kind: 'html', page_id: 'pg1', version: 1, versions: [] },
      { file_id: 'pg1', name: 'Reply page', kind: 'html', page_id: 'pg1', version: 1, versions: [] },
    ],
    to_leave: [],
    links: [],
    ...more,
  }) as unknown as TaskData;

let shown = task();
const files = [file('md1', 'deadlines.md', 'text/markdown', 2), file('pg1', 'reply.html', 'text/html'), file('pdf1', 'letter.pdf', 'application/pdf', 2), file('png1', 'shot.png', 'image/png')];

function base(url: string): Reply {
  if (url.includes('/api/v2/tasks/%2312') || url.endsWith('/api/v2/tasks/t1')) return { body: { task: shown, runs: [], children: [], can_comment: true, mover: true } };
  if (url.endsWith('/api/v2/tasks/t1/files?all=1')) return { body: { files } };
  if (url.endsWith('/api/v2/pages/pg1')) return { body: { page: { file_id: 'pg1', src: 'https://pages.example.test/pg1', archived: false }, can_manage: true } };
  if (url.includes('/api/v2/pages/')) return { status: 404, body: { error: { code: 'not_found', detail: 'Page not found' } } };
  if (url.endsWith('/api/v2/files/md1/info')) return { body: { file: { id: 'md1', title: 'Deadlines', name: 'deadlines.md', mime: 'text/markdown', version: 2 } } };
  if (url.endsWith('/api/v2/files/md1/versions')) return { body: { versions: [{ version: 2, current: true, mime: 'text/markdown', name: 'deadlines.md', size: 40 }] } };
  if (url.endsWith('/api/v2/files/md1/versions/2')) return { body: '# Due dates\n\nAnswer by Friday.', type: 'text/markdown' };
  if (url.includes('/needs-you')) return { body: { items: [] } };
  if (url.includes('/api/v2/pipelines')) return { body: { pipelines: [] } };
  return { body: {} };
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('a task’s tabs: pages and plain files', () => {
  beforeEach(() => {
    shown = task();
  });

  it('lists each page once, its Markdown files and its other files but pictures, then Details', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="details" />, '/tasks/p1/12/details');
    // A file's tab reads its name without the extension; its address keeps the whole name.
    const pdf = await screen.findByRole('link', { name: 'letter' });
    expect(pdf.getAttribute('href')).toBe('/tasks/p1/12/letter-pdf');
    const tabs = screen.getAllByRole('link').map((a) => a.textContent);
    expect(tabs.filter((x) => x === 'Reply')).toHaveLength(1);
    expect(tabs).not.toContain('Reply page');
    expect(tabs).not.toContain('shot');
  });

  it('names a tab without its extension but keeps a version number whole', () => {
    expect(tabLabel('TIDY Response - Nov 18 2026.pdf')).toBe('TIDY Response - Nov 18 2026');
    expect(tabLabel('deadlines.md')).toBe('deadlines');
    expect(tabLabel('Plan v1.2')).toBe('Plan v1.2');
    expect(tabLabel('.env')).toBe('.env');
  });

  it('draws a Markdown file that is no page as formatted text, without asking for a page', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="deadlines" />, '/tasks/p1/12/deadlines');
    expect(await screen.findByRole('heading', { name: 'Due dates' })).toBeTruthy();
    expect(calls.some((c) => c.includes('/api/v2/pages/md1'))).toBe(false);
  });

  it('draws a PDF file in the browser’s own viewer from its address', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="letter-pdf" />, '/tasks/p1/12/letter-pdf');
    const frame = await screen.findByTitle('letter.pdf');
    expect(frame.getAttribute('src')).toBe('/api/v2/files/pdf1?v=2');
  });

  it('draws a page as today, from its page read', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="reply" />, '/tasks/p1/12/reply');
    await waitFor(() => expect(screen.getByTitle('Reply').getAttribute('src')).toBe('https://pages.example.test/pg1'));
  });

  it('draws a page with no address (no page hosting) from its own text at its version, in the page sandbox', async () => {
    reply = (url) =>
      url.endsWith('/api/v2/pages/pg1')
        ? { body: { page: { file_id: 'pg1', src: null, version: 3, archived: false }, can_manage: true } }
        : url.endsWith('/api/v2/files/pg1?v=3')
          ? { body: '<h1>Reviewed</h1>', type: 'text/html' }
          : null;
    show(<TaskPage task="12" pipeline="p1" tab="reply" />, '/tasks/p1/12/reply');
    const frame = await screen.findByTitle('Reply');
    expect(frame.getAttribute('srcdoc')).toContain('<h1>Reviewed</h1>');
    expect(frame.getAttribute('src')).toBeNull();
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts allow-popups allow-popups-to-escape-sandbox');
    reply = () => null;
  });

  it('draws an HTML document the server says is no page from its file read, sandboxed', async () => {
    shown = task({ documents: [{ file_id: 'h1', name: 'Review', kind: 'html', version: 1, versions: [] }] as unknown as TaskData['documents'] });
    reply = (url) =>
      url.endsWith('/api/v2/files/h1/info')
        ? { body: { file: { id: 'h1', title: 'Review', name: 'review.html', mime: 'text/html', version: 1 } } }
        : url.endsWith('/api/v2/files/h1/versions')
          ? { body: { versions: [{ version: 1, current: true, mime: 'text/html', name: 'review.html', size: 30 }] } }
          : url.endsWith('/api/v2/files/h1/versions/1')
            ? { body: '<h1>Looks right</h1>', type: 'text/html' }
            : null;
    show(<TaskPage task="12" pipeline="p1" tab="review" />, '/tasks/p1/12/review');
    const frame = await screen.findByTitle('Review');
    await waitFor(() => expect(frame.getAttribute('srcdoc')).toBe('<h1>Looks right</h1>'));
    expect(frame.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(calls).toContain('GET /api/v2/pages/h1');
    reply = () => null;
  });
});

describe('the file viewer, by type', () => {
  const at = (props: Omit<Parameters<typeof FileFrame>[0], 'title' | 'url'>) => show(<FileFrame {...props} title="Thing" url="/api/v2/files/f1?v=1" />, '/');

  it('shows a picture, a video and a sound with the browser’s own controls', async () => {
    at({ mime: 'image/png' });
    expect((await screen.findByAltText('Thing')).getAttribute('src')).toBe('/api/v2/files/f1?v=1');
  });

  it('plays a video with its controls', async () => {
    at({ mime: 'video/mp4' });
    expect((await screen.findByLabelText('Thing')).hasAttribute('controls')).toBe(true);
  });

  it('plays a sound with its controls', async () => {
    at({ mime: 'audio/mpeg' });
    expect((await screen.findByLabelText('Thing')).tagName).toBe('AUDIO');
  });

  it('reads plain text and a CSV table', async () => {
    reply = (url) => (url.endsWith('/versions/1') ? { body: 'a,b\n1,2', type: 'text/csv' } : null);
    at({ id: 'f1', version: 1, mime: 'text/csv' });
    expect(await screen.findByRole('columnheader', { name: 'a' })).toBeTruthy();
    reply = () => null;
  });

  it('knows a file by its name when the type says nothing', async () => {
    at({ mime: 'application/octet-stream', name: 'scan.pdf' });
    expect((await screen.findByTitle('Thing')).tagName).toBe('IFRAME');
  });

  it('offers a Word file by name and size, with Download and Open, and no made-up preview', async () => {
    at({ mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', name: 'contract.docx', size: 24 * 1024 });
    expect(await screen.findByRole('heading', { name: 'contract.docx' })).toBeTruthy();
    expect(screen.getByText('24 KB')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download' }).getAttribute('download')).toBe('contract.docx');
    const open = screen.getByRole('link', { name: 'Open' });
    expect(open.getAttribute('target')).toBe('_blank');
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('offers anything else for download only', async () => {
    at({ mime: 'application/zip', name: 'logs.zip', size: 900 });
    expect(await screen.findByRole('link', { name: 'Download' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Open' })).toBeNull();
  });

  it('offers a PDF by name with Download where the browser has no PDF viewer, never an empty frame', async () => {
    vi.stubGlobal('navigator', { ...navigator, pdfViewerEnabled: false });
    at({ mime: 'application/pdf', name: 'letter.pdf', size: 4096 });
    expect(await screen.findByRole('heading', { name: 'letter.pdf' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/files/f1?v=1');
    expect(document.querySelector('iframe')).toBeNull();
  });
});

describe('an email the task asks about', () => {
  const send = {
    send: { id: 's1', subject: 'Your quote', mailbox: 'sales@example.test', recipients: ['Pat Lee <pat@example.test>'], attachments: [{ part: 2, name: 'quote.docx', size: 2048, type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }, { part: 3, name: 'terms.pdf', size: 4096, type: 'application/octet-stream' }] },
    body: 'Hello Pat, the quote is attached.',
    bodies: [],
    preview_complete: true,
  };
  beforeEach(() => {
    shown = task({
      documents: [],
      to_leave: [{ requirement_id: 'r1', kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: ['email:s1'], answer: null }],
    } as unknown as Partial<TaskData>);
    reply = (url) => (url.endsWith('/api/v2/email/sends/s1') ? { body: send } : url.includes('/attachments/3') ? { body: '%PDF-1.4', type: 'application/octet-stream' } : null);
  });
  afterEach(() => {
    reply = () => null;
  });

  it('opens on the email that waits, with its words and files', async () => {
    const router = show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    expect(await screen.findByRole('heading', { name: 'Your quote' })).toBeTruthy();
    expect(screen.getByText('Hello Pat, the quote is attached.')).toBeTruthy();
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/email'));
  });

  it('opens on the email that waits, as it will go: subject, From, To, its files as cards @approvals', async () => {
    show(<TaskPage task="12" pipeline="p1" />, '/tasks/p1/12');
    const tabs = await screen.findByRole('navigation', { name: 'Answer the letter pages' });
    // the email is the first tab, marked as waiting on the person
    await waitFor(() => expect(tabs.querySelector('.doc-tab')?.textContent).toBe('Email'));
    expect(tabs.querySelector('.doc-tab .dot')).toBeTruthy();
    expect(await screen.findByText('sales@example.test')).toBeTruthy();
    expect(screen.getByText('Pat Lee <pat@example.test>')).toBeTruthy();
    expect(screen.getByRole('link', { name: /quote\.docx/ }).getAttribute('href')).toBe('/tasks/p1/12/email?part=2');
  });

  it('draws a Word attachment as its words, with Download from its frozen address @approvals', async () => {
    const docx = await zipOf(
      'word/document.xml',
      '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Quote</w:t></w:r></w:p>' +
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Total:</w:t></w:r><w:r><w:t xml:space="preserve"> 40 boxes</w:t></w:r></w:p>' +
        '</w:body></w:document>',
    );
    const before = reply;
    reply = (url) => (url.includes('/attachments/2') ? { body: docx, type: 'application/octet-stream' } : before(url));
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email?part=2');
    const sheet = await screen.findByRole('article', { name: 'quote.docx' });
    expect(within(sheet).getByRole('heading', { name: 'Quote' })).toBeTruthy();
    expect(within(sheet).getByText('Total:').tagName).toBe('B');
    expect(sheet.textContent).toContain('Total: 40 boxes');
    expect(screen.getByRole('link', { name: 'Download' }).getAttribute('href')).toBe('/api/v2/email/sends/s1/attachments/2');
  });

  it('shows a PDF attachment inline, read and held as a PDF', async () => {
    show(<TaskPage task="12" pipeline="p1" tab="email" />, '/tasks/p1/12/email?part=3');
    const frame = await screen.findByTitle('terms.pdf');
    expect(frame.getAttribute('src')).toBe('blob:held');
    expect(calls).toContain('GET /api/v2/email/sends/s1/attachments/3');
  });
});
