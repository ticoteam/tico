import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TaskData } from './queries';
import { isSendTask, sendTasksOf, waitSlugs, waitsOnYou } from './waits';

/* An email's OK hung on a matter: which tasks are such OKs, and whether the matter opens on the email. */

const matter = (more: Partial<TaskData> = {}) => ({ id: 't5', children: [], relations: {}, ...more }) as unknown as TaskData;
const sub = { id: 't1', title: 'Send: Re: Notice → dana@example.test', status: 'open', owner_id: 'bot:lo', label: null, stage: null };

afterEach(() => vi.unstubAllGlobals());

describe('an email’s OK hung on a matter @approvals', () => {
  it('is an open "Send:" sub-task or a linked one, never the matter itself', () => {
    expect(isSendTask('Send: Re: Notice')).toBe(true);
    expect(isSendTask('Send an email')).toBe(true);
    expect(isSendTask('Sending the deck')).toBe(false);
    const t = matter({
      children: [sub, { ...sub, id: 't2', status: 'closed' }, { ...sub, id: 't3', title: 'Review: second opinion' }],
      relations: { depends_on: [{ ...sub, id: 't4', direction: 'out' }] },
    } as Partial<TaskData>);
    expect(sendTasksOf(t)).toEqual(['t1', 't4']);
  });

  it('names the emails’ tabs apart from the task’s own', () => {
    expect(waitSlugs(1, ['deadlines'])).toEqual(['email']);
    expect(waitSlugs(1, ['email'])).toEqual(['email-2']);
    expect(waitSlugs(2, [])).toEqual(['email-1', 'email-2']);
  });

  it('opens the matter on the email only while its send is still to go out', async () => {
    let state = 'waiting_approval';
    vi.stubGlobal('fetch', async (url: string) => {
      const body = String(url).endsWith('/api/v2/tasks/t1')
        ? { task: { ...sub, to_leave: [{ requirement_id: 'r1', kind: 'ok', met: false, may: ['ok', 'no'], shows: ['email:s1'] }] }, children: [] }
        : { send: { id: 's1', state, attachments: [] }, body: '', preview_complete: true };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const t = matter({ children: [sub] } as Partial<TaskData>);
    expect(await waitsOnYou(new QueryClient(), t)).toBe(true);
    state = 'canceled';
    expect(await waitsOnYou(new QueryClient(), t)).toBe(false);
    expect(await waitsOnYou(new QueryClient(), matter())).toBe(false);
  });
});
