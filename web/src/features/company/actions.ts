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
  decideProposal,
  publishBrand,
  saveBrandDraft,
  updateGoal,
  updateKpi,
  updateProduct,
  updateRisk,
  type KpiFields,
} from './queries';
import { chatAddress } from '@/lib';

/* Every change and every move a person makes on Company, Strategy, Products and Risk. Panels live in `?panel=`, so
   Back closes them; a change is shown first and taken back if the server refuses it. */

const id = s.string({ min: 1, max: 200 });
const actor = s.string({ min: 1, max: 200, pattern: /^(human|bot):\S+$/, what: 'human:<id> or bot:<slug>' });
/** A goal's owner may also be the company itself (backend/goals.py COMPANY). */
const goalOwner = s.string({ min: 1, max: 200, pattern: /^((human|bot):\S+|company)$/, what: 'company, human:<id> or bot:<slug>' });
const day = s.string({ pattern: /^\d{4}-\d{2}-\d{2}$/, what: 'a date, 2026-12-31' });

const GOALS = '/company/strategy/goals';
const BRAND = '/company/strategy/brand';
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

/** A hall's manager's chat beside the Floor (company.js companyChat): who you talk to about that hall. */
export const chatWith = defineAction({
  id: 'company.chatWith',
  title: "Open a hall manager's chat beside the Floor",
  kind: 'view',
  input: s.object({ who: actor }),
  run: ({ router }, { who }) => router.navigate({ to: '/company', search: { chat: chatAddress(who) } as never }),
  did: () => 'Opened the chat',
  target: ({ who }) => ({ kind: 'chat', id: chatAddress(who) }),
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

/** Yes or no on what a teammate proposes for a goal or a KPI (a target, a measure, new words, a flag, a why). */
export const goalDecide = defineAction({
  id: 'goals.decide',
  title: 'Decide a proposal on a goal or a KPI',
  kind: 'change',
  input: s.object({ proposal: id, decision: s.oneOf(['confirm', 'reject'] as const) }),
  run: (_ctx, { proposal, decision }) => decideProposal(proposal, decision),
  did: ({ decision }) => (decision === 'confirm' ? 'Said yes' : 'Said no'),
  target: (_input, out) => (out?.proposal.goal_id ? { kind: 'goal', id: out.proposal.goal_id } : null),
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

// ---------------------------------------------------------------- Strategy and Brand: versions and the brand draft

const versionN = s.number({ int: true, min: 1 });
const nav = (router: AnyRouter, to: string, search: Record<string, unknown> = {}) => router.navigate({ to, search });
/** Sets one key of this page's address, keeping the rest. */
const setSearch = (router: AnyRouter, patch: Record<string, unknown>) =>
  router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, ...patch }) });

export const strategyOpenVersion = defineAction({
  id: 'strategy.openVersion',
  title: 'Open a version of the strategy',
  kind: 'view',
  input: s.object({ v: s.optional(versionN) }),
  run: ({ router }, { v }) => nav(router, '/company/strategy', v ? { v } : {}),
  did: ({ v }) => (v ? `Opened v${v}` : 'Opened the latest version'),
  target: () => ({ kind: 'page', id: '/company/strategy' }),
});

export const brandOpenVersion = defineAction({
  id: 'brand.openVersion',
  title: 'Open a version of the brand book',
  kind: 'view',
  input: s.object({ v: s.optional(versionN) }),
  run: ({ router }, { v }) => nav(router, BRAND, v ? { v } : {}),
  did: ({ v }) => (v ? `Opened v${v}` : 'Opened the latest version'),
  target: () => ({ kind: 'page', id: BRAND }),
});

/** Strategy or the brand book: mark what changed from the version before, or show the clean page. */
export const markChanges = defineAction({
  id: 'strategy.markChanges',
  title: 'Mark what changed from the version before',
  kind: 'view',
  input: s.object({ on: s.boolean() }),
  run: ({ router }, { on }) => setSearch(router, { compare: on || undefined }),
  did: ({ on }) => (on ? 'Marked the changes' : 'Cleared the marks'),
});

export const brandEdit = defineAction({
  id: 'brand.edit',
  title: 'Edit the brand book',
  kind: 'view',
  input: s.object({ on: s.boolean() }),
  run: ({ router }, { on }) => nav(router, BRAND, on ? { edit: true } : {}),
  did: ({ on }) => (on ? 'Opened the brand draft' : 'Closed the brand draft'),
  target: () => ({ kind: 'page', id: BRAND }),
});

// The brand body's shapes (backend/models.py BrandBody); a row keeps the fields this form does not show.
const text = (max: number) => s.string({ max });
const brandSections = s.object({
  name: s.optional(s.nullable(text(60))),
  positioning: s.optional(s.nullable(text(120))),
  voice: s.optional(s.nullable(s.array(s.object({ trait: text(80), do: s.optional(text(300)), dont: s.optional(text(300)) }), { max: 12 }))),
  tone: s.optional(s.nullable(s.array(s.object({ situation: text(80), guide: s.optional(text(300)) }), { max: 20 }))),
  words: s.optional(
    s.nullable(
      s.object({
        use: s.optional(s.array(s.object({ word: text(80), not: s.optional(text(300)) }), { max: 100 })),
        avoid: s.optional(s.array(s.object({ word: text(80), why: s.optional(text(300)), level: s.optional(s.oneOf(['warn', 'block'] as const)) }), { max: 200 })),
      }),
    ),
  ),
  naming: s.optional(s.nullable(s.array(s.object({ name: text(80), rule: s.optional(text(300)), wrong: s.optional(s.array(text(80), { max: 20 })) }), { max: 50 }))),
  visual: s.optional(
    s.nullable(
      s.object({
        colors: s.optional(s.array(s.object({ name: text(80), hex: s.string({ pattern: /^#[0-9A-Fa-f]{3,8}$/, what: 'a color, #2f5d3a' }) }), { max: 30 })),
        fonts: s.optional(s.array(text(80), { max: 10 })),
        logo_files: s.optional(s.array(text(300), { max: 20 })),
        imagery: s.optional(text(1000)),
      }),
    ),
  ),
  claims: s.optional(
    s.nullable(
      s.object({
        allowed: s.optional(s.array(s.object({ claim: text(300), proof: s.optional(text(1000)), check: s.optional(text(300)), status: s.optional(s.oneOf(['proven', 'due'] as const)) }), { max: 100 })),
        never: s.optional(s.array(text(300), { max: 100 })),
      }),
    ),
  ),
  examples: s.optional(s.nullable(s.array(s.object({ text: text(1000), verdict: s.oneOf(['on', 'off'] as const), why: s.optional(text(300)) }), { max: 30 }))),
});

export const brandSave = defineAction({
  id: 'brand.save',
  title: 'Save sections of the brand draft',
  kind: 'change',
  input: s.object({ sections: brandSections, revision: s.optional(s.number({ int: true, min: 0 })) }),
  run: (_ctx, { sections, revision }) => saveBrandDraft(sections, revision),
  did: () => 'Saved the brand draft',
  target: () => ({ kind: 'page', id: BRAND }),
});

export const brandPublish = defineAction({
  id: 'brand.publish',
  title: 'Publish the brand draft as the next version',
  kind: 'change',
  input: s.object({ note: s.string({ max: 500 }) }),
  run: async ({ router }, { note }) => {
    const out = await publishBrand(note);
    await nav(router, BRAND);
    return out;
  },
  did: () => 'Published the brand book',
  target: () => ({ kind: 'page', id: BRAND }),
});
