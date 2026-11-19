import { newId } from './ids';

const TAB_KEY = 'tico-next:tab';
let memoryTab: string | null = null;

/** This tab's id: a uuid kept in sessionStorage, so a reload keeps it and another tab has its own. */
export function tabId(): string {
  try {
    const kept = sessionStorage.getItem(TAB_KEY);
    if (kept) return kept;
    const id = newId();
    sessionStorage.setItem(TAB_KEY, id);
    return id;
  } catch {
    memoryTab ??= newId();
    return memoryTab;
  }
}
