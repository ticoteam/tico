import { describe, expect, it } from 'vitest';
import { patchThreadList, type ThreadListData, type ThreadListRow } from './patch';

const at = (min: number) => new Date(Date.UTC(2026, 0, 1, 12, min)).toISOString();
const row = (id: string, min: number, extra: Partial<ThreadListRow> & { title?: string } = {}) => ({ id, title: `Thread ${id}`, last_message_at: at(min), ...extra });
type Row = ReturnType<typeof row>;
const list = (...rows: Row[]): ThreadListData<Row> => ({ pages: [{ rows, next: null }], pageParams: [null] });
const ids = (d: ThreadListData<Row> | null) => d?.pages.flatMap((p) => p.rows.map((t) => t.id));

describe('the Threads list from the threads topic', () => {
  it('moves a thread with new activity to the top and shows its unread count', () => {
    const next = patchThreadList(list(row('a', 5), row('b', 1)), { kind: 'activity', thread_id: 'b', root_id: 'b', unread_count: 3, thread: { id: 'b', last_message_at: at(9) } });
    expect(ids(next)).toEqual(['b', 'a']);
    expect(next?.pages[0]?.rows[0]?.me?.unread).toBe(3);
  });

  it('asks for a read when activity comes for a thread it never held and cannot place', () => {
    expect(patchThreadList(list(row('a', 5)), { kind: 'activity', thread_id: 'x/sub', root_id: 'x' })).toBeNull();
  });

  it('takes an archived thread out and puts a renamed one right in place', () => {
    const start = list(row('a', 5), row('b', 1));
    expect(ids(patchThreadList(start, { kind: 'archived', thread_id: 'a', root_id: 'a' }))).toEqual(['b']);
    const renamed = patchThreadList(start, { kind: 'renamed', thread_id: 'b', root_id: 'b', thread: { ...row('b', 1), title: 'Launch plan' } });
    expect(renamed?.pages[0]?.rows.find((t) => t.id === 'b')?.title).toBe('Launch plan');
  });

  it('asks for a read when the reader\'s attention moves on a task\'s thread, a marked one, or one it does not hold', () => {
    const start = list(row('plain', 5), row('task', 4, { task_id: 'k1' }), row('marked', 3, { mentioned: true }));
    const attention = (id: string) => patchThreadList(start, { kind: 'attention', thread_id: id, root_id: id });
    expect([attention('task'), attention('marked'), attention('new')]).toEqual([null, null, null]);
    expect(attention('plain')).toBe(start);
  });

  it('clears the unread count on read, and leaves the list as it was when nothing changed', () => {
    const start = list(row('a', 5, { me: { unread: 4 } }));
    expect(patchThreadList(start, { kind: 'read', thread_id: 'a', root_id: 'a', last_read_seq: 9 })?.pages[0]?.rows[0]?.me?.unread).toBe(0);
    expect(patchThreadList(start, { kind: 'counters', thread_id: 'zz', root_id: 'zz' })).toBe(start);
  });
});
