import { api } from '@/api/client';
import type { NeedsYou, NeedsYouItem } from '@/api/schema';

/** The most a page holds (backend/views.py NEEDS_PAGE_MAX), and how many pages are read before the rest is left. */
const PAGE = 200;
const PAGES = 50;

/**
 * Every item of the Needs you list (GET /api/v2/needs-you), page after page: a page names the next with `next`. A
 * cursor from a list that moved meanwhile starts again from the top (`restart`), so what was read before it is dropped.
 */
export async function needsYouItems(signal?: AbortSignal): Promise<NeedsYouItem[]> {
  let items: NeedsYouItem[] = [];
  let cursor: string | null | undefined = null;
  for (let read = 0; read < PAGES; read++) {
    const r: NeedsYou = await api.get<NeedsYou>(`/api/v2/needs-you?limit=${PAGE}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal });
    if (!('items' in r)) break;
    items = r.restart ? r.items : [...items, ...r.items];
    cursor = r.next;
    if (!cursor) break;
  }
  return items;
}
