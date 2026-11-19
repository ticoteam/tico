import type { AnyRouter } from '@tanstack/react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flight, waitForTarget, wash } from '@/motion';
import { bind } from './bind';
import { runIn, showIn } from './run';
import { s } from './schema';
import { defineAction, type AnyAction } from './types';

// the real module, so feature actions that set up their queries on load still find what they use
vi.mock('@/data', async (real) => ({ ...(await real<object>()), queryClient: {} }));
vi.mock('@/motion', async (real) => ({ ...(await real<object>()), flight: vi.fn(), waitForTarget: vi.fn(), wash: vi.fn() }));
vi.mock('@/live', async (real) => ({ ...(await real<object>()), onTopic: vi.fn(() => () => {}) }));
vi.mock('@/search', async (real) => ({ ...(await real<object>()), openSearch: vi.fn() }));

const router = {} as AnyRouter;
const did = vi.fn();
const setOwner = defineAction({
  id: 'task.setOwner',
  title: 'Give a task to someone',
  kind: 'change',
  input: s.object({ task: s.number({ int: true, min: 1 }), owner: s.string({ min: 1 }) }),
  can: (_ctx, { task }) => task !== 13,
  run: (_ctx, i) => did(i),
  show: () => did('show'),
  did: ({ task }) => `Gave #${task} away`,
  target: ({ task }) => ({ kind: 'task', id: task }),
});
const actions = new Map<string, AnyAction>([[setOwner.id, setOwner as AnyAction]]);
const dotEl = document.createElement('span');
const dot = { el: vi.fn<() => Element | null>() };
bind({ dot: dot.el });
const row = document.createElement('div');

beforeEach(() => {
  vi.clearAllMocks();
  dot.el.mockReturnValue(dotEl);
  vi.mocked(waitForTarget).mockResolvedValue(row);
});

