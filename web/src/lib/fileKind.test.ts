import { describe, expect, it } from 'vitest';
import { fileKind } from './fileKind';

describe('fileKind', () => {
  it('reads a file by its name when its type says nothing more', () => {
    expect(fileKind('application/octet-stream', 'IMG_0042.JPG')).toBe('image');
    expect(fileKind('text/plain', 'notes.md')).toBe('markdown');
    expect(fileKind('text/plain', 'fix.patch')).toBe('diff');
    expect(fileKind('', 'export.tsv')).toBe('tsv');
  });

  it('trusts an exact type over the name, parameters and case aside', () => {
    expect(fileKind('Application/PDF; charset=binary', 'scan.bin')).toBe('pdf');
    expect(fileKind('text/html', 'page.txt')).toBe('html');
    expect(fileKind('message/rfc822', 'mail')).toBe('eml');
  });

  it('tells the Office formats a browser can read apart from the rest', () => {
    expect(fileKind('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'a.docx')).toBe('docx');
    expect(fileKind('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'a.xlsx')).toBe('xlsx');
    expect(fileKind('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'a.pptx')).toBe('office');
    expect(fileKind('application/octet-stream', 'old.doc')).toBe('office');
  });

  it("uses the library's own word, and calls anything unknown none", () => {
    expect(fileKind('', 'shot', 'image')).toBe('image');
    expect(fileKind('', 'brief', 'page')).toBe('html');
    expect(fileKind('application/octet-stream', 'data.bin')).toBe('none');
    expect(fileKind(undefined, undefined)).toBe('none');
  });
});
