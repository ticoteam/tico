import { describe, expect, it } from 'vitest';
import { asksOf, byDay, letterRows, senderName, titleOf, toPage, type Letter, type Thread } from './model';

const at = (min: number) => new Date(Date.UTC(2026, 0, 1, 12, min)).toISOString();
const row = (id: string, min: number, extra: Partial<Thread> = {}): Thread => ({
  id,
  title: `Thread ${id}`,
  audience: 'members',
  last_message_at: at(min),
  last_seq: 1,
  last_unread_seq: 0,
  pinned_for_everyone: false,
  root_id: id,
  ...extra,
});

describe('the threads list', () => {
  it('puts threads and DMs in one list, newest first, without archived or sub-threads', () => {
    const page = toPage({
      threads: [row('a', 1), row('old', 5, { archived_at: at(6) }), row('sub', 9, { parent_id: 'a' })],
      dms: [row('dm', 3, { audience: 'personal' })],
      next_cursor: 'n1',
    });
    expect(page.rows.map((t) => t.id)).toEqual(['dm', 'a']);
    expect(page.next).toBe('n1');
  });
});

describe('names', () => {
  it('names a DM after the other person, not its stored title', () => {
    const dm = row('d', 1, { audience: 'personal', title: 'Chat with Ana', dm_with: [{ actor: 'human:me' }, { actor: 'bot:ops' }] });
    expect(titleOf(dm, 'human:me', (a) => (a === 'bot:ops' ? 'Ops Agent' : undefined))).toBe('Ops Agent');
  });

  it('reads the sender from a mail header', () => {
    expect(senderName({ from_header: '"Ana Park" <ana@example.com>', from_addr: 'ana@example.com' })).toBe('Ana Park');
    expect(senderName({ from_addr: 'ana@example.com' })).toBe('ana@example.com');
  });

  it('shows a mail conversation once, with its newest letter and how many it holds', () => {
    const l = (id: string, thread: string, epoch: number): Letter => ({ msg_id: id, thread_id: thread, mailbox: 'm', epoch, subject: id });
    const rows = letterRows([l('1', 't', 10), l('2', 't', 20), l('3', 'u', 5)]);
    expect(rows.map((r) => [r.letter.msg_id, r.count])).toEqual([
      ['2', 2],
      ['3', 1],
    ]);
  });

  it('groups updates by day, newest day first', () => {
    const u = (id: string, day: string) => ({ id, bot: 'ops', kind: 'daily', day, created_at: `${day}T09:00:00Z` });
    expect(byDay([u('a', '2026-01-01'), u('b', '2026-01-02')]).map((g) => g.day)).toEqual(['2026-01-02', '2026-01-01']);
  });
});

describe('what asks you', () => {
  it('is a thread Catch up flags as a mention, or a DM not read; never a task; a DM names its teammate', () => {
    const dm = row('d1', 3, { audience: 'personal', dm_with: [{ actor: 'human:me' }, { actor: 'bot:gm' }], me: { unread: 1 } });
    const read = row('d2', 4, { audience: 'personal', dm_with: [{ actor: 'bot:qa' }], me: { unread: 0 } });
    const team = row('h1', 5, { audience: 'home', home: { actor: 'group:g1' } });
    const task = row('k1', 6, { task_id: 'k1' });
    const quiet = row('q1', 7);
    const a = asksOf({ items: [{ thread: team, mention: true }, { thread: task, mention: true }, { thread: quiet, mention: false }] }, [dm, read, quiet], 'human:me');
    expect([...a.threads].sort()).toEqual(['d1', 'h1']);
    expect([...a.mates]).toEqual(['bot:gm']);
    expect([...a.homes]).toEqual(['group:g1']);
  });
});
