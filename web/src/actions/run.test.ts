import type { AnyRouter } from '@tanstack/react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flight, waitForTarget, wash } from '@/motion';
import { bind } from './bind';
import { runIn, showIn } from './run';
import { s } from './schema';
import { defineAction, type AnyAction } from './types';

vi.mock('@/data', () => ({ queryClient: {} }));
vi.mock('@/motion', () => ({ flight: vi.fn(), waitForTarget: vi.fn(), wash: vi.fn() }));
vi.mock('@/live', () => ({ onTopic: vi.fn(() => () => {}) }));
vi.mock('@/search', () => ({ openSearch: vi.fn() }));

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
