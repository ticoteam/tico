import type { AnyRouter } from '@tanstack/react-router';
import { defineAction, s } from '@/actions';
import { meQuery } from '@/data';
import {
  addReading,
  archiveKpi,
  closeGoal,
  createGoal,
  createKpi,
  createProduct,
  createRisk,
  updateGoal,
  updateKpi,
  updateProduct,
  updateRisk,
  type KpiFields,
} from './queries';

/* Every change and every move a person makes on Company, Strategy, Products and Risk. Panels live in `?panel=`, so
   Back closes them; a change is shown first and taken back if the server refuses it. */

const id = s.string({ min: 1, max: 200 });
const actor = s.string({ min: 1, max: 200, pattern: /^(human|bot):\S+$/, what: 'human:<id> or bot:<slug>' });
/** A goal's owner may also be the company itself (backend/goals.py COMPANY). */
const goalOwner = s.string({ min: 1, max: 200, pattern: /^((human|bot):\S+|company)$/, what: 'company, human:<id> or bot:<slug>' });
const day = s.string({ pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a date, 2026-12-31' });

const GOALS = '/company/strategy/goals';
const go = (router: AnyRouter, to: string, search: Record<string, string> = {}) => router.navigate({ to, search });

// ---------------------------------------------------------------- views

export const openStage = defineAction({
  id: 'company.openStage',
  title: 'Open a stage on the Floor',
  kind: 'view',
  input: s.object({ pipeline: id, stage: id }),
  run: ({ router }, { pipeline, stage }) => go(router, '/company', { panel: `stage:${pipeline}:${stage}` }),
  did: () => 'Opened the stage',
  target: ({ stage }) => ({ kind: 'stage', id: stage }),
});

export const closePanel = defineAction({
  id: 'company.closePanel',
  title: 'Close the panel',
  kind: 'view',
  input: s.object({}),
  run: ({ router }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel: undefined }) }),
  did: () => 'Closed the panel',
});

/** A form or a record in this page's panel: 'goal-new', 'new' (a product or a risk), a product's key. */
export const openPanel = defineAction({
  id: 'company.openPanel',
  title: 'Open a panel',
  kind: 'view',
  input: s.object({ panel: s.string({ min: 1, max: 200 }) }),
  run: ({ router }, { panel }) => router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel }) }),
  did: () => 'Opened the panel',
  target: ({ panel }) => ({ kind: 'panel', id: panel }),
});

export const openTeammate = defineAction({
  id: 'company.openTeammate',
  title: 'Open a teammate',
  kind: 'view',
  input: s.object({ who: id }),
  run: ({ router }, { who }) => router.navigate({ to: `/team/${encodeURIComponent(who)}` }),
  did: () => 'Opened the teammate',
  target: ({ who }) => ({ kind: 'page', id: `/team/${who}` }),
});

export const openGoal = defineAction({
  id: 'goals.open',
  title: 'Open a goal',
  kind: 'view',
  input: s.object({ goal: id }),
  run: ({ router }, { goal }) => go(router, GOALS, { panel: `goal:${goal}` }),
  did: () => 'Opened the goal',
  target: ({ goal }) => ({ kind: 'goal', id: goal }),
});

export const openKpi = defineAction({
  id: 'kpis.open',
  title: 'Open a KPI',
  kind: 'view',
  input: s.object({ kpi: id }),
  run: ({ router }, { kpi }) => go(router, GOALS, { panel: `kpi:${kpi}` }),
  did: () => 'Opened the KPI',
  target: ({ kpi }) => ({ kind: 'kpi', id: kpi }),
});

export const openKpiForm = defineAction({
  id: 'kpis.openForm',
  title: 'Add or edit a KPI',
  kind: 'view',
  input: s.object({ goal: s.optional(id), kpi: s.optional(id) }),
  run: ({ router }, { goal, kpi }) => go(router, GOALS, { panel: kpi ? `kpi-edit:${kpi}` : `kpi-new:${goal ?? ''}` }),
  did: () => 'Opened the KPI form',
  target: () => ({ kind: 'panel', id: 'kpi-form' }),
});

export const openProduct = defineAction({
  id: 'products.open',
  title: 'Open a product',
  kind: 'view',
  input: s.object({ product: id }),
  run: ({ router }, { product }) => go(router, '/company/products', { panel: product }),
  did: () => 'Opened the product',
  target: ({ product }) => ({ kind: 'product', id: product }),
});

export const openRisk = defineAction({
  id: 'risk.open',
  title: 'Open a risk',
  kind: 'view',
  input: s.object({ risk: id }),
  run: ({ router }, { risk }) => go(router, '/company/risk', { risk }),
  did: () => 'Opened the risk',
  target: ({ risk }) => ({ kind: 'risk', id: risk }),
});

export const openMatter = defineAction({
  id: 'risk.openMatter',
  title: 'Open a matter',
  kind: 'view',
  input: s.object({ task: id }),
  run: ({ router }, { task }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, panel: `matter:${task}` }) }),
  did: () => 'Opened the matter',
  target: ({ task }) => ({ kind: 'matter', id: task }),
});

// ---------------------------------------------------------------- goals and KPIs

