import { describe, expect, it } from 'vitest';
import { contextMenuItems } from './contextMenu';

const flags = { canUndo: false, canRedo: false, canCut: true, canCopy: false, canPaste: true, canDelete: false, canSelectAll: true, canEditRichly: false };
const base = { misspelledWord: '', dictionarySuggestions: [] as string[], isEditable: false, editFlags: flags, selectionText: '', linkURL: '' };
const labels = (items: ReturnType<typeof contextMenuItems>) => items.map((i) => (i === 'separator' ? '-' : i.label));

describe('the right-click menu', () => {
  it('shows nothing on plain page text', () => {
    expect(contextMenuItems(base)).toEqual([]);
  });

  it('gives a field spelling suggestions first, then the edit actions', () => {
    const items = contextMenuItems({ ...base, isEditable: true, misspelledWord: 'shedule', dictionarySuggestions: ['schedule', 'shed'] });
    expect(labels(items)).toEqual(['schedule', 'shed', 'Add to dictionary', '-', 'Cut', 'Copy', 'Paste', 'Select all']);
    expect(items[0]).toMatchObject({ action: 'replace', arg: 'schedule' });
    expect(items.find((i) => i !== 'separator' && i.label === 'Copy')).toMatchObject({ enabled: false });
  });

  it('copies a selection, and copies only web and mail links', () => {
    expect(labels(contextMenuItems({ ...base, selectionText: 'Fernhill' }))).toEqual(['Copy']);
    expect(labels(contextMenuItems({ ...base, selectionText: 'x', linkURL: 'https://fernhill.example/a' }))).toEqual(['Copy', '-', 'Copy link']);
    expect(contextMenuItems({ ...base, linkURL: 'javascript:alert(1)' })).toEqual([]);
    expect(contextMenuItems({ ...base, linkURL: 'file:///etc/passwd' })).toEqual([]);
  });
});
