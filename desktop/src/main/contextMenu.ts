// The right-click menu. Electron shows none of its own, so without this a page has no spelling suggestions (spellcheck
// is on, platforms.md 4.3) and no Cut, Copy or Paste. A page that draws its own menu (preventDefault on contextmenu)
// gets nothing from here. Pure: contents.ts shows the items.
import type { ContextMenuParams } from 'electron';

export type ContextAction = 'cut' | 'copy' | 'paste' | 'selectAll' | 'replace' | 'learn' | 'copyLink';
export type ContextItem = { label: string; action: ContextAction; arg?: string; enabled: boolean } | 'separator';

type Params = Pick<ContextMenuParams, 'misspelledWord' | 'dictionarySuggestions' | 'isEditable' | 'editFlags' | 'selectionText' | 'linkURL'>;

export function contextMenuItems(p: Params): ContextItem[] {
  const items: ContextItem[] = [];
  if (p.isEditable && p.misspelledWord) {
    for (const word of p.dictionarySuggestions.slice(0, 5)) items.push({ label: word, action: 'replace', arg: word, enabled: true });
    items.push({ label: 'Add to dictionary', action: 'learn', arg: p.misspelledWord, enabled: true }, 'separator');
  }
  if (p.isEditable) {
    items.push(
      { label: 'Cut', action: 'cut', enabled: p.editFlags.canCut },
      { label: 'Copy', action: 'copy', enabled: p.editFlags.canCopy },
      { label: 'Paste', action: 'paste', enabled: p.editFlags.canPaste },
      { label: 'Select all', action: 'selectAll', enabled: p.editFlags.canSelectAll },
    );
  } else if (p.selectionText.trim()) {
    items.push({ label: 'Copy', action: 'copy', enabled: true });
  }
  if (/^(https?:|mailto:)/i.test(p.linkURL)) {
    if (items.length) items.push('separator');
    items.push({ label: 'Copy link', action: 'copyLink', arg: p.linkURL, enabled: true });
  }
  return items;
}