export const goalCreate = defineAction({
  id: 'goals.create',
  title: 'Add a goal',
  kind: 'change',
  input: s.object({ title: s.string({ min: 1, max: 300 }), owner: goalOwner, parent: s.optional(id) }),
  run: (_ctx, { title, owner, parent }) => createGoal({ title, owner, parent_id: parent ?? null }),
  did: ({ title }) => `Added “${title}”`,
  target: (_input, out) => (out ? { kind: 'goal', id: out.goal.id } : null),
});

export const goalUpdate = defineAction({
  id: 'goals.update',
  title: 'Change a goal',
  kind: 'change',
  input: s.object({ goal: id, title: s.optional(s.string({ min: 1, max: 300 })), owner: s.optional(goalOwner), body: s.optional(s.string({ max: 4000 })) }),
  run: (_ctx, { goal, ...patch }) => updateGoal(goal, patch),
  did: () => 'Changed the goal',
  target: ({ goal }) => ({ kind: 'goal', id: goal }),
});

export const goalClose = defineAction({
  id: 'goals.close',
  title: 'Close a goal',
  kind: 'change',
  input: s.object({ goal: id, status: s.oneOf(['done', 'dropped'] as const) }),
  run: (_ctx, { goal, status }) => closeGoal(goal, status),
  did: ({ status }) => (status === 'done' ? 'Marked the goal done' : 'Archived the goal'),
});

const kpiFields = {
  name: s.string({ min: 1, max: 200 }),
  unit: s.string({ max: 20 }),
  direction: s.oneOf(['up', 'down'] as const),
  target: s.nullable(s.number()),
  deadline: s.nullable(day),
  owner: s.nullable(actor),
};
const fields = (i: KpiFields): KpiFields => ({ name: i.name, unit: i.unit, direction: i.direction, target: i.target, deadline: i.deadline, owner: i.owner });

export const kpiCreate = defineAction({
  id: 'kpis.create',
  title: 'Add a KPI to a goal',
  kind: 'change',
  input: s.object({ goal: id, ...kpiFields }),
  run: async ({ router }, input) => {
    const out = await createKpi(input.goal, fields(input));
    await go(router, GOALS, { panel: `goal:${input.goal}` });
    return out;
  },
  did: ({ name }) => `Added “${name}”`,
  target: (_input, out) => (out ? { kind: 'kpi', id: out.kpi.id } : null),
});

export const kpiUpdate = defineAction({
  id: 'kpis.update',
  title: 'Change a KPI',
  kind: 'change',
  input: s.object({ kpi: id, goal: id, ...kpiFields }),
  run: async ({ router }, input) => {
    await go(router, GOALS, { panel: `kpi:${input.kpi}` });
    return updateKpi(input.kpi, input.goal, fields(input));
  },
  did: () => 'Changed the KPI',
  target: ({ kpi }) => ({ kind: 'kpi', id: kpi }),
});

export const kpiArchive = defineAction({
  id: 'kpis.archive',
  title: 'Archive a KPI',
  kind: 'change',
  input: s.object({ kpi: id }),
  run: (_ctx, { kpi }) => archiveKpi(kpi),
  did: () => 'Archived the KPI',
});

export const kpiAddReading = defineAction({
  id: 'kpis.addReading',
  title: 'Add a KPI reading',
  kind: 'change',
  input: s.object({ kpi: id, value: s.number() }),
  run: ({ queryClient }, { kpi, value }) => addReading(kpi, value, queryClient.getQueryData(meQuery.queryKey)?.id ?? null),
  did: ({ value }) => `Added a reading of ${value}`,
  target: ({ kpi }) => ({ kind: 'kpi', id: kpi }),
});

// ---------------------------------------------------------------- products and risk

export const PRODUCT_FIELDS = ['kind', 'status', 'tax_kind', 'owner_teammate'] as const;

export const productCreate = defineAction({
  id: 'products.create',
  title: 'Add a product',
  kind: 'change',
  input: s.object({ name: s.string({ min: 1, max: 200 }) }),
  run: (_ctx, { name }) => createProduct(name),
  did: ({ name }) => `Added ${name}`,
  target: (_input, out) => (out ? { kind: 'product', id: out.product.key } : null),
});

export const productUpdate = defineAction({
  id: 'products.update',
  title: 'Change a product',
  kind: 'change',
  input: s.object({ product: id, field: s.oneOf(PRODUCT_FIELDS), value: s.string({ max: 200 }) }),
  run: (_ctx, { product, field, value }) => updateProduct(product, { [field]: value }),
  did: () => 'Saved',
  target: ({ product, field }) => ({ kind: 'fact', id: `${product}:${field}` }),
});

export const riskCreate = defineAction({
  id: 'risk.create',
  title: 'Add a risk',
  kind: 'change',
  input: s.object({ label: s.string({ min: 1, max: 200 }) }),
  run: (_ctx, { label }) => createRisk(label),
  did: ({ label }) => `Added ${label}`,
  target: (_input, out) => (out ? { kind: 'risk', id: out.risk.id } : null),
});

export const riskUpdate = defineAction({
  id: 'risk.update',
  title: 'Change a risk',
  kind: 'change',
  input: s.object({ risk: id, version: s.number({ int: true, min: 0 }), label: s.optional(s.string({ min: 1, max: 200 })), owner: s.optional(actor) }),
  run: (_ctx, { risk, version, ...patch }) => updateRisk(risk, patch, version),
  did: () => 'Changed the risk',
  target: ({ risk }) => ({ kind: 'risk', id: risk }),
});
