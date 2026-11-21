import { describe, expect, it } from 'vitest';
import { asksOf, byDay, conversationsOf, letterRows, outsideOf, senderName, titleOf, toPage, type Letter, type Thread } from './model';

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

  it('lists a task\'s thread only while it holds an open @ of you: Needs you lists no @ (#4762)', () => {
    const task = row('k1', 2, { task_id: 'task-1' });
    const quiet = row('k2', 1, { task_id: 'task-2' });
    const page = toPage({ attention: [{ ...task, mentioned: true }], threads: [task, quiet] });
    expect(conversationsOf(page.rows, 'human:me').map((t) => [t.id, !!t.mentioned])).toEqual([['k1', true]]);
    expect(conversationsOf(toPage({ threads: [task, quiet] }).rows, 'human:me')).toEqual([]);
  });

  it('knows a task\'s thread a live brief added without its task_id, by audience or kind, and lists it only when marked', () => {
    const byAudience = row('b1', 3, { audience: 'task' });
    const byKind = row('b2', 2, { kind: 'task' });
    expect(conversationsOf([byAudience, byKind, row('t1', 1)], 'human:me').map((t) => t.id)).toEqual(['t1']);
    expect(conversationsOf([{ ...byAudience, mentioned: true }, byKind], 'human:me').map((t) => t.id)).toEqual(['b1']);
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
    expect(rows.map((r) => [r.letter.msg_id, r.count, r.ids])).toEqual([
      ['2', 2, ['1', '2']],
      ['3', 1, ['3']],
    ]);
  });

  it('groups updates by day, newest day first', () => {
    const u = (id: string, day: string) => ({ id, bot: 'ops', kind: 'daily', day, created_at: `${day}T09:00:00Z` });
    expect(byDay([u('a', '2026-01-01'), u('b', '2026-01-02')]).map((g) => g.day)).toEqual(['2026-01-02', '2026-01-01']);
  });
});

describe('what asks you', () => {
  it('is a thread Catch up flags as a mention, or a person\'s DM not read; an agent\'s DM only when flagged; never a task', () => {
    const dm = row('d1', 3, { audience: 'personal', dm_with: [{ actor: 'human:me' }, { actor: 'human:ana' }], me: { unread: 1 } });
    const read = row('d2', 4, { audience: 'personal', dm_with: [{ actor: 'human:jo' }], me: { unread: 0 } });
    const agent = row('d3', 5, { audience: 'personal', dm_with: [{ actor: 'bot:gm' }], me: { unread: 4 } });
    const asking = row('d4', 6, { audience: 'personal', dm_with: [{ actor: 'bot:qa' }], me: { unread: 1 } });
    const team = row('h1', 5, { audience: 'home', home: { actor: 'group:g1' } });
    const task = row('k1', 6, { task_id: 'k1' });
    const quiet = row('q1', 7);
    const a = asksOf(
      { items: [{ thread: team, mention: true }, { thread: task, mention: true }, { thread: quiet, mention: false }, { thread: asking, mention: true }] },
      [dm, read, quiet, agent, asking],
      'human:me',
    );
    expect([...a.threads].sort()).toEqual(['d1', 'd4', 'h1']);
    expect([...a.mates].sort()).toEqual(['bot:qa', 'human:ana']);
  });
});

describe('Team and Threads', () => {
  it('Threads holds every conversation but your 1:1 DMs and agents\' own rooms; nothing is in both', () => {
    const one = row('d1', 1, { audience: 'personal', dm_with: [{ actor: 'bot:qa' }] });
    const group = row('d2', 2, { audience: 'personal', dm_with: [{ actor: 'human:ana' }, { actor: 'human:jo' }], dm_with_count: 2 });
    const agent = row('h1', 3, { audience: 'home', home: { actor: 'bot:qa' } });
    const team = row('h2', 4, { audience: 'home', home: { actor: 'group:g1' } });
    const task = row('k1', 5, { task_id: 'k1' });
    expect(conversationsOf([one, group, agent, team, task, row('t1', 6)], 'human:me').map((t) => t.id)).toEqual(['d2', 'h2', 't1']);
  });

  it('a thread with a Slack destination and not a team\'s is from outside; its one agent handles it', () => {
    const members = (actors: string[]) => ({ members: { members: actors.map((actor) => ({ actor })) } });
    expect(outsideOf(row('s1', 1, members(['human:me', 'slack:C1', 'bot:fin'])))).toEqual({ handler: 'bot:fin' });
    expect(outsideOf(row('s2', 1, members(['human:me', 'slack:C1'])))).toEqual({ handler: undefined });
    expect(outsideOf(row('s3', 1, { ...members(['slack:C1', 'bot:gm']), audience: 'home', home: { actor: 'group:g1' } }))).toBeUndefined();
    expect(outsideOf(row('s4', 1, members(['human:me', 'bot:fin'])))).toBeUndefined();
  });
});