describe('run', () => {
  it.each(['click', 'key', 'search'] as const)('runs from %s without a flight', async (via) => {
    const r = await runIn(actions, 'task.setOwner', { task: 412, owner: 'agent:qa' }, via, router);
    expect(r).toEqual({ ok: true, did: 'Gave #412 away' });
    expect(did).toHaveBeenCalledWith({ task: 412, owner: 'agent:qa' });
    expect(waitForTarget).not.toHaveBeenCalled();
    expect(flight).not.toHaveBeenCalled();
  });

  it('from Tico, flies from the Dot to the target once the change lands', async () => {
    const r = await runIn(actions, 'task.setOwner', { task: 412, owner: 'agent:qa' }, 'tico', router);
    expect(r.ok).toBe(true);
    expect(waitForTarget).toHaveBeenCalledWith('task', 412, 1500, { show: false, label: 'Gave #412 away' });
    expect(flight).toHaveBeenCalledWith(dotEl, row, { kind: 'edit', label: 'Gave #412 away' });
  });

  it('from Tico with no Dot drawn, washes the target instead', async () => {
    dot.el.mockReturnValue(null);
    await runIn(actions, 'task.setOwner', { task: 412, owner: 'agent:qa' }, 'tico', router);
    expect(flight).not.toHaveBeenCalled();
    expect(wash).toHaveBeenCalledWith(row);
  });

  it('from Tico with no target on screen, still succeeds and does not fly', async () => {
    vi.mocked(waitForTarget).mockResolvedValue(null);
    const r = await runIn(actions, 'task.setOwner', { task: 412, owner: 'agent:qa' }, 'tico', router);
    expect(r.ok).toBe(true);
    expect(flight).not.toHaveBeenCalled();
  });

  it('once the change is made, a throw finding the target or wording the result does not turn it into a failure', async () => {
    const odd = defineAction({
      ...setOwner,
      id: 'task.odd',
      did: () => {
        throw new Error('no words');
      },
      target: () => {
        throw new Error('no target');
      },
    });
    const only = new Map<string, AnyAction>([[odd.id, odd as AnyAction]]);
    const input = { task: 412, owner: 'agent:qa' };
    expect(await runIn(only, 'task.odd', input, 'tico', router)).toEqual({ ok: true, did: 'Give a task to someone' });
    expect(await showIn(only, 'task.odd', input, router)).toEqual({ ok: true, did: 'Give a task to someone' });
    vi.mocked(waitForTarget).mockRejectedValueOnce(new SyntaxError('bad selector'));
    expect(await runIn(actions, 'task.setOwner', input, 'tico', router)).toEqual({ ok: true, did: 'Gave #412 away' });
    expect(flight).not.toHaveBeenCalled();
  });

  it('refuses when can is false, without running', async () => {
    const r = await runIn(actions, 'task.setOwner', { task: 13, owner: 'agent:qa' }, 'tico', router);
    expect(r).toEqual({ ok: false, error: 'not_allowed' });
    expect(did).not.toHaveBeenCalled();
    expect(flight).not.toHaveBeenCalled();
  });

  it('refuses bad input with a readable error', async () => {
    expect(await runIn(actions, 'task.setOwner', { task: 4.5, owner: 'x' }, 'tico', router)).toEqual({
      ok: false,
      error: 'bad_input: task: expected a whole number',
    });
    expect(await runIn(actions, 'task.setOwner', { task: 4 }, 'search', router)).toEqual({
      ok: false,
      error: 'bad_input: owner: is required',
    });
    expect(await runIn(actions, 'task.setOwner', { task: 4, owner: 'x', who: 1 }, 'tico', router)).toEqual({
      ok: false,
      error: 'bad_input: who: is not a known field',
    });
    expect(did).not.toHaveBeenCalled();
  });

  it('refuses an action kept from Tico, from Tico only', async () => {
    const mine = defineAction({ ...setOwner, id: 'task.confirm', tico: false });
    const only = new Map<string, AnyAction>([[mine.id, mine as AnyAction]]);
    const input = { task: 412, owner: 'agent:qa' };
    expect(await runIn(only, 'task.confirm', input, 'tico', router)).toEqual({ ok: false, error: 'not_allowed' });
    expect(await showIn(only, 'task.confirm', input, router)).toEqual({ ok: false, error: 'not_allowed' });
    expect(did).not.toHaveBeenCalled();
    expect(await runIn(only, 'task.confirm', input, 'click', router)).toEqual({ ok: true, did: 'Gave #412 away' });
  });

  it("gives the action's output to the person's own press, never to Tico", async () => {
    did.mockReturnValue({ secret: 'shown-once' });
    const input = { task: 412, owner: 'agent:qa' };
    expect(await runIn(actions, 'task.setOwner', input, 'click', router)).toEqual({ ok: true, did: 'Gave #412 away', out: { secret: 'shown-once' } });
    const fromTico = await runIn(actions, 'task.setOwner', input, 'tico', router);
    expect(fromTico).toEqual({ ok: true, did: 'Gave #412 away' });
    expect('out' in fromTico).toBe(false);
    did.mockReset();
  });

  it('refuses an unknown action', async () => {
    expect(await runIn(actions, 'task.nope', {}, 'tico', router)).toEqual({ ok: false, error: 'unknown_action: task.nope' });
  });

  it('reports a refusal from the server', async () => {
    did.mockRejectedValueOnce(Object.assign(new Error('no'), { status: 403, code: 'forbidden' }));
    expect(await runIn(actions, 'task.setOwner', { task: 1, owner: 'x' }, 'click', router)).toEqual({ ok: false, error: 'not_allowed' });
    did.mockRejectedValueOnce(Object.assign(new Error('gone'), { status: 404, code: 'task_not_found' }));
    expect(await runIn(actions, 'task.setOwner', { task: 1, owner: 'x' }, 'click', router)).toEqual({ ok: false, error: 'task_not_found' });
  });

  it('done from Tico shows only: no write, then the flight', async () => {
    const r = await showIn(actions, 'task.setOwner', { task: 412, owner: 'agent:qa' }, router);
    expect(r).toEqual({ ok: true, did: 'Gave #412 away' });
    expect(did).toHaveBeenCalledTimes(1);
    expect(did).toHaveBeenCalledWith('show');
    expect(waitForTarget).toHaveBeenCalledWith('task', 412, 1500, { show: true, label: 'Gave #412 away' });
    expect(flight).toHaveBeenCalledWith(dotEl, row, { kind: 'open', label: 'Gave #412 away' });
  });
});
