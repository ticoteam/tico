import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import type { GetOrgResponse } from '@/api/schema';
import { buildDirectory, useActor, useDirectory, withStatus, type Directory } from './directory';
import { keys } from './keys';
import { queryClient } from './query-client';

afterEach(() => queryClient.clear());

const access = { see: true, read: true, write: true, manage: false };
const org = {
  people: [{ id: 'p1', name: 'Dana Whit', title: 'Founder', team: 'g1', org_parent: '' }],
  bots: [
    { id: 'builder', display_name: 'Builder', team: 'g1', status: 'active', org_parent: 'g:g1', access },
    { id: 'p1', display_name: 'Same slug', status: 'active', org_parent: '', access },
  ],
  org_groups: [{ id: 'g1', name: 'Engineering' }],
  seats: [],
  teams: {},
} as unknown as GetOrgResponse;

const dir = () => buildDirectory(org, [{ bot: 'builder', state: 'running', focus: 'Fixing the login page' }]);

describe('directory', () => {
  it('names people and agents, with team names and status lines', () => {
    const d = dir();
    expect(d.byId['human:p1']).toMatchObject({ kind: 'person', name: 'Dana Whit', title: 'Founder', team: 'Engineering' });
    expect(d.byId['bot:builder']).toMatchObject({
      kind: 'agent',
      name: 'Builder',
      slug: 'builder',
      team: 'Engineering',
      status: 'Fixing the login page',
      working: true,
    });
    expect(d.byId['human:p1']?.hue).toBe(buildDirectory(org, []).byId['human:p1']?.hue);
  });

  it('answers bare ids: an agent slug first, a person id where no agent has it', () => {
    const d = dir();
    expect(d.byId['builder']).toBe(d.byId['bot:builder']);
    expect(d.byId['p1']).toBe(d.byId['bot:p1']);
    expect(d.actors).toHaveLength(3);
  });

  it('reads who each one reports to, the teams in their order with their leads, and the Assistant', () => {
    const d = buildDirectory(
      {
        people: [
          { id: 'p1', name: 'Dana Whit', team: 'g1', reports_to: '' },
          { id: 'p2', name: 'Lee Park', team: 'g2', reports_to: 'p1' },
        ],
        bots: [
          { id: '0190-em', slug: 'em', display_name: 'Engineering Manager', team: 'g1', reports_to: 'human:p1', access },
          { id: '0191-qa', slug: 'qa', display_name: 'QA Owner', team: 'g1', reports_to: 'em', access },
          { id: '0192-ops', slug: 'ops', display_name: 'Ops', team: '', reports_to: 'p1', access },
          { id: 'assistant', slug: 'assistant', display_name: 'Assistant', template: 'assistant', reports_to: 'human:p1', access },
          { id: '0193-lost', slug: 'lost', display_name: 'Lost', team: 'g9', reports_to: 'nobody', access },
        ],
        org_groups: [
          { id: 'g1', name: 'Engineering', parent: '', order: 1, lead: 'bot:0190-em' },
          { id: 'g2', name: 'Platform', parent: 'g1', order: 2, lead: 'person:p2' },
        ],
        seats: [],
        teams: {},
      } as unknown as GetOrgResponse,
      [],
    );
    expect(d.byId['human:p2']).toMatchObject({ teamId: 'g2', reportsTo: 'human:p1' });
    expect(d.byId['bot:0190-em']).toMatchObject({ teamId: 'g1', reportsTo: 'human:p1' });
    // a slug, and a bare person id, read as who they name
    expect(d.byId['bot:0191-qa']?.reportsTo).toBe('bot:0190-em');
    expect(d.byId['bot:0192-ops']?.reportsTo).toBe('human:p1');
    expect(d.byId['bot:0192-ops']?.teamId).toBeUndefined();
    expect(d.byId['bot:assistant']?.assistant).toBe(true);
    // no one there, no team there: neither is kept
    expect([d.byId['bot:0193-lost']?.reportsTo, d.byId['bot:0193-lost']?.teamId]).toEqual([undefined, undefined]);
    expect(d.teams).toEqual([
      { id: 'g1', name: 'Engineering', parent: '', order: 1, lead: 'bot:0190-em' },
      { id: 'g2', name: 'Platform', parent: 'g1', order: 2, lead: 'human:p2' },
    ]);
  });

  it('changes only the one agent whose status moved', () => {
    const d = dir();
    expect(withStatus(d, 'builder', { bot: 'builder', state: 'running', focus: 'Fixing the login page' })).toBe(d);
    const next = withStatus(d, 'builder', { bot: 'builder', state: 'idle', focus: '' }) as Directory;
    expect(next.byId['bot:builder']).toMatchObject({ status: undefined, working: false });
    expect(next.byId['builder']).toBe(next.byId['bot:builder']);
    expect(next.byId['human:p1']).toBe(d.byId['human:p1']);
    expect(withStatus(d, 'newcomer', null)).toBeNull();
  });

  it('keeps a row still when another agent changes', () => {
    queryClient.setQueryData(keys.directory(), dir());
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: queryClient }, children);
    let renders = 0;
    const { result } = renderHook(
      () => {
        renders++;
        return useActor('human:p1');
      },
      { wrapper },
    );
    expect(result.current?.name).toBe('Dana Whit');
    const before = renders;
    act(() => {
      queryClient.setQueryData(keys.directory(), (d: Directory | undefined) => d && withStatus(d, 'builder', null));
    });
    expect(renders).toBe(before);
  });

  it('draws a selected part again only when that part changes', () => {
    queryClient.setQueryData(keys.directory(), dir());
    const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: queryClient }, children);
    let renders = 0;
    const select = (d: Directory) => !!d.byId['bot:builder'];
    const { result } = renderHook(
      () => {
        renders++;
        return useDirectory(select);
      },
      { wrapper },
    );
    expect(result.current).toBe(true);
    const before = renders;
    act(() => {
      queryClient.setQueryData(keys.directory(), (d: Directory | undefined) => d && withStatus(d, 'builder', null));
    });
    expect(renders).toBe(before);
  });
});
