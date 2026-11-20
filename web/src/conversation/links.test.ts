import { describe, expect, it } from 'vitest';
import { pointerOf, withFileRefs } from './links';

const HERE = 'https://fernhill.tico.team';
const at = (href: string) => pointerOf(href, HERE);

describe('pointerOf', () => {
  it('names a place on the web by what it is', () => {
    expect(at('https://github.com/fernhill/shop/pull/412')).toMatchObject({ kind: 'web', icon: 'pr', label: 'shop #412', site: 'github.com' });
    expect(at('https://github.com/fernhill/shop/issues/9')).toMatchObject({ icon: 'issue', label: 'shop #9' });
    expect(at('https://x.com/fernhill/status/123')).toMatchObject({ icon: 'message', label: '@fernhill' });
    expect(at('https://www.youtube.com/watch?v=abc')).toMatchObject({ icon: 'video', label: 'YouTube' });
    expect(at('https://www.loom.com/share/abc')).toMatchObject({ label: 'Loom' });
    expect(at('https://docs.google.com/spreadsheets/d/abcdefghijkl/edit')).toMatchObject({ label: 'Google Sheet' });
    expect(at('https://www.homedepot.com/p/cedar-raised-bed-4ft/31')).toMatchObject({ icon: 'globe', label: 'homedepot.com/…' });
    expect(at('https://www.homedepot.com/p/cedar-raised-bed-4ft')).toMatchObject({ label: 'homedepot.com · Cedar raised bed 4ft' });
    expect(at('https://fernhill.example')).toMatchObject({ label: 'fernhill.example' });
  });

  it('reads an address on this app, a relative one or an old one as the app’s', () => {
    expect(at('#/task/12')).toEqual({ kind: 'app', to: '/task/12', icon: 'tasks', what: 'task' });
    expect(at('/#/task/12')).toMatchObject({ kind: 'app', to: '/task/12' });
    expect(at(`${HERE}/#/task/12`)).toMatchObject({ kind: 'app', to: '/task/12' });
    expect(at(`${HERE}/team/growth-manager`)).toMatchObject({ kind: 'app', what: 'teammate' });
    expect(at('/threads/t9')).toMatchObject({ kind: 'app', what: 'thread' });
  });

  it('reads the app’s files as files, by any of their addresses', () => {
    expect(at('/api/v2/files/file-0123456789abcdef/versions/2')).toEqual({ kind: 'file', file: 'file-0123456789abcdef', version: 2 });
    expect(at(`${HERE}/api/v2/files/f1?v=3`)).toEqual({ kind: 'file', file: 'f1', version: 3 });
    expect(at('#/page/file-0123456789abcdef')).toMatchObject({ kind: 'file', file: 'file-0123456789abcdef' });
    expect(at('/library/files?file=f2')).toMatchObject({ kind: 'file', file: 'f2' });
  });

  it('leaves what is neither alone', () => {
    expect(at('mailto:ana@fernhill.example')).toBeNull();
    expect(at('/api/v2/me')).toBeNull();
  });
});

describe('withFileRefs', () => {
  it('makes a page ref a link, never inside code or another link', () => {
    expect(withFileRefs('Spec: page:file-0123456789abcdef@2.')).toBe('Spec: [file-0123456789abcdef](/api/v2/files/file-0123456789abcdef/versions/2).');
    expect(withFileRefs('see `file-0123456789abcdef@2`')).toBe('see `file-0123456789abcdef@2`');
    expect(withFileRefs('[spec](#/page/file-0123456789abcdef)')).toBe('[spec](#/page/file-0123456789abcdef)');
  });
});
